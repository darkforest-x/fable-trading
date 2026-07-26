"""Judge short_tip_v2 against the owner's existing gold verdicts on v1b.

Iron rule 12: a detector is promoted on real-tip gold + tip-smoke only; its own
val/mAP never decides. v2 trained to P 0.844 / mAP50 0.897 on its own val, which
says the fit converged and nothing else.

The gold already exists and costs nothing more to use. Reviewing v1b's output the
owner marked 279 boxes: 51 keep (a real dense cluster at the tip) and 228 drop
(not one). Those are the same tip-window images either detector sees, so:

  on the 51 KEEPs -> v2 should still fire   (it must not lose true clusters)
  on the 228 DROPs -> v2 should stay silent (the whole point of the rebuild)

Both halves matter. A detector that fires on nothing scores perfectly on the
drops, so the keep-side recall is reported next to it and neither number is
quoted alone.

Note what this can and cannot say. These images were SELECTED by v1b, so they
are v1b's output distribution, not a fresh sample of the market: the drop-side
number is "how much of v1b's garbage does v2 refuse", not v2's own precision.
v2's precision needs a fresh pack the owner reviews from scratch, which is the
next step. This is the cheap, honest go/no-go before spending that review time.

Read-only. No promote. No holdout.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/eval_short_tip_v2_vs_owner_gold.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)
from src.detection.render import make_chart_transform  # noqa: E402

PACK = PROJECT / "analysis" / "output" / "owner_side_short_tip_v1b_detect1000"
W_V1B = PROJECT / "runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt"
W_V2 = PROJECT / "runs/detect/runs/detect/owner_side_short_tip_v2/weights/best.pt"
W_V3 = PROJECT / "runs/detect/runs/detect/owner_side_short_tip_v3/weights/best.pt"
TIP_EDGE_BARS = 2


def bar_tf():
    dummy = pd.DataFrame({c: np.ones(WINDOW) for c in ("open", "high", "low", "close")})
    return make_chart_transform(dummy)


def fires(model, paths: list[Path], tf, conf: float) -> list[bool]:
    """True if the model puts a box within TIP_EDGE_BARS of the right edge."""
    out: list[bool] = []
    for i in range(0, len(paths), 16):
        chunk = paths[i:i + 16]
        res = model.predict([str(p) for p in chunk], conf=conf, verbose=False, device="cpu")
        for r in res:
            b = r.boxes
            if b is None or len(b) == 0:
                out.append(False)
                continue
            hit = False
            for row in b.xywhn.cpu().numpy():
                cx, w = float(row[0]), float(row[2])
                bar = right_edge_to_bar(cx + w / 2, 0.0, tf, n_bars=WINDOW)
                if (WINDOW - 1) - bar <= TIP_EDGE_BARS:
                    hit = True
                    break
            out.append(hit)
    return out


def main() -> int:
    sheet = pd.read_csv(PACK / "review_sheet.csv")
    graded = sheet[sheet["owner_keep"].isin(["keep", "drop"])].copy()
    if graded.empty:
        print("no owner verdicts yet")
        return 1
    keeps = graded[graded["owner_keep"] == "keep"]
    drops = graded[graded["owner_keep"] == "drop"]
    print(f"owner 判过的: keep {len(keeps)} / drop {len(drops)}\n")

    tf = bar_tf()
    res: dict = {}
    for tag, wp in (("v1b", W_V1B), ("v2", W_V2), ("v3", W_V3)):
        if not wp.exists():
            print(f"missing weights: {wp}")
            return 2
        model = load_yolo_model(str(wp))
        row = {}
        for name, sub in (("keep", keeps), ("drop", drops)):
            paths = [PACK / str(p) for p in sub["image"]]
            paths = [p for p in paths if p.exists()]
            f = fires(model, paths, tf, DEFAULT_CONF)
            row[name] = {"n": len(f), "fired": int(sum(f)),
                         "rate": round(float(np.mean(f)), 4) if f else None}
        res[tag] = row
        k, d = row["keep"], row["drop"]
        print(f"[{tag}] 在 owner 认可的 51 个上开火: {k['fired']}/{k['n']} = {k['rate']*100:.1f}%")
        print(f"[{tag}] 在 owner 否掉的 228 个上开火: {d['fired']}/{d['n']} = {d['rate']*100:.1f}%"
              f"   (越低越好)")

    v2k, v2d = res["v3"]["keep"]["rate"], res["v3"]["drop"]["rate"]
    v1k, v1d = res["v1b"]["keep"]["rate"], res["v1b"]["drop"]["rate"]
    print(f"\n变化(v1b → v3): 保住真检出 {v1k*100:.1f}% → {v2k*100:.1f}% ；"
          f"误检复现 {v1d*100:.1f}% → {v2d*100:.1f}%")
    if v2d < v1d * 0.5 and v2k >= 0.5:
        verdict = "v3 明显更好:大幅拒绝 v1b 的误检,同时保住多数真检出 → 值得做新一轮金标"
    elif v2k < 0.3:
        verdict = "v3 过于保守:真检出也丢了 → 不能只看误检下降"
    elif v2d >= v1d * 0.8:
        verdict = "v3 没有实质改善:仍复现 v1b 的多数误检"
    else:
        verdict = "v3 有改善但不决定性,需新一轮金标再判"
    print(f"判读: {verdict}")

    (PROJECT / "analysis" / "output" / "eval_short_tip_v2_vs_owner_gold.json").write_text(
        json.dumps({"pack": str(PACK.relative_to(PROJECT)), "conf": DEFAULT_CONF,
                    "tip_edge_bars": TIP_EDGE_BARS, "results": res,
                    "verdict": verdict,
                    "caveat": "images were selected BY v1b; the drop-side number is "
                              "'how much of v1b's garbage v2 refuses', not v2's own "
                              "precision, which needs a fresh owner-reviewed pack"},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

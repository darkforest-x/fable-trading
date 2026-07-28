"""v9 fires 137-274x more often than the owner labels. Can a threshold fix that?

The gold comparison that promoted v9 measured one direction only: of the owner's
206 short gold tips, how many does v9 fire on (84.0%). It never measured how often
v9 fires overall, and that number is disqualifying:

    owner's own labelling density   0.18 - 0.36 signals per symbol-month
    v9 in production                48.8 signals per symbol-month

A detector that fires everywhere has high recall by construction, so 84% was never
evidence of agreement. The promotion criterion rewarded exactly the behaviour the
owner objected to on sight.

The question this answers is whether confidence separates the two populations:

  SEPARABLE   v9 scores the owner's gold higher than it scores everything else,
              so raising the floor keeps recall while cutting the flood. There is
              a usable operating point and it is a one-line change.
  FLAT        gold and non-gold get similar confidence, so the floor cuts both in
              proportion. No threshold works, and the fault is in how the training
              set was built -- there were never enough negatives to teach it what
              NOT to fire on. That needs a retrain, not a knob.

Both curves are measured on the same sweep: recall against the 206 gold tips, and
fire density per symbol-month over a real scan window, at each threshold. They
have to be read together -- either alone is what produced this mistake.

Read-only. No promote, no threshold change (that is an owner decision).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v9_precision_vs_recall.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS, add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402
from scripts.build_htip_dataset import resolve_series  # noqa: E402
from scripts.build_star_tip_dataset_v9 import (  # noqa: E402
    archive_index, load_star_boxes, star_side, symbol_of,
)

DEFAULT_WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt"
WEIGHTS = DEFAULT_WEIGHTS          # overridden by --weights
FLOOR = 0.01                       # scan far below anything usable
GRID = (0.01, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
DENSITY_SYMBOLS = 12               # symbols used to measure fire density
DENSITY_BARS = 2000                # bars each, ~21 days at 15m
OWNER_DENSITY = (0.18, 0.36)       # per symbol-month, from 506 stars / 237 symbols


def gold_confidences(model, limit: int = 400) -> list[float]:
    """Best tip-aligned confidence on each of the owner's short gold tips."""
    known = {s for (_x, s) in list_series(bar="15m")}
    arch = archive_index()
    stars = load_star_boxes()
    tmp = PROJECT / "data" / "_pr.png"
    out: list[float] = []
    frames: dict[str, pd.DataFrame | None] = {}

    for stem, boxes in list(stars.items())[:limit]:
        sym = symbol_of(stem, known)
        if sym is None:
            continue
        base = resolve_series(sym)
        if base is None:
            continue
        framed = add_mas(base)
        m = re.search(r"_(\d+)$", stem)
        if not m:
            continue
        stored = cv2.imread(str(arch[stem])) if stem in arch else None
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed,
                              stored_img=stored)
        if r is None:
            continue
        _mo, ws, _mad = r
        sub_old = framed.iloc[ws:ws + WINDOW].reset_index(drop=True)
        if len(sub_old) != WINDOW:
            continue
        _c, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub_old))
        if not spans:
            continue
        _b0, b1, _ph, _pl = spans[0]
        tip = ws + b1
        if tip < WINDOW or tip >= len(framed):
            continue
        ma = np.vstack([framed[c].to_numpy(dtype=float)
                        for c in ALL_MA_COLS if c in framed.columns])
        atrp = add_indicators(framed)["atr_pct"].to_numpy(dtype=float)
        side, _bar = star_side(framed["close"].to_numpy(dtype=float),
                               np.nanmin(ma, axis=0), np.nanmax(ma, axis=0),
                               atrp, tip, len(framed))
        if side >= 0:                                   # short gold only
            continue
        try:
            _, tf = render_chart(framed.iloc[tip - WINDOW + 1:tip + 1], out_path=tmp)
            res = model.predict([str(tmp)], conf=FLOOR, verbose=False, device="mps")[0]
        except Exception:  # noqa: BLE001
            continue
        best = 0.0
        b = res.boxes
        if b is not None and len(b) > 0:
            for row, cf in zip(b.xywhn.cpu().numpy(), b.conf.cpu().numpy()):
                bar = right_edge_to_bar(float(row[0]) + float(row[2]) / 2, 0.0,
                                        tf, n_bars=WINDOW)
                if (WINDOW - 1) - bar <= TIP_EDGE_BARS:
                    best = max(best, float(cf))
        out.append(best)
    tmp.unlink(missing_ok=True)
    return out


def fire_confidences(model) -> tuple[list[float], int, int]:
    """Confidence of every tip-aligned fire over a plain scan. Returns (confs, bars, symbols)."""
    series = list_series(bar="15m")
    syms = sorted({s for (_x, s) in series if s.endswith("_USDT_SWAP")})[:DENSITY_SYMBOLS]
    tmp = PROJECT / "data" / "_pr2.png"
    confs: list[float] = []
    total_bars = 0
    used = 0
    for sym in syms:
        try:
            fr = add_mas(load_series(series[("okx", sym)]))
        except Exception:  # noqa: BLE001
            continue
        if len(fr) < WINDOW + 50:
            continue
        used += 1
        lo = max(WINDOW, len(fr) - DENSITY_BARS)
        total_bars += len(fr) - lo
        last = -10 ** 9
        for t in range(lo, len(fr)):
            try:
                _, tf = render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
                res = model.predict([str(tmp)], conf=FLOOR, verbose=False,
                                    device="mps")[0]
            except Exception:  # noqa: BLE001
                continue
            b = res.boxes
            if b is None or len(b) == 0:
                continue
            best = 0.0
            for row, cf in zip(b.xywhn.cpu().numpy(), b.conf.cpu().numpy()):
                if right_edge_to_bar(float(row[0]), float(row[2]), tf,
                                     n_bars=WINDOW) >= WINDOW - TIP_EDGE_BARS:
                    best = max(best, float(cf))
            if best > 0 and t - last >= MIN_GAP_BARS:
                confs.append(best)
                last = t
        print(f"  {sym}: 扫 {len(fr)-lo} bar, 开火 "
              f"{sum(1 for _ in confs)}", flush=True)
    tmp.unlink(missing_ok=True)
    return confs, total_bars, used


def main() -> int:
    global WEIGHTS
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--tag", default=None, help="suffix for the output json")
    a = ap.parse_args()
    if a.weights:
        WEIGHTS = Path(a.weights)
    if not WEIGHTS.exists():
        print(f"权重缺失: {WEIGHTS}")
        return 2
    model = load_yolo_model(str(WEIGHTS))

    print("① 量 v9 在你的空头金标上的置信度…", flush=True)
    gold = np.array(gold_confidences(model))
    print(f"   金标 {len(gold)} 个\n")

    print(f"② 量 v9 的自由开火率({DENSITY_SYMBOLS} 币 x {DENSITY_BARS} bar)…", flush=True)
    fires, bars, nsym = fire_confidences(model)
    fires = np.array(fires)
    months = bars / nsym / 96 / 30.44          # 96 bars/day at 15m
    print(f"   扫描 {bars} bar / {nsym} 币 = {months:.2f} 币·月,开火 {len(fires)}\n")

    print("=== 门槛扫描:召回 vs 开火密度 ===")
    print(f"{'门槛':>6} {'金标召回':>10} {'开火数':>8} {'每币每月':>10}  {'对比你的密度':>14}")
    rows = []
    for c in GRID:
        rec = float((gold >= c).mean()) if len(gold) else 0.0
        n_fire = int((fires >= c).sum())
        dens = n_fire / months if months > 0 else float("nan")
        ratio_lo = dens / OWNER_DENSITY[1]
        rows.append({"conf": c, "recall": round(rec, 4), "n_fire": n_fire,
                     "density_per_symbol_month": round(dens, 2),
                     "x_owner": round(ratio_lo, 1)})
        flag = "  ← 接近你的密度" if 0.3 <= ratio_lo <= 3 else ""
        print(f"{c:>6.2f} {rec*100:>9.1f}% {n_fire:>8} {dens:>9.1f} "
              f"{ratio_lo:>13.0f}x{flag}")
    print(f"{'你的标注':>6} {'':>10} {'':>8} "
          f"{OWNER_DENSITY[0]:.2f}~{OWNER_DENSITY[1]:.2f}")

    print("\n=== 两个分布分得开吗 ===")
    if len(gold) and len(fires):
        print(f"  金标置信度   p10={np.percentile(gold,10):.3f} "
              f"p50={np.median(gold):.3f} p90={np.percentile(gold,90):.3f}")
        print(f"  自由开火     p10={np.percentile(fires,10):.3f} "
              f"p50={np.median(fires):.3f} p90={np.percentile(fires,90):.3f}")
        # AUC of gold vs generic fires: 0.5 = indistinguishable
        from sklearn.metrics import roc_auc_score
        y = np.r_[np.ones(len(gold)), np.zeros(len(fires))]
        s = np.r_[gold, fires]
        auc = float(roc_auc_score(y, s))
        print(f"  区分度 AUC   {auc:.4f}   (0.5 = 完全分不开)")
    else:
        auc = float("nan")

    usable = [r for r in rows if r["recall"] >= 0.5 and r["x_owner"] <= 3]
    if usable:
        b = max(usable, key=lambda r: r["recall"])
        verdict = (f"存在可用工作点:门槛 {b['conf']:.2f} 时召回 {b['recall']*100:.1f}%,"
                   f"密度 {b['density_per_symbol_month']:.1f}/币·月 "
                   f"({b['x_owner']:.1f} 倍于你的标注)→ 调门槛即可")
    else:
        best_dens = min(rows, key=lambda r: abs(r["x_owner"] - 1))
        verdict = (f"没有可用工作点。要把密度压到你的量级需门槛 "
                   f"≥{best_dens['conf']:.2f},那时召回只剩 "
                   f"{best_dens['recall']*100:.1f}%;区分度 AUC {auc:.3f} 说明 v9 给"
                   f"金标和给垃圾的置信度{'几乎一样' if auc < 0.6 else '有差异但不够'}"
                   f" —— 问题在训练集构造(负样本不足),调门槛救不了,必须重训。")
    print(f"\n判读: {verdict}")
    print("注:门槛与 promote 属 owner 决策,本脚本只测不改。")

    (PROJECT / "analysis" / "output" /
     f"diag_precision_vs_recall_{a.tag or WEIGHTS.parent.parent.name}.json").write_text(
        json.dumps({"weights": WEIGHTS.name, "n_gold": len(gold),
                    "n_fires": len(fires), "symbol_months": round(months, 3),
                    "owner_density": OWNER_DENSITY, "auc_gold_vs_fires": auc,
                    "sweep": rows, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

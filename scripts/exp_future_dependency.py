"""Future-dependency experiment: does the detector need bars after the signal?

Single variable. Every image is WINDOW bars wide, rendered by the same
render_chart the detector trained on, with moving averages computed once over
the full series before slicing — so MA values at the signal bar are identical
across arms. The only thing that changes is how many bars sit to the RIGHT of
the signal:

    future=0   window = [sig-199 .. sig  ]   live tip, nothing after
    future=k   window = [sig-199+k .. sig+k]
    future=99  window = [sig-100 .. sig+99]  signal centred (training view)

Stage A picks samples the detector fires on in the FULL (centred) view, which
is the arm most likely to detect; asking how they decay as future shrinks is
then a fair question. Picking them from the tip view instead would guarantee
future=0 detects and prove nothing.

A box counts as re-finding the SAME signal when its right edge maps back to
within MATCH_TOL bars of signal_i; boxes elsewhere in the frame are counted
as detections but not as matches.

No training, no threshold tuning, no labels touched: conf/iou stay frozen at
the project defaults.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.candidates import (  # noqa: E402
    DEFAULT_CONF,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

FUTURE_ARMS = [0, 5, 10, 20, 40, WINDOW // 2 - 1]  # last == "full" (centred)
FULL_ARM = FUTURE_ARMS[-1]
MATCH_TOL = 2  # bars; same tolerance as the live tip-edge gate
IOU_FROZEN = 0.70


def render_arm(fr: pd.DataFrame, sig_i: int, future: int):
    """Window of WINDOW bars with `future` bars after sig_i. None if out of range."""
    end_i = sig_i + future
    start_i = end_i - WINDOW + 1
    if start_i < 0 or end_i >= len(fr):
        return None
    win = fr.iloc[start_i : end_i + 1]
    img, tf = render_chart(win, out_path=None)
    return img, tf, start_i, end_i


def boxes_of(model, img, tmp_png: Path, device: str, conf: float):
    cv2.imwrite(str(tmp_png), img)
    res = model.predict(str(tmp_png), conf=conf, iou=IOU_FROZEN, verbose=False, device=device)
    r0 = res[0] if res else None
    if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
        return []
    out = []
    for row, cf in zip(r0.boxes.xywhn.cpu().numpy(), r0.boxes.conf.cpu().numpy()):
        cx, cy, w, h = map(float, row)
        out.append({"cx": cx, "cy": cy, "w": w, "h": h, "conf": float(cf)})
    return out


def match_signal(boxes, tf, start_i: int, sig_i: int):
    """Best box whose right edge maps back to sig_i +/- MATCH_TOL."""
    best = None
    for b in boxes:
        b1 = right_edge_to_bar(b["cx"], b["w"], tf, n_bars=WINDOW)
        mapped = start_i + b1
        if abs(mapped - sig_i) <= MATCH_TOL and (best is None or b["conf"] > best["conf"]):
            best = {**b, "mapped_signal_i": int(mapped)}
    return best


def _device() -> str:
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "0"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path,
                    default=PROJECT / "runs/detect/runs/detect/owner_v12_htip/weights/best.pt")
    ap.add_argument("--tag", default="v12_htip")
    ap.add_argument("--samples", type=int, default=100)
    ap.add_argument("--days", type=int, default=30, help="signal search window")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--stride", type=int, default=8, help="stage A window stride")
    ap.add_argument("--max-per-symbol", type=int, default=3)
    ap.add_argument("--gallery-cases", type=int, default=12)
    ap.add_argument("--out-dir", type=Path, default=PROJECT / "reports")
    args = ap.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"missing weights {args.weights}")
    out = args.out_dir
    gallery = out / f"{args.tag}_future_dependency_gallery"
    gallery.mkdir(parents=True, exist_ok=True)
    tmp = out / f"_tmp_{args.tag}.png"

    device = _device()
    model = load_yolo_model(args.weights)
    print(f"device={device} weights={args.weights} conf={args.conf} iou={IOU_FROZEN}", flush=True)

    groups = list_series(PROJECT / "data/kline_fetched", bar="15m")
    series = sorted(
        (sym, paths)
        for (src, sym), paths in groups.items()
        if src == "okx" and str(sym).endswith("_USDT_SWAP") and not is_stockish(sym)
    )
    print(f"symbols={len(series)}", flush=True)

    # ---- Stage A: collect samples the FULL (centred) view fires on
    samples: list[dict] = []
    t0 = time.time()
    for si, (sym, paths) in enumerate(series, 1):
        if len(samples) >= args.samples:
            break
        fr = load_series(paths)
        if fr.empty or len(fr) < WINDOW * 2:
            continue
        fr = add_mas(fr)
        times = pd.to_datetime(fr["open_time"], utc=True)
        t_hi = times.max() - pd.Timedelta(hours=FULL_ARM * 0.25)  # keep room on the right
        t_lo = t_hi - pd.Timedelta(days=args.days)
        idxs = np.flatnonzero(((times >= t_lo) & (times <= t_hi)).to_numpy())
        if len(idxs) == 0:
            continue
        got = 0
        for end_i in range(max(int(idxs[0]), WINDOW - 1), int(idxs[-1]) + 1, args.stride):
            if got >= args.max_per_symbol or len(samples) >= args.samples:
                break
            start_i = end_i - WINDOW + 1
            try:
                img, tf = render_chart(fr.iloc[start_i : end_i + 1], out_path=None)
                bx = boxes_of(model, img, tmp, device, args.conf)
            except Exception:  # noqa: BLE001
                continue
            for b in bx:
                b1 = right_edge_to_bar(b["cx"], b["w"], tf, n_bars=WINDOW)
                sig_i = start_i + b1
                # need the signal to sit far enough from the right edge that this
                # really is a "full context" detection, and room for every arm
                if end_i - sig_i < FULL_ARM:
                    continue
                if sig_i - WINDOW + 1 < 0 or sig_i + FULL_ARM >= len(fr):
                    continue
                if any(s["symbol"] == sym and abs(s["signal_i"] - sig_i) < 18 for s in samples):
                    continue
                samples.append({
                    "sample_id": f"sample_{len(samples):03d}",
                    "symbol": sym,
                    "signal_i": int(sig_i),
                    "signal_time": str(times.iloc[sig_i]),
                    "discovery_conf": round(b["conf"], 4),
                })
                got += 1
                break
        if si % 20 == 0:
            print(f"[A {si}/{len(series)}] samples={len(samples)} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"stage A done: {len(samples)} samples from "
          f"{len({s['symbol'] for s in samples})} symbols, {time.time()-t0:.0f}s", flush=True)

    (out / f"{args.tag}_future_dependency_full.json").write_text(json.dumps({
        "arm": "FULL_CONTEXT",
        "future_bars": FULL_ARM,
        "weights": str(args.weights),
        "conf": args.conf, "iou": IOU_FROZEN, "window": WINDOW,
        "n_samples": len(samples),
        "n_symbols": len({s["symbol"] for s in samples}),
        "gold_labels": None,
        "note": "discovery arm; precision/recall not computed - no gold labels "
                "exist for these auto-discovered points",
        "samples": samples,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- Stage B: ablation over future bars
    by_symbol: dict[str, pd.DataFrame] = {}
    results: list[dict] = []
    t1 = time.time()
    gallery_ids = {s["sample_id"] for s in samples[: args.gallery_cases]}
    for i, s in enumerate(samples, 1):
        sym = s["symbol"]
        if sym not in by_symbol:
            paths = next(p for (src, m), p in groups.items() if m == sym)
            by_symbol[sym] = add_mas(load_series(paths))
        fr = by_symbol[sym]
        row = {**s, "arms": {}}
        for fut in FUTURE_ARMS:
            r = render_arm(fr, s["signal_i"], fut)
            if r is None:
                row["arms"][str(fut)] = {"error": "out_of_range"}
                continue
            img, tf, start_i, end_i = r
            bx = boxes_of(model, img, tmp, device, args.conf)
            m = match_signal(bx, tf, start_i, s["signal_i"])
            row["arms"][str(fut)] = {
                "n_boxes": len(bx),
                "matched": m is not None,
                "conf": round(m["conf"], 4) if m else None,
                "bbox_xywhn": [round(m[k], 4) for k in ("cx", "cy", "w", "h")] if m else None,
                "max_conf_any_box": round(max((b["conf"] for b in bx), default=0.0), 4),
            }
            if s["sample_id"] in gallery_ids:
                ann = img.copy()
                h, w = ann.shape[:2]
                for b in bx:
                    x1, y1 = int((b["cx"] - b["w"] / 2) * w), int((b["cy"] - b["h"] / 2) * h)
                    x2, y2 = int((b["cx"] + b["w"] / 2) * w), int((b["cy"] + b["h"] / 2) * h)
                    hit = m is not None and abs(b["conf"] - m["conf"]) < 1e-9
                    col = (0, 165, 255) if hit else (150, 150, 150)
                    cv2.rectangle(ann, (x1, y1), (x2, y2), col, 3)
                    cv2.putText(ann, f"{b['conf']:.2f}", (x1, max(18, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2, cv2.LINE_AA)
                name = "full" if fut == FULL_ARM else ("tip" if fut == 0 else f"future{fut}")
                cv2.putText(ann, f"{sym} {s['signal_time'][:16]} future={fut}",
                            (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 40, 40), 2, cv2.LINE_AA)
                cv2.imwrite(str(gallery / f"{s['sample_id']}_{name}.png"), ann)
        results.append(row)
        if i % 10 == 0:
            print(f"[B {i}/{len(samples)}] elapsed={time.time()-t1:.0f}s", flush=True)

    (out / f"{args.tag}_future_dependency_tip.json").write_text(json.dumps({
        "arm": "REAL_TIME_TIP", "future_bars": 0,
        "weights": str(args.weights), "conf": args.conf, "iou": IOU_FROZEN, "window": WINDOW,
        "match_tolerance_bars": MATCH_TOL,
        "samples": [{**{k: r[k] for k in ("sample_id", "symbol", "signal_time")},
                     **r["arms"]["0"]} for r in results],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    curve = []
    for fut in FUTURE_ARMS:
        arms = [r["arms"][str(fut)] for r in results if "error" not in r["arms"][str(fut)]]
        matched = [a for a in arms if a["matched"]]
        confs = [a["conf"] for a in matched]
        curve.append({
            "future_bars": fut,
            "is_full_arm": fut == FULL_ARM,
            "n_evaluated": len(arms),
            "n_matched": len(matched),
            "match_rate": round(len(matched) / len(arms), 4) if arms else None,
            "mean_conf_matched": round(float(np.mean(confs)), 4) if confs else None,
            "median_conf_matched": round(float(np.median(confs)), 4) if confs else None,
            "mean_boxes_per_image": round(float(np.mean([a["n_boxes"] for a in arms])), 3) if arms else None,
        })
    (out / f"{args.tag}_future_dependency_curve.json").write_text(json.dumps({
        "weights": str(args.weights), "tag": args.tag,
        "conf": args.conf, "iou": IOU_FROZEN, "window": WINDOW,
        "match_tolerance_bars": MATCH_TOL,
        "n_samples": len(results),
        "n_symbols": len({r["symbol"] for r in results}),
        "curve": curve,
        "per_sample": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\nfuture_bars  match_rate  mean_conf  n", flush=True)
    for c in curve:
        print(f"{c['future_bars']:>10}  {c['match_rate']}  {c['mean_conf_matched']}  "
              f"{c['n_matched']}/{c['n_evaluated']}", flush=True)
    print(f"\nDONE -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Continuous-scan evaluation: the metric mAP cannot give you.

mAP is measured on a set built one-window-per-event. Live, the model sees every
bar of every symbol, most of which hold nothing, and the question is how often it
fires and how much of that is noise. fixed_w10 scored a healthy-looking backtest
while firing on 100% of bars; the number that would have caught it immediately is
fire rate per 1000 bars, and nobody had computed it.

So this slides the window bar by bar over symbols the model never trained on,
counts every firing, and matches firings against the v10-mined boxes.

Two honesty notes carried into the report:
  - the "events" here are teacher boxes, not owner boxes. Recall against them
    means agreement with v10, not correctness.
  - a detection only counts as actionable if its box right edge is within
    FRESH_BARS of the scan tip. Everything else is a signal about the past.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path.home() / "yoyo-trading"))
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402

HOLDOUT = pd.Timestamp("2026-05-04T00:00:00Z")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--detections",
                    default="analysis/output/v10_mine_preholdout/detections.jsonl")
    ap.add_argument("--n-symbols", type=int, default=12)
    ap.add_argument("--start", default="2026-02-01")
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--fresh-bars", type=int, default=2)
    ap.add_argument("--cooldown", type=int, default=3)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    meta = json.loads((Path(args.dataset) / "build_meta.json").read_text())
    W = int(meta["window_bars"])
    val_syms = meta["val_symbols"][: args.n_symbols]
    t0 = pd.Timestamp(args.start, tz="UTC")
    print(f"扫描 {len(val_syms)} 个 val 币 · 窗口 {W} 根 · conf {args.conf} · "
          f"fresh<={args.fresh_bars} 根 · cooldown {args.cooldown}", flush=True)

    truth: dict[str, list[int]] = defaultdict(list)
    for line in open(args.detections):
        if not line.strip():
            continue
        d = json.loads(line)
        if d["symbol"] in set(val_syms) and d.get("side"):
            truth[d["symbol"]].append(int(d.get("tight_i", d["box_end_i"])))
    for s in list(truth):
        truth[s] = sorted(set(truth[s]))

    files = {}
    for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
        m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
        if m:
            files[m.group(1)] = p

    from ultralytics import YOLO
    model = YOLO(args.weights)

    tot_bars = tot_fire = tot_fresh = 0
    per_symbol, deltas = [], []
    # every fresh firing, kept so a threshold sweep needs no second scan
    fire_log: list[dict] = []
    matched_events = total_events = dup = 0

    for si, sym in enumerate(val_syms, 1):
        if sym not in files:
            continue
        fr = add_mas(pd.read_csv(files[sym]).sort_values("ts").reset_index(drop=True))
        t = pd.to_datetime(fr["open_time"], utc=True)
        lo = max(int((t < t0).sum()), 130 + W)
        hi = int((t < HOLDOUT).sum()) - 1
        if hi - lo < 200:
            continue
        tips = list(range(lo, hi + 1))
        fires = []
        for i in range(0, len(tips), args.batch):
            chunk = tips[i:i + args.batch]
            imgs = [render_chart(fr.iloc[e - W + 1:e + 1], out_path=None)[0] for e in chunk]
            res = model.predict(imgs, conf=args.conf, imgsz=args.imgsz,
                                verbose=False, device="mps")
            for e, r in zip(chunk, res):
                if r.boxes is None or len(r.boxes) == 0:
                    continue
                best = None
                for row, cf in zip(r.boxes.xywhn.cpu().numpy(),
                                   r.boxes.conf.cpu().numpy()):
                    cx, _, bw, _ = map(float, row)
                    b_right = e - W + 1 + int(round((cx + bw / 2) * (W - 1)))
                    if best is None or float(cf) > best[2]:
                        best = (e, b_right, float(cf))
                if best:
                    fires.append(best)

        kept, last = [], -10 ** 9
        for e, br, cf in fires:
            if e - last >= args.cooldown:
                kept.append((e, br, cf)); last = e
        fresh = [x for x in kept if x[0] - x[1] <= args.fresh_bars]

        ev = [x for x in truth.get(sym, []) if lo <= x <= hi]
        total_events += len(ev)
        used = set()
        for x in ev:
            cand = [e for e, br, _ in fresh if abs(br - x) <= 3]
            if cand:
                matched_events += 1
                deltas.append(min(cand, key=lambda e: abs(e - x)) - x)
                used.update(cand)
        dup += max(0, len(fresh) - len(used))
        for e, br, cf in fresh:
            near = [x for x in ev if abs(br - x) <= 3]
            fire_log.append({"symbol": sym, "tip": e, "box_right": br,
                             "conf": round(cf, 4), "matched": bool(near),
                             "delta": (br - min(near, key=lambda x: abs(br - x)))
                                      if near else None})

        n_bars = len(tips)
        tot_bars += n_bars; tot_fire += len(kept); tot_fresh += len(fresh)
        days = n_bars * 15 / 60 / 24
        per_symbol.append({"symbol": sym, "bars": n_bars, "fires": len(kept),
                           "fresh": len(fresh), "events": len(ev),
                           "fires_per_1000_bars": round(len(kept) / n_bars * 1000, 2),
                           "fresh_per_day": round(len(fresh) / days, 3)})
        print(f"  [{si}/{len(val_syms)}] {sym}: {n_bars:,} bars · {len(kept)} fires "
              f"({len(kept)/n_bars*1000:.1f}/1000) · {len(fresh)} fresh · {len(ev)} events",
              flush=True)

    days_all = tot_bars * 15 / 60 / 24
    d = np.array(deltas, dtype=float) if deltas else np.array([np.nan])
    summary = {
        "weights": args.weights, "dataset": args.dataset, "window_bars": W,
        "conf": args.conf, "cooldown_bars": args.cooldown,
        "fresh_bars": args.fresh_bars, "imgsz": args.imgsz,
        "symbols": len(per_symbol), "scanned_bars": tot_bars, "n_fires": tot_fire,
        "fires_per_1000_bars": round(tot_fire / max(tot_bars, 1) * 1000, 3),
        "fresh_fires": tot_fresh,
        "fresh_per_symbol_day": round(tot_fresh / max(days_all, 1e-9), 4),
        "teacher_events": total_events,
        "event_recall_within_3_bars":
            round(matched_events / total_events, 4) if total_events else None,
        "median_delay_bars": None if np.isnan(d).all() else float(np.median(d)),
        "p90_delay_bars": None if np.isnan(d).all() else float(np.percentile(d, 90)),
        "unmatched_fresh_fires": dup,
        "unmatched_per_1000_bars": round(dup / max(tot_bars, 1) * 1000, 3),
        "caveat": "events are v10 teacher boxes, not owner labels; "
                  "recall means agreement with v10, not correctness",
        "per_symbol": per_symbol,
        "fires": fire_log,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    print("\n=== 连续扫描结果 ===")
    for k in ("scanned_bars", "n_fires", "fires_per_1000_bars", "fresh_fires",
              "fresh_per_symbol_day", "teacher_events", "event_recall_within_3_bars",
              "median_delay_bars", "p90_delay_bars", "unmatched_per_1000_bars"):
        print(f"  {k:<30}{summary[k]}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

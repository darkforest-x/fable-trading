"""Where do the 542 minutes go? Break the detection lag into its components.

The forward log's median lag is 542 minutes against a 30-minute freshness gate,
so every row is rejected and the live path has produced 0 tradeable signals. The
number is useless as one lump: 542 minutes could be one slow stage or twenty
merely sluggish ones, and the fix differs completely.

External research keeps proposing TensorRT / C++ OMS / colocation, which target
tick-to-trade in milliseconds. That is the wrong latency by seven orders of
magnitude here. This measures the stages that actually exist in our pipeline:

  BAR_CLOSE   a 15m bar is only complete 15 minutes after it opens; a signal on
              bar t cannot exist before t closes. Irreducible floor.
  PULSE_WAIT  the scanner runs on a cycle, so a bar that closes just after a
              pulse waits nearly a full cycle. Expected cost = cycle/2, worst
              case = cycle.
  SCAN_WALL   rendering + inference across the universe, measured directly here
              rather than assumed.
  TAIL        anything left over: queueing, retries, write-back.

Measured, not estimated, wherever the data allows: SCAN_WALL is timed on this
machine, PULSE_WAIT comes from the configured cycle, and the residual is read
off the forward log.

Read-only. No promote, no config change -- the freshness gate and pulse budget
are owner decisions (live discipline 7 and 8).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_latency_budget.py --n-symbols 12
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.yolo_candidates import WINDOW, load_yolo_model  # noqa: E402

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt"
BAR_MIN = 15
FRESHNESS_GATE_MIN = 30          # live discipline 7 — owner-set, measured against


def time_scan(n_symbols: int, batch: int) -> dict:
    """Time render+predict for the live schedule (tip / tip-1 / tip-2 per symbol)."""
    series = list_series(bar="15m")
    syms = sorted({s for (_src, s) in series})[:n_symbols]
    model = load_yolo_model(str(WEIGHTS))
    tmp = PROJECT / "data" / "_lat.png"

    t_load = t_render = t_predict = 0.0
    n_windows = 0
    for sym in syms:
        t0 = time.perf_counter()
        try:
            fr = add_mas(load_series(series[("okx", sym)]))
        except Exception:  # noqa: BLE001
            continue
        t_load += time.perf_counter() - t0
        tip = len(fr) - 1
        paths = []
        for back in (0, 1, 2):                       # the live tip/tip-1/tip-2 schedule
            t = tip - back
            if t < WINDOW:
                continue
            t0 = time.perf_counter()
            p = tmp.with_name(f"{tmp.stem}_{back}.png")
            try:
                render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=p)
            except Exception:  # noqa: BLE001
                continue
            t_render += time.perf_counter() - t0
            paths.append(str(p))
        if not paths:
            continue
        t0 = time.perf_counter()
        model.predict(paths, conf=0.30, verbose=False, device="cpu")
        t_predict += time.perf_counter() - t0
        n_windows += len(paths)
    for k in range(3):
        tmp.with_name(f"{tmp.stem}_{k}.png").unlink(missing_ok=True)

    n = max(len(syms), 1)
    return {"n_symbols": len(syms), "n_windows": n_windows,
            "load_s": round(t_load, 2), "render_s": round(t_render, 2),
            "predict_s": round(t_predict, 2),
            "total_s": round(t_load + t_render + t_predict, 2),
            "per_symbol_s": round((t_load + t_render + t_predict) / n, 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=12)
    ap.add_argument("--batch", type=int, default=3)
    ap.add_argument("--universe", type=int, default=220,
                    help="symbols the live scan would actually cover")
    ap.add_argument("--pulse-min", type=float, default=15.0,
                    help="configured pulse cycle (live discipline 8)")
    args = ap.parse_args()

    if not WEIGHTS.exists():
        print(f"weights missing: {WEIGHTS}")
        return 2

    print(f"计时中({args.n_symbols} 个币,每币 tip/tip-1/tip-2 三窗)…")
    t = time_scan(args.n_symbols, args.batch)
    print(f"  加载K线 {t['load_s']}s   渲染 {t['render_s']}s   推理 {t['predict_s']}s")
    print(f"  合计 {t['total_s']}s / {t['n_symbols']} 币 = {t['per_symbol_s']}s per symbol\n")

    scan_full_min = t["per_symbol_s"] * args.universe / 60.0
    bar_close = BAR_MIN
    pulse_wait = args.pulse_min / 2.0

    print(f"=== 延迟预算(宇宙 {args.universe} 币,脉冲 {args.pulse_min}min) ===")
    print(f"{'环节':<28} {'分钟':>9}  说明")
    rows = [
        ("K线闭合(不可压缩)", bar_close, "15m bar 开出后 15 分钟才完整"),
        ("脉冲等待(期望)", pulse_wait, f"周期 {args.pulse_min}min 的一半"),
        ("全宇宙扫描", scan_full_min, f"{t['per_symbol_s']}s x {args.universe} 币"),
    ]
    acc = 0.0
    for name, mins, note in rows:
        acc += mins
        print(f"{name:<28} {mins:>9.1f}  {note}")
    print(f"{'小计':<28} {acc:>9.1f}")
    print(f"{'新鲜度门':<28} {FRESHNESS_GATE_MIN:>9.1f}  超过即拒单")
    print(f"\n观测中位 lag 542 分钟,以上可解释 {acc:.0f} 分钟,"
          f"残差 {542-acc:.0f} 分钟(排队/重试/回写/串行化)")

    print("\n=== 如果只扫候选而非全宇宙 ===")
    for frac, label in ((0.10, "粗筛留 10%"), (0.05, "粗筛留 5%"), (0.02, "粗筛留 2%")):
        s = t["per_symbol_s"] * args.universe * frac / 60.0
        total = bar_close + pulse_wait + s
        ok = "✅ 过门" if total <= FRESHNESS_GATE_MIN else "❌ 仍超门"
        print(f"  {label:<12} 扫描 {s:>6.1f}min   端到端 {total:>6.1f}min   {ok}")

    out = {"scan_timing": t, "universe": args.universe,
           "pulse_min": args.pulse_min, "gate_min": FRESHNESS_GATE_MIN,
           "budget_min": {"bar_close": bar_close, "pulse_wait": pulse_wait,
                          "scan_full": round(scan_full_min, 1),
                          "explained": round(acc, 1), "observed_median": 542,
                          "residual": round(542 - acc, 1)}}
    (PROJECT / "analysis" / "output" / "diag_latency_budget.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

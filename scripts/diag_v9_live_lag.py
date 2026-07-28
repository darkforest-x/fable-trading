"""Does v9 actually produce fresh signals? Measure end-to-end lag on the live path.

The 542-minute "detection lag" turned out not to be latency. The forward log was
produced by the v11-era detector, whose boxes land a median 33 bars inside the
200-bar window -- so the signal genuinely belonged to a bar 8 hours old, and the
30-minute freshness gate correctly rejected it. Compute was never the problem:
scanning 220 symbols takes 1.3 minutes.

v9 places boxes at the tip (right-edge offset 0 at every percentile on the
owner's gold), so the same pipeline should now emit signals about one bar old.
This measures that end to end rather than assuming it:

  for each symbol, run the LIVE schedule (tip / tip-1 / tip-2 windows, tip-edge
  gate on) exactly as the live scanner would, and record how many bars back the
  fired box maps to. Then add the pipeline's own arithmetic (bar close + pulse
  wait + scan wall) to get the end-to-end age of a signal at the moment it lands.

Pass condition: total age <= the 30-minute freshness gate, using the same three
gate values live uses (executor / TG / dashboard, live discipline 7).

Read-only: no ACTIVE change, no promote, no orders. Whether v9 becomes ACTIVE is
an owner decision (live discipline 10).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v9_live_lag.py --n-symbols 40
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
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    scan_series_with_yolo,
)

MODELS = {
    "v11 (产生542分钟那个)": PROJECT / "runs/detect/runs/detect/owner_v13_pad200/weights/best.pt",
    "v9 (今天训的)": PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt",
}
BAR_MIN = 15
GATE_MIN = 30.0
PULSE_MIN = 15.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=40)
    ap.add_argument("--universe", type=int, default=220)
    args = ap.parse_args()

    series = list_series(bar="15m")
    syms = sorted({s for (_src, s) in series})[: args.n_symbols]
    results: dict[str, dict] = {}

    for name, wp in MODELS.items():
        if not wp.exists():
            print(f"跳过 {name}: 权重不存在")
            continue
        model = load_yolo_model(str(wp))
        offsets: list[int] = []
        n_fire = 0
        t0 = time.perf_counter()
        for sym in syms:
            try:
                fr = add_mas(load_series(series[("okx", sym)]))
            except Exception:  # noqa: BLE001
                continue
            if len(fr) < WINDOW + 4:
                continue
            # exactly the live schedule: tip / tip-1 / tip-2, tip-edge gate on
            try:
                sigs = scan_series_with_yolo(fr, model, conf=DEFAULT_CONF, mode="live")
            except Exception:  # noqa: BLE001
                continue
            if not sigs:
                continue
            n_fire += 1
            tip = len(fr) - 1
            offsets.append(tip - max(sigs))       # bars between newest signal and tip
        wall = time.perf_counter() - t0

        if not offsets:
            print(f"{name}: {len(syms)} 币中 0 个开火")
            results[name] = {"n_fired": 0}
            continue
        a = np.array(offsets)
        per_sym = wall / max(len(syms), 1)
        scan_full = per_sym * args.universe / 60.0
        box_age = float(np.median(a)) * BAR_MIN
        total = BAR_MIN + PULSE_MIN / 2 + scan_full + box_age

        results[name] = {
            "n_fired": n_fire, "n_symbols": len(syms),
            "box_offset_bars": {"p10": float(np.percentile(a, 10)),
                                "p50": float(np.median(a)),
                                "p90": float(np.percentile(a, 90)),
                                "max": int(a.max())},
            "box_age_min": round(box_age, 1),
            "scan_full_min": round(scan_full, 2),
            "end_to_end_min": round(total, 1),
            "passes_gate": bool(total <= GATE_MIN),
        }
        print(f"\n=== {name} ===")
        print(f"  开火 {n_fire}/{len(syms)} 币   扫描 {per_sym:.3f}s/币 "
              f"→ 全宇宙 {scan_full:.1f}min")
        print(f"  框距盘口: p10={np.percentile(a,10):.0f} p50={np.median(a):.0f} "
              f"p90={np.percentile(a,90):.0f} max={a.max()} 根")
        print(f"  端到端 = K线闭合 {BAR_MIN} + 脉冲 {PULSE_MIN/2} + 扫描 {scan_full:.1f} "
              f"+ 框龄 {box_age:.1f} = {total:.1f} min")
        print(f"  新鲜度门 {GATE_MIN}min → {'✅ 过门' if total <= GATE_MIN else '❌ 拒单'}")

    v9 = results.get("v9 (今天训的)")
    verdict = ("v9 端到端 %.1f 分钟,%s 30 分钟门 → 换 v9 后 live 能产出可交易信号"
               % (v9["end_to_end_min"], "在" if v9["passes_gate"] else "仍超")
               if v9 and v9.get("n_fired") else "v9 未开火,无法判定")
    print(f"\n判读: {verdict}")
    print("注:是否把 ACTIVE 切到 v9 属 owner 决策(实盘纪律 10),本脚本不改任何配置。")

    (PROJECT / "analysis" / "output" / "diag_v9_live_lag.json").write_text(
        json.dumps({"gate_min": GATE_MIN, "pulse_min": PULSE_MIN,
                    "universe": args.universe, "models": results,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

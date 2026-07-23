#!/usr/bin/env python3
"""Causal direction-select base rates on dense-emergence tips (train only).

Why (owner 2026-07-23): prior launch-entry split table showed no side ≥1.3.
This experiment asks a narrower question — given the *same* dense tip base as
`p_launch_entry_long_short`, can a causal rule *choose long vs short (or skip)*
so that one side clears PF@maker ≥ 1.3?

Single-variable: only the direction rule changes. Dense gate, entry, barriers,
costs, gap, and wait window stay fixed and match launch_entry_base_rate.py.

Base (honest MA bundle = judgment `add_indicators`):
  dense tip when fast_spread run first hits MIN_DENSE_BARS under
  FAST_MAX/FULL_MAX (same emergence tip as launch_entry).

Direction rules (causal; signal bar and earlier only):
  1. ctrl_fixed_long / ctrl_fixed_short — no select; tip always +1 / −1
  2. arrange_order_score — order_score≥3 & >down → long;
     down_order_score≥3 & >order → short; else skip
     (threshold = STRICT_THRESHOLDS order_score_min = 3)
  3. range_break_n20 — after tip, first close beyond prior N hi/lo; follow
  4. spread_expand_chg8 — after tip, Δfast_spread(8)≥thr; dir vs cluster_mid

Settlement: direction>0 → label_candidate(TP5/SL2); <0 → label_short_candidate.
Costs: SWAP maker 0.06% and legacy 0.20%. Primary table = long | short rows;
both is secondary control only. Train window only (<2026-05-04). No holdout.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/direction_select_base_rate.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/direction_select_base_rate.py --n-symbols 0 \\
      --tag direction_select_base_rate
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import (  # noqa: E402
    STRICT_THRESHOLDS,
    add_indicators,
)
from src.judgment.labeling import (  # noqa: E402
    ATR_PCT_MIN,
    HORIZON_BARS,
    label_candidate,
    label_short_candidate,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FAST_MAX, FULL_MAX = 0.0028, 0.0055
MIN_DENSE_BARS = 5
MIN_GAP_BARS = 18
WARMUP = 288
TP_MULT, SL_MULT = 5.0, 2.0

MAX_WAIT_BARS = 48
RANGE_N = 20
SPREAD_CHG8_THR = 0.00383
ORDER_SCORE_MIN = int(STRICT_THRESHOLDS["order_score_min"])  # 3

SUCCESS_PF_MAKER = 1.3

VARIANT_ORDER = [
    "ctrl_fixed_long",
    "ctrl_fixed_short",
    "arrange_order_score",
    "range_break_n20",
    "spread_expand_chg8",
]

DIR_RULES = {
    "ctrl_fixed_long": "fixed +1 at dense tip (run==5); no select",
    "ctrl_fixed_short": "fixed -1 at dense tip (run==5); no select",
    "arrange_order_score": (
        f"at tip: order_score>={ORDER_SCORE_MIN} and >down_order_score → +1; "
        f"down_order_score>={ORDER_SCORE_MIN} and >order_score → -1; else skip"
    ),
    "range_break_n20": (
        "after tip, first close>max(high[i-N:i]) → +1; "
        "close<min(low[i-N:i]) → -1; N=20; wait≤48"
    ),
    "spread_expand_chg8": (
        "after tip, first fast_spread[i]-fast_spread[i-8] >= thr; "
        "dir = +1 if close>=cluster_mid else -1"
    ),
}


def _dense_run(fast: np.ndarray, full: np.ndarray) -> np.ndarray:
    dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
    run = np.zeros(len(dense), dtype=int)
    for i in range(len(dense)):
        run[i] = (run[i - 1] + 1) if dense[i] and i > 0 else (1 if dense[i] else 0)
    return run


def _dedup(fires: list[tuple[int, int]], gap: int = MIN_GAP_BARS) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i, d in fires:
        if not out or i - out[-1][0] >= gap:
            out.append((i, d))
    return out


def _resolve_gross(frame: pd.DataFrame, signal_i: int, direction: int) -> float | None:
    if direction > 0:
        out = label_candidate(frame, signal_i, tp_mult=TP_MULT, sl_mult=SL_MULT)
    else:
        out = label_short_candidate(frame, signal_i, tp_mult=TP_MULT, sl_mult=SL_MULT)
    if out is None:
        return None
    return float(out.realized_ret)


def _stats(gross: list[float], cost: float) -> dict:
    if not gross:
        return {
            "n": 0,
            "win_rate": None,
            "mean_gross": None,
            "mean_net": None,
            "profit_factor": None,
        }
    g = np.asarray(gross, dtype=float)
    net = g - cost
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n": int(len(g)),
        "win_rate": round(float((net > 0).mean()), 4),
        "mean_gross": round(float(g.mean()), 5),
        "mean_net": round(float(net.mean()), 5),
        "profit_factor": round(float(w / -l), 3) if l < 0 else None,
    }


def _side_block(gross: list[float], dirs: list[int]) -> dict:
    g_long = [g for g, d in zip(gross, dirs) if d > 0]
    g_short = [g for g, d in zip(gross, dirs) if d < 0]
    return {
        "n_long": len(g_long),
        "n_short": len(g_short),
        "long_only": {
            "maker_0.06pct": _stats(g_long, FORWARD_COST),
            "legacy_0.20pct": _stats(g_long, LEGACY_P0_ROUND_TRIP),
            "gross_pre_cost": _stats(g_long, 0.0),
        },
        "short_only": {
            "maker_0.06pct": _stats(g_short, FORWARD_COST),
            "legacy_0.20pct": _stats(g_short, LEGACY_P0_ROUND_TRIP),
            "gross_pre_cost": _stats(g_short, 0.0),
        },
        "both": {
            "maker_0.06pct": _stats(gross, FORWARD_COST),
            "legacy_0.20pct": _stats(gross, LEGACY_P0_ROUND_TRIP),
            "gross_pre_cost": _stats(gross, 0.0),
        },
    }


def collect_signals(
    enriched: pd.DataFrame,
) -> tuple[dict[str, list[tuple[int, int]]], dict[str, int]]:
    """Return (variant -> fires, audit counts). Causal at/before signal_i."""
    n = len(enriched)
    close = enriched["close"].to_numpy(dtype=float)
    high = enriched["high"].to_numpy(dtype=float)
    low = enriched["low"].to_numpy(dtype=float)
    fast = pd.to_numeric(enriched["fast_spread"], errors="coerce").to_numpy()
    full = pd.to_numeric(enriched["full_spread"], errors="coerce").to_numpy()
    order = enriched["order_score"].to_numpy(dtype=float)
    down = enriched["down_order_score"].to_numpy(dtype=float)
    cluster_mid = (
        (enriched["cluster_max"].to_numpy(dtype=float) + enriched["cluster_min"].to_numpy(dtype=float))
        / 2.0
    )
    run = _dense_run(fast, full)

    fixed_long: list[tuple[int, int]] = []
    fixed_short: list[tuple[int, int]] = []
    arrange: list[tuple[int, int]] = []
    tips_raw = 0
    arrange_skips = 0

    for i in range(WARMUP, n - 1):
        if run[i] != MIN_DENSE_BARS:
            continue
        tips_raw += 1
        fixed_long.append((i, +1))
        fixed_short.append((i, -1))
        o, d = float(order[i]), float(down[i])
        if o >= ORDER_SCORE_MIN and o > d:
            arrange.append((i, +1))
        elif d >= ORDER_SCORE_MIN and d > o:
            arrange.append((i, -1))
        else:
            arrange_skips += 1

    range_f: list[tuple[int, int]] = []
    spread_f: list[tuple[int, int]] = []
    armed_from: int | None = None
    fired_range = fired_spread = False

    for i in range(WARMUP, n - 1):
        if run[i] == MIN_DENSE_BARS:
            armed_from = i
            fired_range = fired_spread = False

        if armed_from is None:
            continue
        if i - armed_from > MAX_WAIT_BARS:
            armed_from = None
            continue
        if i <= armed_from:
            continue

        if not fired_range and i >= RANGE_N:
            prior_hi = float(np.max(high[i - RANGE_N : i]))
            prior_lo = float(np.min(low[i - RANGE_N : i]))
            if close[i] > prior_hi:
                range_f.append((i, +1))
                fired_range = True
            elif close[i] < prior_lo:
                range_f.append((i, -1))
                fired_range = True

        if not fired_spread and i >= 8:
            chg8 = float(fast[i] - fast[i - 8])
            if np.isfinite(chg8) and chg8 >= SPREAD_CHG8_THR:
                mid = cluster_mid[i]
                if np.isfinite(mid) and mid > 0:
                    spread_f.append((i, +1 if close[i] >= mid else -1))
                    fired_spread = True

        if fired_range and fired_spread:
            armed_from = None

    sigs = {
        "ctrl_fixed_long": _dedup(fixed_long),
        "ctrl_fixed_short": _dedup(fixed_short),
        "arrange_order_score": _dedup(arrange),
        "range_break_n20": _dedup(range_f),
        "spread_expand_chg8": _dedup(spread_f),
    }
    audit = {"tips_raw": tips_raw, "arrange_skips": arrange_skips}
    return sigs, audit


def _pf(block: dict, side: str, cost_key: str) -> float | None:
    return block[side][cost_key]["profit_factor"]


def _write_csv(path: Path, variants_out: dict) -> None:
    rows = []
    for name in VARIANT_ORDER:
        block = variants_out[name]
        for side, label in (("long_only", "long"), ("short_only", "short"), ("both", "both")):
            m = block[side]["maker_0.06pct"]
            leg = block[side]["legacy_0.20pct"]
            rows.append(
                {
                    "variant": name,
                    "side": label,
                    "n": m["n"],
                    "win_rate_maker": m["win_rate"],
                    "mean_net_maker": m["mean_net"],
                    "pf_maker": m["profit_factor"],
                    "pf_legacy_0.20": leg["profit_factor"],
                    "dir_rule": block["dir_rule"],
                }
            )
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            w.writeheader()
            w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=60, help="0 = all SWAP")
    ap.add_argument("--tag", default="direction_select_base_rate")
    args = ap.parse_args()

    bags: dict[str, dict[str, list]] = {
        v: {"gross": [], "dir": []} for v in VARIANT_ORDER
    }
    n_sym = 0
    t_min = t_max = None
    arrange_skips_total = 0
    arrange_tips_raw = 0

    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < WARMUP + HORIZON_BARS + 50:
            continue

        enriched = add_indicators(frame)
        sigs, audit = collect_signals(enriched)
        arrange_skips_total += audit["arrange_skips"]
        arrange_tips_raw += audit["tips_raw"]

        t = pd.to_datetime(enriched["open_time"], utc=True)
        for name, pairs in sigs.items():
            for i, d in pairs:
                g = _resolve_gross(enriched, i, d)
                if g is None:
                    continue
                bags[name]["gross"].append(g)
                bags[name]["dir"].append(d)
                ti = t.iloc[i]
                if t_min is None or ti < t_min:
                    t_min = ti
                if t_max is None or ti > t_max:
                    t_max = ti

        n_sym += 1
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols …")
        if args.n_symbols and n_sym >= args.n_symbols:
            break

    variants_out = {}
    for name in VARIANT_ORDER:
        block = _side_block(bags[name]["gross"], bags[name]["dir"])
        block["dir_rule"] = DIR_RULES[name]
        variants_out[name] = block

    best_side = None
    best_pf = None
    for name, block in variants_out.items():
        for side in ("long_only", "short_only"):
            pf = _pf(block, side, "maker_0.06pct")
            if pf is None:
                continue
            if best_pf is None or pf > best_pf:
                best_pf = pf
                best_side = {"variant": name, "side": side, "pf_maker": pf}

    cleared = best_pf is not None and best_pf >= SUCCESS_PF_MAKER
    verdict = (
        "值得谈影子/继续"
        if cleared
        else "择向未救出可交易边"
    )

    skip_rate = (
        round(arrange_skips_total / arrange_tips_raw, 4) if arrange_tips_raw else None
    )

    out = {
        "tag": args.tag,
        "question": (
            "On the same dense-emergence tip base as p_launch_entry_long_short, "
            "can a causal direction-select rule make one side PF@maker ≥ 1.3?"
        ),
        "success_criterion": {
            "pf_maker_ge": SUCCESS_PF_MAKER,
            "pass_label": "值得谈影子/继续",
            "fail_label": "择向未救出可交易边",
        },
        "verdict": verdict,
        "best_side": best_side,
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "entry": "next_bar_open",
            "exit": f"TP{TP_MULT:g}/SL{SL_MULT:g}/{HORIZON_BARS}bar",
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "atr_pct_min": ATR_PCT_MIN,
            "bundle": (
                "judgment add_indicators only; dense tip = EMA8-55 fast / "
                "EMA8-55+144/200 full; same as launch_entry_base_rate"
            ),
            "base": "emergence tip run==5 (comparable to p_launch_entry_long_short)",
            "order_score_min": ORDER_SCORE_MIN,
            "max_wait_bars_after_dense": MAX_WAIT_BARS,
            "range_n": RANGE_N,
            "spread_chg8_thr": SPREAD_CHG8_THR,
            "primary_verdict": "long_only and short_only; both is secondary",
        },
        "data": {
            "n_symbols": n_sym,
            "time_range": [
                str(t_min) if t_min is not None else None,
                str(t_max) if t_max is not None else None,
            ],
            "triggers_both": {
                k: variants_out[k]["both"]["maker_0.06pct"]["n"] for k in VARIANT_ORDER
            },
            "arrange_tips_raw": arrange_tips_raw,
            "arrange_skips_raw": arrange_skips_total,
            "arrange_skip_rate_raw": skip_rate,
        },
        "variants": variants_out,
        "comparable_to": "analysis/p_launch_entry_long_short.md",
    }

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.tag}.json"
    csv_path = out_dir / f"{args.tag}.csv"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    _write_csv(csv_path, variants_out)

    print(
        f"n_symbols={n_sym}  window=<{HOLDOUT_START.date()}  "
        f"entry=next_open  TP5/SL2/72  verdict={verdict}"
    )
    if best_side:
        print(f"best={best_side['variant']} {best_side['side']} PF@m={best_side['pf_maker']}")
    print(
        f"arrange skip_rate_raw={skip_rate} "
        f"({arrange_skips_total}/{arrange_tips_raw})"
    )
    hdr = (
        f"{'variant':22s} {'side':6s} {'n':>6} {'win':>6} "
        f"{'net@m':>9} {'PF@m':>6} {'PF@0.2':>6}"
    )
    print(hdr)
    for name in VARIANT_ORDER:
        block = variants_out[name]
        for side, label in (("long_only", "long"), ("short_only", "short"), ("both", "both")):
            m = block[side]["maker_0.06pct"]
            leg = block[side]["legacy_0.20pct"]
            if m["n"] == 0:
                print(f"{name:22s} {label:6s} {0:6d} {'—':>6} {'—':>9} {'—':>6} {'—':>6}")
                continue
            print(
                f"{name:22s} {label:6s} {m['n']:6d} "
                f"{(m['win_rate'] or 0):6.1%} "
                f"{(m['mean_net'] or 0):+9.5f} "
                f"{(m['profit_factor'] or 0):6.3f} "
                f"{(leg['profit_factor'] or 0):6.3f}"
            )
    print(f"\nwrote {out_path}")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

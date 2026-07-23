#!/usr/bin/env python3
"""Trend-style exits on a fixed causal entry (train only; no holdout).

Owner 2026-07-23: treat the strategy as trend-following; approve changing the
*exit* while keeping one entry rule fixed. Goal = net return / PF (not AUC).

Entry base (strongest prior evidence from p_launch_entry_long_short /
p_direction_select_base_rate): **spread_expand_chg8** after dense tip —
Δfast_spread(8) ≥ 0.00383, direction = sign(close − cluster_mid).

Entry fill: **next_open** (p_entry_timing_close_vs_next not published yet;
default documented in the report).

Single theme: exit structure only. Baseline TP5/SL2/72 retained as a row.
Primary table = long | short. Costs: SWAP maker 0.06% + legacy 0.20%.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/trend_exit_base_rate.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/trend_exit_base_rate.py --n-symbols 0 \\
      --tag trend_exit_base_rate
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from scripts.direction_select_base_rate import (  # noqa: E402
    HOLDOUT_START,
    SL_MULT,
    SPREAD_CHG8_THR,
    TP_MULT,
    WARMUP,
    collect_signals,
)
from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import (  # noqa: E402
    ATR_PCT_MIN,
    HORIZON_BARS,
    BarrierOutcome,
    EntryMode,
    label_candidate,
    label_candidate_ma_exit,
    label_candidate_sl_only,
    label_candidate_structure_exit,
    label_candidate_time_stop,
    label_candidate_trailing,
    label_short_candidate,
    label_short_candidate_ma_exit,
    label_short_candidate_trailing,
)

SUCCESS_PF_MAKER = 1.3
ENTRY_RULE = "spread_expand_chg8"
ENTRY_FILL: EntryMode = "next_open"
TREND_HORIZON = 144  # timeout for trail / MA / structure / SL-only (trends need time)
MA_COL = "ema55"
TRAIL_MULT = 3.0
REDENSE_FAST_MAX = 0.0028

# Exit variants: name -> (description, resolver)
ExitResolver = Callable[[pd.DataFrame, int, int], Optional[BarrierOutcome]]


def _baseline(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    if d > 0:
        return label_candidate(
            frame, i, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS, entry=ENTRY_FILL
        )
    return label_short_candidate(
        frame, i, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS, entry=ENTRY_FILL
    )


def _trail3(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    if d > 0:
        return label_candidate_trailing(
            frame, i, trail_mult=TRAIL_MULT, horizon=TREND_HORIZON, entry=ENTRY_FILL
        )
    return label_short_candidate_trailing(
        frame, i, trail_mult=TRAIL_MULT, horizon=TREND_HORIZON, entry=ENTRY_FILL
    )


def _ma55(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    if d > 0:
        return label_candidate_ma_exit(
            frame, i, ma_col=MA_COL, horizon=TREND_HORIZON, entry=ENTRY_FILL
        )
    return label_short_candidate_ma_exit(
        frame, i, ma_col=MA_COL, horizon=TREND_HORIZON, entry=ENTRY_FILL
    )


def _structure(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    return label_candidate_structure_exit(
        frame,
        i,
        direction=d,
        redense_fast_max=REDENSE_FAST_MAX,
        horizon=TREND_HORIZON,
        entry=ENTRY_FILL,
    )


def _time48(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    return label_candidate_time_stop(
        frame, i, direction=d, hold_bars=48, entry=ENTRY_FILL
    )


def _time96(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    return label_candidate_time_stop(
        frame, i, direction=d, hold_bars=96, entry=ENTRY_FILL
    )


def _sl_only(frame: pd.DataFrame, i: int, d: int) -> BarrierOutcome | None:
    return label_candidate_sl_only(
        frame, i, direction=d, sl_mult=SL_MULT, horizon=TREND_HORIZON, entry=ENTRY_FILL
    )


EXIT_ORDER = [
    "baseline_tp5_sl2_h72",
    "trail3_atr_h144",
    "ma_ema55_h144",
    "structure_mid_redense_h144",
    "time_stop_48",
    "time_stop_96",
    "no_tp_sl2_h144",
]

EXIT_RESOLVERS: dict[str, ExitResolver] = {
    "baseline_tp5_sl2_h72": _baseline,
    "trail3_atr_h144": _trail3,
    "ma_ema55_h144": _ma55,
    "structure_mid_redense_h144": _structure,
    "time_stop_48": _time48,
    "time_stop_96": _time96,
    "no_tp_sl2_h144": _sl_only,
}

EXIT_RULES = {
    "baseline_tp5_sl2_h72": "fixed TP5/SL2, horizon 72 (mainline control)",
    "trail3_atr_h144": (
        f"ATR trailing {TRAIL_MULT:g}×ATR14(signal); no fixed TP; "
        f"seed stop at entry±trail; timeout {TREND_HORIZON}"
    ),
    "ma_ema55_h144": (
        f"exit when close crosses against {MA_COL}; no fixed TP/SL; "
        f"timeout {TREND_HORIZON}"
    ),
    "structure_mid_redense_h144": (
        "exit at close when price flips to opposite side of cluster_mid "
        f"OR fast_spread≤{REDENSE_FAST_MAX}; timeout {TREND_HORIZON}"
    ),
    "time_stop_48": "force flat at close of path bar 48; no TP/SL",
    "time_stop_96": "force flat at close of path bar 96; no TP/SL",
    "no_tp_sl2_h144": (
        f"SL{SL_MULT:g} only, no TP; timeout {TREND_HORIZON} (let winners run)"
    ),
}


def _max_drawdown(net: np.ndarray) -> float | None:
    """Peak-to-trough MDD on the cumulative sum of per-trade net returns."""
    if len(net) == 0:
        return None
    eq = np.cumsum(net)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    return round(float(dd.min()), 5)


def _stats(gross: list[float], holds: list[int], cost: float) -> dict:
    if not gross:
        return {
            "n": 0,
            "win_rate": None,
            "mean_gross": None,
            "mean_net": None,
            "sum_net": None,
            "profit_factor": None,
            "mean_hold_bars": None,
            "max_dd_sum_net": None,
        }
    g = np.asarray(gross, dtype=float)
    net = g - cost
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n": int(len(g)),
        "win_rate": round(float((net > 0).mean()), 4),
        "mean_gross": round(float(g.mean()), 5),
        "mean_net": round(float(net.mean()), 5),
        "sum_net": round(float(net.sum()), 5),
        "profit_factor": round(float(w / -l), 3) if l < 0 else None,
        "mean_hold_bars": round(float(np.mean(holds)), 2) if holds else None,
        "max_dd_sum_net": _max_drawdown(net),
    }


def _side_block(
    gross: list[float], dirs: list[int], holds: list[int]
) -> dict:
    long_g = [g for g, d in zip(gross, dirs) if d > 0]
    short_g = [g for g, d in zip(gross, dirs) if d < 0]
    long_h = [h for h, d in zip(holds, dirs) if d > 0]
    short_h = [h for h, d in zip(holds, dirs) if d < 0]
    return {
        "n_long": len(long_g),
        "n_short": len(short_g),
        "long_only": {
            "maker_0.06pct": _stats(long_g, long_h, FORWARD_COST),
            "legacy_0.20pct": _stats(long_g, long_h, LEGACY_P0_ROUND_TRIP),
            "gross_pre_cost": _stats(long_g, long_h, 0.0),
        },
        "short_only": {
            "maker_0.06pct": _stats(short_g, short_h, FORWARD_COST),
            "legacy_0.20pct": _stats(short_g, short_h, LEGACY_P0_ROUND_TRIP),
            "gross_pre_cost": _stats(short_g, short_h, 0.0),
        },
        "both": {
            "maker_0.06pct": _stats(gross, holds, FORWARD_COST),
            "legacy_0.20pct": _stats(gross, holds, LEGACY_P0_ROUND_TRIP),
            "gross_pre_cost": _stats(gross, holds, 0.0),
        },
    }


def _write_csv(path: Path, variants_out: dict) -> None:
    rows = []
    for name in EXIT_ORDER:
        block = variants_out[name]
        for side, label in (("long_only", "long"), ("short_only", "short"), ("both", "both")):
            m = block[side]["maker_0.06pct"]
            leg = block[side]["legacy_0.20pct"]
            rows.append(
                {
                    "exit": name,
                    "side": label,
                    "n": m["n"],
                    "win_rate_maker": m["win_rate"],
                    "mean_net_maker": m["mean_net"],
                    "sum_net_maker": m["sum_net"],
                    "pf_maker": m["profit_factor"],
                    "pf_legacy_0.20": leg["profit_factor"],
                    "mean_hold_bars": m["mean_hold_bars"],
                    "max_dd_sum_net_maker": m["max_dd_sum_net"],
                    "exit_rule": block["exit_rule"],
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
    ap.add_argument("--tag", default="trend_exit_base_rate")
    args = ap.parse_args()

    bags: dict[str, dict[str, list]] = {
        v: {"gross": [], "dir": [], "hold": []} for v in EXIT_ORDER
    }
    n_sym = 0
    t_min = t_max = None
    n_fires = 0
    n_settled = {v: 0 for v in EXIT_ORDER}

    # Trend exits need longer path; gate series length on max horizon used.
    max_h = max(TREND_HORIZON, HORIZON_BARS, 96)

    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < WARMUP + max_h + 50:
            continue

        enriched = add_indicators(frame)
        sigs, _audit = collect_signals(enriched)
        pairs = sigs.get(ENTRY_RULE, [])
        t = pd.to_datetime(enriched["open_time"], utc=True)

        for i, d in pairs:
            n_fires += 1
            ti = t.iloc[i]
            if t_min is None or ti < t_min:
                t_min = ti
            if t_max is None or ti > t_max:
                t_max = ti
            for name, resolve in EXIT_RESOLVERS.items():
                out = resolve(enriched, i, d)
                if out is None:
                    continue
                bags[name]["gross"].append(float(out.realized_ret))
                bags[name]["dir"].append(int(d))
                bags[name]["hold"].append(int(out.exit_offset))
                n_settled[name] += 1

        n_sym += 1
        if n_sym % 40 == 0:
            print(f"  scanned {n_sym} symbols …")
        if args.n_symbols and n_sym >= args.n_symbols:
            break

    variants_out = {}
    for name in EXIT_ORDER:
        block = _side_block(
            bags[name]["gross"], bags[name]["dir"], bags[name]["hold"]
        )
        block["exit_rule"] = EXIT_RULES[name]
        variants_out[name] = block

    # Rank by sum_net@maker then PF@maker on each side separately.
    ranking: list[dict] = []
    for name, block in variants_out.items():
        for side in ("long_only", "short_only"):
            m = block[side]["maker_0.06pct"]
            if m["n"] == 0:
                continue
            ranking.append(
                {
                    "exit": name,
                    "side": side,
                    "n": m["n"],
                    "sum_net_maker": m["sum_net"],
                    "mean_net_maker": m["mean_net"],
                    "pf_maker": m["profit_factor"],
                    "win_rate": m["win_rate"],
                    "mean_hold_bars": m["mean_hold_bars"],
                    "max_dd_sum_net_maker": m["max_dd_sum_net"],
                }
            )
    ranking.sort(
        key=lambda r: (
            r["sum_net_maker"] if r["sum_net_maker"] is not None else -1e18,
            r["pf_maker"] if r["pf_maker"] is not None else -1e18,
        ),
        reverse=True,
    )

    # Success line for the headline: PF≥1.3 is the deployable bar; positive
    # sum_net with PF<1.3 is reported as tension, not a pass.
    pf_cleared = [
        r for r in ranking
        if r["pf_maker"] is not None and r["pf_maker"] >= SUCCESS_PF_MAKER
    ]
    positive_net = [
        r for r in ranking
        if r["sum_net_maker"] is not None and r["sum_net_maker"] > 0
    ]

    if pf_cleared:
        verdict = "趋势出场抬过 PF≥1.3"
    elif positive_net:
        best = positive_net[0]
        pf = best["pf_maker"]
        if pf is not None and pf < SUCCESS_PF_MAKER:
            verdict = (
                "趋势出场净收益转正但 PF<1.3（张力）"
            )
        else:
            verdict = "趋势出场净收益转正"
    else:
        verdict = "换出场仍不够"

    out = {
        "tag": args.tag,
        "question": (
            "On fixed spread_expand_chg8 entry (next_open), does a trend-style "
            "exit lift long|short train PF@maker ≥ 1.3 or turn sum_net clearly > 0?"
        ),
        "success_criterion": {
            "primary_sort": ["sum_net_maker", "pf_maker"],
            "pf_maker_ge": SUCCESS_PF_MAKER,
            "also_success": "sum_net_maker >> 0 after cost (report tension if PF<1.3)",
        },
        "verdict": verdict,
        "best_by_sum_net": ranking[0] if ranking else None,
        "pf_cleared_sides": pf_cleared,
        "positive_sum_net_sides": positive_net[:8],
        "ranking_sum_net": ranking,
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "entry_rule": ENTRY_RULE,
            "entry_rule_why": (
                "strongest prior side from launch/direction-select: "
                "spread-short PF@maker 1.245 under TP5/SL2/72"
            ),
            "entry_fill": ENTRY_FILL,
            "entry_fill_note": (
                "p_entry_timing_close_vs_next not published; default next_open"
            ),
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "atr_pct_min": ATR_PCT_MIN,
            "baseline_exit": f"TP{TP_MULT:g}/SL{SL_MULT:g}/{HORIZON_BARS}bar",
            "trend_timeout_bars": TREND_HORIZON,
            "ma_col": MA_COL,
            "trail_mult": TRAIL_MULT,
            "spread_chg8_thr": SPREAD_CHG8_THR,
            "redense_fast_max": REDENSE_FAST_MAX,
            "primary_verdict": "long_only and short_only; both is secondary",
            "theme": "exit from fixed barriers to trend-style; entry fixed",
        },
        "data": {
            "n_symbols": n_sym,
            "time_range": [
                str(t_min) if t_min is not None else None,
                str(t_max) if t_max is not None else None,
            ],
            "n_entry_fires": n_fires,
            "n_settled_by_exit": n_settled,
        },
        "variants": variants_out,
        "comparable_to": [
            "analysis/p_launch_entry_long_short.md",
            "analysis/p_direction_select_base_rate.md",
        ],
    }

    out_dir = PROJECT / "analysis" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.tag}.json"
    csv_path = out_dir / f"{args.tag}.csv"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    _write_csv(csv_path, variants_out)

    print(
        f"n_symbols={n_sym}  window=<{HOLDOUT_START.date()}  "
        f"entry={ENTRY_RULE}/{ENTRY_FILL}  verdict={verdict}"
    )
    if ranking:
        b = ranking[0]
        print(
            f"best_sum_net={b['exit']} {b['side']} "
            f"sum={b['sum_net_maker']:+.5f} PF@m={b['pf_maker']} n={b['n']}"
        )
    hdr = (
        f"{'exit':28s} {'side':6s} {'n':>6} {'win':>6} "
        f"{'sum@m':>10} {'net@m':>9} {'PF@m':>6} {'PF@0.2':>6} "
        f"{'hold':>6} {'mdd':>9}"
    )
    print(hdr)
    for name in EXIT_ORDER:
        block = variants_out[name]
        for side, label in (("long_only", "long"), ("short_only", "short")):
            m = block[side]["maker_0.06pct"]
            leg = block[side]["legacy_0.20pct"]
            if m["n"] == 0:
                print(f"{name:28s} {label:6s} {0:6d}")
                continue
            print(
                f"{name:28s} {label:6s} {m['n']:6d} "
                f"{(m['win_rate'] or 0):6.1%} "
                f"{(m['sum_net'] or 0):+10.5f} "
                f"{(m['mean_net'] or 0):+9.5f} "
                f"{(m['profit_factor'] or 0):6.3f} "
                f"{(leg['profit_factor'] or 0):6.3f} "
                f"{(m['mean_hold_bars'] or 0):6.1f} "
                f"{(m['max_dd_sum_net'] or 0):+9.5f}"
            )
    print(f"\nwrote {out_path}")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

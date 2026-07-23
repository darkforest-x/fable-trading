#!/usr/bin/env python3
"""Causal base rates for LAUNCH-entry definitions, split long vs short.

Why (owner 2026-07-23 round-2): prior run pooled long+short into one PF row.
Owner: "一眼就知道多空没区分好". This rewrite keeps the same entry/exit/cost/
dense thresholds and forces per-side reporting. Measurement fix only — not
signal rescue.

Each variant fires only with information at bar i (and earlier). Entry =
next-bar open (labeling.py). Exit = TP5/SL2/72 via label_candidate /
label_short_candidate. Costs: SWAP maker 0.06% and legacy 0.20%. Train only
(<2026-05-04).

Bundle: judgment-layer add_indicators spreads only (same as round-1).

Variants (separate; never packed):
  1. emergence_always_long  — dense run first hits 5; fixed long
  2. emergence_always_short — same tip; fixed short (mid-dense side control)
  3. emergence_mom24        — same tip; dir = sign(close[i]/close[i-24]-1)
  4. range_break_n20        — after dense; first close beyond prior N hi/lo
  5. vol_break_n20_k1.5     — same + volume > mean(M)*k
  6. spread_expand_chg8     — after dense; fast_spread chg8 >= thr; dir vs mid
  7. ma_arrange_cross       — after dense; ema8×ema21 or close×sma20 cross

Per variant the report emits three slices: long-only | short-only | both.
Primary verdict = long-only and short-only; both is retained but not decisive.

Direction rules (causal, written into JSON + report):
  range/vol break : close > prior_hi → +1; close < prior_lo → -1
  spread expand   : close >= cluster_mid → +1 else -1
  ma cross        : golden/death of ema8×ema21, else close×sma20
  mom24           : sign of 24-bar close return (0 → skip)

Usage:
  PYTHONPATH=. .venv/bin/python scripts/launch_entry_base_rate.py --n-symbols 20
  PYTHONPATH=. .venv/bin/python scripts/launch_entry_base_rate.py --n-symbols 0 \\
      --tag launch_entry_long_short
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from src.costs import FORWARD_COST, LEGACY_P0_ROUND_TRIP  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
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
VOL_M = 20
VOL_K = 1.5
SPREAD_CHG8_THR = 0.00383

EMERGENCE_PF_MAKER_PUB = 0.874
OWNER_BOX_ORACLE_PF_MAKER = 1.183
OWNER_BOX_ORACLE_PF_LEGACY = 1.039
DIR_ORACLE_PF_TP3SL1 = 2.683

VARIANT_ORDER = [
    "emergence_always_long",
    "emergence_always_short",
    "emergence_mom24",
    "range_break_n20",
    "vol_break_n20_k1.5",
    "spread_expand_chg8",
    "ma_arrange_cross",
]

DIR_RULES = {
    "emergence_always_long": "fixed +1 at dense tip (run==5)",
    "emergence_always_short": "fixed -1 at dense tip (run==5)",
    "emergence_mom24": "sign(close[i]/close[i-24]-1); mom==0 skipped",
    "range_break_n20": (
        "after tip, first close>max(high[i-N:i]) → +1; "
        "close<min(low[i-N:i]) → -1; N=20"
    ),
    "vol_break_n20_k1.5": (
        "range break AND volume[i] > mean(volume[i-M:i])*k; M=20 k=1.5"
    ),
    "spread_expand_chg8": (
        "first fast_spread[i]-fast_spread[i-8] >= thr; "
        "dir = +1 if close>=cluster_mid else -1"
    ),
    "ma_arrange_cross": (
        "first ema8×ema21 cross (priority) else close×sma20 cross; follow cross"
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
    """Gross realized_ret under TP5/SL2/72; None if untakeable / incomplete."""
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
    """Force long-only / short-only / both under both cost conventions."""
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


def collect_signals(enriched: pd.DataFrame) -> dict[str, list[tuple[int, int]]]:
    """Return variant -> list of (signal_i, direction) with +1 long / -1 short.

    All decisions use columns at or before signal_i only.
    """
    n = len(enriched)
    close = enriched["close"].to_numpy(dtype=float)
    high = enriched["high"].to_numpy(dtype=float)
    low = enriched["low"].to_numpy(dtype=float)
    volume = enriched["volume"].to_numpy(dtype=float)
    fast = pd.to_numeric(enriched["fast_spread"], errors="coerce").to_numpy()
    full = pd.to_numeric(enriched["full_spread"], errors="coerce").to_numpy()
    ema8 = enriched["ema8"].to_numpy(dtype=float)
    ema21 = enriched["ema21"].to_numpy(dtype=float)
    cluster_mid = (
        (enriched["cluster_max"].to_numpy(dtype=float) + enriched["cluster_min"].to_numpy(dtype=float))
        / 2.0
    )
    sma20 = pd.Series(close).rolling(20, min_periods=20).mean().to_numpy()
    run = _dense_run(fast, full)

    emerg_long: list[tuple[int, int]] = []
    emerg_short: list[tuple[int, int]] = []
    emerg_mom: list[tuple[int, int]] = []
    for i in range(WARMUP, n - 1):
        if run[i] != MIN_DENSE_BARS:
            continue
        emerg_long.append((i, +1))
        emerg_short.append((i, -1))
        if i >= 24 and close[i - 24] > 0:
            mom = close[i] / close[i - 24] - 1.0
            if mom == 0:
                continue
            emerg_mom.append((i, +1 if mom > 0 else -1))

    range_f: list[tuple[int, int]] = []
    vol_f: list[tuple[int, int]] = []
    spread_f: list[tuple[int, int]] = []
    ma_f: list[tuple[int, int]] = []

    armed_from: int | None = None
    fired_range = fired_vol = fired_spread = fired_ma = False

    for i in range(WARMUP, n - 1):
        if run[i] == MIN_DENSE_BARS:
            armed_from = i
            fired_range = fired_vol = fired_spread = fired_ma = False

        if armed_from is None:
            continue
        if i - armed_from > MAX_WAIT_BARS:
            armed_from = None
            continue
        # Skip emergence bar: launch = subsequent confirmation.
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

        if not fired_vol and i >= max(RANGE_N, VOL_M):
            prior_hi = float(np.max(high[i - RANGE_N : i]))
            prior_lo = float(np.min(low[i - RANGE_N : i]))
            vol_mean = float(np.mean(volume[i - VOL_M : i]))
            vol_ok = vol_mean > 0 and volume[i] > vol_mean * VOL_K
            if vol_ok and close[i] > prior_hi:
                vol_f.append((i, +1))
                fired_vol = True
            elif vol_ok and close[i] < prior_lo:
                vol_f.append((i, -1))
                fired_vol = True

        if not fired_spread and i >= 8:
            chg8 = float(fast[i] - fast[i - 8])
            if np.isfinite(chg8) and chg8 >= SPREAD_CHG8_THR:
                mid = cluster_mid[i]
                if np.isfinite(mid) and mid > 0:
                    d = +1 if close[i] >= mid else -1
                    spread_f.append((i, d))
                    fired_spread = True

        if not fired_ma and i >= 1 and np.isfinite(sma20[i]) and np.isfinite(sma20[i - 1]):
            d = 0
            if ema8[i - 1] <= ema21[i - 1] and ema8[i] > ema21[i]:
                d = +1
            elif ema8[i - 1] >= ema21[i - 1] and ema8[i] < ema21[i]:
                d = -1
            elif close[i - 1] <= sma20[i - 1] and close[i] > sma20[i]:
                d = +1
            elif close[i - 1] >= sma20[i - 1] and close[i] < sma20[i]:
                d = -1
            if d != 0:
                ma_f.append((i, d))
                fired_ma = True

        if fired_range and fired_vol and fired_spread and fired_ma:
            armed_from = None

    return {
        "emergence_always_long": _dedup(emerg_long),
        "emergence_always_short": _dedup(emerg_short),
        "emergence_mom24": _dedup(emerg_mom),
        "range_break_n20": _dedup(range_f),
        "vol_break_n20_k1.5": _dedup(vol_f),
        "spread_expand_chg8": _dedup(spread_f),
        "ma_arrange_cross": _dedup(ma_f),
    }


def _pf(block: dict, side: str, cost_key: str) -> float | None:
    return block[side][cost_key]["profit_factor"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=60, help="0 = all SWAP")
    ap.add_argument("--tag", default="launch_entry_long_short")
    args = ap.parse_args()

    bags: dict[str, dict[str, list]] = {
        v: {"gross": [], "dir": []} for v in VARIANT_ORDER
    }
    n_sym = 0
    t_min = t_max = None
    # Audit counters: confirm break variants never flip side vs trigger.
    audit = {
        "range_up_as_long": 0,
        "range_dn_as_short": 0,
        "range_mismatch": 0,
        "vol_up_as_long": 0,
        "vol_dn_as_short": 0,
        "vol_mismatch": 0,
    }

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
        sigs = collect_signals(enriched)
        t = pd.to_datetime(enriched["open_time"], utc=True)
        close = enriched["close"].to_numpy(dtype=float)
        high = enriched["high"].to_numpy(dtype=float)
        low = enriched["low"].to_numpy(dtype=float)
        volume = enriched["volume"].to_numpy(dtype=float)

        # Direction integrity audit on raw fire bars (pre-label filter).
        for i, d in sigs["range_break_n20"]:
            prior_hi = float(np.max(high[i - RANGE_N : i]))
            prior_lo = float(np.min(low[i - RANGE_N : i]))
            if close[i] > prior_hi and d > 0:
                audit["range_up_as_long"] += 1
            elif close[i] < prior_lo and d < 0:
                audit["range_dn_as_short"] += 1
            else:
                audit["range_mismatch"] += 1
        for i, d in sigs["vol_break_n20_k1.5"]:
            prior_hi = float(np.max(high[i - RANGE_N : i]))
            prior_lo = float(np.min(low[i - RANGE_N : i]))
            vol_mean = float(np.mean(volume[i - VOL_M : i]))
            vol_ok = vol_mean > 0 and volume[i] > vol_mean * VOL_K
            if vol_ok and close[i] > prior_hi and d > 0:
                audit["vol_up_as_long"] += 1
            elif vol_ok and close[i] < prior_lo and d < 0:
                audit["vol_dn_as_short"] += 1
            else:
                audit["vol_mismatch"] += 1

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

    leak_flags = []
    for name, block in variants_out.items():
        for side in ("long_only", "short_only"):
            pf = _pf(block, side, "maker_0.06pct")
            if pf is not None and pf > 1.3:
                leak_flags.append(
                    {"variant": name, "side": side, "pf_maker": pf, "action": "manual_leak_check"}
                )

    out = {
        "tag": args.tag,
        "round1_note": (
            "Round-1 pooled long+short into one PF (measurement presentation bug). "
            "Direction rules for break/cross were not inverted; round-1 both-side "
            "PF must be down-weighted — use this split table as primary."
        ),
        "discipline": {
            "holdout_start": str(HOLDOUT_START),
            "holdout_touched": False,
            "entry": "next_bar_open",
            "exit": f"TP{TP_MULT:g}/SL{SL_MULT:g}/{HORIZON_BARS}bar",
            "costs": {"swap_maker": FORWARD_COST, "legacy_p0": LEGACY_P0_ROUND_TRIP},
            "atr_pct_min": ATR_PCT_MIN,
            "bundle": (
                "judgment add_indicators only; dense gate = EMA8-55 fast / "
                "EMA8-55+144/200 full"
            ),
            "max_wait_bars_after_dense": MAX_WAIT_BARS,
            "range_n": RANGE_N,
            "vol_m": VOL_M,
            "vol_k": VOL_K,
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
        },
        "direction_audit": audit,
        "variants": variants_out,
        "published_refs": {
            "emergence_pf_maker": EMERGENCE_PF_MAKER_PUB,
            "owner_box_oracle_pf_maker": OWNER_BOX_ORACLE_PF_MAKER,
            "owner_box_oracle_pf_legacy": OWNER_BOX_ORACLE_PF_LEGACY,
            "direction_oracle_pf_tp3sl1": DIR_ORACLE_PF_TP3SL1,
            "note_direction_oracle": (
                "2.68 is TP3/SL1 pick-better-side ceiling; NOT TP5/SL2"
            ),
        },
        "leak_flags": leak_flags,
    }

    out_path = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    print(
        f"n_symbols={n_sym}  window=<{HOLDOUT_START.date()}  "
        f"entry=next_open  TP5/SL2/72  audit_mismatch="
        f"range={audit['range_mismatch']} vol={audit['vol_mismatch']}"
    )
    hdr = (
        f"{'variant':28s} {'side':6s} {'n':>6} {'win':>6} "
        f"{'net@m':>9} {'PF@m':>6} {'PF@0.2':>6}"
    )
    print(hdr)
    for name in VARIANT_ORDER:
        block = variants_out[name]
        for side, label in (("long_only", "long"), ("short_only", "short"), ("both", "both")):
            m = block[side]["maker_0.06pct"]
            leg = block[side]["legacy_0.20pct"]
            if m["n"] == 0:
                print(f"{name:28s} {label:6s} {0:6d} {'—':>6} {'—':>9} {'—':>6} {'—':>6}")
                continue
            print(
                f"{name:28s} {label:6s} {m['n']:6d} "
                f"{(m['win_rate'] or 0):6.1%} "
                f"{(m['mean_net'] or 0):+9.5f} "
                f"{(m['profit_factor'] or 0):6.3f} "
                f"{(leg['profit_factor'] or 0):6.3f}"
            )
    if leak_flags:
        print("LEAK FLAGS:", leak_flags)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Let realised P&L, not my eye, decide where the Grade-A box belongs.

Owner directive 2026-08-30. Two definitions of "densest crossing" were tried by
inspection and both failed: maximum pairwise crossing count drifts into the
launch (a fast MA sweeping down through the slow ones scores crossings all the
way through the breakdown), and minimum six-MA bandwidth is dominated by the
120-period lines and lands left of what the eye reads as the crossing. Rather
than propose a third guess, this measures which geometry the PROFITABLE events
actually have.

Looking at the future is legitimate here and is not a leak. Iron rule 3 permits
exactly this: features may only use the signal bar and earlier, "only labels may
see the future". This script builds a LABEL. Nothing it computes is ever handed
to the detector at inference; the detector still only sees its frozen window.

What it produces, per independent event:

  outcome   the frozen TP5/SL2/72 barrier resolved from a realistic entry, net
            of the frozen 0.2% round trip, using yoyo.contracts.outcomes rather
            than a private re-implementation
  geometry  where each candidate anchor sits relative to the current box, plus
            the shape scalars, so "winners are anchored at X" is measurable

The analysis that follows must not fit the anchor on all 1,043 events and then
report the same events as evidence. Events are split chronologically so an
anchor chosen on the early block is scored on the later one, and any profit
claim is compared against a matched random control -- same symbol, same month,
same ATR tercile, same barriers, same cost -- because a pool of launches during
a trending month is beta, not geometry.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.contracts.outcomes import resolve_barrier_outcome  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas, ALL_MA_COLS  # noqa: E402

SRC = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
OUT = ROOT / "analysis/output/grade_a_label_optimisation_20260830"

MA_COLS = list(ALL_MA_COLS)
FAST_MA = ["sma20", "ema20", "sma60", "ema60"]
MA_PAIRS = list(itertools.combinations(MA_COLS, 2))

# Frozen economics. None of these are tuned here; they are the project's
# existing contract and changing them is an owner decision.
TP_ATR, SL_ATR, HORIZON = 5.0, 2.0, 72
ROUND_TRIP_COST = 0.002
ENTRY_LAG_BARS = 2          # earliest honest emit under the completed-history contract
ATR_PERIOD = 14
SEARCH_PRE, SEARCH_POST = 6, 12


def atr_series(frame: pd.DataFrame) -> pd.Series:
    """Strict ATR: no value until 14 true ranges exist.

    docs/consolidation/DUPLICATE_SEMANTICS.md section 4 records two ATR
    implementations that disagree by 0.109 at bar 14. Entries here sit tens of
    thousands of bars into each series, where the two agree to under 1e-7, so
    the unresolved divergence cannot change a single outcome in this study.
    """
    high, low, close = frame["high"], frame["low"], frame["close"]
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    atr = tr.ewm(alpha=1.0 / ATR_PERIOD, adjust=False, ignore_na=True).mean()
    atr.iloc[:ATR_PERIOD] = np.nan
    return atr


def anchors(enriched: pd.DataFrame, core_start: int, core_end: int) -> dict[str, int]:
    """Candidate "this is the densest bar" definitions, as absolute bar indices."""
    lo = core_start - SEARCH_PRE
    hi = min(core_end + SEARCH_POST, len(enriched) - 2)
    window = enriched.iloc[lo : hi + 1]
    close = window["close"].to_numpy(dtype=float)

    full_bw = (window[MA_COLS].max(axis=1) - window[MA_COLS].min(axis=1)).to_numpy(float) / close
    fast_bw = (window[FAST_MA].max(axis=1) - window[FAST_MA].min(axis=1)).to_numpy(float) / close

    seg = enriched[MA_COLS].iloc[lo - 1 : hi + 1]
    cross = np.zeros(hi - lo + 1, dtype=int)
    for a, b in MA_PAIRS:
        sign = np.sign((seg[a] - seg[b]).to_numpy(dtype=float))
        cross += (sign[1:] * sign[:-1] < 0).astype(int)

    values = window[MA_COLS].to_numpy(dtype=float)
    tangle = np.array([
        np.mean([abs(values[i, MA_COLS.index(a)] - values[i, MA_COLS.index(b)]) for a, b in MA_PAIRS])
        / close[i] for i in range(len(close))
    ])

    return {
        "full_bandwidth_min": lo + int(np.argmin(full_bw)),
        "fast_bandwidth_min": lo + int(np.argmin(fast_bw)),
        "crossings_max": lo + int(np.argmax(cross)),
        "tangle_min": lo + int(np.argmin(tangle)),
    }


def simulate(records: list[dict[str, Any]]) -> pd.DataFrame:
    cache: dict[str, pd.DataFrame] = {}
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    for rec in records:
        if rec.get("sample_kind") != "positive" or rec["event_id"] in seen:
            continue
        seen.add(rec["event_id"])
        path = rec["source_path"]
        if path not in cache:
            frame = add_mas(pd.read_csv(ROOT / path if not Path(path).is_absolute() else path))
            frame["atr14"] = atr_series(frame)
            cache[path] = frame
            if len(cache) > 40:
                cache.pop(next(iter(cache)))
        enriched = cache[path]

        core_start = int(rec["source_core_start_i"])
        core_end = int(rec["source_core_end_i"])
        entry_i = core_end + ENTRY_LAG_BARS
        if entry_i + HORIZON >= len(enriched) or core_start - SEARCH_PRE < 1:
            continue
        atr = float(enriched["atr14"].iloc[entry_i])
        entry_price = float(enriched["close"].iloc[entry_i])
        if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry_price) or entry_price <= 0:
            continue

        side = rec["direction"]
        res = resolve_barrier_outcome(
            enriched.iloc[entry_i:].reset_index(drop=True),
            side=side.lower(), entry_i=0, entry_price=entry_price, atr=atr,
            tp_atr_mult=TP_ATR, sl_atr_mult=SL_ATR, horizon_bars=HORIZON,
            same_bar_policy="conservative_sl", gap_policy="barrier_price",
            return_convention="linear_long" if side == "LONG" else "linear_short",
            allow_partial=False,
        )
        if res.gross_ret is None:
            continue

        anc = anchors(enriched, core_start, core_end)
        core_centre = (core_start + core_end) / 2.0
        row: dict[str, Any] = {
            "event_id": rec["event_id"],
            "symbol": rec["symbol"],
            "direction": side,
            "time_block": rec["time_block"],
            "core_start_i": core_start,
            "core_end_i": core_end,
            "core_bars": int(rec["core_bars"]),
            "entry_time": str(enriched["open_time"].iloc[entry_i]),
            "atr": atr,
            "atr_pct": atr / entry_price,
            "outcome": res.outcome,
            "gross_ret": float(res.gross_ret),
            "net_ret": float(res.gross_ret) - ROUND_TRIP_COST,
            "exit_offset": int(res.exit_offset),
        }
        for name, bar in anc.items():
            row[f"offset_{name}"] = bar - core_centre
        for key in ("tightening_ratio_vs_pre12", "six_ma_end_bandwidth_bps",
                    "six_ma_core_mean_bandwidth_bps", "core_width_end_start_ratio",
                    "pre12_median_bandwidth_bps"):
            row[key] = rec["strict_metrics"].get(key)
        rows.append(row)
        if len(rows) % 200 == 0:
            print(f"  {len(rows)} events simulated ...", flush=True)

    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    records = [json.loads(l) for l in (SRC / "manifest.jsonl").read_text().splitlines() if l.strip()]
    if args.limit:
        records = records[: args.limit]
    print(f"manifest rows: {len(records)}")
    table = simulate(records)
    OUT.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT / "event_outcomes_geometry.csv", index=False)

    print(f"\n=== simulated {len(table)} independent events ===")
    print(f"  entry: close of core_end + {ENTRY_LAG_BARS} bars (earliest honest emit)")
    print(f"  barriers: TP {TP_ATR} ATR / SL {SL_ATR} ATR / {HORIZON} bars, cost {ROUND_TRIP_COST:.3%}")
    print(f"\n  outcome mix: {table['outcome'].value_counts().to_dict()}")
    print(f"  net return  mean {table.net_ret.mean()*100:+.3f}%   median {table.net_ret.median()*100:+.3f}%")
    print(f"  win rate (net>0): {(table.net_ret > 0).mean()*100:.1f}%")
    by_side = table.groupby("direction").net_ret.agg(["count", "mean", "median"])
    print(f"\n{by_side}")
    print(f"\nwrote {OUT / 'event_outcomes_geometry.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Resolve barrier outcomes for the 5m MA-launch patterns.

The 15m pool was simulated at TP5/SL2 over 72 bars, which is 18 hours. Reusing
72 bars on 5m would be 6 hours -- a different question, not the same one -- and
a ten-pattern spot check already showed the difference: 4 timeouts out of 10,
against 13% timeouts on 15m. So the horizon is swept rather than assumed, and
the sweep is reported in full instead of only at whichever value looks best.

Every other term is the frozen 15m contract: entry at the close of core_end+2,
TP 5 ATR, SL 2 ATR, conservative same-bar stop, 0.2% round trip. Returns are
reported in ATR units because the barriers are ATR multiples -- measuring them
in percent measures the coin's volatility instead of the pattern, which is how
an earlier pass produced a sign-flipped result.

Choosing a horizon from these numbers is an owner decision. This only measures.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.contracts.outcomes import resolve_barrier_outcome  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402

CANDIDATES = ROOT / "analysis/output/ma_launch_5m_candidates_20260830/candidates_5m.jsonl"
OUT = ROOT / "analysis/output/ma_launch_5m_outcomes_20260830"
TP_ATR, SL_ATR, COST, ENTRY_LAG = 5.0, 2.0, 0.002, 2
HORIZONS = (72, 144, 216, 288)          # 6h, 12h, 18h, 24h on 5m bars


def atr_series(frame: pd.DataFrame) -> pd.Series:
    prev = frame["close"].shift(1)
    tr = pd.concat([(frame["high"] - frame["low"]),
                    (frame["high"] - prev).abs(),
                    (frame["low"] - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    atr = tr.ewm(alpha=1.0 / 14, adjust=False, ignore_na=True).mean()
    atr.iloc[:14] = np.nan
    return atr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = [json.loads(l) for l in CANDIDATES.read_text().splitlines() if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    rows.sort(key=lambda r: r["source_path"])          # keep the file cache warm
    print(f"patterns: {len(rows)}", flush=True)

    cache: dict[str, pd.DataFrame] = {}
    out: list[dict] = []
    skipped: Counter[str] = Counter()
    for i, row in enumerate(rows, 1):
        path = row["source_path"]
        if path not in cache:
            frame, _ = read_preholdout_prefix(ROOT / path, end_exclusive=HOLDOUT_START, bar_minutes=5)
            frame = add_mas(frame)
            frame["atr14"] = atr_series(frame)
            cache = {path: frame}                      # one file at a time; 5m series are large
        enriched = cache[path]

        entry_i = int(row["source_core_end_i"]) + ENTRY_LAG
        if entry_i + max(HORIZONS) >= len(enriched):
            skipped["not enough forward bars"] += 1
            continue
        atr = float(enriched["atr14"].iloc[entry_i])
        price = float(enriched["close"].iloc[entry_i])
        if not np.isfinite(atr) or atr <= 0 or not np.isfinite(price) or price <= 0:
            skipped["invalid atr or price"] += 1
            continue

        side = str(row["direction"])
        record = {"symbol": row["symbol"], "direction": side,
                  "core_end_time": str(row["core_end_time"]), "atr_pct": atr / price}
        forward = enriched.iloc[entry_i:].reset_index(drop=True)
        for horizon in HORIZONS:
            res = resolve_barrier_outcome(
                forward, side=side.lower(), entry_i=0, entry_price=price, atr=atr,
                tp_atr_mult=TP_ATR, sl_atr_mult=SL_ATR, horizon_bars=horizon,
                same_bar_policy="conservative_sl", gap_policy="barrier_price",
                return_convention="linear_long" if side == "LONG" else "linear_short",
                allow_partial=False)
            if res.gross_ret is None:
                record[f"outcome_{horizon}"] = None
                continue
            record[f"outcome_{horizon}"] = res.outcome
            record[f"net_atr_{horizon}"] = res.gross_ret / (atr / price) - COST / (atr / price)
        out.append(record)
        if i % 400 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    table = pd.DataFrame(out)
    OUT.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT / "outcomes_5m.csv", index=False)

    print(f"\n=== 5m outcomes, n={len(table)} ===")
    for key, value in skipped.items():
        print(f"  skipped {key}: {value}")
    print(f"\n{'horizon':>10}{'TP':>7}{'SL':>7}{'timeout':>9}{'TP率':>8}{'净收益均值':>12}{'中位':>9}")
    for horizon in HORIZONS:
        col, net = f"outcome_{horizon}", f"net_atr_{horizon}"
        sub = table[table[col].notna()]
        counts = sub[col].value_counts()
        hours = horizon * 5 / 60
        print(f"{horizon:>6} ({hours:.0f}h){counts.get('tp',0):>7}{counts.get('sl',0):>7}"
              f"{counts.get('timeout',0):>9}{(sub[col]=='tp').mean()*100:>7.1f}%"
              f"{sub[net].mean():>+12.3f}{sub[net].median():>+9.3f}")
    print(f"\n  breakeven TP rate at 5:2 = {2/(5+2)*100:.1f}%")
    print(f"wrote {OUT / 'outcomes_5m.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

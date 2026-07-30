#!/usr/bin/env python3
"""Build data/judgment_yolo_swap_v10.csv from judgment_v10_wide for L2 freeze.

Owner 2026-07-31: L2 mainline pairs with L1 short_star_v10. Maps
net_barrier_taker -> realized_ret, label_barrier -> label. Pre-holdout only.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import HOLDOUT_START

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "data" / "judgment_v10_wide.csv"
DST = PROJECT / "data" / "judgment_yolo_swap_v10.csv"


def main() -> int:
    if not SRC.exists():
        raise SystemExit(f"missing {SRC}")
    d = pd.read_csv(SRC, parse_dates=["signal_time"])
    if d["signal_time"].dt.tz is None:
        d["signal_time"] = d["signal_time"].dt.tz_localize("UTC")
    else:
        d["signal_time"] = d["signal_time"].dt.tz_convert("UTC")
    d = d[d["signal_time"] < HOLDOUT_START].copy()
    d["realized_ret"] = d["net_barrier_taker"].astype(float)
    d["label"] = d["label_barrier"].astype(int)
    if "side" not in d.columns:
        d["side"] = "short"
    keep = list(
        dict.fromkeys(
            list(FEATURE_COLUMNS)
            + [
                "source",
                "symbol",
                "side",
                "signal_i",
                "signal_time",
                "entry_price",
                "atr14",
                "atr_pct",
                "realized_ret",
                "label",
                "outcome_barrier",
                "gross_barrier",
                "net_barrier_maker",
                "net_barrier_taker",
            ]
        )
    )
    keep = [c for c in keep if c in d.columns]
    out = d[keep].sort_values("signal_time").reset_index(drop=True)
    need = list(FEATURE_COLUMNS) + ["realized_ret", "label", "signal_time"]
    out = out.dropna(subset=[c for c in need if c in out.columns])
    DST.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(DST, index=False)
    print(f"wrote {DST.relative_to(PROJECT)} rows={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

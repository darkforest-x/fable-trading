"""Unit contracts for the three-day daily-mover artifact verifier."""
from __future__ import annotations

import pandas as pd

from scripts.verify_15m_ma_launch_t3_daily_movers import (
    MoversVerificationError,
    assert_rank_contract,
    assert_signal_contract,
)


def _rankings() -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2026-08-23", periods=3, freq="D", tz="UTC"):
        for rank in range(1, 21):
            rows.append(
                {
                    "day": day,
                    "rank": rank,
                    "symbol": f"S{rank:02d}_USDT_SWAP",
                    "daily_return": (21 - rank) / 100,
                    "abs_return": (21 - rank) / 100,
                    "eligible_daily_universe": 274,
                }
            )
    return pd.DataFrame(rows)


def test_rank_contract_accepts_exact_three_top20_boards() -> None:
    frame = _rankings()
    assert_rank_contract(frame, sorted(frame["day"].unique().tolist()))


def test_rank_contract_rejects_non_top20_rank() -> None:
    frame = _rankings()
    frame.loc[0, "rank"] = 2
    try:
        assert_rank_contract(frame, sorted(frame["day"].unique().tolist()))
    except MoversVerificationError:
        return
    raise AssertionError("duplicate rank must fail closed")


def test_signal_contract_accepts_t3_geometry_and_five_bar_spacing() -> None:
    rankings = _rankings()
    day = pd.Timestamp("2026-08-23T00:00:00Z")
    signals = pd.DataFrame(
        [
            {
                "day": day,
                "rank": 1,
                "symbol": "S01_USDT_SWAP",
                "daily_return": 0.20,
                "class_id": 0,
                "class_name": "dense_long",
                "confidence": 0.40,
                "core_start_time": day + pd.Timedelta(hours=1),
                "core_end_time": day + pd.Timedelta(hours=2, minutes=15),
                "window_end_time": day + pd.Timedelta(hours=3),
                "core_start_i": 100,
                "core_end_i": 105,
                "core_length_bars": 6,
                "confirmation_bars": 3,
                "window_start_i": 94,
                "window_end_i": 108,
                "window_len": 15,
            }
        ]
    )
    signals.loc[0, "window_len"] = 14
    signals.loc[0, "window_start_i"] = 95
    summary = assert_signal_contract(
        signals,
        rankings,
        [day],
        expected_count=1,
    )
    assert summary["signals"] == 1
    assert summary["direction_aligned"] == 1

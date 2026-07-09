from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.backtest.maker_val_sim import maker_cost_for_dataset, taker_cost_for_dataset
from src.judgment.trend_filter import add_h9_flags


def test_add_h9_flags_uses_only_completed_hourly_bars() -> None:
    open_time = pd.date_range("2026-01-01", periods=320, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": open_time,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
        }
    )
    frame.loc[frame["open_time"] == open_time[247], "close"] = 1000.0
    rows = pd.DataFrame(
        {
            "source": ["okx", "okx"],
            "symbol": ["BTC_USDT", "BTC_USDT"],
            "signal_time": [open_time[243], open_time[247]],
        }
    )

    out = add_h9_flags(rows, series_frames={("okx", "BTC_USDT"): frame})

    assert bool(out.loc[0, "h1_ok"])
    assert not bool(out.loc[0, "h1_above_ma"])
    assert bool(out.loc[1, "h1_above_ma"])


def test_maker_val_sim_costs_follow_dataset_universe() -> None:
    assert maker_cost_for_dataset(Path("data/swap_replication/swap_tp5_sl2.csv")) == 0.0006
    assert taker_cost_for_dataset(Path("data/swap_replication/swap_tp5_sl2.csv")) == 0.0010
    assert maker_cost_for_dataset(Path("data/sweep_v3/judgment_v3_tp5_sl2_h72.csv")) == 0.0016
    assert taker_cost_for_dataset(Path("data/sweep_v3/judgment_v3_tp5_sl2_h72.csv")) == 0.003

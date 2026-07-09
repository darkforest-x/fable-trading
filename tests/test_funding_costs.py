from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from src.data.funding import funding_costs_for_trades, load_funding_series


def _write_funding(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "funding_time_ms,funding_rate,realized_rate",
                "1775548800000,0.0001,0.0001",
                "1775577600000,-0.0002,-0.0002",
                "1775606400000,0.0003,0.0003",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_funding_costs_sum_settlements_held_through(tmp_path: Path) -> None:
    _write_funding(tmp_path / "BTC_USDT_SWAP.csv")
    trades = pd.DataFrame(
        [
            {
                "symbol": "BTC_USDT_SWAP",
                "signal_time": pd.Timestamp("2026-04-07 07:30:00", tz="UTC"),
                "exit_offset": 33,
            }
        ]
    )

    costs = funding_costs_for_trades(trades, funding_dir=tmp_path)

    assert math.isclose(float(costs.iloc[0]), -0.0001, abs_tol=1e-12)


def test_funding_costs_mark_missing_when_trade_precedes_history(tmp_path: Path) -> None:
    _write_funding(tmp_path / "BTC_USDT_SWAP.csv")
    trades = pd.DataFrame(
        [
            {
                "symbol": "BTC_USDT_SWAP",
                "signal_time": pd.Timestamp("2026-04-06 00:00:00", tz="UTC"),
                "exit_offset": 1,
            }
        ]
    )

    costs = funding_costs_for_trades(trades, funding_dir=tmp_path)

    assert math.isnan(float(costs.iloc[0]))


def test_load_funding_series_prefers_realized_rate(tmp_path: Path) -> None:
    (tmp_path / "BTC_USDT_SWAP.csv").write_text(
        "\n".join(
            [
                "funding_time_ms,funding_rate,realized_rate",
                "1775548800000,0.0005,0.0001",
                "1775577600000,-0.0005,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    series = load_funding_series("BTC_USDT_SWAP", funding_dir=tmp_path)

    assert series is not None
    assert series.long_cost.tolist() == [0.0001, -0.0005]

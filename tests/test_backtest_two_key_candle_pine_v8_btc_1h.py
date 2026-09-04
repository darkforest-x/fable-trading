"""Unit contracts for the frozen BTCUSDT.P Pine-v8 1h replay."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from scripts import backtest_two_key_candle_pine_v8_btc_1h as replay


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=len(rows), freq="1h", tz="UTC"),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
        }
    )


def _execution_config(horizon: int = 3) -> dict:
    config = copy.deepcopy(replay.load_config())
    config["execution"]["horizon_bars"] = horizon
    return config


def test_holdout_authorization_and_frozen_execution_contract() -> None:
    config = replay.load_config()
    assert config["owner_authorization"]["verbatim"] == "批准读取 2026-05-04 之后的价格"
    assert config["owner_authorization"]["configuration_holdout_use"] == 1
    assert config["diagnostics"]["frozen_before_outcomes"] is True
    assert config["instrument"]["bar"] == "1H"
    assert config["signal"]["profile"] == "Core recall · 2–8"
    assert config["execution"]["target_r"] == 3.0
    assert config["execution"]["horizon_bars"] == 12
    assert config["execution"]["same_bar_collision"] == "conservative_sl"
    assert config["execution"]["round_trip_cost_fraction"] == 0.002
    assert replay.sha256_file(replay.PINE_PATH) == config["signal"]["pine_source_sha256"]


def test_long_path_target_stop_collision_and_timeout_rules() -> None:
    config = _execution_config()

    target = replay.resolve_path(
        _bars([(100.0, 103.1, 99.7, 102.8), (102.8, 103.0, 102.0, 102.4), (102.4, 102.8, 102.1, 102.5)]),
        signal_i=-1,
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=1.0,
        config=config,
    )
    assert target["outcome"] == "tp"
    assert target["hold_bars"] == 1
    assert np.isclose(target["gross_return"], 0.03)
    assert np.isclose(target["net_return"], 0.028)

    collision = replay.resolve_path(
        _bars([(100.0, 103.1, 98.9, 100.0), (100.0, 100.2, 99.8, 100.0), (100.0, 100.2, 99.8, 100.0)]),
        signal_i=-1,
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=1.0,
        config=config,
    )
    assert collision["outcome"] == "sl_ambiguous"
    assert collision["exit_price"] == 99.0

    stopped = replay.resolve_path(
        _bars([(100.0, 100.4, 99.2, 99.7), (99.7, 100.0, 98.9, 99.1), (99.1, 99.5, 98.8, 99.2)]),
        signal_i=-1,
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=1.0,
        config=config,
    )
    assert stopped["outcome"] == "sl"
    assert stopped["hold_bars"] == 2
    assert np.isclose(stopped["net_return"], -0.012)

    timeout = replay.resolve_path(
        _bars([(100.0, 100.5, 99.5, 100.2), (100.2, 100.7, 99.8, 100.4), (100.4, 100.8, 100.0, 100.5)]),
        signal_i=-1,
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=1.0,
        config=config,
    )
    assert timeout["outcome"] == "timeout"
    assert timeout["hold_bars"] == 3
    assert np.isclose(timeout["exit_price"], 100.5)


def test_incomplete_tail_is_unresolved_and_not_scored() -> None:
    result = replay.resolve_path(
        _bars([(100.0, 100.4, 99.6, 100.1), (100.1, 100.5, 99.8, 100.2)]),
        signal_i=-1,
        entry_i=0,
        direction=-1,
        entry_price=100.0,
        risk_price=1.0,
        config=_execution_config(horizon=3),
    )
    assert result["outcome"] == "unresolved"
    assert result["resolved"] is False
    assert result["net_return"] is None
    assert result["hold_bars"] == 2

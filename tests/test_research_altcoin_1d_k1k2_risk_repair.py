from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import research_altcoin_1d_k1k2_episode_runner as parent
from scripts import research_altcoin_1d_k1k2_risk_repair as repair

ROOT = Path(__file__).resolve().parents[1]
PARENT_CONFIG = ROOT / "experiments/active/exp-altcoin-1d-k1k2-episode-runner-preholdout-20260905-v1/config.json"
CONFIG = ROOT / "experiments/active/exp-altcoin-1d-k1k2-close-stop-bank-repair-preholdout-20260905-v2/config.json"


def _configs() -> tuple[dict, dict]:
    return (
        json.loads(PARENT_CONFIG.read_text(encoding="utf-8")),
        json.loads(CONFIG.read_text(encoding="utf-8")),
    )


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    times = pd.date_range("2025-01-01", periods=len(rows), freq="1D", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "atr": 10.0,
            "fast_ma": 100.0,
            "slow_ma": 99.0,
            "segment_id": 1,
        }
    )


def _event() -> dict:
    return {
        "setup_id": "synthetic",
        "symbol": "TEST",
        "direction": 1,
        "signal_i": -1,
        "signal_time": pd.Timestamp("2024-12-31", tz="UTC"),
        "entry_i": 0,
        "entry_time": pd.Timestamp("2025-01-01", tz="UTC"),
        "entry_price": 100.0,
        "signal_atr": 10.0,
        "k2_low": 94.0,
        "k2_high": 102.0,
        "transition_votes": 3,
        "signal_score": 2.0,
    }


def test_v2_baseline_replays_parent_v1_execution() -> None:
    parent_config, config = _configs()
    parent_config = deepcopy(parent_config)
    config = deepcopy(config)
    parent_config["execution"]["minimum_phase_remaining_bars"] = 2
    parent_config["execution"]["maximum_horizon_bars"] = 3
    config["execution"]["minimum_phase_remaining_bars"] = 2
    config["execution"]["maximum_horizon_bars"] = 3
    frame = _frame([(100, 118, 95, 117), (116, 117, 88, 90), (90, 91, 89, 90)])
    v1_params = deepcopy(parent_config["selection"]["initial"])
    v2_params = deepcopy(config["selection"]["initial"])
    phase_end = pd.Timestamp("2025-01-04", tz="UTC")

    v1 = parent.resolve_trade(frame, _event(), parent_config, v1_params, phase_end=phase_end)
    v2 = repair.resolve_trade(frame, _event(), parent_config, config, v2_params, phase_end=phase_end)

    assert v1["resolved"] and v2["resolved"]
    assert v2["risk_atr"] == v1["risk_atr"]
    assert np.isclose(v2["gross_return"], v1["gross_return"], rtol=0, atol=1e-15)
    assert np.isclose(v2["net_return"], v1["net_return"], rtol=0, atol=1e-15)


def test_close_confirmed_structure_ignores_a_wick_sweep() -> None:
    parent_config, config = _configs()
    config = deepcopy(config)
    config["execution"]["minimum_phase_remaining_bars"] = 2
    config["execution"]["maximum_horizon_bars"] = 3
    frame = _frame([(100, 105, 90, 100), (100, 116, 95, 112), (112, 115, 108, 114)])
    baseline = deepcopy(config["selection"]["initial"])
    close_policy = deepcopy(baseline)
    close_policy["stop_policy"] = "close_all_hard_2_0"
    phase_end = pd.Timestamp("2025-01-04", tz="UTC")

    stopped = repair.resolve_trade(frame, _event(), parent_config, config, baseline, phase_end=phase_end)
    survived = repair.resolve_trade(frame, _event(), parent_config, config, close_policy, phase_end=phase_end)

    assert stopped["outcome"] == "hard_disaster_stop"
    assert survived["outcome"] == "horizon_timeout"
    assert survived["net_return"] > stopped["net_return"]


def test_completed_close_structure_exit_fills_next_daily_open() -> None:
    parent_config, config = _configs()
    config = deepcopy(config)
    config["execution"]["minimum_phase_remaining_bars"] = 2
    config["execution"]["maximum_horizon_bars"] = 3
    frame = _frame([(100, 102, 90, 92), (91, 95, 88, 94), (94, 96, 93, 95)])
    params = deepcopy(config["selection"]["initial"])
    params["stop_policy"] = "close_all_hard_2_0"

    trade = repair.resolve_trade(
        frame,
        _event(),
        parent_config,
        config,
        params,
        phase_end=pd.Timestamp("2025-01-04", tz="UTC"),
    )

    assert trade["outcome"] == "structure_1close_next_open"
    assert trade["exit_price"] == 91.0
    assert trade["exit_time"] == pd.Timestamp("2025-01-02", tz="UTC")
    assert trade["hold_bars"] == 1


def test_protected_schedule_banks_thirty_percent_at_one_r() -> None:
    parent_config, config = _configs()
    config = deepcopy(config)
    config["execution"]["minimum_phase_remaining_bars"] = 2
    config["execution"]["maximum_horizon_bars"] = 2
    frame = _frame([(100, 108, 95, 105), (105, 108, 100, 106)])
    params = deepcopy(config["selection"]["initial"])
    params["bank_schedule"] = "protected_tail30"

    trade = repair.resolve_trade(
        frame,
        _event(),
        parent_config,
        config,
        params,
        phase_end=pd.Timestamp("2025-01-03", tz="UTC"),
    )

    assert trade["bank_hits"] == 1
    assert trade["banked_fraction"] == 0.30
    assert trade["remaining_fraction"] == 0.70


def test_symbol_without_a_setup_is_a_valid_zero_event_partition() -> None:
    parent_config, config = _configs()
    trades, rejected = repair._resolve_symbol(
        pd.DataFrame(),
        _frame([(100, 101, 99, 100), (100, 101, 99, 100)]),
        parent_config,
        config,
        config["selection"]["initial"],
        phase_end=pd.Timestamp("2025-01-03", tz="UTC"),
    )

    assert trades.empty
    assert rejected.empty

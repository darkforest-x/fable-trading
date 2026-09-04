from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import research_altcoin_1d_k1k2_episode_runner as research

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/active/exp-altcoin-1d-k1k2-episode-runner-preholdout-20260905-v1/config.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_daily_aggregation_keeps_only_exact_96_bar_utc_days() -> None:
    first = pd.date_range("2025-01-01", periods=96, freq="15min", tz="UTC")
    second = pd.date_range("2025-01-02", periods=96, freq="15min", tz="UTC").delete(17)
    times = first.append(second)
    raw = pd.DataFrame(
        {
            "open_time": times,
            "open": np.arange(len(times), dtype=float) + 100,
            "high": np.arange(len(times), dtype=float) + 101,
            "low": np.arange(len(times), dtype=float) + 99,
            "close": np.arange(len(times), dtype=float) + 100.5,
            "volume": 1.0,
        }
    )

    daily, quality = research.aggregate_complete_utc_days(raw)

    assert len(daily) == 1
    assert daily.loc[0, "open_time"] == pd.Timestamp("2025-01-01", tz="UTC")
    assert daily.loc[0, "source_rows"] == 96
    assert quality["discarded_wrong_count"] == 1


def test_one_neutral_episode_cannot_emit_repeated_k1_k2_pairs() -> None:
    config = _config()
    config["source_contract"]["minimum_daily_history_bars"] = 0
    params = deepcopy(config["selection"]["initial"])
    params["transition_min_votes"] = 1
    times = pd.date_range("2025-01-01", periods=12, freq="1D", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 100.0,
            "segment_id": 1,
            "atr": 1.0,
            "fast_ma": 10.0,
            "slow_ma": 10.0,
            "spread_atr": 0.0,
            "fast_slope3_atr": 0.0,
            "prior_range_median20": 0.5,
            "prior_volume_median20": 100.0,
            "neutral_complete": False,
            "ma_profile": params["ma_profile"],
        }
    )
    frame.loc[0:2, "neutral_complete"] = True
    for k1_i, k2_i in ((3, 4), (7, 8)):
        frame.loc[k1_i, ["open", "high", "low", "close", "volume"]] = [
            9.8,
            11.0,
            9.7,
            10.8,
            200.0,
        ]
        frame.loc[k2_i, ["open", "high", "low", "close"]] = [10.55, 10.9, 9.95, 10.8]
        frame.loc[k2_i, "spread_atr"] = 0.2
        frame.loc[k2_i, "fast_slope3_atr"] = 0.1

    attempts, pairs = research.build_episode_signals(frame, "TEST", config, params)

    assert len(pairs) == 1
    assert pairs.loc[0, "k1_i"] == 3
    assert pairs.loc[0, "k2_i"] == 4
    assert pairs.loc[0, "accepted_by_votes"]
    assert attempts["episode_id"].nunique() == 1


def test_trade_banks_profit_before_a_later_structural_stop() -> None:
    config = _config()
    config["execution"]["minimum_phase_remaining_bars"] = 2
    config["execution"]["maximum_horizon_bars"] = 3
    params = deepcopy(config["selection"]["initial"])
    times = pd.date_range("2025-01-01", periods=3, freq="1D", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0, 116.0, 90.0],
            "high": [118.0, 117.0, 91.0],
            "low": [95.0, 88.0, 89.0],
            "close": [117.0, 90.0, 90.0],
            "atr": [10.0, 10.0, 10.0],
            "fast_ma": [101.0, 102.0, 103.0],
            "slow_ma": [99.0, 100.0, 101.0],
            "segment_id": 1,
        }
    )
    event = {
        "setup_id": "synthetic",
        "symbol": "TEST",
        "direction": 1,
        "signal_i": -1,
        "signal_time": pd.Timestamp("2024-12-31", tz="UTC"),
        "entry_i": 0,
        "entry_time": times[0],
        "entry_price": 100.0,
        "signal_atr": 10.0,
        "k2_low": 90.0,
        "k2_high": 101.0,
        "transition_votes": 3,
        "signal_score": 2.0,
    }

    trade = research.resolve_trade(
        frame,
        event,
        config,
        params,
        phase_end=pd.Timestamp("2025-01-04", tz="UTC"),
    )

    assert trade["resolved"]
    assert trade["bank_hits"] >= 1
    assert trade["banked_gross_return"] > 0
    assert trade["outcome"] in {"slow_ma_runner_stop", "structural_stop"}


def test_control_risk_override_preserves_candidate_risk_in_atr_units() -> None:
    config = _config()
    config["execution"]["minimum_phase_remaining_bars"] = 2
    config["execution"]["maximum_horizon_bars"] = 2
    params = deepcopy(config["selection"]["initial"])
    times = pd.date_range("2025-01-01", periods=2, freq="1D", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.5, 100.5],
            "atr": [2.0, 2.0],
            "fast_ma": [100.0, 100.0],
            "slow_ma": [100.0, 100.0],
            "segment_id": 1,
        }
    )
    event = {
        "setup_id": "control",
        "symbol": "TEST",
        "direction": -1,
        "signal_i": -1,
        "signal_time": pd.Timestamp("2024-12-31", tz="UTC"),
        "entry_i": 0,
        "entry_time": times[0],
        "entry_price": 100.0,
        "signal_atr": 2.0,
        "risk_atr_override": 1.5,
        "transition_votes": np.nan,
        "signal_score": np.nan,
    }

    trade = research.resolve_trade(
        frame,
        event,
        config,
        params,
        phase_end=pd.Timestamp("2025-01-03", tz="UTC"),
    )

    assert trade["resolved"]
    assert trade["risk_atr"] == 1.5
    assert trade["initial_stop"] == 103.0

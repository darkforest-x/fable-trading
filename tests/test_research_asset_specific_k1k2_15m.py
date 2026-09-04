from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_asset_specific_k1k2_15m import (
    _attach_transition_features,
    _profile_frame,
    resolve_trade,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / (
    "experiments/active/"
    "exp-ethusdtp-15m-asset-specific-k1k2-preholdout-20260905-v19/"
    "config.json"
)


def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def raw_frame(rows: int = 220) -> pd.DataFrame:
    time = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    base = 100.0 + np.linspace(0.0, 8.0, rows) + np.sin(np.arange(rows) / 8.0)
    return pd.DataFrame(
        {
            "open_time": time,
            "open": base - 0.1,
            "high": base + 0.4,
            "low": base - 0.4,
            "close": base + 0.1,
            "volume": 1000.0 + np.arange(rows),
            "segment_id": 1,
            "atr": 1.0,
        }
    )


def test_profile_features_do_not_change_before_future_mutation() -> None:
    source = raw_frame()
    changed = source.copy()
    changed.loc[changed.index > 160, ["open", "high", "low", "close"]] *= 1.4
    changed.loc[changed.index > 160, "volume"] *= 7.0
    first = _profile_frame(source, config(), "ema30_sma60")
    second = _profile_frame(changed, config(), "ema30_sma60")
    columns = [
        "reference_ma",
        "trend_ma",
        "fast_slow_spread_atr",
        "bb_width_ratio96",
        "prior_range_median24",
    ]
    pd.testing.assert_frame_equal(first.loc[:160, columns], second.loc[:160, columns])


def test_transition_votes_use_pre_k1_k1_and_completed_k2_only() -> None:
    frame = raw_frame(130)
    frame["bb_width_ratio96"] = 1.5
    frame["prior_range_median24"] = 1.0
    frame.loc[99, "bb_width_ratio96"] = 0.8
    frame.loc[100, "high"] = 102.0
    frame.loc[100, "low"] = 100.0
    pairs = pd.DataFrame(
        [
            {
                "k1_i": 100,
                "k2_close_side_atr": 0.2,
                "k2_touch_depth_atr": 0.5,
                "k2_wick_share": 0.4,
            }
        ]
    )
    result = _attach_transition_features(pairs, frame, config())
    assert result.loc[0, "pre_k1_bb_ratio"] == 0.8
    assert result.loc[0, "k1_release_ratio"] == 2.0
    assert result.loc[0, "transition_votes"] == 3


def test_same_bar_stop_wins_before_profit_bank() -> None:
    frame = raw_frame(12)
    frame.loc[1, ["open", "high", "low", "close"]] = [100.0, 103.0, 97.0, 101.0]
    frame["trend_ma"] = 100.0
    event = {
        "setup_id": "synthetic",
        "signal_i": 0,
        "signal_time": frame.loc[0, "open_time"],
        "entry_i": 1,
        "entry_time": frame.loc[1, "open_time"],
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
        "transition_votes": 3,
        "signal_score": 1.0,
    }
    params = {
        "ma_profile": "ema30_sma60",
        "transition_min_votes": 0,
        "runner_buffer_atr": 1.0,
        "bank_total_fraction": 0.1,
    }
    result = resolve_trade(frame, event, config(), params)
    assert result["outcome"] == "hard_stop"
    assert result["partial_hits"] == 0
    assert result["exit_price"] == 98.0


def test_registered_confirmation_ends_before_holdout() -> None:
    for experiment in (
        "exp-ethusdtp-15m-asset-specific-k1k2-preholdout-20260905-v19",
        "exp-xauusdtp-15m-asset-specific-k1k2-preholdout-20260905-v1",
    ):
        path = ROOT / "experiments" / "active" / experiment / "config.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        end = pd.Timestamp(payload["splits"]["confirmation"]["end_exclusive"])
        holdout = pd.Timestamp(payload["source_contract"]["holdout_start"])
        assert end < holdout
        assert payload["source_contract"]["repository_holdout_rows_allowed"] == 0

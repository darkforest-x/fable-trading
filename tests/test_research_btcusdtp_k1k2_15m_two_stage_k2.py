from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_k1k2_15m_two_stage_k2 import (
    accept_k2_events,
    build_k2_event_candidates,
    development_passed,
)


PROJECT = Path(__file__).resolve().parents[1]
CONFIG = json.loads(
    (
        PROJECT
        / "experiments/active/exp-btcusdtp-k1k2-15m-two-stage-k2-preholdout-20260904-v1/config.json"
    ).read_text(encoding="utf-8")
)


def frame_for_delayed_short() -> pd.DataFrame:
    rows = 12
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": np.full(rows, 99.0),
            "high": np.full(rows, 99.4),
            "low": np.full(rows, 98.6),
            "close": np.full(rows, 98.9),
            "volume": np.full(rows, 100.0),
            "atr": np.full(rows, 2.0),
            "sma40_hl2": np.full(rows, 100.0),
            "ma_shift_candle_side": np.full(rows, -1),
            "segment_id": np.ones(rows, dtype=int),
        }
    )
    # K1 at 2: a large bearish body crossing SMA40.
    frame.loc[2, ["open", "high", "low", "close"]] = [100.4, 100.5, 98.3, 98.5]
    # Touch at 5: upper wick reaches above SMA but close is too weak a rejection.
    frame.loc[5, ["open", "high", "low", "close"]] = [99.3, 100.4, 98.8, 99.6]
    # Interior bar at 6 remains below SMA with aligned colour.
    frame.loc[6, ["open", "high", "low", "close"]] = [99.1, 99.5, 98.5, 98.9]
    # Confirmation at 7: no SMA touch, strong upper-wick rejection.
    frame.loc[7, ["open", "high", "low", "close"]] = [99.0, 99.8, 98.6, 98.7]
    frame.loc[8, "open"] = 98.6
    return frame


def test_delayed_confirmation_is_found_and_uses_interval_extreme() -> None:
    frame = frame_for_delayed_short()
    candidates = build_k2_event_candidates(
        frame, CONFIG, maximum_confirmation_delay_bars=2
    )
    row = candidates.loc[
        candidates["direction"].eq(-1)
        & candidates["k1_i"].eq(2)
        & candidates["touch_i"].eq(5)
    ].iloc[0]
    assert int(row["k2_i"]) == 7
    assert int(row["confirmation_delay_bars"]) == 2

    accepted = accept_k2_events(candidates, frame, CONFIG)
    event = accepted.loc[accepted["k1_i"].eq(2)].iloc[0]
    assert event["entry_i"] == 8
    assert event["entry_price"] == 98.6
    assert event["stop_price"] == 100.4
    assert np.isclose(event["risk_price"], 1.8)


def test_delay_zero_requires_touch_and_strong_rejection_on_same_bar() -> None:
    frame = frame_for_delayed_short()
    delayed = build_k2_event_candidates(
        frame, CONFIG, maximum_confirmation_delay_bars=2
    )
    same_bar = build_k2_event_candidates(
        frame, CONFIG, maximum_confirmation_delay_bars=0
    )
    key = lambda table: table[
        table["direction"].eq(-1)
        & table["k1_i"].eq(2)
        & table["touch_i"].eq(5)
    ]
    assert len(key(delayed)) == 1
    assert key(same_bar).empty


def test_earliest_qualifying_confirmation_wins() -> None:
    frame = frame_for_delayed_short()
    frame.loc[6, ["open", "high", "low", "close"]] = [99.0, 99.7, 98.7, 98.8]
    candidates = build_k2_event_candidates(
        frame, CONFIG, maximum_confirmation_delay_bars=2
    )
    row = candidates.loc[
        candidates["direction"].eq(-1)
        & candidates["k1_i"].eq(2)
        & candidates["touch_i"].eq(5)
    ].iloc[0]
    assert int(row["k2_i"]) == 6
    assert int(row["confirmation_delay_bars"]) == 1


def test_wrong_side_bar_between_touch_and_confirmation_rejects_delay_two() -> None:
    frame = frame_for_delayed_short()
    frame.loc[6, ["open", "high", "low", "close", "ma_shift_candle_side"]] = [
        100.2,
        100.6,
        99.8,
        100.3,
        1,
    ]
    candidates = build_k2_event_candidates(
        frame, CONFIG, maximum_confirmation_delay_bars=2
    )
    selected = candidates.loc[
        candidates["direction"].eq(-1)
        & candidates["k1_i"].eq(2)
        & candidates["touch_i"].eq(5)
    ]
    assert selected.empty


def test_future_mutation_does_not_change_completed_candidate() -> None:
    frame = frame_for_delayed_short()
    before = build_k2_event_candidates(
        frame, CONFIG, maximum_confirmation_delay_bars=2
    )
    mutated = frame.copy()
    mutated.loc[9:, ["open", "high", "low", "close"]] = [200.0, 250.0, 150.0, 220.0]
    after = build_k2_event_candidates(
        mutated, CONFIG, maximum_confirmation_delay_bars=2
    )
    columns = ["direction", "k1_i", "touch_i", "k2_i", "confirmation_delay_bars"]
    pd.testing.assert_frame_equal(
        before.loc[before["k2_i"].le(7), columns].reset_index(drop=True),
        after.loc[after["k2_i"].le(7), columns].reset_index(drop=True),
    )


def test_development_gate_is_fail_closed() -> None:
    baseline = {"metrics": {"robust_score_bp": -10.0}}
    candidate = {
        "metrics": {
            "eligible": True,
            "mean_net_bp": 1.0,
            "robust_score_bp": 1.0,
            "worst_fold_net_bp": 0.0,
            "matched_control_excess_bp": 1.0,
            "paired_signflip_p_one_sided": 0.02,
        },
        "folds": pd.DataFrame({"mean_net_bp": [1.0, 1.0, 1.0, 1.0]}),
    }
    passed, failures = development_passed(baseline, candidate)
    assert not passed
    assert "paired_signflip_p_not_below_0.01" in failures

from __future__ import annotations

import pandas as pd

from scripts.research_btcusdtp_k1k2_sweep_reclaim_entry import (
    is_sweep_reclaim,
    run_sweep_arm,
    select_entry,
    wait_bars,
)


def _config() -> dict:
    return {
        "timeframe_fixed": {
            "15m": {
                "minutes_per_bar": 15,
                "horizon_bars": 2,
                "cooldown_bars": 1,
            }
        },
        "execution_frozen": {
            "round_trip_cost_fraction": 0.002,
            "next_open_risk_atr_min": 0.15,
            "next_open_risk_atr_max": 2.5,
            "fee_to_risk_max": 1.25,
            "target_r": 3.0,
            "profit_protection_trigger_close_r": 1.5,
        },
    }


def _frame() -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=8, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0, 100.5, 100.4, 101.2, 101.5, 102.0, 102.5, 103.0],
            "high": [101.0, 101.5, 101.2, 102.0, 103.0, 103.0, 103.5, 104.0],
            "low": [99.0, 99.5, 99.8, 98.8, 101.0, 101.5, 102.0, 102.5],
            "close": [100.5, 100.8, 100.6, 101.5, 102.5, 102.5, 103.0, 103.5],
            "atr": [2.0] * 8,
            "ma": [100.0] * 8,
            "segment_id": [0] * 8,
        }
    )


def _candidate() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "direction": 1,
                "ma_period": 40,
                "k1_i": 1,
                "k2_i": 2,
                "gap_bars": 1,
                "secondary_score": 0.8,
            }
        ]
    )


def test_wait_bars_uses_clock_time() -> None:
    assert wait_bars(_config(), "15m", 60) == 4


def test_long_sweep_reclaim_is_directional_and_strict() -> None:
    row = _frame().loc[3]
    assert is_sweep_reclaim(row, 1, 99.8)
    changed = row.copy()
    changed["low"] = 99.8
    assert not is_sweep_reclaim(changed, 1, 99.8)


def test_sweep_entry_uses_next_open_and_original_k2_stop() -> None:
    frame = _frame()
    decisions, events = run_sweep_arm(
        _candidate(),
        frame,
        _config(),
        "15m",
        {"ma_period": 40, "score_floor": 0.4, "gap_min_bars": 1, "gap_max_bars": 4},
        {"label": "sweep_reclaim_30m", "max_wait_minutes": 30},
        pd.Timestamp("2024-01-02", tz="UTC"),
    )
    assert len(events) == 1
    assert int(events.iloc[0]["confirmation_i"]) == 3
    assert int(events.iloc[0]["entry_i"]) == 4
    assert float(events.iloc[0]["entry_price"]) == 101.5
    assert float(events.iloc[0]["stop_price"]) == 99.8
    assert decisions["decision"].eq("accepted").sum() == 1


def test_entry_decision_stops_reading_after_first_confirmation() -> None:
    frame = _frame()
    args = (
        _candidate(),
        frame,
        _config(),
        "15m",
        {"ma_period": 40, "score_floor": 0.4, "gap_min_bars": 1, "gap_max_bars": 4},
        {"label": "sweep_reclaim_30m", "max_wait_minutes": 30},
        pd.Timestamp("2024-01-02", tz="UTC"),
    )
    before, _ = run_sweep_arm(*args)
    mutated = frame.copy()
    mutated.loc[5:, ["open", "high", "low", "close", "ma"]] = 1_000_000.0
    after, _ = run_sweep_arm(args[0], mutated, *args[2:])
    cols = ["confirmation_i", "entry_i", "entry_price", "stop_price", "decision"]
    pd.testing.assert_frame_equal(before[cols], after[cols])


def test_select_entry_obeys_improvement_and_worst_fold_guard() -> None:
    baseline = {
        "entry_rule": "immediate_next_open",
        "max_wait_minutes": 0,
        "eligible": True,
        "robust_score_bp": -10.0,
        "worst_fold_net_bp": -15.0,
        "events": 300,
    }
    good = {
        "entry_rule": "sweep_reclaim_30m",
        "max_wait_minutes": 30,
        "eligible": True,
        "robust_score_bp": -7.0,
        "worst_fold_net_bp": -17.0,
        "events": 100,
    }
    bad_worst = {
        "entry_rule": "sweep_reclaim_60m",
        "max_wait_minutes": 60,
        "eligible": True,
        "robust_score_bp": 1.0,
        "worst_fold_net_bp": -19.0,
        "events": 100,
    }
    selected, reason = select_entry([baseline, good, bad_worst], baseline)
    assert selected["entry_rule"] == "sweep_reclaim_30m"
    assert reason == "move_by_preregistered_rule"

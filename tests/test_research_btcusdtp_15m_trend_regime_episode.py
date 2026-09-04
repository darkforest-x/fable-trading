from __future__ import annotations

import numpy as np
import pandas as pd

import scripts.research_btcusdtp_15m_trend_regime_episode as subject


def _config() -> dict:
    return {
        "trend_regime": {
            "neutral_abs_spread_atr_max": 0.1,
            "neutral_abs_slope_atr_per_bar_max": 0.005,
        },
        "execution": {
            "horizon_bars": 96,
            "initial_disaster_stop_atr": 2.0,
            "round_trip_cost_fraction": 0.002,
        },
    }


def _params() -> dict:
    return {
        "entry_spread_atr": 0.5,
        "entry_slope_atr_per_bar": 0.02,
        "strong_dwell_bars": 3,
        "neutral_dwell_bars": 4,
    }


def _frame(spread: list[float], slope: list[float]) -> pd.DataFrame:
    rows = len(spread)
    return pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2025-01-01", periods=rows, freq="15min", tz="UTC"
            ),
            "open": np.full(rows, 100.0),
            "high": np.full(rows, 101.0),
            "low": np.full(rows, 99.0),
            "close": np.full(rows, 100.0),
            "atr": np.full(rows, 1.0),
            "reference_ma": 100.0 + np.asarray(spread),
            "trend_ma": np.full(rows, 100.0),
            "fast_slow_spread_atr": spread,
            "fast_slope4_atr_per_bar": slope,
            "segment_id": np.ones(rows, dtype=int),
        }
    )


def _pair(signal_i: int, regime_direction: int) -> dict:
    return {
        "signal_i": signal_i,
        "signal_time": pd.Timestamp("2025-01-01", tz="UTC")
        + pd.Timedelta(minutes=15 * signal_i),
        "direction": regime_direction,
        "signal_family": "strict_k1_k2",
        "signal_score": 0.8,
        "signal_atr": 1.0,
        "signal_ma": 100.0,
        "k1_i": signal_i - 2,
        "k1_gap": 2,
    }


def test_regime_needs_strong_dwell_and_neutral_dwell_to_rearm() -> None:
    spread = [0.0] * 3 + [0.6] * 5 + [0.0] * 4 + [0.6] * 4
    slope = [0.0] * 3 + [0.03] * 5 + [0.0] * 4 + [0.03] * 4
    table = subject.build_regime_table(_frame(spread, slope), _config(), _params())

    assert table.loc[4, "regime_direction"] == 0
    assert table.loc[5, "regime_direction"] == 1
    first_id = int(table.loc[5, "regime_id"])
    assert table.loc[10, "regime_direction"] == 1
    assert table.loc[11, "regime_direction"] == 0
    assert table.loc[13, "regime_direction"] == 0
    assert table.loc[14, "regime_direction"] == 1
    assert int(table.loc[14, "regime_id"]) == first_id + 1


def test_regime_prefix_is_unchanged_when_future_bars_change() -> None:
    spread = [0.0] * 3 + [0.6] * 8 + [0.0] * 8
    slope = [0.0] * 3 + [0.03] * 8 + [0.0] * 8
    original = subject.build_regime_table(_frame(spread, slope), _config(), _params())
    changed_spread = spread.copy()
    changed_slope = slope.copy()
    changed_spread[12:] = [-0.8] * (len(spread) - 12)
    changed_slope[12:] = [-0.04] * (len(slope) - 12)
    changed = subject.build_regime_table(
        _frame(changed_spread, changed_slope), _config(), _params()
    )

    pd.testing.assert_frame_equal(original.iloc[:12], changed.iloc[:12])


def test_position_exit_does_not_emit_second_trade_in_same_regime(
    monkeypatch,
) -> None:
    spread = [0.0] * 3 + [0.6] * 12 + [0.0] * 4 + [0.6] * 8
    slope = [0.0] * 3 + [0.03] * 12 + [0.0] * 4 + [0.03] * 8
    frame = _frame(spread, slope)
    pairs = pd.DataFrame([_pair(6, 1), _pair(10, 1), _pair(22, 1)])

    def resolved(event, _frame, _config):
        return {
            **event,
            "resolved": True,
            "exit_i": int(event["signal_i"]),
            "exit_time": event["signal_time"] + pd.Timedelta(minutes=15),
            "exit_price": 100.0,
            "gross_return": 0.0,
            "net_return": -0.002,
            "risk_fraction": 0.02,
            "return_r": 0.0,
            "net_return_r": -0.1,
            "runner_armed": False,
            "hold_bars": 1,
            "horizon_mfe_atr": 0.0,
            "capture_of_horizon_mfe": np.nan,
        }

    monkeypatch.setattr(subject, "_resolve", resolved)
    events = subject.simulate_regime(pairs, frame, _config(), _params())

    assert events["signal_i"].tolist() == [6, 22]
    assert events["regime_id"].nunique() == 2

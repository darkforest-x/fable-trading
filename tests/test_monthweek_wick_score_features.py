import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monthweek_wick_score_features import (
    BASE_COLUMNS,
    FEATURES,
    OUTPUT_COLUMNS,
    HOUR_MS,
    build_feature_row,
    prepare_touch_index,
)


def _case(side=1):
    hours = np.arange(320, dtype=np.int64)
    raw = np.column_stack((
        hours * HOUR_MS,
        np.full(len(hours), 100.0),
        np.full(len(hours), 101.0),
        np.full(len(hours), 99.0),
        np.full(len(hours), 100.0),
    ))
    raw[150, 2] = 110.0
    raw[160, 3] = 90.0
    raw[199, 4] = 100.5
    window = {
        "month": "2026-01",
        "start_ms": 32 * HOUR_MS,
        "ready_ms": 200 * HOUR_MS,
        "expected_1h_bars": 168,
        "expires_ms": 1000 * HOUR_MS,
    }
    week_high, week_low = 110.0, 90.0
    rail = week_low - .618 * (week_high - week_low) if side == 1 else week_high + .618 * (week_high - week_low)
    raw[248, 1] = 100.0
    raw[248, 2] = 103.0 if side == 1 else rail + 1.0
    raw[248, 3] = rail - 0.64 if side == 1 else 98.0
    raw[248, 4] = 100.5 if side == 1 else 99.5
    raw[249, 2] = max(raw[249, 2], 103.0)
    raw[250, 2] = max(raw[250, 2], 103.0)
    raw[251, 2] = max(raw[251, 2], 103.0)
    raw[251, 4] = 101.5 if side == 1 else 99.0
    if side == 1:
        raw[248, 3] = rail - 0.64
        raw[248, 2] = 103.0
        raw[249:252, 3] = np.maximum(raw[249:252, 3], 98.0)
    else:
        raw[248, 2] = rail + 0.64
        raw[248, 3] = 98.0
        raw[249:252, 3] = np.maximum(raw[249:252, 3], 98.0)
    event = {
        "event_id": f"AAA/2026-01/{'lower' if side == 1 else 'upper'}",
        "symbol": "AAA",
        "month": "2026-01",
        "side": side,
        "first_breach_ms": 248 * HOUR_MS,
        "rail": rail,
        "week_high": week_high,
        "week_low": week_low,
        "status": "entered",
        "tf": 4,
        "bar_ms": 248 * HOUR_MS,
        "close_ms": 252 * HOUR_MS,
        # These future execution fields are deliberately irrelevant to features.
        "entry": 1e12,
        "stop": -1e12,
        "risk_frac": 1e12,
    }
    touch_rows = [
        {"symbol": "BBB", "side": side, "touch_ms": 249 * HOUR_MS},
        {"symbol": "BBB", "side": side, "touch_ms": 240 * HOUR_MS},
        {"symbol": "CCC", "side": side, "touch_ms": 226 * HOUR_MS},  # exactly 24h boundary: excluded
        {"symbol": "DDD", "side": side, "touch_ms": 250 * HOUR_MS},
        {"symbol": "FUT", "side": side, "touch_ms": 251 * HOUR_MS},  # not yet closed: excluded
        {"symbol": "AAA", "side": side, "touch_ms": 249 * HOUR_MS},  # same symbol: excluded
        {"symbol": "OPP", "side": -side, "touch_ms": 249 * HOUR_MS},
    ]
    return raw, event, window, prepare_touch_index(pd.DataFrame(touch_rows))


@pytest.mark.parametrize("side", [1, -1])
def test_features_use_selected_close_and_ignore_future_tape(side):
    raw, event, window, touch_index = _case(side)
    row = build_feature_row(event, raw, window, touch_index, stop_buffer=0.01)

    changed_future = raw.copy()
    changed_future[252:, 2] = 10_000.0
    changed_future[252:, 3] = 1.0
    changed_future[252:, 4] = 5_000.0
    future_variant = build_feature_row(event, changed_future, window, touch_index, stop_buffer=0.01)
    changed_execution = {**event, "entry": 0.0001, "stop": 9e99, "risk_frac": -9e99}
    execution_variant = build_feature_row(changed_execution, raw, window, touch_index, stop_buffer=0.01)

    for name in FEATURES:
        assert row[name] == pytest.approx(future_variant[name], nan_ok=True)
        assert row[name] == pytest.approx(execution_variant[name], nan_ok=True)
    assert row["other_side_touch_count_24h"] == 2
    assert row["week_close_position"] == pytest.approx((100.5 - 90.0) / 20.0)
    assert np.isfinite(row["log1p_wick_reclaim_ratio"])


def test_columns_are_unique_and_strict_wick_geometry_is_required():
    assert len(OUTPUT_COLUMNS) == len(set(OUTPUT_COLUMNS))
    assert set(FEATURES).issubset(OUTPUT_COLUMNS)
    assert OUTPUT_COLUMNS.count("side") == 1
    assert set(BASE_COLUMNS).issubset(OUTPUT_COLUMNS)

    raw, event, window, touch_index = _case(side=1)
    row = build_feature_row(event, raw, window, touch_index, stop_buffer=0.01)
    table = pd.DataFrame([row], columns=OUTPUT_COLUMNS)
    assert all(pd.api.types.is_numeric_dtype(table[name]) for name in FEATURES)

    invalid = raw.copy()
    invalid[248, 1] = event["rail"] - 0.1  # Open is outside the lower rail but above the wick tip.
    with pytest.raises(ValueError, match="strict outward wick"):
        build_feature_row(event, invalid, window, touch_index, stop_buffer=0.01)


def test_zero_body_has_missing_log_wick_body_ratio():
    raw, event, window, touch_index = _case(side=1)
    raw[248, 1] = 100.0
    raw[251, 4] = 100.0
    row = build_feature_row(event, raw, window, touch_index, stop_buffer=0.01)
    assert np.isnan(row["log1p_outside_wick_to_body_ratio"])

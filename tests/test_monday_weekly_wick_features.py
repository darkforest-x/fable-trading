import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import monday_weekly_wick_features as f


def _fixture(side=1, symbol="AAA"):
    week = "2026-01-05"
    anchor_start = int(pd.Timestamp(week, tz=f.NY).tz_convert("UTC").timestamp() * 1000)
    anchor_ready = anchor_start + f.DAY_MS
    base = anchor_start - 360 * f.HOUR_MS
    end = anchor_ready + 360 * f.HOUR_MS
    ts = np.arange(base, end, f.HOUR_MS, dtype=np.int64)
    raw = np.column_stack((
        ts,
        np.full(len(ts), 100.0),
        np.full(len(ts), 101.0),
        np.full(len(ts), 99.0),
        np.full(len(ts), 100.0),
    ))
    anchor = int(np.searchsorted(ts, anchor_start))
    raw[anchor, 1:5] = [100.0, 105.0, 99.0, 101.0]
    raw[anchor + 23, 1:5] = [101.0, 103.0, 95.0, 100.5]
    if side == -1:
        old = raw[:, 1:5].copy()
        raw[:, 1] = 200.0 - old[:, 0]
        raw[:, 2] = 200.0 - old[:, 2]
        raw[:, 3] = 200.0 - old[:, 1]
        raw[:, 4] = 200.0 - old[:, 3]
    week_high, week_low = 105.0, 95.0
    rail = week_low - .618 * (week_high - week_low) if side == 1 else week_high + .618 * (week_high - week_low)
    # The containing UTC 4h candle starts at 08:00Z, after Tuesday 00:00 NY.
    bar_ms = int(pd.Timestamp("2026-01-06T08:00:00Z").timestamp() * 1000)
    first_breach_ms = bar_ms + 2 * f.HOUR_MS
    i = int(np.searchsorted(ts, first_breach_ms))
    if side == 1:
        raw[i, 1:5] = [100.0, 101.0, rail - 0.5, 100.2]
    else:
        raw[i, 1:5] = [100.0, rail + 0.5, 99.0, 99.8]
    # Last H1 candle sets the selected signal close without changing the tip.
    raw[i + 1, 1:5] = [raw[i, 4], 101.0, 99.0, 100.5 if side == 1 else 99.5]
    bar_start = int(np.searchsorted(ts, bar_ms))
    bar_end = bar_start + 4
    block = raw[bar_start:bar_end]
    bar = (float(block[0, 1]), float(block[:, 2].max()),
           float(block[:, 3].min()), float(block[-1, 4]))
    event = {
        "event_id": f"{symbol}/{week}/{'lower' if side == 1 else 'upper'}",
        "symbol": symbol,
        "week": week,
        "side": side,
        "tf": 4,
        "bar_ms": bar_ms,
        "close_ms": bar_ms + 4 * f.HOUR_MS,
        "first_breach_ms": first_breach_ms,
        "anchor_start_ms": anchor_start,
        "anchor_ready_ms": anchor_ready,
        "week_high": week_high,
        "week_low": week_low,
        "rail": rail,
        "bar_open": bar[0],
        "bar_high": bar[1],
        "bar_low": bar[2],
        "bar_close": bar[3],
        "status": "entered",
        # These are deliberately post-close and must not drive feature values.
        "entry": -1e20,
        "entry_ms": event_close if (event_close := bar_ms + 4 * f.HOUR_MS) else None,
        "stop": 1e20,
        "risk_frac": 1e20,
        "outcome": "tp",
    }
    return raw, event


def _ledger(event):
    return pd.DataFrame([
        {"event_id": event["event_id"], "symbol": event["symbol"], "week": event["week"],
         "side": event["side"], "tf": event["tf"], "bar_ms": event["bar_ms"],
         "close_ms": event["close_ms"], "first_breach_ms": event["first_breach_ms"],
         "anchor_start_ms": event["anchor_start_ms"], "anchor_ready_ms": event["anchor_ready_ms"],
         "week_high": event["week_high"], "week_low": event["week_low"], "rail": event["rail"],
         "bar_open": event["bar_open"], "bar_high": event["bar_high"],
         "bar_low": event["bar_low"], "bar_close": event["bar_close"], "status": event["status"]}
    ])


@pytest.mark.parametrize("side", [1, -1])
def test_future_truncation_and_execution_fields_do_not_change_features(side):
    raw, event = _fixture(side)
    row = f.build_feature_row(event, raw)
    truncated = raw[raw[:, 0] < event["close_ms"]]
    shorter = f.build_feature_row(event, truncated)
    changed = raw.copy()
    future = changed[:, 0] >= event["close_ms"]
    changed[future, 1:5] = np.array([1_000.0, 1_500.0, 900.0, 1_200.0])
    future_variant = f.build_feature_row(event, changed)
    execution_variant = f.build_feature_row(
        {**event, "entry": -9e99, "entry_ms": event["close_ms"] + 99_000_000,
         "stop": 9e99, "risk_frac": -9e99, "outcome": "sl", "status": "censored"}, raw
    )
    for name in f.FEATURES:
        assert row[name] == pytest.approx(shorter[name], nan_ok=True)
        assert row[name] == pytest.approx(future_variant[name], nan_ok=True)
        assert row[name] == pytest.approx(execution_variant[name], nan_ok=True)
    assert np.isfinite(row["side_return_24h"])
    assert np.isfinite(row["tr_pct_24h"])


def test_long_short_reflection_preserves_oriented_wick_geometry():
    long_tape, long_event = _fixture(1, "AAA")
    short_tape, short_event = _fixture(-1, "BBB")
    long = f.build_feature_row(long_event, long_tape)
    short = f.build_feature_row(short_event, short_tape)
    for name in ("monday_side_return", "monday_efficiency", "wick_depth_bar_frac",
                 "log1p_wick_reclaim_ratio", "body_frac_range", "signal_close_position_side",
                 "signal_range_week_frac"):
        assert long[name] == pytest.approx(short[name])
    assert long["week_close_position"] + short["week_close_position"] == pytest.approx(1.0)


def test_btc_features_are_asof_signal_close_and_breadth_stops_one_hour_early():
    own, event = _fixture(1)
    btc, _ = _fixture(1, "BTCUSDT")
    btc[:, 4] = np.linspace(90.0, 110.0, len(btc))
    btc[:, 1] = btc[:, 4]
    btc[:, 2] = btc[:, 4] + 0.5
    btc[:, 3] = btc[:, 4] - 0.5
    # Keep the BTC Monday anchor compatible with the base candle properties.
    anchor = int(np.searchsorted(btc[:, 0], event["anchor_start_ms"]))
    btc[anchor, 1:5] = [100.0, 105.0, 99.0, 101.0]
    btc[anchor + 23, 1:5] = [101.0, 103.0, 95.0, 100.5]
    close_ms = event["close_ms"]
    changed_btc = btc.copy()
    future = changed_btc[:, 0] >= close_ms
    changed_btc[future, 1:5] = np.array([1_000.0, 1_500.0, 900.0, 1_200.0])
    row = f.build_feature_row(event, own, btc_tape=btc)
    changed = f.build_feature_row(event, own, btc_tape=changed_btc)
    for name in ("btc_side_return_24h", "btc_side_return_168h", "btc_tr_pct_24h"):
        assert row[name] == pytest.approx(changed[name], nan_ok=True)

    ledger = _ledger(event)
    rows = [
        ("BBB", 1, event["first_breach_ms"] - 2 * f.HOUR_MS),  # known before end: include
        ("CCC", 1, event["first_breach_ms"] - f.HOUR_MS),      # known at close_ms - 1h: include
        ("DDD", 1, close_ms - 2 * f.HOUR_MS),                   # known at close_ms - 1h: include
        ("EEE", 1, close_ms - f.HOUR_MS),                       # known at signal close: exclude
        ("AAA", 1, event["first_breach_ms"] - 2 * f.HOUR_MS),  # same symbol: exclude
        ("FFF", -1, event["first_breach_ms"] - 2 * f.HOUR_MS), # other side: exclude
        ("GGG", 1, event["first_breach_ms"] - 2 * f.HOUR_MS),  # other NY week: exclude
    ]
    extra = []
    for symbol, side, touch in rows:
        extra.append({"event_id": f"{symbol}/{event['week']}/{side}", "symbol": symbol,
                      "week": "2026-01-12" if symbol == "GGG" else event["week"],
                      "side": side, "first_breach_ms": touch})
    all_breaches = pd.concat([ledger, pd.DataFrame(extra)], ignore_index=True, sort=False)
    breadth = f.prepare_breadth_index(all_breaches)
    counted = f.build_feature_row(event, own, breadth_index=breadth)
    assert counted["other_side_touch_count_24h"] == 3


def test_gaps_preserve_nan_and_groups_are_stable():
    raw, event = _fixture()
    row = f.build_feature_row(event, raw)
    gap_ts = event["close_ms"] - 100 * f.HOUR_MS
    gap = raw[raw[:, 0] != gap_ts]
    gapped = f.build_feature_row(event, gap)
    assert np.isnan(gapped["side_return_168h"])
    assert np.isnan(gapped["tr_pct_168h"])
    assert np.isfinite(gapped["side_return_24h"])
    assert np.isfinite(gapped["side_return_6h"])
    assert len(f.CORE_FEATURES) < len(f.FEATURES) <= 40
    assert len(f.FEATURES) == len(set(f.FEATURES))
    assert set(f.CORE_FEATURES).issubset(f.FEATURES)
    assert np.isnan(row["other_side_touch_count_24h"])


def test_datetime_dataframe_tape_uses_millisecond_timestamps():
    raw, event = _fixture()
    frame = pd.DataFrame(raw, columns=["open_ms", "open", "high", "low", "close"])
    frame["open_ms"] = pd.to_datetime(frame.open_ms.astype("int64"), unit="ms", utc=True)
    values, ts = f._as_hourly(frame, event["close_ms"])
    assert ts[-1] == event["close_ms"] - f.HOUR_MS
    assert values[-1, 0] == ts[-1]

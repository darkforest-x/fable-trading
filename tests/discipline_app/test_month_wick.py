"""Month-first-week touch anchors are validated without exchange/account access."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from yoyo.discipline_app.monitor_feed import MonitorUnavailable
from yoyo.discipline_app.month_wick import month_wick_anchor
from yoyo.monitor.month_first_week_1618 import HOUR_MS, MINUTE_MS, FRESH_MS, month_window


NOW = int(datetime(2026, 11, 11, 12, 34, 20, tzinfo=timezone.utc).timestamp() * 1000)
SYMBOL = "BTC-USDT-SWAP"


def snapshot(*, side="upper", source="live", candle_ms=None, now_ms=NOW):
    window = month_window(now_ms)
    candle_ms = candle_ms if candle_ms is not None else now_ms // MINUTE_MS * MINUTE_MS
    edge_level = 110.0 if side == "upper" else 80.0
    occurred_ms = candle_ms + 10_000 if source == "live" else candle_ms
    event_id = f"{window['month']}|{SYMBOL}|{side}"
    event = {
        "id": 1,
        "event_id": event_id,
        "type": "month_first_week_touch",
        "month": window["month"],
        "symbol": SYMBOL,
        "side": side,
        "level": edge_level,
        "price": edge_level,
        "candle_extreme": edge_level + 1 if side == "upper" else edge_level - 1,
        "occurred_ms": occurred_ms,
        "candle_ms": candle_ms,
        "created_ms": now_ms,
        "expires_ms": window["expires_ms"],
        "fresh_until_ms": occurred_ms + FRESH_MS,
        "source": source,
        "touch_confirmed": True,
        "candle_confirmed": source == "history",
    }
    item = {
        "symbol": SYMBOL,
        "state": "ready",
        "upper": 110.0 if side == "upper" else 120.0,
        "lower": 80.0 if side == "lower" else 90.0,
        "coverage_complete": True,
        "history_gaps": 0,
        "touches": {side: event},
    }
    return {
        "schema": "month_first_week_1618.v1",
        "now_ms": now_ms,
        "timezone": "America/New_York",
        "month": window["month"],
        "window": {key: window[key] for key in
                   ("month", "first_monday", "start_ms", "ready_ms", "expires_ms", "complete")},
        "coverage": {"source": "fixture"},
        "stream": {"connected": True, "last_message_ms": now_ms},
        "worker": {"alive": True},
        "items": [item],
        "touches": [event],
    }


class Monitor:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def month_first_week_1618(self):
        self.calls += 1
        return self.value


def test_live_touch_resolves_exact_minute_and_trade_side_maps_to_edge():
    upper_ms = NOW // MINUTE_MS * MINUTE_MS - MINUTE_MS
    monitor = Monitor(snapshot(side="upper", source="live", candle_ms=upper_ms))
    assert month_wick_anchor(monitor, SYMBOL, "short", NOW) == (
        "2026-11", 110.0, upper_ms, upper_ms + MINUTE_MS)
    assert monitor.calls == 1

    lower_ms = upper_ms - MINUTE_MS
    monitor = Monitor(snapshot(side="lower", source="live", candle_ms=lower_ms))
    assert month_wick_anchor(monitor, SYMBOL, "long", NOW) == (
        "2026-11", 80.0, lower_ms, lower_ms + MINUTE_MS)


def test_closed_h1_history_returns_only_that_hour_for_minute_recovery():
    hour_ms = NOW // HOUR_MS * HOUR_MS - 2 * HOUR_MS
    monitor = Monitor(snapshot(side="upper", source="history", candle_ms=hour_ms))
    assert month_wick_anchor(monitor, SYMBOL, "short", NOW) == (
        "2026-11", 110.0, hour_ms, hour_ms + HOUR_MS)


def test_history_minute_recovery_keeps_its_exact_minute_interval():
    minute_ms = NOW // MINUTE_MS * MINUTE_MS - 17 * MINUTE_MS
    assert minute_ms % HOUR_MS != 0
    monitor = Monitor(snapshot(side="lower", source="history", candle_ms=minute_ms))
    assert month_wick_anchor(monitor, SYMBOL, "long", NOW) == (
        "2026-11", 80.0, minute_ms, minute_ms + MINUTE_MS)


def test_history_minute_at_hour_boundary_uses_created_ms_to_avoid_h1_widening():
    hour_ms = NOW // HOUR_MS * HOUR_MS
    monitor = Monitor(snapshot(side="upper", source="history", candle_ms=hour_ms))
    assert month_wick_anchor(monitor, SYMBOL, "short", NOW) == (
        "2026-11", 110.0, hour_ms, hour_ms + MINUTE_MS)


def test_minute_touch_is_clipped_at_month_expiry():
    initial = snapshot(side="upper", source="history")
    expiry = initial["window"]["expires_ms"]
    near_expiry = expiry - 1_000
    minute_ms = expiry - MINUTE_MS
    monitor = Monitor(snapshot(side="upper", source="live", candle_ms=minute_ms, now_ms=near_expiry))
    assert month_wick_anchor(monitor, SYMBOL, "short", near_expiry) == (
        "2026-11", 110.0, minute_ms, expiry)


def test_snapshot_clock_a_few_ms_after_request_is_allowed_and_returns_forming_minute():
    # The monitor samples its snapshot after the request caller captured now_ms.
    raw = snapshot(now_ms=NOW + 25)
    start = NOW // MINUTE_MS * MINUTE_MS
    assert month_wick_anchor(Monitor(raw), SYMBOL, "short", NOW) == (
        "2026-11", 110.0, start, start + MINUTE_MS)

    closed_ms = NOW // MINUTE_MS * MINUTE_MS - MINUTE_MS
    raw = snapshot(candle_ms=closed_ms, now_ms=NOW + 25)
    assert month_wick_anchor(Monitor(raw), SYMBOL, "short", NOW) == (
        "2026-11", 110.0, closed_ms, closed_ms + MINUTE_MS)


def test_persisted_event_survives_anchor_recovery_and_needs_no_push_payload_fields():
    hour_ms = NOW // HOUR_MS * HOUR_MS - 2 * HOUR_MS
    raw = snapshot(side="upper", source="history", candle_ms=hour_ms)
    row, event = raw["items"][0], raw["touches"][0]
    row.update(state="recovering", coverage_complete=False, history_gaps=3,
               upper=None, lower=None)
    for key in ("fresh_until_ms", "price", "candle_extreme"):
        event.pop(key)
    assert month_wick_anchor(Monitor(raw), SYMBOL, "short", NOW) == (
        "2026-11", 110.0, hour_ms, hour_ms + HOUR_MS)


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(schema="other"),
    lambda s: s.update(timezone="UTC"),
    lambda s: s.update(month="2026-10"),
    lambda s: s["window"].update(ready_ms=s["window"]["ready_ms"] + 1),
    lambda s: s.update(now_ms=NOW - 60_001),
    lambda s: s["touches"][0].update(source="unknown"),
    lambda s: s["touches"][0].update(touch_confirmed=False),
    lambda s: s["touches"][0].update(event_id="2026-11|ETH-USDT-SWAP|upper"),
    lambda s: s["touches"][0].update(level=0),
    lambda s: s["touches"][0].update(expires_ms=s["window"]["expires_ms"] - MINUTE_MS),
])
def test_missing_stale_or_contradictory_snapshot_fails_closed(mutate):
    raw = snapshot()
    mutate(raw)
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(raw), SYMBOL, "short", NOW)


def test_absent_or_duplicate_period_event_fails_closed():
    raw = snapshot()
    raw["touches"] = []
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(raw), SYMBOL, "short", NOW)

    raw = snapshot()
    raw["touches"].append(deepcopy(raw["touches"][0]))
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(raw), SYMBOL, "short", NOW)


def test_wrong_symbol_side_pre_ready_and_expired_month_fail_closed():
    raw = snapshot()
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(raw), "ETH-USDT-SWAP", "short", NOW)
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(raw), SYMBOL, "upper", NOW)

    before_ready = month_window(NOW)["ready_ms"] - 1
    pre_ready = snapshot(now_ms=before_ready)
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(pre_ready), SYMBOL, "short", before_ready)

    expiry = month_window(NOW)["expires_ms"]
    with pytest.raises(MonitorUnavailable):
        month_wick_anchor(Monitor(snapshot(now_ms=expiry - 1)), SYMBOL, "short", expiry)

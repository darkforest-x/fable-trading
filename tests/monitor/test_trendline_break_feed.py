from __future__ import annotations

import sqlite3
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor.trendline_break_feed import (
    BAR_MS, TrendlineBreakFeed, install, verify_event_validity,
)


ASOF_MS = 1_800_000_000_000  # aligned to both the 1H and 4H UTC bar clocks
SYMBOL = "TEST-USDT-SWAP"


def _raw_bar(time_ms, *, confirm="1"):
    return [str(time_ms), "100", "101", "99", "100", "1", "1", "100", confirm]


class CandleClient:
    def __init__(self, series=None, *, clock=None):
        self.series = series or {}
        self._clock = clock if clock is not None else ASOF_MS + 5_000
        self.calls = []

    def clock(self):
        return self._clock

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        symbol, timeframe = params["instId"], params["bar"]
        step = BAR_MS[timeframe]
        after = int(params["after"]) if "after" in params else None
        values = self.series.get((symbol, timeframe), [])
        if after is not None:
            values = [row for row in values if int(row[0]) < after]
        return list(reversed(values[-int(params.get("limit", 300)):]))


def _history(asof_ms=ASOF_MS, *, bars_4h=3000, bars_1h=2000, gap_1h_at=None, open_candles=False):
    result = {}
    for timeframe, count in (("4H", bars_4h), ("1H", bars_1h)):
        step = BAR_MS[timeframe]
        first = asof_ms - count * step
        rows = [_raw_bar(first + index * step) for index in range(count)]
        if timeframe == "1H" and gap_1h_at is not None:
            rows = [row for index, row in enumerate(rows) if index != gap_1h_at]
        if open_candles:
            rows.append(_raw_bar(asof_ms, confirm="0"))
        result[(SYMBOL, timeframe)] = rows
    return result


def _event(event_id, symbol, confirmed_ms):
    candle_ms = confirmed_ms - BAR_MS["1H"]
    return {"event_id": event_id, "symbol": symbol, "confirmed_ms": confirmed_ms,
            "candle_ms": candle_ms, "level": 101.0, "close": 102.0,
            "a_ms": candle_ms - BAR_MS["4H"], "a_price": 110.0,
            "b_ms": candle_ms - BAR_MS["1H"], "b_price": 105.0}


def _events(symbol, four_hour, hourly, asof_ms):
    assert four_hour and hourly
    assert four_hour[-1]["time"] == asof_ms - BAR_MS["4H"]
    assert hourly[-1]["time"] == asof_ms - BAR_MS["1H"]
    return [_event("newest", symbol, asof_ms - BAR_MS["1H"]),
            _event("second", symbol, asof_ms - 3 * BAR_MS["1H"]),
            _event("expired", symbol, asof_ms - 31 * 24 * 60 * 60_000)]


def _feed(tmp_path, client, *, events=_events):
    return TrendlineBreakFeed(tmp_path, client=client, symbols_source=lambda: [SYMBOL],
                              event_fn=events, seed_root=tmp_path / "no-4h-seed",
                              h1_seed_root=tmp_path / "no-1h-seed")


def _validity_event(candle_ms):
    return {"event_id": "break", "symbol": SYMBOL, "confirmed_ms": candle_ms + BAR_MS["1H"],
            "candle_ms": candle_ms, "level": 101.0, "close": 102.0,
            "a_ms": candle_ms - 20 * BAR_MS["1H"], "a_price": 110.0,
            "b_ms": candle_ms - 10 * BAR_MS["1H"], "b_price": 105.0}


def _validity_bars(candle_ms, asof_ms, *, lows=None, missing=(), open_low=None):
    step = BAR_MS["1H"]
    start_ms = candle_ms - 10 * step
    latest = (asof_ms // step) * step - step
    missing = set(missing)
    rows = []
    for timestamp in range(start_ms, latest + step, step):
        if timestamp in missing:
            continue
        low = (lows or {}).get(timestamp, 100.0)
        rows.append({"t": timestamp, "o": 100.5, "h": 101.0, "l": low, "c": 100.5})
    if open_low is not None:
        rows.append({"t": (asof_ms // step) * step, "o": 100.5, "h": 101.0,
                     "l": open_low, "c": 100.5})
    return rows


def test_hourly_scan_uses_confirmed_paged_ohlc_and_persists_recent_events(tmp_path):
    client = CandleClient(_history(open_candles=True))
    feed = _feed(tmp_path, client)

    snapshot = feed.run_once(asof_ms=ASOF_MS)

    assert snapshot["state"] == "ready"
    assert snapshot["progress"]["done"] == snapshot["progress"]["total"] == 1
    assert snapshot["coverage"]["covered"] == 1
    assert snapshot["coverage"]["h1_history_bars"] == 2000
    assert snapshot["coverage"]["h4_history_bars"] == 3000
    assert [item["event_id"] for item in snapshot["items"]] == ["newest", "second"]
    assert all(item["validity_status"] == "valid" for item in snapshot["items"])
    assert all(item["prebreak_low_bars"] == 11 for item in snapshot["items"])
    assert len([call for call in client.calls if call[1]["bar"] == "1H"]) == 7
    assert all(call[0] == "/api/v5/market/history-candles" for call in client.calls)

    feed.close()
    restored = TrendlineBreakFeed(tmp_path, client=client, symbols_source=lambda: [SYMBOL], event_fn=_events)
    cached = restored.snapshot()
    assert cached["state"] == "warming"
    assert cached["asof_ms"] == ASOF_MS
    assert [item["event_id"] for item in cached["items"]] == ["newest", "second"]
    restored.close()


def test_gap_and_short_history_are_reported_as_partial(tmp_path):
    client = CandleClient(_history(bars_1h=1800, gap_1h_at=300))
    feed = _feed(tmp_path, client)

    snapshot = feed.run_once(asof_ms=ASOF_MS)

    assert snapshot["state"] == "partial"
    assert snapshot["coverage"]["gap_symbols"] == 1
    assert snapshot["coverage"]["insufficient_history"] == 1
    assert snapshot["coverage"]["short_history_symbols"] == [SYMBOL]
    feed.close()


def test_short_history_without_gaps_cannot_report_ready(tmp_path):
    client = CandleClient(_history(bars_1h=1800))
    feed = _feed(tmp_path, client)

    snapshot = feed.run_once(asof_ms=ASOF_MS)

    assert snapshot["coverage"]["gap_symbols"] == 0
    assert snapshot["coverage"]["insufficient_history"] == 1
    assert snapshot["state"] == "partial"
    feed.close()


def test_native_4h_and_1h_seed_files_avoid_historical_rest_pages(tmp_path):
    h4_root, h1_root = tmp_path / "seed-4h", tmp_path / "seed-1h"
    h4_root.mkdir()
    h1_root.mkdir()
    for timeframe, count, directory, name in (
            ("4H", 3000, h4_root, "okx_TEST_USDT_SWAP_4H_3000.csv"),
            ("1H", 2000, h1_root, "okx_TEST_USDT_SWAP_1H.csv")):
        step = BAR_MS[timeframe]
        with (directory / name).open("w", encoding="utf-8") as handle:
            handle.write("ts,open,high,low,close\n")
            first = ASOF_MS - count * step
            for index in range(count):
                stamp = first + index * step
                handle.write(f"{stamp},100,101,99,100\n")

    client = CandleClient()
    feed = TrendlineBreakFeed(tmp_path, client=client, symbols_source=lambda: [SYMBOL], event_fn=_events,
                              seed_root=h4_root, h1_seed_root=h1_root)
    snapshot = feed.run_once(asof_ms=ASOF_MS)

    assert snapshot["state"] == "ready"
    assert snapshot["coverage"]["seed_symbols"] == 1
    assert snapshot["coverage"]["seed_1h_symbols"] == 1
    assert client.calls == []
    feed.close()


def test_empty_market_data_is_degraded_and_visible(tmp_path):
    client = CandleClient()
    feed = _feed(tmp_path, client)

    snapshot = feed.run_once(asof_ms=ASOF_MS)

    assert snapshot["state"] == "degraded"
    assert snapshot["coverage"]["no_data"] == 1
    assert snapshot["coverage"]["missing_4h"] == 1
    assert snapshot["coverage"]["issues"] == [
        {"symbol": SYMBOL, "stage": "evaluate", "code": "no_confirmed_4h_data"}
    ]
    feed.close()


def test_read_only_api_returns_all_by_default_and_supports_full_pagination(tmp_path):
    client = CandleClient(_history())
    feed = _feed(tmp_path, client)
    feed.run_once(asof_ms=ASOF_MS)
    call_count = len(client.calls)

    app = FastAPI()
    install(app, feed)
    http = TestClient(app)
    full = http.get("/api/trendline-breaks")
    assert full.status_code == 200
    assert [item["event_id"] for item in full.json()["items"]] == ["newest", "second"]
    assert full.json()["total"] == 2

    page = http.get("/api/trendline-breaks", params={"limit": 1, "offset": 1})
    assert page.status_code == 200
    assert [item["event_id"] for item in page.json()["items"]] == ["second"]
    assert page.json()["next_offset"] is None
    assert not page.json()["has_more"]
    assert len(client.calls) == call_count  # HTTP did not fetch or recompute

    invalid = http.get("/api/trendline-breaks", params={"limit": 10001})
    assert invalid.status_code == 422
    feed.close()


def test_validity_requires_complete_history_and_strictly_lower_closed_hourly_low():
    step = BAR_MS["1H"]
    candle_ms = ASOF_MS - 4 * step
    asof_ms = ASOF_MS
    event = _validity_event(candle_ms)
    equal_and_open_lower = _validity_bars(candle_ms, asof_ms,
                                          lows={candle_ms + step: 100.0}, open_low=90.0)

    valid = verify_event_validity(event, equal_and_open_lower, asof_ms)
    invalid = verify_event_validity(
        event, _validity_bars(candle_ms, asof_ms, lows={candle_ms + step: 99.999}), asof_ms)

    assert valid["validity_status"] == "valid"
    assert valid["prebreak_low"] == 100.0
    assert valid["prebreak_low_bars"] == 11
    assert "invalidated_ms" not in valid
    assert invalid["validity_status"] == "invalid"
    assert invalid["invalidated_ms"] == candle_ms + 2 * step
    assert invalid["invalidated_low"] == 99.999


def test_validity_is_unverified_for_missing_prebreak_or_postbreak_data():
    step = BAR_MS["1H"]
    candle_ms = ASOF_MS - 4 * step
    event = _validity_event(candle_ms)
    missing_prebreak = verify_event_validity(
        event, _validity_bars(candle_ms, ASOF_MS, missing={candle_ms - 5 * step}), ASOF_MS)
    missing_postbreak = verify_event_validity(
        event, _validity_bars(candle_ms, ASOF_MS, missing={candle_ms + 2 * step}), ASOF_MS)

    assert missing_prebreak["validity_status"] == "unverified"
    assert missing_prebreak["validity_reason"] == "insufficient_prebreak_history"
    assert missing_postbreak["validity_status"] == "unverified"
    assert missing_postbreak["validity_reason"] == "gap_after_breakout"


def test_legacy_and_invalid_cached_events_are_never_exposed_after_restart(tmp_path):
    step = BAR_MS["1H"]
    legacy = _event("legacy", SYMBOL, ASOF_MS - step)
    legacy_feed = _feed(tmp_path, CandleClient())
    legacy_feed._persist_symbol(SYMBOL, [legacy], {"h1_latest_ms": ASOF_MS - step})
    legacy_feed.close()

    legacy_restored = _feed(tmp_path, CandleClient())
    assert legacy_restored.snapshot()["items"] == []
    legacy_restored.close()

    candle_ms = ASOF_MS - 4 * step
    invalid = verify_event_validity(
        _validity_event(candle_ms),
        _validity_bars(candle_ms, ASOF_MS, lows={candle_ms + step: 99.0}), ASOF_MS)
    invalid_feed = _feed(tmp_path, CandleClient())
    invalid_feed._persist_symbol(SYMBOL, [invalid], {"h1_latest_ms": ASOF_MS - step})
    invalid_feed.close()

    invalid_restored = _feed(tmp_path, CandleClient())
    assert invalid_restored.snapshot()["items"] == []
    invalid_restored.close()

    with sqlite3.connect(tmp_path / "trendline-break-feed.sqlite3") as connection:
        payloads = [row[0] for row in connection.execute("SELECT payload FROM events")]
    assert len(payloads) == 1
    assert '"validity_status":"invalid"' in payloads[0]


def test_saved_valid_event_is_visible_only_for_its_verified_hour_without_database_writes(tmp_path):
    candle_ms = ASOF_MS - 4 * BAR_MS["1H"]
    event = verify_event_validity(_validity_event(candle_ms), _validity_bars(candle_ms, ASOF_MS), ASOF_MS)
    client = CandleClient(clock=ASOF_MS + 5_000)
    feed = _feed(tmp_path, client)
    feed._persist_symbol(SYMBOL, [event], {"h1_latest_ms": ASOF_MS - BAR_MS["1H"]})
    feed.close()

    restored = _feed(tmp_path, client)
    assert [item["event_id"] for item in restored.snapshot()["items"]] == ["break"]
    client._clock = ASOF_MS + BAR_MS["1H"] + 5_000
    assert restored.snapshot()["items"] == []
    restored.close()

    with sqlite3.connect(tmp_path / "trendline-break-feed.sqlite3") as connection:
        payload = connection.execute("SELECT payload FROM events").fetchone()[0]
    assert '"validity_status":"valid"' in payload


def test_waiting_for_universe_keeps_currently_verified_cache_read_only(tmp_path):
    candle_ms = ASOF_MS - 4 * BAR_MS["1H"]
    event = verify_event_validity(_validity_event(candle_ms), _validity_bars(candle_ms, ASOF_MS), ASOF_MS)
    client = CandleClient(clock=ASOF_MS + 5_000)
    feed = _feed(tmp_path, client)
    feed._persist_symbol(SYMBOL, [event], {"h1_latest_ms": ASOF_MS - BAR_MS["1H"]})
    feed.close()
    feed = _feed(tmp_path, client)
    before = feed._db().execute("SELECT payload FROM events").fetchone()[0]
    feed.symbols_source = lambda: []

    snapshot = feed.run_once(asof_ms=ASOF_MS)

    after = feed._db().execute("SELECT payload FROM events").fetchone()[0]
    assert snapshot["progress"]["stage"] == "waiting_for_universe"
    assert [item["event_id"] for item in snapshot["items"]] == ["break"]
    assert before == after
    feed.close()


def test_prior_invalid_event_is_retained_and_cannot_be_revived(tmp_path):
    candle_ms = ASOF_MS - 4 * BAR_MS["1H"]
    invalid = verify_event_validity(
        _validity_event(candle_ms),
        _validity_bars(candle_ms, ASOF_MS, lows={candle_ms + BAR_MS["1H"]: 99.0}), ASOF_MS)
    feed = _feed(tmp_path, CandleClient())
    feed._events_by_symbol[SYMBOL] = [invalid]
    revived = dict(invalid, validity_status="valid", validity_reason="validated",
                   invalidated_ms=None, invalidated_low=None)
    retained = feed._merge_event_history(SYMBOL, [revived])
    still_retained = feed._merge_event_history(SYMBOL, [])

    assert retained[0]["validity_status"] == "invalid"
    assert retained[0]["invalidated_low"] == 99.0
    assert still_retained[0]["validity_status"] == "invalid"
    feed.close()


def test_worker_waits_for_the_next_hour_but_retries_missing_universe_soon(tmp_path):
    client = CandleClient(clock=ASOF_MS + 5_000)
    feed = _feed(tmp_path, client)
    waits = []

    def finish_wait(seconds):
        waits.append(seconds)
        feed.stop.set()
        return True

    feed.run_once = lambda: {"state": "partial", "progress": {"stage": "partial"}}
    feed.stop.wait = finish_wait
    feed._run()
    assert len(waits) == 1
    assert abs(waits[0] - (BAR_MS["1H"] - 3_000) / 1000) < 0.001

    feed.stop.clear()
    waits[:] = []
    feed.run_once = lambda: {"state": "warming", "progress": {"stage": "waiting_for_universe"}}
    feed.stop.wait = finish_wait
    feed._run()
    assert waits == [15.0]
    feed.close()


@pytest.mark.parametrize(("first", "second", "reason"), [
    ("target", "low", "rally_target_reached"),
    ("low", "target", "postbreak_low_broken"),
    ("gap", "target", "gap_after_breakout"),
    ("target", "gap", "rally_target_reached"),
    ("neither", "neither", "validated"),
])
def test_first_touch_after_confirmation_is_decisive(first, second, reason):
    step = BAR_MS["1H"]
    start = ASOF_MS - 3 * step
    event = dict(_validity_event(start), close=100)
    bars = _validity_bars(start, ASOF_MS)
    # A high in the signal bar is outside the post-confirmation window.
    bars[-3].update(h=180, c=100)
    for index, kind in reversed(list(enumerate((first, second), len(bars)-2))):
        if kind == "target":
            bars[index]["h"] = 110
        if kind == "low":
            bars[index]["l"] = 99
        if kind == "gap":
            del bars[index]
    result = verify_event_validity(event, bars, ASOF_MS)
    assert result["rally_target"] == 110
    assert result["validity_reason"] == reason


def test_recompute_legacy_invalid_without_reemission_and_keep_lock_on_restart(tmp_path):
    step = BAR_MS["1H"]
    start = ASOF_MS - 4 * step
    feed = _feed(tmp_path, CandleClient())
    old = dict(_validity_event(start), validity_schema=1, validity_status="invalid",
               validity_reason="postbreak_low_broken", invalidated_ms=ASOF_MS, invalidated_low=90)
    feed._events_by_symbol[SYMBOL] = [old]
    bars = _validity_bars(start, ASOF_MS, lows={start+2*step: 90})
    bars[-3]["h"] = 112.2  # exactly 102 * 1.10
    events = feed._normalize_events(SYMBOL, [], bars, ASOF_MS, ASOF_MS)
    locked, = feed._merge_event_history(SYMBOL, events)
    assert locked["rally_confirmed_ms"] == start+2*step
    assert "invalidated_ms" not in locked
    feed._persist_symbol(SYMBOL, [locked], {})
    feed.close()

    restored = _feed(tmp_path, CandleClient(clock=ASOF_MS+step))
    assert restored.snapshot()["items"] == [locked]
    assert restored._merge_event_history(SYMBOL, []) == [locked]
    restored.client._clock = ASOF_MS + 30*24*step
    assert restored.snapshot()["items"] == []
    restored.close()


@pytest.mark.parametrize("mode,expected", [
    ("target_first", "rally_target_reached"), ("floor_first", "postbreak_low_broken"),
    ("same_minute", "rally_target_reached"), ("missing_minutes", "intrabar_order_pending"),
])
def test_double_touch_resolves_with_confirmed_minutes_then_trades(tmp_path, mode, expected):
    step = BAR_MS["1H"]
    start = ASOF_MS - 2*step
    stamp = start+step
    rows = _validity_bars(start, ASOF_MS)
    rows[-1].update(h=120, l=90)
    minutes = [[str(stamp+i*60_000), "100.5", "101", "100", "100.5", "1", "1", "1", "1"]
               for i in range(60)]
    minutes[0 if mode in ("target_first", "same_minute") else 1][2] = "120"
    minutes[1 if mode == "target_first" else 0][3] = "90"
    feed = _feed(tmp_path, CandleClient())
    def read(path, params):
        if path.endswith("history-trades"):
            assert mode == "same_minute"
            return [{"instId": SYMBOL, "ts": str(stamp+1000), "tradeId": str(i+1), "px": price}
                    for i, price in reversed(list(enumerate(("100.5", "120", "90", "100.5"))))]
        assert params["bar"] == "1m" and int(params["after"]) == ASOF_MS
        return minutes[:-1] if mode == "missing_minutes" else list(reversed(minutes))
    feed.client.get = read
    event = verify_event_validity(_validity_event(start), rows, ASOF_MS, resolve_hour=feed._resolve_hour)
    assert event["validity_reason"] == expected
    if mode == "missing_minutes":
        assert event["validity_status"] == "valid" and "rally_confirmed_ms" not in event
    feed.close()


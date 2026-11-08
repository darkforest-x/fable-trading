from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import breadth_ema20_feed as feed_module
from yoyo.monitor.breadth_ema20_feed import (
    BAR_MS,
    DAY_MS,
    FOUR_HOURS_MS,
    HOUR_MS,
    BinanceBreadthEma20Feed,
    asof_4h_ema20,
    first_touch_from_hour,
    four_hour_ema20,
    trailing28_unique_count,
    week_window_utc,
    weekly_levels_from_hourly,
)


def _ohlc_frame(stamps, *, open_=105.0, high=110.0, low=100.0, close=105.0):
    return pd.DataFrame({
        "open_ms": np.asarray(stamps, dtype=np.int64),
        "open": open_, "high": high, "low": low, "close": close,
    })


def test_weekly_levels_use_new_york_calendar_and_0618_extension():
    mon, tue, next_mon = week_window_utc(date(2026, 10, 26))
    assert pd.Timestamp(mon, unit="ms", tz="UTC") == pd.Timestamp("2026-10-26T04:00:00Z")
    assert pd.Timestamp(tue, unit="ms", tz="UTC") == pd.Timestamp("2026-10-27T04:00:00Z")
    # This window crosses the November fallback, so the UTC span is 169h.
    assert next_mon - mon == 7 * DAY_MS + HOUR_MS

    spring_mon, spring_tue, spring_next = week_window_utc(date(2026, 3, 2))
    assert pd.Timestamp(spring_mon, unit="ms", tz="UTC") == pd.Timestamp("2026-03-02T05:00:00Z")
    assert spring_tue - spring_mon == DAY_MS
    assert spring_next - spring_mon == 7 * DAY_MS - HOUR_MS

    bars = _ohlc_frame(np.arange(mon, tue, HOUR_MS, dtype=np.int64))
    row = weekly_levels_from_hourly("BTCUSDT", bars)[0]
    assert row["complete"] is True
    assert row["anchor_hours"] == 24
    assert row["lower"] == pytest.approx(100 - 0.618 * 10)
    assert row["upper"] == pytest.approx(110 + 0.618 * 10)

    incomplete = bars.drop(index=7)
    assert weekly_levels_from_hourly("BTCUSDT", incomplete)[0]["complete"] is False


def test_first_native_5m_touch_accepts_gap_through_and_requires_all_12_bars():
    hour = 2 * HOUR_MS
    stamps = np.arange(hour, hour + HOUR_MS, BAR_MS, dtype=np.int64)
    bars = _ohlc_frame(stamps, open_=95, high=96, low=94, close=95)
    # The candle opens and closes wholly below the lower level. The source rule
    # is low <= level, so an OHLC gap-through still counts as a touch.
    bars.loc[0, ["open", "high", "low", "close"]] = [89, 90, 85, 88]
    row = first_touch_from_hour("ADAUSDT", hour, 93.82, 1, bars)
    assert row is not None
    assert row["touch_ms"] == hour
    assert first_touch_from_hour("ADAUSDT", hour, 93.82, 1, bars.iloc[1:]) is None

    upper = _ohlc_frame(stamps, open_=117, high=120, low=116, close=119)
    row = first_touch_from_hour("ADAUSDT", hour, 116.18, -1, upper)
    assert row is not None and row["touch_ms"] == hour


def test_trailing28_counts_unique_symbols_on_both_sides_at_known_close():
    known = 50 * DAY_MS
    rows = [
        {"symbol": "ADAUSDT", "side": 1, "touch_ms": known - 28 * DAY_MS - BAR_MS},
        {"symbol": "ADAUSDT", "side": -1, "touch_ms": known - BAR_MS},
        {"symbol": "SOLUSDT", "side": -1, "touch_ms": known - BAR_MS - 1},
        {"symbol": "OLDUSDT", "side": 1, "touch_ms": known - 28 * DAY_MS - 2 * BAR_MS},
    ]
    # The left edge is inclusive on bar-close availability, and symbol identity
    # is de-duplicated across both directions.
    assert trailing28_unique_count(rows, known) == 2


def test_4h_ema_uses_only_complete_closed_4h_bars_and_resets_after_gap():
    stamps = np.arange(0, 84 * HOUR_MS, HOUR_MS, dtype=np.int64)
    close = 100.0 + np.arange(len(stamps), dtype=float)
    hourly = pd.DataFrame({"open_ms": stamps, "open": close, "high": close + 1,
                           "low": close - 1, "close": close})
    ema = four_hour_ema20(hourly)
    before_latest = asof_4h_ema20(ema, 84 * HOUR_MS - 1)
    assert before_latest["bar_close_ms"] == 80 * HOUR_MS
    assert before_latest["valid"] is True
    latest = asof_4h_ema20(ema, 84 * HOUR_MS)
    assert latest["bar_close_ms"] == 84 * HOUR_MS

    broken = hourly.loc[hourly.open_ms.ne(40 * HOUR_MS)]
    broken_ema = four_hour_ema20(broken)
    assert asof_4h_ema20(broken_ema, 84 * HOUR_MS)["valid"] is False


def test_failed_new_listing_warmup_defers_close(tmp_path, monkeypatch):
    feed = BinanceBreadthEma20Feed(cache_root=tmp_path)
    feed._warmed = True
    monkeypatch.setattr(feed, "_universe", lambda: setattr(feed, "_warmed", False))
    monkeypatch.setattr(feed, "warmup", lambda: {"complete": False})
    with pytest.raises(RuntimeError, match="Incomplete market history"):
        next(feed.iter_closed_events(5 * BAR_MS, 6 * BAR_MS))


def _candidate_fixture(tmp_path: Path, symbols, known_ms, decision_ms):
    feed = BinanceBreadthEma20Feed(cache_root=tmp_path)
    feed._hourly_ema20 = pd.DataFrame()
    feed._touches = {
        (f"OLD{i}USDT", "2026-11-02", 1): {
            "symbol": f"OLD{i}USDT", "week": "2026-11-02", "side": 1,
            "touch_ms": known_ms - (i + 1) * BAR_MS - DAY_MS,
            "level": 90.0,
        }
        for i in range(38 - len(symbols))
    }
    initial = []
    bars = {}
    for index, symbol in enumerate(symbols):
        level = 100.0
        initial.append({"symbol": symbol, "week": "2026-11-02", "side": 1,
                        "touch_ms": known_ms - BAR_MS, "level": level})
        before_ms = known_ms - BAR_MS
        after_ms = decision_ms - BAR_MS
        feed._five_rows[symbol] = {
            before_ms: {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            after_ms: {"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0},
        }
        bars[symbol] = {"bar_open_ms": decision_ms - BAR_MS, "open": 102.0,
                        "high": 104.0, "low": 101.0, "close": 103.0,
                        "next_open_ms": decision_ms, "next_open": 104.0,
                        "bar_available": True, "next_open_available": True}
    bars["BTCUSDT"] = {"bar_open_ms": decision_ms - BAR_MS, "open": 50_000.0,
                       "high": 50_100.0, "low": 49_900.0, "close": 50_050.0,
                       "next_open_ms": decision_ms, "next_open": 50_060.0,
                       "bar_available": True, "next_open_available": True}
    return feed, initial, bars


def test_candidate_rules_use_alt_only_features_but_no_ema_veto_in_feed(tmp_path):
    known = 60 * DAY_MS
    decision = known + 30 * 60_000
    feed, initial, bars = _candidate_fixture(tmp_path, ["ADAUSDT", "BNBUSDT"], known, decision)
    candidate = feed._candidate("BTC_case", known, decision, initial, [], bars)
    assert candidate["rule_pass"] is True
    assert candidate["data_complete"] is True
    assert candidate["can_open"] is True
    assert candidate["alt_observed_move_pct"] == pytest.approx(3.0)
    assert candidate["btc_4h"]["valid"] is False
    assert candidate["next_open_ms"] == decision
    assert candidate["next_open"] == pytest.approx(50_060.0)

    ordinary = feed._candidate("BTC_ordinary", known, decision, initial, [], bars)
    assert ordinary["rule_pass"] is True
    assert ordinary["reclaim_fraction"] == pytest.approx(1.0)
    assert ordinary["data_complete"] is True  # the account applies the EMA gate after reserving its slot


def test_alt_initial_minimum_counts_raw_touch_cohort_then_filters_basket(tmp_path):
    known = 60 * DAY_MS
    feed, initial, bars = _candidate_fixture(tmp_path, ["ADAUSDT", "BNBUSDT"], known, known)
    initial.append({"symbol": "BTCUSDT", "week": "2026-11-02", "side": 1,
                    "touch_ms": known - BAR_MS, "level": 50_000.0})
    feed._touches[("BTCUSDT", "2026-11-02", 1)] = initial[-1]
    bars["BTCUSDT"]["bar_open_ms"] = known - BAR_MS
    bars["BTCUSDT"]["next_open_ms"] = known
    candidate = feed._candidate("ALT", known, known, [initial[0], initial[-1]], [], bars)
    assert candidate["rule_pass"] is True
    assert candidate["expected_member_n"] == 1
    assert [item["symbol"] for item in candidate["members"]] == ["ADAUSDT"]
    assert candidate["data_complete"] is True
    assert candidate["btc_4h"]["valid"] is False


def test_live_bar_does_not_cache_unclosed_next_candle(tmp_path):
    bar_open = 90 * BAR_MS
    next_open = bar_open + BAR_MS
    requests = []

    def rest_get(url):
        requests.append(url)
        return [
            [bar_open, "10", "12", "9", "11"],
            [next_open, "13", "14", "12", "13.5"],
        ]

    feed = BinanceBreadthEma20Feed(cache_root=tmp_path, rest_get=rest_get)
    symbol, row = feed._fetch_live_symbol("BTCUSDT", bar_open, next_open)
    assert symbol == "BTCUSDT"
    assert row["bar_available"] is True and row["next_open_available"] is True
    assert row["next_open"] == pytest.approx(13.0)
    assert set(feed._five_rows["BTCUSDT"]) == {bar_open}
    assert len(requests) == 1


def test_saved_touch_ledger_is_idempotent_and_rejects_identity_conflict(tmp_path):
    feed = BinanceBreadthEma20Feed(cache_root=tmp_path)
    row = {"symbol": "ADAUSDT", "week": "2026-11-02", "side": 1,
           "touch_ms": 1_800_000, "level": 93.82}
    assert feed.seed_saved_touches([row]) == 1
    assert feed.seed_saved_touches([row]) == 0
    assert feed.recent_touches(as_of_ms=row["touch_ms"] + BAR_MS, days=35) == [
        {**row, "source": "runner_saved_touch", "verified_5m": True}
    ]
    with pytest.raises(ValueError, match="Conflicting saved first touch"):
        feed.seed_saved_touches([{**row, "touch_ms": row["touch_ms"] + BAR_MS}])


def test_event_exposes_funding_both_edges_without_duplicate_time_fields(tmp_path, monkeypatch):
    boundary = 90 * BAR_MS
    bar_open = boundary - BAR_MS
    feed = BinanceBreadthEma20Feed(cache_root=tmp_path)
    feed._symbols = {"BTCUSDT": feed_module._UniverseSymbol("BTCUSDT", 0, "TRADING")}
    feed._last_universe_refresh = feed_module._now_ms()
    feed._warmed = True
    feed._market_seeded_through_ms = boundary
    monkeypatch.setattr(feed_module, "_now_ms", lambda: boundary + 1_000)
    monkeypatch.setattr(feed, "_live_bars", lambda *_args, **_kwargs: {
        "BTCUSDT": {"bar_open_ms": bar_open, "open": 100.0, "high": 102.0,
                    "low": 99.0, "close": 101.0, "next_open_ms": boundary,
                    "next_open": 101.5, "bar_available": True,
                    "next_open_available": True, "source": "fixture"}
    })
    monkeypatch.setattr(feed, "_candidates_at", lambda *_args: [])
    calls = []

    def fake_funding(symbols, at_ms, _rest):
        calls.append((set(symbols), at_ms))
        return [], {"BTCUSDT": {"time_ms": at_ms, "due": False, "available": True}}

    monkeypatch.setattr(feed_module, "funding_at", fake_funding)
    event = next(feed.iter_closed_events(bar_open, boundary, position_symbols=["BTCUSDT"]))
    assert calls == [({"BTCUSDT"}, bar_open), ({"BTCUSDT"}, boundary)]
    assert event["funding_open_due"] == [{"symbol": "BTCUSDT", "time_ms": bar_open,
                                         "due": False, "available": True}]
    assert event["funding_due"] == [{"symbol": "BTCUSDT", "time_ms": boundary,
                                    "due": False, "available": True}]
    assert event["source_coverage_complete"] is True
    assert event["trailing28_any_touch_unique_n"] == 0

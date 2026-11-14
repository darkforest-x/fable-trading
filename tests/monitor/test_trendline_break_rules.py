from __future__ import annotations

from yoyo.monitor import trendline_break_rules as rules


def _line(*, known_ms: int, retired_ms: int | None = None) -> rules._Line:
    # At known_ms the line is 100.0 and slopes down by 0.5 per hour.
    a = rules._Pivot(0, known_ms - 20 * rules.ONE_HOUR_MS, 110.0, 1.0)
    b = rules._Pivot(10, known_ms - 10 * rules.ONE_HOUR_MS, 105.0, 1.0)
    return rules._Line(a, b, known_ms - rules.FOUR_HOUR_MS, known_ms, retired_ms)


def _h1(times: list[int], closes: list[float]) -> list[dict[str, float | int]]:
    if len(times) != len(closes):
        raise AssertionError("each hourly timestamp needs one close")
    return [
        {"time": timestamp, "open": close, "high": close + 0.1, "low": close - 0.1, "close": close}
        for timestamp, close in zip(times, closes)
    ]


def _h4(times: list[int]) -> list[dict[str, float | int]]:
    return [
        {"time": timestamp, "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0}
        for timestamp in times
    ]


def _mock_line(monkeypatch, line: rules._Line) -> None:
    monkeypatch.setattr(rules, "_select_lines", lambda bars, asof_ms: [line])


def test_event_uses_first_close_and_level_at_hourly_open(monkeypatch) -> None:
    known = 100 * rules.FOUR_HOUR_MS
    line = _line(known_ms=known)
    _mock_line(monkeypatch, line)
    times = [known - rules.ONE_HOUR_MS, known, known + rules.ONE_HOUR_MS]
    closes = [100.0, 100.2, 99.0]

    not_yet_closed = rules.events(
        "BTC-USDT-SWAP", _h4([known - rules.FOUR_HOUR_MS]), _h1(times, closes),
        asof_ms=known + rules.ONE_HOUR_MS // 2,
    )
    found = rules.events("BTC-USDT-SWAP", _h4([known - rules.FOUR_HOUR_MS]), _h1(times, closes),
                         asof_ms=known + 2 * rules.ONE_HOUR_MS)

    assert not_yet_closed == []
    assert len(found) == 1
    event = found[0]
    assert event["symbol"] == "BTC-USDT-SWAP"
    assert event["candle_ms"] == known
    assert event["confirmed_ms"] == known + rules.ONE_HOUR_MS
    assert event["level"] == 100.0
    assert event["close"] == 100.2
    assert event["prebreak_low"] is None  # Fewer than eleven closed hourly candles.
    assert event["prebreak_low_bars"] == 2
    assert event["a_ms"] == known - 20 * rules.ONE_HOUR_MS
    assert event["b_ms"] == known - 10 * rules.ONE_HOUR_MS
    assert event["event_id"] == rules.events(
        "BTC-USDT-SWAP", _h4([known - rules.FOUR_HOUR_MS]), _h1(times, closes),
        asof_ms=known + 2 * rules.ONE_HOUR_MS,
    )[0]["event_id"]


def test_event_freezes_lowest_low_of_breakout_and_previous_ten_bars(monkeypatch) -> None:
    known = 100 * rules.FOUR_HOUR_MS
    _mock_line(monkeypatch, _line(known_ms=known))
    times = [known - 12 * rules.ONE_HOUR_MS + index * rules.ONE_HOUR_MS for index in range(13)]
    hourly = _h1(times, [100.0] * 13)
    hourly[-11]["low"] = 96.0
    hourly[-2]["close"] = 100.0  # Below the line at known - 1H (100.5).
    hourly[-2]["open"] = 100.0
    hourly[-1]["close"] = 100.2  # Above the line at known (100.0).
    hourly[-1]["open"] = 100.2
    hourly[-1]["high"] = 100.3

    found = rules.events("BTC", _h4([known - rules.FOUR_HOUR_MS]), hourly,
                         asof_ms=known + rules.ONE_HOUR_MS)

    assert len(found) == 1
    event = found[0]
    assert event["candle_ms"] == known
    assert event["prebreak_low"] == 96.0
    assert event["prebreak_low_start_ms"] == known - 10 * rules.ONE_HOUR_MS
    assert event["prebreak_low_end_ms"] == known
    assert event["prebreak_low_bars"] == 11


def test_missing_birth_predecessor_cannot_certify_first_cross(monkeypatch) -> None:
    known = 100 * rules.FOUR_HOUR_MS
    _mock_line(monkeypatch, _line(known_ms=known))
    hourly = _h1([known, known + rules.ONE_HOUR_MS], [101.0, 102.0])

    assert rules.events("BTC", _h4([known - rules.FOUR_HOUR_MS]), hourly,
                        asof_ms=known + 2 * rules.ONE_HOUR_MS) == []


def test_gap_after_birth_never_resumes_first_cross_scan(monkeypatch) -> None:
    known = 100 * rules.FOUR_HOUR_MS
    _mock_line(monkeypatch, _line(known_ms=known))
    hourly = _h1(
        [known - rules.ONE_HOUR_MS, known, known + 2 * rules.ONE_HOUR_MS, known + 3 * rules.ONE_HOUR_MS],
        [99.0, 99.0, 102.0, 103.0],
    )

    assert rules.events("BTC", _h4([known - rules.FOUR_HOUR_MS]), hourly,
                        asof_ms=known + 4 * rules.ONE_HOUR_MS) == []


def test_cross_on_line_end_close_is_included(monkeypatch) -> None:
    known = 100 * rules.FOUR_HOUR_MS
    line = _line(known_ms=known, retired_ms=known + 2 * rules.ONE_HOUR_MS)
    _mock_line(monkeypatch, line)
    hourly = _h1([known - rules.ONE_HOUR_MS, known, known + rules.ONE_HOUR_MS], [99.0, 99.0, 99.8])

    found = rules.events("BTC", _h4([known - rules.FOUR_HOUR_MS]), hourly,
                         asof_ms=known + 2 * rules.ONE_HOUR_MS)

    assert len(found) == 1
    assert found[0]["confirmed_ms"] == line.retired_ms


def test_four_hour_gap_retires_old_line_and_does_not_emit_post_gap_cross(monkeypatch) -> None:
    known = rules.FOUR_HOUR_MS
    line = _line(known_ms=known)

    def one_line_per_pre_gap_segment(segment, asof_ms):
        return [line] if segment[0].time == 0 else []

    monkeypatch.setattr(rules, "_select_lines", one_line_per_pre_gap_segment)
    source_times = [0, rules.FOUR_HOUR_MS, 3 * rules.FOUR_HOUR_MS, 4 * rules.FOUR_HOUR_MS]
    times = list(range(3 * rules.ONE_HOUR_MS, 14 * rules.ONE_HOUR_MS, rules.ONE_HOUR_MS))
    closes = [120.0 if timestamp == 3 * rules.FOUR_HOUR_MS else 50.0 for timestamp in times]

    found = rules.events("BTC", _h4(source_times), _h1(times, closes),
                         asof_ms=5 * rules.FOUR_HOUR_MS)

    assert line.retired_ms == 3 * rules.FOUR_HOUR_MS - 1
    assert found == []


def test_stale_tail_suppresses_new_cross_but_keeps_pre_stale_history(monkeypatch) -> None:
    known = 100 * rules.FOUR_HOUR_MS
    line = _line(known_ms=known)
    monkeypatch.setattr(rules, "_select_lines", lambda bars, asof_ms: [line])
    source = _h4([known - rules.FOUR_HOUR_MS])
    times = [known - rules.ONE_HOUR_MS, known, known + rules.ONE_HOUR_MS,
             known + 2 * rules.ONE_HOUR_MS, known + 3 * rules.ONE_HOUR_MS,
             known + 4 * rules.ONE_HOUR_MS]

    before_stale = rules.events("BTC", source, _h1(times[:4], [99.0, 99.0, 99.0, 99.0]),
                                asof_ms=known + 3 * rules.ONE_HOUR_MS)
    historical_cross = rules.events("BTC", source, _h1(times, [99.0, 99.0, 99.0, 101.0, 99.0, 110.0]),
                                    asof_ms=known + 5 * rules.ONE_HOUR_MS)
    stale_only = rules.events("BTC", source, _h1(times, [99.0, 99.0, 99.0, 99.0, 99.0, 110.0]),
                              asof_ms=known + 5 * rules.ONE_HOUR_MS)

    assert before_stale == []
    assert len(historical_cross) == 1
    assert historical_cross[0]["candle_ms"] == known + 2 * rules.ONE_HOUR_MS
    assert stale_only == []


def test_default_engine_builds_v19_descending_resistance_line() -> None:
    bars = []
    for index in range(160):
        high = {30: 102.0, 70: 101.7, 110: 101.4}.get(index, 101.1)
        bars.append(rules._Bar(index * rules.FOUR_HOUR_MS, 101.0, high, 100.9, 101.0))

    lines = rules._select_lines(bars, bars[-1].time + rules.FOUR_HOUR_MS)

    assert len(lines) == 1
    assert (lines[0].a.x, lines[0].b.x) == (30, 110)
    assert lines[0].known_ms == 120 * rules.FOUR_HOUR_MS


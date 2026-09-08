"""Causality, confirmation clocks and event semantics for the live monitor."""
from copy import deepcopy
import json

import numpy as np
import pytest

from yoyo.monitor import signals


def candles(n=400, duration=3_600_000, start=0, oscillate=False):
    result = []
    for i in range(n):
        c = 100 + (2 * np.sin(i / 8) + .4 * np.sin(i / 2) if oscillate else 0)
        result.append(dict(t=start + i * duration, o=c, h=c + 1, l=c - 1, c=c, v=10))
    return result


def patch_features(monkeypatch, changes):
    """Inject exact boundary cases into the causal state machine, not quotes."""
    original = signals._compute

    def compute(b):
        f = original(b)
        for key, values in changes.items():
            for index, value in values.items():
                if index < len(b["t"]):
                    f[key][index] = value
        return f

    monkeypatch.setattr(signals, "_compute", compute)


def test_empty_warmup_and_json_are_explicit():
    empty = signals.analyze([], [], "1H")
    assert empty["state"]["phase"] == "loading"
    assert empty["state"]["price"] is None
    result = signals.analyze(candles(340), [], "1H")
    assert result["events"] == []
    assert result["state"]["phase"] == "loading"
    ready = signals.analyze(candles(341), [], "1H")
    assert ready["state"]["ready"]
    assert ready["state"]["phase"] == "building"
    assert ready["state"]["near_zero_bars"] == 1
    assert ready["state"]["htf_side"] == "unknown"
    json.dumps(result, allow_nan=False)
    json.dumps(empty, allow_nan=False)
    json.dumps(ready, allow_nan=False)


def test_zero_true_range_never_becomes_ready():
    b = candles(500)
    for c in b:
        c["h"] = c["l"] = c["o"] = c["c"]
    result = signals.analyze(b, [], "1H")
    assert result["events"] == []
    assert not result["state"]["ready"]
    assert result["state"]["phase"] == "loading"


def test_real_long_flat_release_is_not_an_entry_or_backpaint():
    b = candles(353)
    b[352].update(o=100, h=141, l=99, c=140)
    r = signals.analyze(b, [], "1H")
    assert all(not p["focus"] for p in r["chart"][:351])
    assert r["chart"][350]["near_zero_bars"] == 11
    assert r["chart"][351]["focus"]
    assert r["chart"][351]["focus_qualified_ms"] == b[351]["t"]
    release = [e for e in r["events"] if e["kind"] == "release"]
    assert len(release) == 1
    event = release[0]
    assert event["side"] == "long"
    assert event["near_zero_bars"] == 12
    assert event["price"] == 140
    assert event["zone_high"] == 101  # The release candle is excluded.
    assert event["zone_end_ms"] == b[352]["t"]
    assert event["bar_close_ms"] == b[352]["t"] + 3_600_000
    assert not event["is_system_entry"] and not event["system_entry_same_bar"]
    assert not event["dense"]
    assert not any(e["kind"] == "entry" for e in r["events"])
    assert not r["chart"][352]["focus"]
    assert r["chart"][352]["glow_side"] == "long"
    zero_events = [e for e in r["events"] if e["kind"] == "zero_breakout"]
    assert len(zero_events) == 1
    assert zero_events[0]["side"] == "long" and zero_events[0]["previous_md"] == 0
    assert zero_events[0]["near_zero_bars"] == 12
    assert r["chart"][352]["zero_breakout_side"] == "long"
    visible = [e for e in r["events"] if e["kind"] == "tv_start"]
    assert len(visible) == 1 and visible[0]["price"] == 140
    assert visible[0]["near_zero_bars"] == 12
    assert visible[0]["source_kind"] == "release"
    assert visible[0]["tv_marker"] == "focus_release"
    assert visible[0]["tv_profile"] == signals.TV_PROFILE
    assert visible[0]["is_monitor_signal"] is True
    assert r["chart"][352]["tv_start_side"] == "long"


def test_qualification_freezes_band_when_atr_shrinks(monkeypatch):
    patch_features(monkeypatch, {"atr": {351: .01},
                                "md": {352: .15, 353: .21},
                                "sb": {352: .05, 353: .06}})
    r = signals.analyze(candles(354), [], "1H")
    assert r["chart"][351]["focus_band"] == pytest.approx(.2)
    assert r["chart"][352]["focus"]  # .15 exceeds .001 candidate but not frozen .2.
    assert r["chart"][352]["near_zero_bars"] == 13
    release = [e for e in r["events"] if e["kind"] == "release"]
    assert len(release) == 1 and release[0]["bar_open_ms"] == 353 * 3_600_000
    assert release[0]["focus_band"] == pytest.approx(.2)
    assert release[0]["near_zero_bars"] == 13


def test_signal_line_only_departure_does_not_invent_direction_or_reseed(monkeypatch):
    patch_features(monkeypatch, {"md": {352: .1}, "sb": {352: .3}})
    r = signals.analyze(candles(354), [], "1H")
    assert not any(e["kind"] == "release" for e in r["events"])
    assert r["chart"][352]["near_zero_bars"] == 0
    assert not r["chart"][352]["focus"]
    assert r["chart"][353]["near_zero_bars"] == 1


def test_default_dense_entry_is_independent_of_htf_and_exits_on_zero(monkeypatch):
    patch_features(monkeypatch, {"md": {342: 1, 343: 2}, "dense": {342: True}})
    r = signals.analyze(candles(345), [], "1H")
    core = [e for e in r["events"] if e["kind"] in ("entry", "exit")]
    assert [(e["kind"], e["side"], e["bar_open_ms"]) for e in core] == [
        ("entry", "long", 342 * 3_600_000), ("exit", "long", 344 * 3_600_000)]
    assert core[0]["htf_allowed"] is None
    assert core[0]["is_system_entry"]
    assert core[0]["zero_bars"] >= 1
    assert not core[1]["is_system_entry"]


def test_wick_retest_requires_qualified_segment_and_body_outside():
    b = candles(353)
    b[350].update(o=100.1, c=100.1)
    b[351].update(o=100.1, c=100.1)
    r = signals.analyze(b, [], "1H")
    retests = [e for e in r["events"] if e["kind"] == "retest"]
    assert [e["bar_open_ms"] for e in retests] == [b[351]["t"]]
    assert retests[0]["side"] == "long"
    assert retests[0]["retest_ma"] == "SMA20"
    assert r["chart"][350]["retest_side"] is None  # Eleventh bar is not qualified.
    assert r["chart"][351]["retest_side"] == "long"
    crossed = deepcopy(b)
    crossed[351]["o"] = 99.9  # Body crosses, despite a long close and lower wick.
    bad = signals.analyze(crossed, [], "1H")
    assert bad["chart"][351]["retest_side"] is None


def test_short_wick_retest_is_symmetric():
    b = candles(352)
    b[350].update(o=99.9, c=99.9)
    b[351].update(o=99.9, c=99.9)
    r = signals.analyze(b, [], "1H")
    assert r["chart"][351]["retest_side"] == "short"
    assert [e["side"] for e in r["events"] if e["kind"] == "retest"] == ["short"]


def test_prefix_is_unchanged_by_arbitrary_future_ohlc_or_htf_mutation():
    b = candles(460, oscillate=True)
    original = signals.analyze(b, [], "1H")
    changed = deepcopy(b)
    for row in changed[400:]:
        row.update(o=900, h=1001, l=1, c=1000, v=9000)
    altered = signals.analyze(changed, [], "1H")
    prefix = signals.analyze(b[:400], [], "1H")
    assert original["chart"][:400] == altered["chart"][:400] == prefix["chart"]
    cutoff = b[400]["t"]
    assert [e for e in original["events"] if e["bar_open_ms"] < cutoff] == prefix["events"]
    assert [e for e in altered["events"] if e["bar_open_ms"] < cutoff] == prefix["events"]
    assert prefix["state"]["bar_close_ms"] == cutoff


@pytest.mark.parametrize("timeframe,high_tf", [("15m", "1H"), ("1H", "4H"), ("4H", "1Dutc")])
def test_htf_uses_local_open_not_local_close_and_warms_independently(timeframe, high_tf):
    duration = signals.TIMEFRAMES[timeframe]
    high_duration = signals.TIMEFRAMES[high_tf]
    high = candles(350, high_duration)
    # Index 340 closes here, the first eligible HTF close.
    first_known = 341 * high_duration
    low = candles(342, duration, first_known - 341 * duration)
    before = signals.analyze(low[:-1], high, timeframe)
    after = signals.analyze(low, high, timeframe)
    assert before["state"]["bar_close_ms"] == first_known
    assert before["state"]["htf_side"] == "unknown"  # It closes with that bar, too late.
    assert after["state"]["bar_open_ms"] == first_known
    assert after["state"]["htf_side"] == "flat"
    assert after["state"]["htf_bar_close_ms"] == first_known
    assert after["state"]["htf_long_allowed"] is True
    assert after["state"]["htf_short_allowed"] is True
    # Mutate the unclosed high bar dramatically: annotations must not change.
    modified = deepcopy(high)
    modified[341].update(o=100, h=2001, l=99, c=2000)
    compare = signals.analyze(low, modified, timeframe)
    assert compare == after
    # The last known quote cannot be carried through a missing expected HTF.
    stale = candles(342, duration, first_known + high_duration - 341 * duration)
    assert signals.analyze(stale, high[:341], timeframe)["state"]["htf_side"] == "unknown"


@pytest.mark.parametrize("defect", ["gap", "duplicate", "reverse", "nan", "body", "unconfirmed"])
def test_invalid_input_fails_closed(defect):
    b = candles(360)
    if defect == "gap":
        del b[100]
    elif defect == "duplicate":
        b[100]["t"] = b[99]["t"]
    elif defect == "reverse":
        b.reverse()
    elif defect == "nan":
        b[100]["c"] = float("nan")
    elif defect == "body":
        b[100]["c"] = 105
    else:
        b[100]["confirm"] = "0"
    with pytest.raises(ValueError):
        signals.analyze(b, [], "1H")


def test_unsupported_timeframe_is_rejected():
    with pytest.raises(ValueError):
        signals.analyze([], [], "5m")


def test_protocol_and_output_are_json_safe():
    r = signals.analyze(candles(480, oscillate=True), [], "1H")
    assert r["protocol"]["mode"] == "visible_tv_focus_release"
    assert r["protocol"]["notification_event"] == "tv_start"
    assert r["protocol"]["tv_profile"] == "imacd-v2.2-focus12-band0.10-marks-off"
    assert r["protocol"]["show_focus"] is True
    assert r["protocol"]["show_marks"] is False
    assert r["protocol"]["focus_min_bars"] == 12
    assert r["protocol"]["focus_atr_band"] == .10
    assert r["protocol"]["htf_filters_default_entries"] is False
    assert r["protocol"]["orders_enabled"] is False
    assert r["state"]["protocol_version"] == signals.PROTOCOL_VERSION
    json.dumps(r, allow_nan=False)


def test_breakout_candle_does_not_rewrite_formation():
    b = candles(401, oscillate=True)
    before = signals.analyze(b, [], "1H")
    b[-1].update(o=100, h=1001, l=1, c=1000)
    after = signals.analyze(b, [], "1H")
    for field in ("dense", "prior_width_atr", "prior_crosses"):
        assert after["state"][field] == before["state"][field]


@pytest.mark.parametrize("value,side", [(2., "long"), (-2., "short"),
                                      (1e-14, "long"), (-1e-14, "short")])
def test_zero_breakout_fires_on_first_nonzero_even_below_atr_band(monkeypatch, value, side):
    patch_features(monkeypatch, {"md": {342: value, 343: value, 344: value * 2}})
    b = candles(345)
    result = signals.analyze(b, [], "1H")
    events = [e for e in result["events"] if e["kind"] == "zero_breakout"]
    assert len(events) == 1
    event = events[0]
    assert event["bar_open_ms"] == b[342]["t"]
    assert event["bar_close_ms"] == b[342]["t"] + 3_600_000
    assert event["side"] == side and event["md"] == value
    assert event["previous_md"] == 0
    assert event["zero_bars"] == 309  # Valid SMMA bars 33..341, before departure.
    assert event["near_zero_bars"] == 2  # Focus run before updating bar 342.
    assert event["dense"] is False and event["htf_allowed"] is None
    assert event["price"] == b[342]["c"] and event["price_basis"] == "signal_candle_close"
    assert event["confirmed"] and not event["is_monitor_signal"]
    assert not event["is_system_entry"]
    assert result["chart"][342]["zero_breakout_side"] == side
    assert result["chart"][342]["zero_bars"] == 0
    assert result["chart"][342]["entry_side"] is None
    assert all(row["zero_breakout_side"] is None for row in result["chart"][343:])
    if abs(value) < .2:
        assert not any(e["kind"] == "release" for e in result["events"])
    json.dumps(result, allow_nan=False)


def test_zero_breakout_ignores_htf_denial_and_signal_line_position(monkeypatch):
    patch_features(monkeypatch, {"md": {342: .01}, "sb": {342: 10.}, "dense": {342: False}})
    denied = dict(htf_known=True, htf_side="short", htf_md=-1., htf_sh=-1.,
                  htf_bar_close_ms=0, htf_long_allowed=False, htf_short_allowed=True)
    monkeypatch.setattr(signals, "_higher_at", lambda *args: dict(denied))
    result = signals.analyze(candles(343), [], "1H")
    events = [e for e in result["events"] if e["kind"] == "zero_breakout"]
    assert len(events) == 1 and events[0]["side"] == "long"
    assert events[0]["htf_allowed"] is False
    assert events[0]["dense"] is False
    assert events[0]["md"] < events[0]["sb"]
    assert result["state"]["zero_breakout_side"] == "long"


def test_continuation_and_direct_sign_reversal_do_not_repeat_zero_breakout(monkeypatch):
    patch_features(monkeypatch, {"md": {340: 1., 341: 2., 342: -1., 343: -2.,
                                        344: 0., 345: -.01, 346: -.02}})
    result = signals.analyze(candles(347), [], "1H")
    events = [e for e in result["events"] if e["kind"] == "zero_breakout"]
    assert [(e["bar_open_ms"], e["side"]) for e in events] == [
        (340 * 3_600_000, "long"), (345 * 3_600_000, "short")]
    assert events[-1]["zero_bars"] == 1
    assert result["chart"][342]["zero_breakout_side"] is None  # Direct + -> -.
    assert result["chart"][346]["zero_breakout_side"] is None  # Continued negative.


def test_signal_line_crossing_without_md_departure_is_not_a_monitor_signal(monkeypatch):
    patch_features(monkeypatch, {"sb": {340: -1., 341: 1., 342: -1.}})
    result = signals.analyze(candles(343), [], "1H")
    assert all(row["md"] == 0 for row in result["chart"])
    assert not any(e["kind"] == "zero_breakout" for e in result["events"])
    assert not any(row["zero_breakout_side"] for row in result["chart"])


def test_warmup_does_not_turn_an_existing_nonzero_run_into_a_new_breakout(monkeypatch):
    patch_features(monkeypatch, {"md": {339: 1., 340: 2., 341: 0., 342: 1.}})
    result = signals.analyze(candles(343), [], "1H")
    events = [e for e in result["events"] if e["kind"] == "zero_breakout"]
    assert [e["bar_open_ms"] for e in events] == [342 * 3_600_000]
    assert not result["chart"][339]["ready"]
    assert result["chart"][340]["ready"]
    assert result["chart"][340]["zero_breakout_side"] is None


def test_real_zero_breakout_history_survives_future_quote_mutation():
    b = candles(410)
    b[352].update(o=100, h=141, l=99, c=140)
    original = signals.analyze(b, [], "1H")
    changed = deepcopy(b)
    for row in changed[370:]:
        row.update(o=100, h=2001, l=1, c=2000)
    altered = signals.analyze(changed, [], "1H")
    prefix = signals.analyze(b[:370], [], "1H")
    expected = [e for e in prefix["events"] if e["kind"] == "zero_breakout"]
    assert expected and expected[0]["bar_open_ms"] == b[352]["t"]
    for result in (original, altered):
        assert [e for e in result["events"] if e["kind"] == "zero_breakout"
                and e["bar_open_ms"] < b[370]["t"]] == expected
        assert result["chart"][:370] == prefix["chart"]


@pytest.mark.parametrize("timeframe", ["15m", "1H", "4H"])
def test_visible_start_waits_for_frozen_band_after_raw_zero_departure(monkeypatch, timeframe):
    duration = signals.TIMEFRAMES[timeframe]
    patch_features(monkeypatch, {"md": {352: .10, 353: .19, 354: .21, 355: .25},
                                "sb": {352: .02, 353: .04, 354: .06, 355: .08}})
    result = signals.analyze(candles(356, duration=duration), [], timeframe)
    observed_zero = [e for e in result["events"] if e["kind"] == "zero_breakout"]
    visible = [e for e in result["events"] if e["kind"] == "tv_start"]
    assert [e["bar_open_ms"] for e in observed_zero] == [352 * duration]
    assert [e["bar_open_ms"] for e in visible] == [354 * duration]
    event = visible[0]
    assert event["previous_md"] == .19 and event["previous_sb"] == .04
    assert event["zero_bars"] == 0  # A visible arrow does not require previous md == 0.
    assert event["near_zero_bars"] == 14
    assert event["focus_band"] == pytest.approx(.2)
    assert event["focus_start_ms"] == 340 * duration
    assert event["focus_qualified_ms"] == 351 * duration
    assert event["zone_end_ms"] == 354 * duration
    assert event["confirmed"] and event["ready"] and event["focus_qualified_before"]
    assert event["tv_marker_visible"] and event["tv_show_focus"]
    assert event["tv_show_marks"] is False
    assert event["source_kind"] == "release" and not event["is_system_entry"]
    assert all(not e["is_monitor_signal"] for e in result["events"] if e["kind"] != "tv_start")
    assert [result["chart"][i]["tv_start_side"] for i in (352, 353, 354, 355)] == [None, None, "long", None]


@pytest.mark.parametrize("value,side", [(.3, "long"), (-.3, "short")])
def test_visible_start_is_directionally_symmetric_and_ignores_hidden_filters(monkeypatch, value, side):
    patch_features(monkeypatch, {"md": {352: value}, "dense": {352: False}})
    opposite = -1. if value > 0 else 1.
    info = dict(htf_known=True, htf_side="short" if value > 0 else "long", htf_md=opposite,
                htf_sh=opposite, htf_bar_close_ms=0, htf_long_allowed=opposite > 0,
                htf_short_allowed=opposite < 0)
    monkeypatch.setattr(signals, "_higher_at", lambda *args: dict(info))
    result = signals.analyze(candles(353), [], "1H")
    visible = [e for e in result["events"] if e["kind"] == "tv_start"]
    assert len(visible) == 1 and visible[0]["side"] == side
    assert visible[0]["dense"] is False and visible[0]["htf_allowed"] is False
    assert result["state"]["tv_start_side"] == side
    assert not any(e["kind"] == "entry" for e in result["events"])


def test_signal_line_alone_ends_segment_without_a_visible_start(monkeypatch):
    patch_features(monkeypatch, {"md": {352: .1, 353: .4}, "sb": {352: .3, 353: .35}})
    result = signals.analyze(candles(354), [], "1H")
    assert result["chart"][351]["focus"] is True
    assert result["chart"][352]["focus"] is False
    assert not any(e["kind"] == "tv_start" for e in result["events"])
    assert not result["chart"][353]["tv_start_side"]  # Outside band next bar, but prior segment ended.


def test_glow_continuation_never_repeats_the_visible_label(monkeypatch):
    patch_features(monkeypatch, {"md": {i: .3 for i in range(352, 368)}})
    result = signals.analyze(candles(368), [], "1H")
    visible = [e for e in result["events"] if e["kind"] == "tv_start"]
    assert len(visible) == 1 and visible[0]["bar_open_ms"] == 352 * 3_600_000
    assert all(row["glow_side"] == "long" for row in result["chart"][352:364])
    assert result["chart"][364]["glow_side"] is None
    assert all(row["tv_start_side"] is None for row in result["chart"][353:])


def test_no_visible_start_without_twelve_qualified_preparation_bars(monkeypatch):
    patch_features(monkeypatch, {"md": {351: .3, 352: .4}})
    result = signals.analyze(candles(353), [], "1H")
    assert result["chart"][350]["near_zero_bars"] == 11
    assert any(e["kind"] == "zero_breakout" for e in result["events"])
    assert not any(e["kind"] == "tv_start" for e in result["events"])
    assert not any(row["tv_start_side"] for row in result["chart"])


def test_exact_frozen_threshold_does_not_draw_visible_start(monkeypatch):
    patch_features(monkeypatch, {"md": {352: .2, 353: .20000000001}})
    result = signals.analyze(candles(354), [], "1H")
    assert result["chart"][352]["tv_start_side"] is None
    visible = [e for e in result["events"] if e["kind"] == "tv_start"]
    assert len(visible) == 1 and visible[0]["bar_open_ms"] == 353 * 3_600_000

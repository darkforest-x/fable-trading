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


@pytest.mark.parametrize("timeframe,high_tf", [("1H", "4H"), ("4H", "1Dutc")])
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
        signals.analyze([], [], "15m")


def test_protocol_and_output_are_json_safe():
    r = signals.analyze(candles(480, oscillate=True), [], "1H")
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

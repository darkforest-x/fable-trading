"""Focused causal checks for the bounded V12.6 research engine."""
from __future__ import annotations

import numpy as np

from yoyo.evaluation.spike_v10_4 import Line, V104Params
from yoyo.evaluation.spike_v126_engine import (
    V126Params,
    _store_merged_sidecar,
    line_events,
    pair_events,
)


def _local_market(n=180):
    """The existing V12 local-touch geometry, with two post-birth breaks."""
    x = np.arange(n); line = 120.0 + (110.0 - 120.0) * (x - 20) / 40.0
    close = line - 6; open_ = close.copy(); high = line - 5; low = line - 7
    close[:20] = open_[:20] = 95; high[:20] = 96; low[:20] = 94
    for i, price in ((20, 120.0), (60, 110.0), (92, float(line[92]))):
        high[i], close[i], open_[i], low[i] = price, price - .1, price - .1, price - 10
    low[21:60], low[61:92] = 100, 95
    for i in (95, 96): close[i] = open_[i] = line[i] + .5; high[i] = close[i] + .1; low[i] = close[i] - .2
    return open_, high, low, close, np.ones(n)


def test_local_engine_is_prefix_causal_and_records_unverified_pivot_tie_boundary():
    arrays = _local_market(); can = np.ones(len(arrays[0]), bool)
    full = line_events(*arrays, can_run=can, tick=.01)
    prefix = line_events(*(item[:97] for item in arrays), can_run=can[:97], tick=.01)
    assert np.array_equal(prefix.born_event, full.born_event[:97])
    assert np.array_equal(prefix.break_event, full.break_event[:97])
    assert full.lines[0]["kind"] == "local_touch"
    assert full.events[0]["break_i"] == 96
    assert full.trace["pivot_tie_status"] == "unverified_tradingview_native_semantics"


def test_cross_family_dedup_and_protected_capacity_use_merged_pool_rules():
    p = V104Params(); n = 600; high = close = body = np.zeros(n); atr = np.ones(n)
    pool, kinds = [], {}
    first = Line(10, 20., 40, 19., 100, 18., .2, 100, 0); first.score = .2
    assert _store_merged_sidecar(pool, first, kinds, 100, high, close, body, atr, .01, p, 2) == 1
    duplicate_local = Line(10, 20., 40, 19., 120, 17., .1, 120, 0); duplicate_local.score = .1
    # V12 deduplicates geometry across major/local families before capacity.
    assert _store_merged_sidecar(pool, duplicate_local, {**kinds, id(duplicate_local): 1}, 120, high, close, body, atr, .01, p, 2) == 0
    second = Line(120, 40., 150, 39., 200, 38., .2, 200, 0); second.score = .2
    kinds[id(second)] = 0
    assert _store_merged_sidecar(pool, second, kinds, 200, high, close, body, atr, .01, p, 2) == 1
    first.usable = True
    better = Line(220, 60., 250, 59., 300, 58., .01, 300, 0); better.score = .01
    kinds[id(better)] = 0
    # One protected V9/break line leaves no evictable worst member once the
    # other member is also made pending.
    second.spike = 201
    assert _store_merged_sidecar(pool, better, kinds, 300, high, close, body, atr, .01, p, 2) == 5


def test_capacity_replacement_uses_pine_remove_then_push_order():
    p = V104Params(); high = close = body = np.zeros(600); atr = np.ones(600)
    pool, kinds = [], {}
    def line(ax, score):
        item = Line(ax, 80. + ax, ax + 30, 79. + ax, ax + 80, 78. + ax, score, ax + 80, 0)
        item.score = score; kinds[id(item)] = 0
        return item
    first, second, incoming = line(10, .3), line(120, .2), line(230, .1)
    assert _store_merged_sidecar(pool, first, kinds, 90, high, close, body, atr, .01, p, 2) == 1
    assert _store_merged_sidecar(pool, second, kinds, 200, high, close, body, atr, .01, p, 2) == 1
    assert _store_merged_sidecar(pool, incoming, kinds, 310, high, close, body, atr, .01, p, 2) == 1
    assert pool == [second, incoming]


def test_chart_keeps_parallel_breaks_but_htf_emits_only_best_same_bar(monkeypatch):
    import yoyo.evaluation.spike_v126_engine as engine

    n = 30; o = h = lo = np.zeros(n); c = np.zeros(n); c[21:23] = 300; atr = np.ones(n)
    calls = {"pivot": 0}
    def fake_pivots(values, left, right, mode):
        out = np.full(n, -1, dtype=np.int64)
        if left == 12: out[20] = 10
        return out, 0, 0
    def fake_search(xs, ps, ats, source, i, low, close_i, atr, tick, p):
        if i != 20: return []
        one = Line(0, 100. + source * 100, 10, 90. + source * 100, 15, 80. + source * 100, .2 - .1 * source, i, source)
        one.score = .2 - .1 * source
        return [one]
    monkeypatch.setattr(engine, "pivots", fake_pivots); monkeypatch.setattr(engine, "_search", fake_search)
    chart = engine.line_events(o, h, lo, c, atr, can_run=np.ones(n, bool), tick=.01)
    htf = engine.line_events(o, h, lo, c, atr, can_run=np.ones(n, bool), tick=.01, htf=True)
    assert len(chart.events) == 2 and {event["source"] for event in chart.events} == {"raw", "soft"}
    assert len(htf.events) == len(htf.winner_events) == 1
    assert htf.events[0]["source"] == "soft"


def test_break_buffer_uses_raw_subtick_atr_not_tick_clamp(monkeypatch):
    import yoyo.evaluation.spike_v126_engine as engine
    n = 30; o = h = lo = np.zeros(n); c = np.zeros(n); atr = np.full(n, .01)
    c[21:23] = 79.01
    def fake_pivots(values, left, right, mode):
        out = np.full(n, -1, dtype=np.int64)
        if left == 12: out[20] = 10
        return out, 0, 0
    def fake_search(xs, ps, ats, source, i, low, close_i, atr, tick, p):
        if i == 20 and source == 0:
            item = Line(0, 100., 10, 90., 15, 80., .1, i, source); item.score = .1
            return [item]
        return []
    monkeypatch.setattr(engine, "pivots", fake_pivots); monkeypatch.setattr(engine, "_search", fake_search)
    result = engine.line_events(o, h, lo, c, atr, can_run=np.ones(n, bool), tick=.1)
    assert result.break_event[22]  # raw threshold: .20 * .01 = .002, not .02


def _event(uid, source="chart", score=.2, visible_i=2):
    record = {"uid": uid, "i": 2, "break_i": 2, "born_i": 0, "ax": 0, "ap": 10., "bx": 1, "bp": 9., "cx": 1, "cp": 9., "source": "raw", "kind": "three_major", "score": score, "visible_i": visible_i}
    if source == "htf": record.update({"ax_t": 0., "bx_t": 1., "cx_t": 1.})
    return record


def test_pairing_is_box_first_then_held_break_first_with_htf_samebar_precedence():
    close = np.full(8, 20.); times = np.arange(8, dtype=float)
    boxes = np.full(8, -1); boxes[2] = 2; boxes[5] = 5
    confirmed = np.zeros(8, bool); confirmed[5] = True
    chart = [_event(1, score=.3), _event(2, score=.1)]
    htf = [_event(9, "htf", score=.9)]
    # At box 2, the simultaneous HTF source wins.  The chart candidates are
    # consumed with that box and cannot leak into box 5.
    events = pair_events(close, bar_times=times, chart_breaks=chart, htf_breaks=htf,
                         box_id=boxes, confirmed_long=confirmed)
    assert [(event["joint_i"], event["source"], event["order"]) for event in events] == [(2, "htf", "box-first")]
    # A fresh break before a later confirmed V9 joins only after every close
    # was strictly above its frozen line.
    later = pair_events(close, bar_times=times, chart_breaks=[_event(3, visible_i=3)], htf_breaks=[],
                        box_id=boxes, confirmed_long=confirmed)
    assert [(event["joint_i"], event["order"], event["box_entry_i"]) for event in later] == [(5, "break-first", 5)]


def test_htf_hold_uses_chart_visible_clock_and_checks_fresh_close():
    close = np.full(5, 20.); times = np.arange(5, dtype=float)
    boxes = np.full(5, -1); boxes[3] = 3; confirmed = np.zeros(5, bool); confirmed[3] = True
    stale = _event(10, "htf", visible_i=1); stale["break_i"] = 99
    # Lifetime is from visible chart bar 1, not the incomparable HTF index 99.
    assert pair_events(close, bar_times=times, chart_breaks=[], htf_breaks=[stale], box_id=boxes,
                       confirmed_long=confirmed, life=1) == []

    below = _event(11, "htf", visible_i=1); below.update({"ap": 30., "bp": 30.})
    # No active box at visibility: below-line HTF evidence must never enter held state.
    assert pair_events(close, bar_times=times, chart_breaks=[], htf_breaks=[below], box_id=boxes,
                       confirmed_long=confirmed) == []
    immediate_boxes = np.full(5, -1); immediate_boxes[1] = 1
    # The existing-box path intentionally accepts the current break before the
    # held-line freshness filter, matching Pine's ordered branches.
    immediate = pair_events(close, bar_times=times, chart_breaks=[], htf_breaks=[below], box_id=immediate_boxes,
                            confirmed_long=np.zeros(5, bool))
    assert [(event["joint_i"], event["order"]) for event in immediate] == [(1, "box-first")]


def test_chart_and_htf_held_caps_are_independent():
    n = 27; close = np.full(n, 20.); times = np.arange(n, dtype=float)
    boxes = np.full(n, -1); boxes[25] = 25; confirmed = np.zeros(n, bool); confirmed[25] = True
    chart = [_event(1, visible_i=0)]
    chart[0].update({"ap": 10., "bp": 10.})
    htf = []
    for visible in range(1, 25):
        event = _event(100 + visible, "htf", visible_i=visible)
        # Above through bar 24, then rises past the chart close at the V9 bar.
        event.update({"ax_t": float(visible), "bx_t": 25., "cx_t": 25., "ap": 10., "bp": 20.1})
        htf.append(event)
    events = pair_events(close, bar_times=times, chart_breaks=chart, htf_breaks=htf,
                         box_id=boxes, confirmed_long=confirmed)
    # A combined 24-element cap would evict the old chart break. Pine keeps
    # independent chart/HTF arrays, then removes the crossed HTF holds.
    assert [(event["joint_i"], event["source"], event["uid"]) for event in events] == [(25, "chart", 1)]

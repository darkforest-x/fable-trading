"""Causal synthetic checks for the bounded SPIKE V12 local-touch reference."""

from __future__ import annotations

from collections import Counter

import numpy as np

from yoyo.evaluation.spike_v10_4 import Line, V104Params, _validate
from yoyo.evaluation.spike_v12_local_touch import (
    LocalTouchParams,
    _local_search,
    _store_local,
    auxiliary_touch_events,
)


def market(*, a_i: int = 20, b_i: int = 60, c_i: int = 92, n: int = 180):
    """Three descending peaks with deep valleys and a later line break."""

    x = np.arange(n)
    line = 120.0 + (110.0 - 120.0) * (x - a_i) / float(b_i - a_i)
    close = line - 6.0
    open_ = close.copy()
    high = line - 5.0
    low = line - 7.0
    close[:a_i] = 95.0
    open_[:a_i] = 95.0
    high[:a_i] = 96.0
    low[:a_i] = 94.0
    c_price = float(line[c_i])
    for idx, price in ((a_i, 120.0), (b_i, 110.0), (c_i, c_price)):
        high[idx] = price
        # Keep the capped soft price equal to raw at the three anchors so the
        # duplicate-track test exercises the original _same rule.
        close[idx] = price - 0.1
        open_[idx] = close[idx]
        low[idx] = price - 10.0
    low[a_i + 1 : b_i] = 100.0
    low[b_i + 1 : c_i] = 95.0
    # C is confirmed on c_i+2.  The two closes after birth c_i+2 are the
    # first allowed break confirmations.
    for idx in (c_i + 3, c_i + 4):
        close[idx] = line[idx] + 0.5
        open_[idx] = close[idx]
        high[idx] = close[idx] + 0.1
        low[idx] = close[idx] - 0.2
    atr = np.ones(n)
    return open_, high, low, close, atr


def run_market(*, a_i: int = 20, b_i: int = 60, c_i: int = 92, n: int = 180, **kwargs):
    arrays = market(a_i=a_i, b_i=b_i, c_i=c_i, n=n)
    return arrays, auxiliary_touch_events(*arrays, can_run=np.ones(n, bool), tick=0.01, **kwargs)


def test_shorter_local_c_is_accepted_even_when_not_a_major_pivot():
    # B=85 and C=92 leave only seven bars between the major rebound and the
    # local touch.  C is therefore shorter than the original 12/8 pivot while
    # B is known at the local decision bar (85+8 <= 92+2).
    arrays, result = run_market(b_i=85, c_i=92)
    _, high, _, _, _ = arrays
    # C=92 is a local 2/2 pivot, but it is not a major 12/8 pivot: B is inside
    # its twelve-bar left window.
    from yoyo.evaluation.spike_v10_4 import pivots

    assert pivots(high, 12, 8)[0][100] != 92
    assert result.born_event.tolist() == [i == 94 for i in range(len(high))]
    assert result.lines[0].cx == 92
    assert result.trace["lines"][0]["kind"] == "auxiliary_touch"
    assert result.trace["lines"][0]["track"] == "raw"


def test_b_unknown_at_local_decision_does_not_backfill_without_fresh_c():
    # B+8=88 while C+2=86.  The local event is therefore too early and the
    # later B confirmation cannot re-run the old C search.
    arrays, result = run_market(a_i=12, b_i=80, c_i=84, n=180)
    assert not result.born_event.any()
    assert not result.trace["lines"]
    assert any(row["kind"] == "local" and row["pivot_i"] == 84 for row in result.trace["pivot_events"])
    assert any(row["kind"] == "major" and row["pivot_i"] == 80 and row["i"] == 88
               for row in result.trace["pivot_events"])


def test_invalid_geometry_and_historical_validation_are_rejected():
    o, h, lo, c, atr = market()
    p = V104Params()
    lp = LocalTouchParams()
    # A/B are known by i=94, but C is above B, so the geometry search is empty.
    assert not _local_search([20, 60], [120.0, 110.0], [1.0, 1.0], cx=92, cp=111.0, ca=1.0,
                             source=0, i=94, low=lo, close_i=float(c[94]), atr=atr, tick=0.01,
                             p=p, local_params=lp)
    candidates = _local_search([20, 60], [120.0, 110.0], [1.0, 1.0], cx=92, cp=102.0, ca=1.0,
                               source=0, i=94, low=lo, close_i=float(c[94]), atr=atr, tick=0.01,
                               p=p, local_params=lp)
    assert candidates
    item = candidates[0]
    body = np.maximum(o, c)
    # Body validation is historical and includes every bar A..birth.
    body_bad = body.copy()
    body_bad[30] = item.at(30) + p.body_tol + 0.1
    high_bad = h.copy()
    high_bad[30] = body_bad[30]
    assert _validate(item, 94, high_bad, c, body_bad, atr, 0.01, p)[0] == 1
    # A fit outside the .35 ATR C tolerance is rejected before storage.
    assert not _local_search([20, 60], [120.0, 110.0], [1.0, 1.0], cx=92, cp=102.5, ca=1.0,
                             source=0, i=94, low=lo, close_i=float(c[94]), atr=atr, tick=0.01,
                             p=p, local_params=lp)


def test_birth_bar_cannot_break_and_two_later_closes_make_one_break():
    _, result = run_market()
    assert result.born_event[94]
    assert not result.break_event[94]
    assert result.break_event[96]
    assert result.events == [result.trace["events"][0]]
    assert result.events[0]["A"] == 20 and result.events[0]["B"] == 60 and result.events[0]["C"] == 92
    assert result.events[0]["born_i"] == 94 and result.events[0]["break_i"] == 96
    assert result.trace["lines"][0]["break_i"] == 96


def test_prefix_invariance_and_gap_reset():
    arrays, full = run_market()
    prefix = auxiliary_touch_events(*(x[:97] for x in arrays), can_run=np.ones(97, bool), tick=0.01)
    assert np.array_equal(prefix.born_event, full.born_event[:97])
    assert np.array_equal(prefix.break_event, full.break_event[:97])

    can = np.ones(len(arrays[0]), bool)
    can[95] = False
    gapped = auxiliary_touch_events(*arrays, can_run=can, tick=0.01)
    assert gapped.born_event[94]
    assert not gapped.break_event.any()
    assert 95 in gapped.trace["gaps"]


def test_local_confirmation_cannot_borrow_left_context_across_gap():
    arrays = market()
    can = np.ones(len(arrays[0]), bool)
    can[91] = False  # C=92 needs bars 90..94 for the local 2/2 pivot.
    result = auxiliary_touch_events(*arrays, can_run=can, tick=0.01)
    assert not result.born_event.any()
    assert not any(row["kind"] == "local" and row["pivot_i"] == 92
                   for row in result.trace["pivot_events"])


def test_raw_soft_duplicate_is_one_stored_line_and_one_break_event():
    _, result = run_market()
    # No wick in the synthetic peaks means raw and the .6 ATR capped track
    # describe the same candidate; original _same collapses the duplicate.
    assert len(result.trace["lines"]) == 1
    assert result.store_codes[0] >= 1
    assert result.break_event.sum() == 1
    assert len(result.events) == 1


def test_disabled_family_emits_no_auxiliary_outputs():
    arrays = market()
    result = auxiliary_touch_events(*arrays, can_run=np.ones(len(arrays[0]), bool), tick=0.01,
                                    params=LocalTouchParams(enabled=False))
    assert not result.born_event.any() and not result.break_event.any()
    assert result.lines == [] and result.events == [] and result.trace["lines"] == []


def test_pool_capacity_is_two_per_source_and_span_bucket():
    p = V104Params()
    atr = np.ones(1200)
    high = np.zeros(1200)
    close = np.zeros(1200)
    body = np.zeros(1200)
    pool = []
    serial = 0
    spans = (80, 200, 400)
    for source in (0, 1):
        for span in spans:
            for slot in range(2):
                ax = 10 + serial * 4
                bx = ax + 30
                cx = ax + span
                # Distinct intercepts keep this storage-only fixture from
                # being collapsed by the cross-track original _same rule.
                offset = 5.0 * source + 0.01 * span
                item = Line(ax, 20.0 + offset - slot, bx, 19.0 + offset - slot,
                            cx, 18.0 + offset - slot, 0.1 + slot,
                            cx, source)
                item.score = 0.1 + slot
                code = _store_local(pool, item, i=cx, high=high, close=close, body=body, atr=atr,
                                    tick=0.01, p=p, pool_cap=2)
                assert code == 1
                serial += 1
    assert len(pool) == 12
    extra = Line(1000, 20.0, 1030, 19.0, 1080, 18.0, 0.9, 1080, 0)
    extra.score = 99.0
    assert _store_local(pool, extra, i=1080, high=high, close=close, body=body, atr=atr,
                        tick=0.01, p=p, pool_cap=2) == 5
    assert len(pool) == 12
    buckets = Counter(
        (line.source, 0 if line.cx - line.ax < p.span * 2 else
         1 if line.cx - line.ax < p.span * 4 else 2)
        for line in pool
    )
    assert max(buckets.values()) == 2

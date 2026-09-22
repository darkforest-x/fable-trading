"""Behavioral checks for the V12.7 held-break age cap and its latency reference."""
import numpy as np

from yoyo.evaluation.spike_v126_engine import pair_events
from yoyo.evaluation.spike_v127_held_age import ARMS, HELD_LIFE, cross_start, line_value


def _event(uid, break_i, source="chart", visible_i=None, score=.2):
    record = {"uid": uid, "i": break_i, "break_i": break_i, "born_i": 0, "ax": 0, "ap": 10., "bx": 10, "bp": 9.,
              "cx": 10, "cp": 9., "source": "raw", "kind": "local_touch", "score": score,
              "visible_i": break_i if visible_i is None else visible_i}
    if source == "htf":
        record.update({"ax_t": 0., "bx_t": 10., "cx_t": 10.})
    return record


def _box_from(n, start):
    boxes = np.full(n, -1); boxes[start:] = start
    confirmed = np.zeros(n, bool); confirmed[start] = True
    return boxes, confirmed


def test_arms_differ_only_in_held_life():
    assert ARMS == ("baseline", "held_age_8")
    assert HELD_LIFE == {"baseline": 600, "held_age_8": 8}


def test_age_eight_pairs_and_age_nine_expires():
    n = 20; close = np.full(n, 20.); times = np.arange(n, dtype=float)
    for age, expected in ((8, [(10, "break-first")]), (9, [])):
        boxes, confirmed = _box_from(n, 10)
        events = pair_events(close, bar_times=times, chart_breaks=[_event(1, 10 - age)], htf_breaks=[],
                             box_id=boxes, confirmed_long=confirmed, life=8)
        assert [(e["joint_i"], e["order"]) for e in events] == expected


def test_expired_evidence_leaves_box_open_for_a_fresh_box_first_break():
    n = 40; close = np.full(n, 20.); times = np.arange(n, dtype=float)
    boxes, confirmed = _box_from(n, 30)
    chart = [_event(1, 5), _event(2, 33, score=.1)]
    old = pair_events(close, bar_times=times, chart_breaks=chart, htf_breaks=[], box_id=boxes,
                      confirmed_long=confirmed, life=600)
    capped = pair_events(close, bar_times=times, chart_breaks=chart, htf_breaks=[], box_id=boxes,
                         confirmed_long=confirmed, life=8)
    assert [(e["joint_i"], e["order"], e["uid"]) for e in old] == [(30, "break-first", 1)]
    assert [(e["joint_i"], e["order"], e["uid"]) for e in capped] == [(33, "box-first", 2)]


def test_htf_age_uses_chart_visible_clock_not_htf_index():
    n = 20; close = np.full(n, 20.); times = np.arange(n, dtype=float)
    boxes, confirmed = _box_from(n, 12)
    htf = _event(9, 999, "htf", visible_i=4)
    assert [(e["joint_i"], e["source"]) for e in pair_events(close, bar_times=times, chart_breaks=[], htf_breaks=[htf],
            box_id=boxes, confirmed_long=confirmed, life=8)] == [(12, "htf")]
    htf = _event(9, 999, "htf", visible_i=3)
    assert pair_events(close, bar_times=times, chart_breaks=[], htf_breaks=[htf], box_id=boxes,
                       confirmed_long=confirmed, life=8) == []


def test_baseline_life_matches_engine_default():
    n = 700; close = np.full(n, 20.); times = np.arange(n, dtype=float)
    boxes, confirmed = _box_from(n, 650)
    chart = [_event(1, 60)]
    chart[0].update({"ap": 10., "bp": 10.})  # flat line stays positive for 600 bars
    kwargs = dict(bar_times=times, chart_breaks=chart, htf_breaks=[], box_id=boxes, confirmed_long=confirmed)
    default = pair_events(close, **kwargs)
    assert len(default) == 1 and default == pair_events(close, life=HELD_LIFE["baseline"], **kwargs)


def test_cross_start_walks_back_through_the_uninterrupted_run_only():
    # Line from (0,10) to (10,9): 10 - 0.1x.  Closes: below until 4, above at 5,
    # below at 6, then above from 7 through the confirmation at 9.
    close = np.array([5, 5, 5, 5, 5, 11, 5, 11, 11, 11, 11], float)
    assert cross_start(close, 10., 9., 0, 10, 0, 9) == 7
    # Never walks to or before the line's birth bar.
    assert cross_start(np.full(11, 20.), 10., 9., 0, 10, 3, 9) == 4
    # Future closes after the confirmation are never read.
    changed = close.copy(); changed[10] = -1
    assert cross_start(changed, 10., 9., 0, 10, 0, 9) == 7
    assert line_value(10., 9., 0, 10, 5) == 9.5

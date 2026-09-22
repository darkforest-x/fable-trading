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


def test_default_rule_needs_all_three_non_inferiority_checks():
    import pandas as pd
    from yoyo.evaluation.spike_v127_held_age_report import default_rule
    rows = []
    for arm, full_r, later_r, n, gt5 in (("baseline", -.09, -.23, 1000, 25), ("held_age_8", -.08, -.22, 800, 20)):
        rows += [{"arm": arm, "period": "full", "mean_net_r": full_r, "n": n, "gt5_final_net_r": gt5},
                 {"arm": arm, "period": "later", "mean_net_r": later_r, "n": n // 2, "gt5_final_net_r": gt5 // 2}]
    assert default_rule(pd.DataFrame(rows))["default_on"]
    rows[3]["mean_net_r"] = -.24
    verdict = default_rule(pd.DataFrame(rows))
    assert not verdict["default_on"] and verdict["checks"] == {"mean_net_r_full": True, "mean_net_r_later": False, "gt5_rate_full": True}


def test_latency_measures_bars_from_cross_close_and_chase_in_r():
    import pandas as pd
    from yoyo.evaluation.spike_v127_held_age_report import latency
    decisions = pd.DataFrame([{"trade_key": "k", "arm": arm, "order": "break-first", "pair_source": "chart",
        "signal_close": "2025-01-01T03:00Z", "cross_close_time": "2025-01-01T01:00Z", "cross_close": 100.} for arm in ("baseline", "held_age_8")])
    trades = pd.DataFrame([{"trade_key": "k", "arm": arm, "order": "break-first", "signal_close": "2025-01-01T03:00Z",
        "censored": False, "entry_price": 104., "initial_stop": 96.} for arm in ("baseline", "held_age_8")])
    out = latency({"decisions": decisions, "trades": trades}, pd.Timestamp("2025-09-10", tz="UTC"))
    row = out.loc[out.arm.eq("baseline") & out.group.eq("all")].iloc[0]
    assert row.lag_bars_median == 8 and row.chase_r_median == .5 and row.missing_cross == 0

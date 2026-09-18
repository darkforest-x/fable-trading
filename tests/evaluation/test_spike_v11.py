"""Guards for the V11 higher-timeframe source (Pine spike_burst_v11.pine).

What must hold: with no higher-timeframe break the V10.4 result is untouched; a
higher break is placed on the first chart bar that opens at the higher bar's
close and is dropped if that chart bar is missing; a chart SPIKE pairs with it
only within 6 chart bars, on the later event's bar, and only if the line was
confirmed before the SPIKE; one SPIKE is used once; future bars never change a
past event.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v10_4 import V104Params, joint_events

from test_spike_v10_4 import default_facts, staircase


def rising(n=200):
    close = 100 + np.arange(n) * 0.1       # strictly rising: the chart engine finds no pivot high
    open_ = close - 0.05
    return open_, close + 0.02, open_ - 0.02, close, np.full(n, 0.5)


def htf_dict(n, known_at=(), born_t=0.0, line=50.0):
    h = {k: np.full(n, np.nan) for k in ("ax_t", "ap", "bx_t", "bp", "born_t")}
    h["known"] = np.zeros(n, bool)
    for j in known_at:
        h["known"][j] = True
        h["ax_t"][j], h["ap"][j], h["bx_t"][j], h["bp"][j], h["born_t"][j] = 0.0, line, 10.0, line, born_t
    h["bar_t"] = np.arange(n, dtype=float)
    h["gap"] = np.zeros(n, bool)
    return h


def run(n, spikes, known_at, born_t=0.0, **over):
    o, h, lo, c, a = rising(n)
    confirmed = np.zeros(n, bool)
    confirmed[list(spikes)] = True
    facts = default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9), **over)
    return joint_events(o, h, lo, c, a, tick=0.01, htf=htf_dict(n, known_at, born_t), **facts)


def test_no_higher_break_leaves_v10_4_untouched():
    o, h, lo, c, a = staircase(periods=2400, seed=5)
    n = len(c)
    confirmed = np.zeros(n, bool); confirmed[np.arange(300, n, 7)] = True
    facts = default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9))
    plain = joint_events(o, h, lo, c, a, tick=0.01, **facts)
    with_htf = joint_events(o, h, lo, c, a, tick=0.01, htf=htf_dict(n), **facts)
    assert np.array_equal(plain.joint_event, with_htf.joint_event) and plain.joints == with_htf.joints
    assert not with_htf.htf_joint_event.any()


@pytest.mark.parametrize("spike,known,expected", [(100, 100, 100), (100, 104, 104), (100, 106, 106),
                                                  (100, 107, None), (104, 100, 104), (106, 100, 106), (107, 100, None)])
def test_pairs_within_six_chart_bars_on_the_later_event(spike, known, expected):
    r = run(200, [spike], [known])
    fired = np.flatnonzero(r.htf_joint_event).tolist()
    assert fired == ([] if expected is None else [expected])


def test_line_must_be_confirmed_before_the_spike():
    assert np.flatnonzero(run(200, [100], [103], born_t=99.0).htf_joint_event).tolist() == [103]
    assert not run(200, [100], [103], born_t=101.0).htf_joint_event.any()


def test_second_bar_gate_refuses():
    gate = np.ones(200, bool); gate[103] = False
    r = run(200, [100], [103], current_gate=gate)
    assert not r.htf_joint_event.any() and r.htf_refusals == [{"i": 103, "reason": "gate"}]


def test_one_spike_is_used_once_across_breaks():
    r = run(200, [100], [101, 103])
    assert np.flatnonzero(r.htf_joint_event).tolist() == [101]


def test_chart_source_off_suppresses_only_chart_joints():
    o, h, lo, c, a = staircase(periods=2400, seed=5)
    n = len(c)
    confirmed = np.zeros(n, bool); confirmed[np.arange(300, n, 7)] = True
    facts = default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9))
    on = joint_events(o, h, lo, c, a, tick=0.01, **facts)
    off = joint_events(o, h, lo, c, a, tick=0.01, use_chart=False, **facts)
    assert on.joint_event.any() and not off.joint_event.any()
    assert np.array_equal(on.break_event, off.break_event)


def test_no_future_bar_changes_a_past_higher_joint():
    full = run(300, [100, 180], [103, 176])
    for cut in (104, 150, 177, 200):
        o, h, lo, c, a = (x[:cut] for x in rising(300))
        confirmed = np.zeros(cut, bool)
        confirmed[[i for i in (100, 180) if i < cut]] = True
        facts = default_facts(cut, confirmed_long=confirmed, parent_high=np.full(cut, -1e9))
        part = joint_events(o, h, lo, c, a, tick=0.01, htf=htf_dict(cut, [k for k in (103, 176) if k < cut]), **facts)
        assert np.array_equal(part.htf_joint_event, full.htf_joint_event[:cut])


def test_higher_break_is_placed_on_the_first_chart_bar_after_its_close(monkeypatch):
    chart = pd.date_range("2025-01-01", periods=40, freq="15min", tz="UTC")
    htf = pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0},
                       index=pd.date_range("2024-12-31", periods=40, freq="h", tz="UTC"))
    record = {"i": 25, "ax": 2, "ap": 2.0, "bx": 10, "bp": 1.5, "cx": 20, "cp": 1.2, "born_i": 21, "source": 0,
              "score": 0.1}
    monkeypatch.setattr(v11, "htf_breaks", lambda *a, **k: [record])
    out, placed = v11.htf_inputs(chart, 15, htf, 60, 0.01, V104Params())
    close_of_break = htf.index[25] + pd.Timedelta(hours=1)          # 2025-01-01 02:00
    assert chart[np.flatnonzero(out["known"])[0]] == close_of_break
    assert out["born_t"][np.flatnonzero(out["known"])[0]] == (htf.index[21] + pd.Timedelta(hours=1)).value // 60_000_000_000
    missing = chart.delete(8)                                          # the 02:00 chart bar is absent
    out2, placed2 = v11.htf_inputs(missing, 15, htf, 60, 0.01, V104Params())
    assert not out2["known"].any() and placed2[0]["visible"] is False

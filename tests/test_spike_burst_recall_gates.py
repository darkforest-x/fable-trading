"""Small synthetic decomposition tests; no market outcomes or threshold search."""
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_recall_gates as gates


def fixture():
    index = pd.date_range("2026-08-19T12:00Z", periods=5, freq="h")
    f = pd.DataFrame(dict(open=102., high=103.1, low=102., close=103., atr=1., md=.5, sb=.1,
        ropeHigh=102.5, pastWidth=1., pastCrosses=3., prog_dense_hits=1., prog_prior_high=102.,
        prog_advance=1.5, prog_volume_base=100., prog_volume_ratio=1.5, prog_efficiency=.55,
        prog_close_position=.65, prog_recent_dense=True, ready=True, prog_up=True), index=index)
    f["middle"] = np.arange(5) + 100.
    f.loc[index[0], "prog_up"] = False
    s = pd.DataFrame(dict(burst=False, route="", trend_side=0, exit=False, entry_bar=np.nan,
        entry_ref=np.nan, initial_stop=np.nan, risk_valid=False, protection=np.nan, state="waiting"), index=index)
    return f, s


def test_threshold_equality_and_first_middle_are_exact():
    f, s = fixture()
    out = gates.gate_table(f, s)
    assert not out.all_criteria.iloc[0]
    assert out.all_criteria.iloc[1:].all()
    assert out.pass_advance_3bar.all() and out.pass_efficiency_3bar.all()
    f.loc[f.index[2], "prog_volume_ratio"] = 1.499999
    f.loc[f.index[2], "prog_up"] = False
    out = gates.gate_table(f, s)
    assert out.failed_criteria.iloc[2] == "volume_3bar"


def test_hold_exit_and_hard_route_priority_are_distinct():
    f, s = fixture()
    s.loc[s.index[0]:s.index[1], "trend_side"] = 1
    s.loc[s.index[2], "exit"] = True
    s.loc[s.index[3], ["burst", "route", "trend_side"]] = [True, "price_first", 1]
    out = gates.gate_table(f, s)
    assert not out.pass_no_existing_hold.iloc[1]
    assert out.pass_not_exit_bar.iloc[1]
    assert out.pass_no_existing_hold.iloc[2] and not out.pass_not_exit_bar.iloc[2]
    assert not out.pass_no_v1_priority.iloc[3]
    assert not out.progressive_eligible.iloc[1:4].any()


def test_nonexclusive_two_bar_failure_counts_are_not_summed_events():
    f, s = fixture()
    f.loc[f.index[1], ["prog_volume_ratio", "prog_efficiency"]] = [1., .4]
    f.loc[f.index[2], "prog_efficiency"] = .4
    f.loc[f.index[[1, 2]], "prog_up"] = False
    table = gates.gate_table(f, s)
    labels = pd.DataFrame([dict(instrument="x", event_i=1, in_study=True, label="positive", label_id="one",
                               decision_time=f.index[1] + pd.Timedelta(hours=1))])
    detection = pd.DataFrame([dict(instrument="x", event_i=1, arm="v2", hit_1=False,
                                  already_tracking=False, first_signal_lag=3.)])
    rows, count = gates.miss_records(labels, detection, table, "x", "X")
    assert count["positive_events"] == count["misses_1"] == 1
    assert rows[0]["fail_t_volume_3bar"] and not rows[0]["fail_both_volume_3bar"]
    assert rows[0]["fail_both_efficiency_3bar"]
    summary = gates.summarize_misses(pd.DataFrame(rows))
    group = summary.loc[summary.period.eq("full") & summary.cohort.eq("all")]
    assert group.missed_events.eq(1).all()
    assert group.loc[group.gate.eq("efficiency_3bar"), "fail_both"].iloc[0] == 1
    assert group.loc[group.gate.eq("volume_3bar"), "fail_both"].iloc[0] == 0
    assert not group.mutually_exclusive.any()


def test_target_open_and_earlier_tracking_entry_keep_their_real_confirmation():
    f, s = fixture()
    s.loc[s.index[0], ["burst", "route"]] = [True, "progressive"]
    s["trend_side"], s["entry_bar"] = 1, 0.
    table = gates.gate_table(f, s)
    rows, context = gates.case_rows(table, s, "HYPE", "x")
    target = next(row for row in rows if "target_open_0819_2200" in row["observation_roles"])
    assert target["bar_open"].hour == 14 and target["confirmed_at"].hour == 15
    assert target["active_entry_confirmed_at"].hour == 13
    assert not target["v2_burst"]
    assert context["first_entry_inside_night"].hour == 13

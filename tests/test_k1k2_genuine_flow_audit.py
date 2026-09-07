"""Synthetic no-outcome roster projection and independent event-clock checks."""
import pandas as pd
import pytest

from yoyo.evaluation.k1k2_genuine_flow_audit import MOTHER, REQUEST, STATUS, make_windows, project


def rosters():
    h = pd.Timestamp("2023-01-01T01:00:00Z")
    m = pd.DataFrame([dict(event_id="one", signal_time=h, decision_time=h+pd.Timedelta(hours=1), direction=1, fold="2023H1"),
                      dict(event_id="two", signal_time=h+pd.Timedelta(hours=10), decision_time=h+pd.Timedelta(hours=11), direction=-1, fold="2023H1")])
    r = pd.DataFrame([dict(event_id="one", direction=1, fold="2023H1", mother_signal_time=h,
        mother_decision_time=h+pd.Timedelta(hours=1), k2_time=h+pd.Timedelta(hours=2),
        decision_time=h+pd.Timedelta(hours=3), wait_hours=2)])
    s = pd.DataFrame([dict(event_id="one", mother_signal_time=h, mother_decision_time=h+pd.Timedelta(hours=1),
        terminal_time=h+pd.Timedelta(hours=3), status="request_emitted", k2_time=h+pd.Timedelta(hours=2)),
        dict(event_id="two", mother_signal_time=h+pd.Timedelta(hours=10), mother_decision_time=h+pd.Timedelta(hours=11),
        terminal_time=h+pd.Timedelta(hours=12), status="invalidated_ma_colour", k2_time=pd.NaT)])
    return m, r, s


def test_all_mothers_k1_and_emitted_k2_have_separate_clocks():
    m, r, s = rosters()
    saved = [x.copy(deep=True) for x in [m, r, s]]
    w = make_windows(m, r, s, "case")
    assert len(w) == 4
    assert list(w.window_kind) == ["k1", "k1", "k2", "post_k1_through_k2"]
    assert list(w.loc[w.window_kind.eq("k1"), "event_id"]) == ["one", "two"]
    assert (w.window_end == w.decision_time).all()
    assert w.iloc[3].window_start == r.iloc[0].mother_decision_time
    assert w.iloc[3].window_end == r.iloc[0].decision_time
    assert not {"status", "terminal_time", "k2_time", "wait_hours"}.intersection(w)
    for original, before in zip([m, r, s], saved):
        pd.testing.assert_frame_equal(original, before)


def test_future_wait_termination_never_selects_k1_windows():
    m, r, s = rosters()
    before = make_windows(m, r, s, "case")
    s.loc[1, "status"] = "expired_no_k2"
    s.loc[1, "terminal_time"] += pd.Timedelta(hours=6)
    after = make_windows(m, r, s, "case")
    pd.testing.assert_frame_equal(before, after)


def test_projection_does_not_load_ohlc_or_outcomes(tmp_path):
    p = tmp_path / "roster.csv"
    # Synthetic forbidden column sentinels must not materialize in projection.
    pd.DataFrame([dict(event_id="one", signal_time="2023-01-01T01:00:00Z",
        decision_time="2023-01-01T02:00:00Z", direction=1, fold="2023H1",
        net_return="FORBIDDEN", signal_open="FORBIDDEN")]).to_csv(p, index=False)
    f = project(p, MOTHER)
    assert list(f) == MOTHER
    assert "FORBIDDEN" not in f.to_string()
    assert not set(MOTHER+REQUEST+STATUS).intersection({"open", "signal_open", "net_return", "mfe", "outcome"})


@pytest.mark.parametrize("change", ["duplicate_mother", "missing_status", "missing_request", "bad_status",
    "bad_k1_close", "bad_request_close", "bad_gap", "bad_direction", "bad_fold", "bad_status_clock",
    "wrong_k2_status_clock", "wrong_terminal", "out_of_scope", "bad_cohort"])
def test_roster_contract_fails_closed(change):
    m, r, s = rosters()
    cohort = "case"
    if change == "duplicate_mother": m = pd.concat([m,m.iloc[:1]])
    elif change == "missing_status": s = s.iloc[:1]
    elif change == "missing_request": r = r.iloc[:0]
    elif change == "bad_status": s.loc[1,"status"] = "WINNER_ONLY"
    elif change == "bad_k1_close": m.loc[0,"decision_time"] += pd.Timedelta(hours=1)
    elif change == "bad_request_close": r.loc[0,"decision_time"] += pd.Timedelta(hours=1)
    elif change == "bad_gap": r.loc[0,"wait_hours"] = 3
    elif change == "bad_direction": r.loc[0,"direction"] = -1
    elif change == "bad_fold": r.loc[0,"fold"] = "2024H2"
    elif change == "bad_status_clock": s.loc[1,"mother_signal_time"] += pd.Timedelta(hours=1)
    elif change == "wrong_k2_status_clock": s.loc[0,"k2_time"] += pd.Timedelta(hours=1)
    elif change == "wrong_terminal": s.loc[0,"terminal_time"] += pd.Timedelta(hours=1)
    elif change == "out_of_scope": m.loc[0,"signal_time"] = pd.Timestamp("2022-12-31T23:00:00Z"); m.loc[0,"decision_time"] = pd.Timestamp("2023-01-01T00:00:00Z")
    elif change == "bad_cohort": cohort = "winner"
    with pytest.raises(ValueError): make_windows(m, r, s, cohort)


def test_no_emitted_request_retains_all_mothers():
    m, r, s = rosters()
    r = r.iloc[:0]
    s.loc[0,"status"] = "data_gap"
    s.loc[0,"k2_time"] = pd.NaT
    w = make_windows(m, r, s, "control")
    assert len(w) == len(m)
    assert w.window_kind.eq("k1").all()
    assert w.window_id.str.startswith("control:").all()

"""Synthetic mother-intention accounting; no market archive is read."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_k2_research import (
    compare_episodes, describe, direct_requests, episode_ledger,
    matched_episodes, simulate_requests, single_pending_ledger,
)


def mothers(n=3):
    times = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"event_id": [f"m{i}" for i in range(n)], "decision_time": times,
                         "signal_time": times-pd.Timedelta(hours=1), "fold": "2023H1",
                         "direction": 1, "initial_stop": 90., "signal_atr": 5.})


def trade(event_id, entry, *, net=.01, closed=True, outcome="colour_exit"):
    return {"event_id": event_id, "entry_time": entry, "exit_time": entry+pd.Timedelta(hours=2),
            "closed": closed, "outcome": outcome, "net_return": net}


def test_observed_nonentry_zero_missing_path_unknown():
    m = mothers()
    _, s = direct_requests(m)
    s.loc[1, "status"] = "expired_no_k2"
    s.loc[2, "status"] = "data_gap"
    e = episode_ledger(m, s, pd.DataFrame([trade("m0", m.iloc[0].decision_time)]))
    assert list(e.observed) == [True, True, False]
    assert e.episode_net_return.iloc[:2].tolist() == [.01, 0]
    assert pd.isna(e.episode_net_return.iloc[2])
    assert e.occupied_until.iloc[2] == e.mother_deadline.iloc[2]


def test_invalid_risk_zero_but_entry_missing_is_unknown():
    m = mothers(2)
    _, s = direct_requests(m)
    t = pd.DataFrame([trade("m0", m.iloc[0].decision_time, net=np.nan, closed=False, outcome="entry_invalid_risk"),
                      trade("m1", m.iloc[1].decision_time, net=np.nan, closed=False, outcome="entry_missing")])
    e = episode_ledger(m, s, t)
    assert e.episode_net_return.iloc[0] == 0
    assert pd.isna(e.episode_net_return.iloc[1])
    assert not e.executed.any()


def test_all_mothers_and_emitted_requests_must_be_accounted():
    m = mothers()
    _, s = direct_requests(m)
    with pytest.raises(ValueError, match="Every original"):
        episode_ledger(m, s.iloc[:2], pd.DataFrame())
    with pytest.raises(ValueError, match="Emitted requests"):
        episode_ledger(m, s, pd.DataFrame())


def test_pending_blocks_before_entry_and_releases_on_terminal_equality():
    m = mothers(4)
    _, s = direct_requests(m)
    s["status"] = "expired_no_k2"
    s.loc[0, "terminal_time"] = m.iloc[2].decision_time
    e = episode_ledger(m, s, pd.DataFrame())
    serial = single_pending_ledger(e)
    assert serial.portfolio_selected.tolist() == [True, False, True, True]


def test_unknown_episode_locks_through_maternal_deadline():
    m = mothers(2)
    _, s = direct_requests(m)
    s["status"] = "data_gap"
    e = episode_ledger(m, s, pd.DataFrame())
    assert single_pending_ledger(e).portfolio_selected.tolist() == [True, False]


def test_matched_nonentries_remain_in_all_three_control_mean():
    m = mothers(1)
    _, s = direct_requests(m)
    e = episode_ledger(m, s, pd.DataFrame([trade("m0", m.iloc[0].decision_time)]))
    c = pd.concat([e.assign(event_id=f"c{i}", parent_event_id="m0", episode_net_return=v) for i,v in enumerate([0,0,-.003])])
    p, summary = matched_episodes(e, c)
    assert p.control_mean_return.iloc[0] == pytest.approx(-.001)
    assert summary["coverage"] == 1
    assert summary["mean_excess_bp"] == pytest.approx(110)
    c.iloc[0, c.columns.get_loc("observed")] = False
    c.iloc[0, c.columns.get_loc("episode_net_return")] = np.nan
    _, incomplete = matched_episodes(e, c)
    assert incomplete["assignment_coverage"] == 1
    assert incomplete["coverage"] == 0


def test_compare_counts_missed_realised_winners_not_success_survivors():
    m = mothers(2)
    _, s = direct_requests(m)
    a = episode_ledger(m, s, pd.DataFrame([trade("m0", m.iloc[0].decision_time, net=.02),
                                          trade("m1", m.iloc[1].decision_time, net=-.01)]))
    s["status"] = "expired_no_k2"
    b = episode_ledger(m, s, pd.DataFrame())
    _, diff = compare_episodes(a, b)
    assert diff["n"] == 2
    assert diff["mean_bp"] == pytest.approx(-50)
    assert diff["missed_net_winners"] == diff["avoided_net_losers"] == 1
    assert diff["missed_winner_total_bp"] == 200


def test_month_cluster_ci_deterministic_and_zero_preserved():
    x = pd.Series([0, .01, -.01, .02, 0, -.02, .01, 0])
    times = pd.Series(pd.date_range("2023-01-01", periods=8, freq="MS", tz="UTC"))
    a, b = describe(x, times, draws=1000), describe(x, times, draws=1000)
    assert a == b
    assert a["n"] == 8 and a["zero_fraction"] == 3/8
    assert a["ci95_bp"][0] < a["mean_bp"] < a["ci95_bp"][1]


def test_simulation_horizon_shortens_by_delay(monkeypatch):
    m = mothers(1)
    entries, _ = direct_requests(m)
    entries["wait_hours"] = 8
    entries["decision_time"] += pd.Timedelta(hours=8)
    observed = []
    class Study:
        folds = [("2023H1", "2023-01-01", "2023-07-01")]
        raw = pd.DataFrame()
        config = {"execution": {"max_hours": 72, "cost_fraction": .002}}
        def featured(self, *args):
            return pd.DataFrame()
    def fake(raw, management, part, policy, **kwargs):
        observed.append(policy["max_hours"])
        return part
    monkeypatch.setattr("yoyo.evaluation.hourly_impulse_k2_research.simulate_events", fake)
    simulate_requests(Study(), entries, {})
    assert observed == [64]
    entries["mother_deadline"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="absolute horizon"):
        simulate_requests(Study(), entries, {})

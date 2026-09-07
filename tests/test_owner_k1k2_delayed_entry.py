"""Synthetic V39 request accounting tests; no market files or saved outcomes.

Finite permutation oracle checks row-order invariance and fee decomposition;
unknown requests and original three-control groups cannot become survivors-only.
"""
from itertools import permutations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import owner_k1k2_delayed_entry as m


def sample(ids=("a", "b", "c")):
    t = pd.Timestamp("2023-02-01T10:00:00Z")
    return pd.DataFrame([dict(event_id=i, decision_time=t, fold="2023H1",
        direction=1, initial_stop=98., signal_atr=1., request_known=True,
        request_gross=g, request_net=g-.002, gross_return=g, net_return=g-.002,
        outcome="colour_exit", entry_time=t, entry_price=100.,
        exit_time=t+pd.Timedelta(minutes=30), hold_minutes=30., closed=True)
        for i, g in zip(ids, [.01, -.005, .001])])


def nofill(frame, index=0):
    frame.loc[index, ["request_net", "request_gross"]] = 0.
    frame.loc[index, ["net_return", "gross_return", "entry_price", "hold_minutes"]] = np.nan
    frame.loc[index, ["entry_time", "exit_time"]] = pd.NaT
    frame.loc[index, "closed"] = False
    frame.loc[index, "outcome"] = "no_fill_expired"
    return frame


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_comparison_invariant_to_order_with_cash_and_delayed_fill(order):
    a = sample(); b = nofill(a.copy())
    b.loc[1, "entry_time"] += pd.Timedelta(minutes=5)
    b.loc[1, "request_gross"] += .003
    b.loc[1, "request_net"] += .003
    got = m.compare(a.iloc[list(order)], b.iloc[list(reversed(order))])
    wanted = m.compare(a, b)
    pd.testing.assert_frame_equal(got, wanted)
    np.testing.assert_allclose(got.delta_saved_cost, [.002, 0., 0.], atol=1e-15)
    assert got.diagnostic_group.tolist() == ["foregone_request", "delayed_fill", "same_or_unknown_entry"]


@pytest.mark.parametrize("column,value", [("event_id", "z"), ("direction", -1),
    ("initial_stop", 97.), ("signal_atr", 2.), ("fold", "2024H1"),
    ("decision_time", pd.Timestamp("2023-02-01T11:00:00Z"))])
def test_comparison_rejects_original_request_drift(column, value):
    a = sample(); b = a.copy(); b.loc[0, column] = value
    with pytest.raises((ValueError, AssertionError)):
        m.compare(a, b)


def test_unknown_is_not_no_fill_or_known_request_mean():
    a = sample(); b = a.copy()
    b.loc[0, ["request_net", "request_gross", "net_return", "gross_return"]] = np.nan
    b.loc[0, ["request_known", "closed"]] = False
    b.loc[0, "outcome"] = "censored_missing_source"
    got = m.compare(a, b)
    assert not got.known.iloc[0] and pd.isna(got.delta_net.iloc[0])
    stats = m.metrics(b)
    assert stats["requests"] == 3 and stats["unknown"] == 1
    assert stats["mean_request_net_bp"] is None
    assert stats["executed_closed"] == 2 and stats["known_no_fill"] == 0


def test_trade_metrics_do_not_count_cash_as_closed_or_a_win():
    b = nofill(sample())
    stats = m.metrics(b)
    assert stats["requests"] == 3 and stats["known"] == 3
    assert stats["executed_closed"] == 2 and stats["known_no_fill"] == 1
    assert stats["trade_win_rate"] == 0.
    assert stats["mean_request_net_bp"] == pytest.approx(-80/3)
    assert stats["mean_trade_net_bp"] == pytest.approx(-40)


def test_baseline_invalid_risk_placeholder_is_not_an_execution():
    b = sample(); b.loc[0, "closed"] = False
    b.loc[0, "outcome"] = "entry_invalid_risk"
    b.loc[0, ["request_net", "request_gross"]] = 0.
    b.loc[0, ["net_return", "gross_return"]] = np.nan
    stats = m.metrics(b)
    assert stats["known_no_fill"] == 1 and stats["executed_closed"] == 2


def test_empty_selected_ledger_has_no_invented_metrics():
    stats = m.metrics(sample().iloc[:0])
    assert stats["requests"] == stats["executed_closed"] == 0
    assert stats["mean_request_net_bp"] is None and stats["trade_pf"] is None


def matched():
    a = sample(("a",)); b = nofill(a.copy())
    ca = sample(("x", "y", "z")); cb = nofill(ca.copy(), 1)
    assignments = pd.DataFrame(dict(event_id=["a"], match_status=["matched"]))
    requests = pd.DataFrame(dict(event_id=["x", "y", "z"], parent_event_id=["a"]*3))
    return m.compare(a, b), m.compare(ca, cb), assignments, requests


def test_pairs_keep_cash_controls_and_exact_three_member_denominator():
    c, r, assignments, requests = matched()
    p = m.pairs(c, r, assignments, requests)
    assert len(p) == 1 and p.complete_pair.all() and p.controls.iloc[0] == 3
    assert p.delta_excess_net.iloc[0] == pytest.approx(-.008 - .007/3)
    assert p.delta_excess_saved_cost.iloc[0] == pytest.approx(.002-.002/3)


@pytest.mark.parametrize("member", ["case", "control"])
def test_unknown_member_nulls_entire_original_pair(member):
    c, r, assignments, requests = matched()
    (c if member == "case" else r).loc[0, "known"] = False
    p = m.pairs(c, r, assignments, requests)
    assert not p.complete_pair.iloc[0]
    assert p.filter(regex="excess").isna().all().all()


def test_missing_control_cannot_silently_reweight_survivors():
    c, r, assignments, requests = matched()
    with pytest.raises(ValueError, match="three-control"):
        m.pairs(c, r.iloc[:2], assignments, requests)


def test_body_ratio_reference_uses_all_known_requests_including_cash():
    b = nofill(sample()); b["body_ratio"] = [.9, .6, .5]
    rank = b.copy(); rank["closed"] = rank.request_known
    rank["net_return"] = rank.request_net; rank["gross_return"] = rank.request_gross
    stats = m.rank_diagnostics(rank, "body_ratio")
    assert stats["n"] == 3 and stats["top_decile_n"] == 1
    assert stats["top_decile_net_bp"] == 0.


def test_unknown_pair_cannot_get_a_significance_test():
    c, r, assignments, requests = matched(); r.loc[0, "known"] = False
    p = m.pairs(c, r, assignments, requests)
    assert m.month_inference(p, "delta_excess_net")["status"] == "unknown_pairs"


def test_diagnostic_group_unknown_does_not_compare_different_denominators():
    a = sample(); b = a.copy()
    b.loc[0, ["request_net", "request_gross"]] = np.nan
    b.loc[0, "request_known"] = False
    b.loc[1, ["request_net", "request_gross"]] += .003
    result = m.diagnostic_groups(m.compare(a, b), "case")[0]
    assert result["n"] == 3 and result["known"] == 2 and result["unknown"] == 1
    assert all(result[x] is None for x in ["immediate_net_bp", "delayed_net_bp", "delta_net_bp", "saved_cost_bp", "lost_winners"])


def test_complete_diagnostic_group_decomposition_reconciles():
    a = sample(); b = nofill(a.copy())
    for row in m.diagnostic_groups(m.compare(a, b), "case"):
        assert row["unknown"] == 0
        assert row["delta_net_bp"] == pytest.approx(row["delayed_net_bp"]-row["immediate_net_bp"])
        assert row["delta_net_bp"] == pytest.approx(row["delta_gross_bp"]+row["saved_cost_bp"])

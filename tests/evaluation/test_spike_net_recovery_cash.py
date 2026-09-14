"""Synthetic debt/capacity checks; no market data or historical output reads."""
import pytest

from yoyo.evaluation.spike_net_recovery_cash import simulate_recovery


def trades(values, reasons=None, fractions=None):
    reasons = reasons or ["initial_stop" if n < 0 else "take_profit" for n in values]
    fractions = fractions or [.01] * len(values)
    return [dict(signal_i=i*3, entry_i=i*3+1, exit_i=i*3+2,
                 entry_time=f"t{i}", exit_time=f"x{i}", side=1,
                 exit_at_open=False, initial_risk_frac=f, net_r=n,
                 gross_r=n+.002/f, cost_r=.002/f, exit_reason=r,
                 censored=False) for i, (n,r,f) in enumerate(zip(values,reasons,fractions))]


def test_be_keeps_debt_and_risk_until_recovery():
    rows=trades([-1,-1,0,1,1], ["initial_stop","initial_stop","cost_be","take_profit","take_profit"])
    r=simulate_recovery(rows,leverage_cap=None)
    assert [x["risk"] for x in r["ledger"]] == [1,2,4,4,1]
    assert r["ledger"][2]["cycle_net_after"] == -3
    assert r["ledger"][2]["cycle_end"] is None
    assert r["summary"]["max_net_loss_streak"] == 2
    assert r["summary"]["n_net_be"] == 1


def test_positive_trade_does_not_forgive_fee_debt():
    r=simulate_recovery(trades([-1.6,-1.6,1,1,1]),leverage_cap=None)
    assert [x["risk"] for x in r["ledger"]] == [1,2,4,4,1]
    assert r["ledger"][2]["cycle_net_after"] == pytest.approx(-.8)
    assert r["ledger"][2]["cycle_end"] is None


def test_no_artificial_six_loss_reset_and_nine_loss_cash_failure():
    r=simulate_recovery(trades([-1]*12, fractions=[1.]*12),leverage_cap=None)
    s=r["summary"]
    assert s["n_accepted"] == 9
    assert s["final_balance"] == 489
    assert s["halt_risk"] == 512
    assert s["residual_debt"] == 511
    assert s["max_net_loss_streak"] == 9
    assert s["recovered_cycles"] == 0


def test_margin_rejection_keeps_debt_and_can_accept_later_signal():
    r=simulate_recovery(trades([-1,1,1],fractions=[.5,.001,.5]),initial_balance=10,leverage_cap=10)
    assert [x["accepted"] for x in r["ledger"]] == [True,False,True]
    assert r["ledger"][1]["reason"] == "capacity_wait"
    assert r["ledger"][2]["risk"] == 2
    assert r["ledger"][2]["cycle_net_before"] == -1
    assert r["summary"]["final_balance"] == 11


def test_capacity_reserve_is_not_a_second_fee():
    r=simulate_recovery(trades([.7]),leverage_cap=None)
    assert r["summary"]["final_balance"] == pytest.approx(1000.7)


def test_tick_residue_counted_as_be_not_profit_but_retained_in_cash():
    r=simulate_recovery(trades([-1,.001], ["initial_stop","cost_be"]),leverage_cap=None)
    assert r["summary"]["n_profit"] == 0
    assert r["summary"]["n_net_be"] == 1
    assert r["summary"]["final_balance"] == pytest.approx(999.002)
    assert r["summary"]["residual_debt"] == pytest.approx(.998)


def test_gap_through_cost_protection_is_a_real_loss():
    r=simulate_recovery(trades([-.4,1], ["cost_be_gap","take_profit"]),leverage_cap=None)
    assert r["ledger"][1]["risk"] == 2
    assert r["summary"]["n_net_be"] == 0
    assert r["summary"]["n_net_loss"] == 1


def test_boundary_is_marked_but_does_not_reset_cycle():
    rows=trades([-1,1]);rows[1]["censored"]=True
    r=simulate_recovery(rows,leverage_cap=None)
    assert r["summary"]["n_natural"] == 1
    assert r["summary"]["recovered_cycles"] == 0
    assert r["summary"]["n_boundary"] == 1


def test_fixed_reference_stays_at_one_despite_debt():
    r=simulate_recovery(trades([-1]*8+[1]),schedule="fixed",leverage_cap=None)
    assert all(x["risk"]==1 for x in r["ledger"])
    assert r["summary"]["max_unrecovered_loss_events"] == 8

"""Slot sizing simulators start from exactly 1000 USDT (regression for the 2026-10-27 starting-capital error)."""
import pandas as pd
import pytest

from yoyo.evaluation import monday_limit_spike_opt as so
from yoyo.evaluation import monday_limit_v3_slots as v3s

H = 3_600_000_000_000


def trades(*rows):
    """rows of (entry hour, exit hour, net bp, MAE percent)."""
    return pd.DataFrame([dict(entry_ns=a*H, exit_ns=b*H, net=n, mae_pct=m) for a, b, n, m in rows])


@pytest.mark.parametrize("simulate", [so.simulate, v3s.simulate])
def test_one_trade_moves_the_account_by_its_own_pnl(simulate):
    r = simulate(trades((0, 1, 1000., 1.)), 3., 20)
    assert r["final_multiple"] == pytest.approx(1+50*3*.1/1000)


@pytest.mark.parametrize("simulate", [so.simulate, v3s.simulate])
def test_flat_trades_leave_1000(simulate):
    r = simulate(trades((0, 1, 0., 1.), (2, 3, 0., 1.)), 3., 10)
    assert r["final_multiple"] == pytest.approx(1.)


@pytest.mark.parametrize("simulate", [so.simulate, v3s.simulate])
def test_a_liquidated_slot_loses_only_its_margin_and_is_refilled_from_the_pool(simulate):
    r = simulate(trades((0, 1, -3000., 40.), (2, 3, 1000., 1.)), 3., 20)
    assert r["liquidations"] == 1
    assert r["final_multiple"] == pytest.approx((1000-50+50*3*.1)/1000)


def test_resize_waits_for_the_account_to_double():
    # 19 winners of +50 USDT each lift a 1000 USDT pool to 1950: no resize, every slot still posts 50 USDT.
    rows = [(k, k+1, 10000/3, 1.) for k in range(19)]+[(30, 31, 1000., 1.)]
    r = so.simulate(trades(*rows), 3., 20)
    assert r["final_multiple"] == pytest.approx((1000+19*50+15)/1000)

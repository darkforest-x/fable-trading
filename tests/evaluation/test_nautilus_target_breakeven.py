"""Native fill-triggered sibling protection, including intrabar reversals."""
import numpy as np
import pandas as pd
import pytest
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
from yoyo.evaluation.monday_fib_confirmation import oracle,check_oracle


def replay(side=1, enabled=True, bars=None, delay=None, unrelated=False):
    if bars is None:
        bars = [[100., 100., 100., 100.], [100., 102.5, 99.5, 100.]] if side == 1 else [
            [100., 100., 100., 100.], [100., 100.5, 97.5, 100.]]
    f = pd.DataFrame(bars, columns=['open', 'high', 'low', 'close'],
                     index=pd.date_range('2026-01-06T00:01Z', periods=len(bars), freq='min'))
    rows = [dict(trade_id=f'p-{j}', parent_id='p', entry_i=0, entry=100., side=side,
                 stop=100.-side, risk=1., target_price=100.+side*(j+2),
                 tp1=100.+side*2, end_i=len(f)-1)
            for j in range(4)]
    if unrelated:
        rows.append(dict(rows[-1], trade_id='unrelated', parent_id='q'))
    result = run_native(f, np.full(len(f), np.nan), np.zeros(len(f)), pd.DataFrame(rows),
        ExitPolicy(target_from_entry=True, target_breakeven=enabled,
                   opposite_hour_exit=False, time_breakeven_minutes=delay),
        tick=.1, symbol='TEST', execution_minutes=1)
    for r in result[0].itertuples():
        if r.parent_id == 'q':
            continue
        expected=oracle(np.rint(f.to_numpy()/.1).astype(int),r,delay or 0,.1,.002,target_be=enabled)
        assert (r.exit_i,r.reason,r.be_trigger_i,r.be_effective_i)==(expected[0],*expected[2:])
        assert abs(r.exit_price-expected[1])<1e-8
    return result


@pytest.mark.parametrize('side', [1, -1])
def test_tp_immediately_protects_same_bar_reversal(side):
    rows, fills, audit = replay(side)
    rows = rows.set_index('trade_id')
    assert rows.loc['p-0', 'reason'] == 4
    assert rows.loc[['p-1', 'p-2', 'p-3'], 'reason'].eq(5).all()
    np.testing.assert_allclose(rows.loc[['p-1', 'p-2', 'p-3'], 'exit_price'], 100.+side*.2)
    np.testing.assert_allclose(rows.loc[['p-1', 'p-2', 'p-3'], 'net_r'], 0., atol=1e-10)
    assert rows.be_effective_i.tolist().count(1) == 3
    assert len(fills) == 8 and audit['target_breakeven']


def test_parent_isolation_and_disabled_default():
    rows, _, _ = replay(unrelated=True)
    unrelated = rows.set_index('trade_id').loc['unrelated']
    assert unrelated.reason == 0 and unrelated.be_effective_i == -1
    rows, _, _ = replay(enabled=False)
    assert rows.reason.eq(0).sum() == 3 and rows.be_effective_i.eq(-1).all()


def test_two_targets_then_same_bar_reversal():
    rows, _, _ = replay(bars=[[100.]*4, [100., 103.5, 99.5, 100.]])
    assert rows.reason.eq(4).sum() == 2 and rows.reason.eq(5).sum() == 2


def test_initial_stop_precedes_later_target_for_short_ohlc():
    rows, _, _ = replay(-1, bars=[[100.]*4, [100., 101.5, 96., 100.]])
    assert rows.reason.eq(1).all() and rows.be_effective_i.eq(-1).all()


def test_timer_first_not_overwritten_by_later_tp():
    bars = [[100.]*4] + [[100.5, 100.6, 100.4, 100.5]]*9
    bars.append([100.5, 102.5, 100.3, 100.5])
    bars.append([100.5, 100.6, 100.1, 100.2])
    rows, _, _ = replay(bars=bars, delay=5)
    assert rows.be_effective_i.eq(9).all()
    assert rows.reason.eq(4).sum() == 1 and rows.reason.eq(5).sum() == 3


def test_tp_first_not_waiting_timer():
    rows, _, _ = replay(delay=60)
    assert rows.reason.eq(5).sum() == 3
    assert rows.loc[rows.reason.eq(5), 'be_effective_i'].eq(1).all()


@pytest.mark.parametrize('side', [1, -1])
def test_opening_gap_crosses_two_targets(side):
    b=[103.5,104.5,100.,100.] if side==1 else [96.5,100.,95.5,100.]
    rows,_,_=replay(side,bars=[[100.]*4,b])
    # OHLC short reversal reaches protection at H before the later favorable L.
    expected_targets=3 if side==1 else 2
    assert rows.reason.eq(4).sum()==expected_targets
    assert rows.reason.eq(5).sum()==4-expected_targets


def test_same_endpoint_target_metadata_does_not_relax_stop_or_cash_checks():
    assert check_oracle((20,102.,4,-1,-1),(20,102.,4,20,20),'target')
    with pytest.raises(AssertionError):
        check_oracle((20,100.2,5,-1,-1),(20,100.2,5,20,20),'stop')
    with pytest.raises(AssertionError):
        check_oracle((20,101.9,4,-1,-1),(20,102.,4,20,20),'wrong cash')
    with pytest.raises(AssertionError):
        check_oracle((20,102.,4,19,19),(20,102.,4,20,20),'earlier protection')

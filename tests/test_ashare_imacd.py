"""Causal A-share replay invariants; no network, saved data, or real orders."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.ashare_imacd import (
    Costs, Parameters, buy_quantity, indicators, limit_fraction, prepare_books,
    price_limits, randomized_signals, simulate,
)


def row(day, *, open=100., high=None, low=None, close=None, preclose=100.,
        signal=False, board='main_sh', stop=95., factor=1., atr=1., **extra):
    close = open if close is None else close
    high = max(open,close)+1 if high is None else high
    low = min(open,close)-1 if low is None else low
    return dict(date=day,open=open*factor,high=high*factor,low=low*factor,close=close*factor,
                raw_open=open,raw_high=high,raw_low=low,raw_close=close,raw_preclose=preclose,
                board=board,isST='0',tradestatus='1',volume=100_000,signal=signal,
                ready=True,structure_stop=stop*factor,atr=atr*factor,score=-1.,atr_pct=.01,
                prior_width=1.,vol_bucket=1,**extra)


DAYS = ['2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-01-08']


def replay(a, b=None, **kwargs):
    frames = {'sh.600001':pd.DataFrame(a)}
    if b is not None:
        frames['sh.600002'] = pd.DataFrame(b)
    dates = [r['date'] for rows in (a,b or []) for r in rows]
    return simulate(frames,Parameters(),min(dates),max(dates),**kwargs)


@pytest.mark.parametrize('board,day,st,expected',[
    ('main_sh','2024-01-02',False,.10),('main_sz','2024-01-02',True,.05),
    ('main_sh','2026-07-06',True,.10),('chinext','2020-08-21',False,.10),
    ('chinext','2020-08-21',True,.05),('chinext','2020-08-24',True,.20),
    ('star','2019-08-01',False,.20),('STAR','2024-01-02',True,.20),
])
def test_board_dates_match_data_contract(board,day,st,expected):
    assert limit_fraction(board,day,st) == expected


@pytest.mark.parametrize('board,budget,expected',[
    ('star',250,250),('STAR',199,0),('main_sh',250,200),('chinext',199,100),
])
def test_lot_sizes(board,budget,expected):
    assert buy_quantity(budget,1,10000,board) == expected


@pytest.mark.parametrize('board,rate',[('main_sh','0.10'),('star','0.20')])
def test_limit_prices_use_decimal_half_up(board,rate):
    for cents in range(100,2500):
        ref = Decimal(cents)/100
        expected = tuple(float((ref*m).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
                         for m in (1-Decimal(rate),1+Decimal(rate)))
        assert price_limits(dict(board=board,date=DAYS[0],isST='0',raw_preclose=float(ref))) == expected


def test_dates_and_separate_slippage_cash_cost():
    result = replay([row(DAYS[0],signal=True),row(DAYS[1],open=109.99,high=110,low=109,close=110),
                     row(DAYS[2],open=110,preclose=110)])
    trade = result['trades'].iloc[0]
    assert trade.entry_date == DAYS[1]
    assert trade.entry == 109.99  # Never exceeds the 110 exchange upper limit.
    assert trade.exit == 110
    assert trade.entry_slippage == pytest.approx(trade.entry_notional*.0005)
    assert trade.exit_slippage == pytest.approx(trade.exit_notional*.0005)
    assert trade.pnl == pytest.approx(trade.pnl_gross-trade.fees-trade.slippage)
    assert trade.return_gross == pytest.approx(110/109.99-1)
    assert result['equity'].iloc[-1].equity == pytest.approx(1_000_000+trade.pnl)
    assert trade.return_net < trade.return_gross


def test_buy_day_stop_waits_until_next_session():
    result = replay([row(DAYS[0],signal=True),row(DAYS[1],low=94,close=96),
                     row(DAYS[2],open=96,preclose=96)])
    trade = result['trades'].iloc[0]
    assert trade.entry_date == DAYS[1] and trade.exit_date == DAYS[2]
    assert trade.exit == 96 and trade.reason == 'entry_day_stop_T1'
    assert trade.holding_days == 1


def test_intraday_stop_neither_funds_nor_frees_capacity_for_open_entry():
    a = [row(DAYS[0],signal=True),row(DAYS[1]),row(DAYS[2],low=94)]
    b = [row(DAYS[0]),row(DAYS[1],signal=True),row(DAYS[2])]
    result = replay(a,b,max_positions=1,allocation_fraction=1.,risk_fraction=1.)
    assert set(result['trades'].code) == {'sh.600001'}
    assert result['trades'].iloc[0].exit == 95
    assert result['metrics']['skips']['position_capacity'] == 1


def test_gap_stop_cash_and_capacity_are_available_at_open():
    a = [row(DAYS[0],signal=True),row(DAYS[1]),row(DAYS[2],open=94)]
    b = [row(DAYS[0]),row(DAYS[1],signal=True),row(DAYS[2])]
    result = replay(a,b,max_positions=1,allocation_fraction=1.,risk_fraction=1.)
    assert set(result['trades'].code) == {'sh.600001','sh.600002'}
    assert result['trades'].iloc[0].exit == 94
    assert result['trades'].iloc[1].entry_date == DAYS[2]
    assert result['equity'].cash.min() >= 0


def test_intraday_low_does_not_change_other_open_position_size():
    a = [row(DAYS[0],signal=True),row(DAYS[1]),row(DAYS[2],low=99)]
    b = [row(DAYS[0]),row(DAYS[1],signal=True),row(DAYS[2])]
    first = replay(a,b,allocation_fraction=.60,risk_fraction=1.)
    a[-1]['low'] = 94
    second = replay(a,b,allocation_fraction=.60,risk_fraction=1.)
    quantities = [r['trades'].loc[r['trades'].code=='sh.600002','quantity'].iat[0] for r in (first,second)]
    assert quantities[0] == quantities[1]


def test_stop_at_daily_lower_limit_waits_for_liquidity():
    result = replay([row(DAYS[0],signal=True,stop=90),row(DAYS[1]),
                     row(DAYS[2],open=95,high=96,low=90,close=90),
                     row(DAYS[3],open=92,preclose=90)])
    trade = result['trades'].iloc[0]
    assert trade.exit_date == DAYS[3] and trade.exit == 92
    assert trade.reason == 'stop_delayed_limit_down'


def test_open_limit_down_preserves_exit_request():
    result = replay([row(DAYS[0],signal=True),row(DAYS[1],low=94,close=100),
                     row(DAYS[2],open=90,high=90,low=90,close=90),
                     row(DAYS[3],open=92,preclose=90)])
    trade = result['trades'].iloc[0]
    assert trade.exit_date == DAYS[3] and trade.exit == 92
    assert result['metrics']['skips']['exit_open_limit_down'] == 1


def test_suspended_next_session_purchase_expires():
    a = [row(DAYS[0],signal=True),row(DAYS[2])]
    b = [row(day) for day in DAYS[:3]]
    result = replay(a,b)
    assert result['trades'].empty
    assert result['metrics']['skips']['entry_suspended_or_missing'] == 1


def test_chinext_actual_lowercase_board_accepts_15_percent_gap():
    result = replay([row(DAYS[0],signal=True,board='chinext'),
                     row(DAYS[1],open=115,board='chinext')])
    assert len(result['trades']) == 1
    assert result['trades'].iloc[0].entry == 115


def test_trailing_stop_uses_prior_close_level_and_has_no_fixed_target():
    result = replay([row(DAYS[0],signal=True),row(DAYS[1],close=108),
                     row(DAYS[2],open=110,close=118,preclose=108),
                     row(DAYS[3],open=117,close=113,preclose=118),
                     row(DAYS[4],open=112,preclose=113)])
    trade = result['trades'].iloc[0]
    assert trade.reason == 'trend_close_exit'
    assert trade.exit_date == DAYS[4] and trade.exit == 112
    assert trade.peak_r == pytest.approx(3.6)
    assert result['metrics']['trades'] == 1


def test_terminal_stale_valuation_is_not_a_natural_trade():
    a = [row(DAYS[0],signal=True),row(DAYS[1],close=104)]
    b = [row(day) for day in DAYS[:4]]
    result = replay(a,b)
    trade = result['trades'].iloc[0]
    assert trade.reason == 'terminal_stale_valuation'
    assert trade.exit == 104 and trade.stale_days == 2 and trade.terminal_uncertain
    metrics = result['metrics']
    assert metrics['trades'] == 0 and metrics['win_rate'] is None
    assert metrics['terminal_uncertain'] == metrics['open_at_end'] == 1
    assert metrics['net_return'] > 0 > metrics['zero_recovery_net_return']
    assert metrics['zero_recovery_max_drawdown'] > metrics['max_drawdown']
    assert result['equity'].iloc[-1].equity-result['zero_recovery_equity'].iloc[-1].equity == pytest.approx(metrics['terminal_uncertain_value'])


def test_adjusted_coordinates_preserve_raw_lots_cash_and_returns():
    plain = [row(DAYS[0],signal=True),row(DAYS[1]),row(DAYS[2],open=104)]
    scaled = [row(DAYS[0],signal=True,factor=8),row(DAYS[1],factor=8),row(DAYS[2],open=104,factor=8)]
    x,y = replay(plain),replay(scaled)
    assert x['trades'].iloc[0].quantity == y['trades'].iloc[0].quantity
    assert x['trades'].iloc[0].return_net == pytest.approx(y['trades'].iloc[0].return_net)
    pd.testing.assert_frame_equal(x['equity'],y['equity'])


def test_prepared_fold_replay_is_reusable_and_immutable():
    frames = {'sh.600001':pd.DataFrame([row(DAYS[0],signal=True),row(DAYS[1]),row(DAYS[2],open=104)])}
    prepared = prepare_books(frames,DAYS[0],DAYS[2]); original = deepcopy(prepared)
    x = simulate(frames,Parameters(),DAYS[0],DAYS[2],prepared=prepared)
    y = simulate(frames,Parameters(),DAYS[0],DAYS[2])
    assert prepared == original
    pd.testing.assert_frame_equal(x['trades'],y['trades'])
    pd.testing.assert_frame_equal(x['equity'],y['equity'])


def test_indicator_prefix_and_readiness_are_causal():
    count = 620
    dates = pd.bdate_range('2018-01-01',periods=count).strftime('%Y-%m-%d')
    values = 100+np.sin(np.arange(count)/17)*4
    values[480:] += np.arange(count-480)*.3
    frame = pd.DataFrame([row(day,open=float(price),signal=False) for day,price in zip(dates,values)])
    frame = frame.drop(columns=['ready'])
    p = Parameters()
    full, prefix = indicators(frame,p),indicators(frame.iloc[:500],p)
    pd.testing.assert_frame_equal(full.iloc[:500].reset_index(drop=True),prefix)
    assert not full.ready.iloc[:340].any() and full.ready.iloc[340:].all()
    assert not full.signal.iloc[:340].any()
    assert (full.loc[full.signal,'md']>0).all()
    longer = indicators(frame,replace(p,ma_length=55))
    assert not longer.ready.iloc[:550].any() and longer.ready.iloc[550:].all()


def test_random_controls_cannot_bypass_indicator_warmup():
    dates = pd.bdate_range('2024-01-02',periods=50).strftime('%Y-%m-%d')
    f = pd.DataFrame([row(day,signal=i>=40) for i,day in enumerate(dates)])
    f['ready'] = np.arange(50)>=40
    for seed in range(10):
        result = randomized_signals({'sh.600001':f},dates[0],dates[-1],seed)
        assert result['sh.600001'] == set(dates[40:])


def test_commission_transfer_and_stamp_dates():
    costs = Costs()
    assert costs.fee(100_000,'2022-04-28',False) == pytest.approx(27)
    assert costs.fee(100_000,'2022-04-29',False) == pytest.approx(26)
    assert costs.fee(100_000,'2023-08-25',True) == pytest.approx(126)
    assert costs.fee(100_000,'2023-08-28',True) == pytest.approx(76)
    assert costs.fee(1000,'2024-01-02',False) == pytest.approx(5.01)

"""Cash and information-timing contracts for the multi-market account."""
import pandas as pd
import pytest
from yoyo.evaluation.altseason_portfolio import run_portfolio


def fixtures():
    clocks=pd.date_range('2026-01-01',periods=4,freq='h',tz='UTC')
    prices={'gate:A':pd.DataFrame({'open':[100.,100.,110.,120.],'close':[100.,110.,120.,120.]},index=clocks),
            'binance:A':pd.DataFrame({'open':[100.,100.,110.,120.],'close':[100.,110.,120.,120.]},index=clocks)}
    row=dict(event_id='1',instrument='gate:A',asset='A',venue='gate',symbol='A',entry_time=clocks[1],exit_time=clocks[3],
        valid=True,initial_risk_frac=.05,entry_price=100.,exit_price=120.,net_return=.198,prior24h_quote_volume=1e8,signal_quote_volume=1e7,natural_exit=True,censored=False)
    return clocks,prices,row


def test_same_asset_cross_venue_only_once_with_known_turnover_rank():
    c,p,row=fixtures();other=dict(row,event_id='2',venue='binance',instrument='binance:A',prior24h_quote_volume=2e8)
    path,events=run_portfolio(pd.DataFrame([row,other]),p,c[0],c[-1]+pd.Timedelta(hours=1),60)
    got=events.set_index('event_id')
    assert not got.loc['1','portfolio_selected'] and got.loc['2','portfolio_selected']
    assert got.loc['2','notional']==10000.
    assert path.equity.iloc[-1]==pytest.approx(101980.)
    assert path.cash.min()>=0


def test_intrabar_exit_cash_only_available_after_enclosing_close():
    c,p,row=fixtures();first=dict(row,exit_time=c[2],exit_price=95.,net_return=-.052,exit_timing='intrabar_unknown')
    early=dict(row,event_id='2',entry_time=c[1],exit_time=c[3],prior24h_quote_volume=5e7)
    later=dict(row,event_id='3',entry_time=c[2],entry_price=110.,exit_time=c[3],exit_price=120.,net_return=120/110-1-.002)
    _,events=run_portfolio(pd.DataFrame([first,early,later]),p,c[0],c[-1]+pd.Timedelta(hours=1),60)
    assert events.set_index('event_id').portfolio_selected.to_dict()=={'1':True,'2':False,'3':True}


def test_next_open_gap_not_used_in_previous_close_equity():
    c,p,row=fixtures();row.update(exit_price=130.,exit_time=c[2],exit_timing='open',net_return=.298)
    p['gate:A'].loc[c[2],'open']=130.
    path,_=run_portfolio(pd.DataFrame([row]),p,c[0],c[-1]+pd.Timedelta(hours=1),60)
    at=path.set_index('time')
    assert at.loc[c[2],'equity']==pytest.approx(100990.)
    assert at.loc[c[3],'equity']==pytest.approx(102980.)


@pytest.mark.parametrize('field,value',[('entry_price',-100.),('entry_price',50.),('entry_time',pd.Timestamp('2026-01-01T01:30Z'))])
def test_misaligned_or_wrong_venue_event_prices_fail(field,value):
    c,p,row=fixtures();row[field]=value
    with pytest.raises(ValueError):run_portfolio(pd.DataFrame([row]),p,c[0],c[-1]+pd.Timedelta(hours=1),60)


def test_capacity_missing_is_not_unlimited_liquidity():
    c,p,row=fixtures();row['signal_quote_volume']=float('nan')
    path,events=run_portfolio(pd.DataFrame([row]),p,c[0],c[-1]+pd.Timedelta(hours=1),60)
    assert events.portfolio_rejection.iloc[0]=='capacity_missing'
    assert path.equity.iloc[-1]==100000.

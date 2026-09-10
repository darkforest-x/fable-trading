"""Regression checks for a fresh cashbook after asset removal, not subtraction."""
import pandas as pd
import pytest

from yoyo.evaluation.launch_quality_accounts import evaluate_account, without_asset, candidate_summary, load_prices, sha


def test_without_useless_removes_every_venue_and_reallocates_released_slot():
    index=pd.date_range('2026-01-01',periods=4,freq='h',tz='UTC')
    prices={};rows=[]
    for number in range(11):
        asset='USELESS' if number==0 else 'A'+str(number)
        instrument='okx:'+asset
        exit_price=200. if number==10 else 110.
        prices[instrument]=pd.DataFrame({'open':[100.,100.,exit_price,exit_price],
                                        'close':[100.,105.,exit_price,exit_price]},index=index)
        rows.append(dict(event_id=str(number),instrument=instrument,asset=asset,venue='okx',symbol=asset,
            valid=True,entry_time=index[1],exit_time=index[2],entry_price=100.,exit_price=exit_price,
            initial_risk_frac=.05,net_return=exit_price/100.-1-.002,
            prior24h_quote_volume=1e9-number*1000,signal_quote_volume=1e8,
            natural_exit=True,censored=False,exit_reason='md',exit_timing='open'))
    events=pd.DataFrame(rows)
    full,_,ledger=evaluate_account(events,prices,index[0],index[-1]+pd.Timedelta(hours=1))
    reduced,_,new_ledger=evaluate_account(without_asset(events),prices,index[0],index[-1]+pd.Timedelta(hours=1))
    assert not ledger.set_index('event_id').loc['10','portfolio_selected']
    assert new_ledger.set_index('event_id').loc['10','portfolio_selected']
    static=full['return_pct']-ledger.loc[ledger.asset.eq('USELESS'),'realized_net_pnl'].sum()/1000
    assert reduced['return_pct']>static
    duplicates=pd.concat([events,events.loc[events.asset.eq('USELESS')].assign(venue='gate')])
    assert not without_asset(duplicates).asset.eq('USELESS').any()


def test_no_events_means_cash_and_no_invented_wins():
    frame=pd.DataFrame(columns=['event_id','asset','valid'])
    start=pd.Timestamp('2026-01-01T00:00Z')
    result,curve,ledger=evaluate_account(frame,{},start,start+pd.Timedelta(hours=3))
    assert result['return_pct']==0 and result['trades']==0
    assert result['max_drawdown_pct']==0
    assert curve.equity.eq(100000).all()
    assert pd.isna(result['initial_stop_rate'])


def test_control_zero_coverage_is_distinct_from_any_control_coverage():
    row=dict(event_id='a',valid=True,asset='A',venue='okx',decision_time=pd.Timestamp('2026-01-01T01:00Z'),
        net_bp=100.,gross_bp=120.,net_return=.01,natural_exit=True,censored=False,exit_reason='md')
    actual=pd.DataFrame([row]);controls=pd.DataFrame([dict(row,event_id='c0',matched_event_id='a',control_number=0,valid=False),
        dict(row,event_id='c1',matched_event_id='a',control_number=1)])
    result=candidate_summary(actual,controls)
    assert result['paired']==0 and result['paired0_fraction']==0
    assert result['any_control_fraction']==1


def test_mark_source_hash_must_match_even_when_entry_open_is_unchanged(tmp_path):
    path=tmp_path/'prices.pkl.gz'
    f=pd.DataFrame({'open':[100.,100.],'close':[100.,101.]},index=pd.date_range('2026-01-01',periods=2,freq='h',tz='UTC'))
    f.to_pickle(path);expected=sha(path)
    rows=pd.DataFrame([dict(instrument='okx:A',features_path=str(path))])
    assert load_prices(rows,rows.iloc[:0],{str(path):expected})['okx:A'].close.iloc[-1]==101.
    f.loc[f.index[-1],'close']=90.;f.to_pickle(path)
    with pytest.raises(ValueError,match='Unverified'):load_prices(rows,rows.iloc[:0],{str(path):expected})

"""Minute-source display parity never silently repairs the source or fill data."""
from types import SimpleNamespace
import pandas as pd
import pytest
from yoyo.evaluation.spike_2026_minute_charts import verify_frame


def sample():
    idx=pd.date_range('2026-09-01T14:55Z',periods=55,freq='min')
    minute=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=10.),index=idx)
    minute.loc[idx[15],:]=[100.,100.,100.,100.,0.]
    minute.loc[idx[16],'open']=101.
    original=minute.resample('5min').agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
    original.loc[idx[15],'open']=101.
    row=SimpleNamespace(entry_bar=str(idx[30]),exit_bar=str(idx[30]),entry_i=6,exit_i=6,event='fixture')
    return minute,original,row


def test_empty_leading_context_minute_is_disclosed_without_rewriting_prices():
    minute,original,row=sample();before=minute.copy(deep=True)
    window,quality=verify_frame(minute,original,row)
    assert len(quality['empty_minute_open_differences'])==1
    assert quality['empty_minute_open_differences'][0]['scope']=='outside_trade_interval'
    assert window.open.iloc[15]==100 and original.open.iloc[3]==101
    pd.testing.assert_frame_equal(minute,before)


@pytest.mark.parametrize('field',['high','low','close','volume'])
def test_other_price_or_volume_disagreement_still_fails(field):
    minute,original,row=sample();original.loc[original.index[3],field]+=1
    with pytest.raises(AssertionError,match='mismatch'):verify_frame(minute,original,row)


def test_nonempty_open_and_missing_minute_still_fail():
    minute,original,row=sample();minute.loc[minute.index[15],'volume']=1
    with pytest.raises(AssertionError,match='mismatch'):verify_frame(minute,original,row)
    minute,original,row=sample()
    with pytest.raises(AssertionError,match='missing minute'):verify_frame(minute.drop(minute.index[2]),original,row)


def test_execution_interval_open_exception_is_not_accepted():
    minute,original,row=sample();original.loc[original.index[3],'open']=100
    minute.loc[minute.index[30],:]=[100.,100.,100.,100.,0.]
    minute.loc[minute.index[31],'open']=101.
    original.loc[original.index[6],['open','volume']]=[101.,40.]
    with pytest.raises(AssertionError,match='mismatch'):verify_frame(minute,original,row)


def test_empty_minute_must_carry_previous_close():
    minute,original,row=sample()
    minute.loc[minute.index[15],['open','high','low','close']]=99.5
    with pytest.raises(AssertionError,match='mismatch'):verify_frame(minute,original,row)

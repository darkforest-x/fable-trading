"""Funding time and nominal cost accounting must not invent zeroes or fills."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
from yoyo.evaluation.altseason_costs import trade_funding


def test_unknown_history_stays_unknown():
    assert not trade_funding(None,None,None)['funding_known']


def test_boundary_funding_is_bracketed_and_long_uses_positive_rate_as_cost():
    row=SimpleNamespace(entry_time='2026-08-01T00:00Z',exit_time_lower='2026-08-01T12:00Z',exit_time_upper='2026-08-01T13:00Z',entry_price=10.)
    stamps=pd.to_datetime(['2026-08-01T00:00:01Z','2026-08-01T08:00:00Z','2026-08-01T12:00:00Z'])
    f=pd.DataFrame(dict(funding_time=stamps.asi8//1000000,realized_rate=[.001,.001,-.002],mark_price=[10.,20.,np.nan]))
    hourly=pd.DataFrame({'open':[30.]},index=pd.to_datetime(['2026-08-01T12:00Z']))
    got=trade_funding(row,f,hourly)
    assert got['funding_known'] and got['ambiguous_settlements']==2 and got['proxy_price_count']==1
    assert np.isclose(got['observed_funding_return'],.002)
    assert np.isclose(got['observed_funding_low'],-.005)
    assert np.isclose(got['observed_funding_high'],.009)


def test_missing_price_does_not_make_zero_cost():
    row=SimpleNamespace(entry_time='2026-08-01T00:00Z',exit_time_lower='2026-08-02T00:00Z',exit_time_upper='2026-08-02T00:00Z',entry_price=10.)
    f=pd.DataFrame(dict(funding_time=[1785571200000],realized_rate=[.01],mark_price=[np.nan]))
    got=trade_funding(row,f,pd.DataFrame({'open':[]},index=pd.DatetimeIndex([],tz='UTC')))
    assert not got['funding_known'] and np.isnan(got['observed_funding_return'])

"""Synthetic V29 contract tests; never read real study prices or outcomes."""
import inspect

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import hourly_impulse_classifier_support as model
from yoyo.evaluation import hourly_impulse_classifier_research as study


def hours(n=230):
    x = np.arange(n, dtype=float)
    close = 100+x*.1+np.sin(x/3)
    return pd.DataFrame(dict(open_time=pd.date_range("2024-01-01T00:00:00Z", periods=n, freq="h"),
                             open=close, high=close+1, low=close-1, close=close))


@pytest.mark.parametrize("center,previous,close,step,expected", [
    (100.,99.,103.,2.,1.), (100.,99.,102.,2.,0.), (100.,99.,98.,2.,0.),
    (100.,101.,97.,2.,-1.), (100.,101.,98.,2.,0.), (100.,100.,97.,2.,-1.),
    (100.,100.,103.,2.,0.), (100.,99.,100.,0.,0.), (100.,99.,100.01,0.,1.),
    (100.,101.,100.,0.,0.), (100.,101.,99.99,0.,-1.),
    (100.,np.nan,103.,2.,np.nan), (np.nan,99.,103.,2.,np.nan), (100.,99.,103.,np.nan,np.nan)])
def test_exact_pine_state_boundaries(center, previous, close, step, expected):
    actual = float(model.classify_state(np.array(center),np.array(previous),np.array(close),np.array(step)))
    assert np.isnan(actual) if np.isnan(expected) else actual == expected


def test_prefix_causality_warmup_and_source_immutability():
    source=hours();before=source.copy(deep=True)
    actual=model.classifier_trace(source)
    assert not actual.classifier_known.iloc[:99].any()
    assert actual.classifier_known.iloc[99:].all()
    for end in [1,9,10,11,99,100,170]:
        assert_frame_equal(actual.iloc[:end],model.classifier_trace(source.iloc[:end]))
    mutated=source.copy();mutated.loc[170:,['open','high','low','close']]*=100
    assert_frame_equal(actual.iloc[:170],model.classifier_trace(mutated).iloc[:170])
    assert (actual.classifier_available_at==actual.open_time+pd.Timedelta(hours=1)).all()
    assert_frame_equal(source,before)


def test_gap_resets_seed_and_both_windows_without_filling():
    source=hours(350).drop(index=150)
    actual=model.classifier_trace(source)
    restart=model.classifier_trace(source.iloc[150:].reset_index(drop=True))
    cols=[c for c in actual if c!='classifier_segment']
    assert_frame_equal(actual.iloc[150:][cols].reset_index(drop=True),restart[cols])
    assert actual.classifier_segment.nunique()==2
    assert not actual.classifier_known.iloc[150:249].any()
    assert actual.classifier_known.iloc[249]


def test_empty_constant_and_duplicate_indices():
    assert model.classifier_trace(hours(0)).empty
    source=hours(110);source[['open','high','low','close']]=100.
    source.index=[0]*len(source)
    actual=model.classifier_trace(source)
    assert actual.classifier_direction.iloc[99:].eq(0).all()
    assert actual.classifier_step.iloc[99:].eq(0).all()


@pytest.mark.parametrize('mutation',['reverse','duplicate','naive','offset','subhour','nan','inf','negative','bool','geometry'])
def test_invalid_source_rejected(mutation):
    source=hours()
    if mutation=='reverse': source=source.iloc[::-1]
    elif mutation=='duplicate': source.loc[1,'open_time']=source.loc[0,'open_time']
    elif mutation in ['naive','offset','subhour']:
        source['open_time']=source.open_time.astype(object)
        source.loc[1,'open_time']={'naive':'2024-01-01 01:00:00','offset':'2024-01-01T09:00:00+08:00','subhour':'2024-01-01T01:05:00Z'}[mutation]
    elif mutation=='bool':source['close']=source.close.astype(object);source.loc[1,'close']=True
    else:source.loc[1,'close']={'nan':np.nan,'inf':np.inf,'negative':-1,'geometry':10000}[mutation]
    with pytest.raises((ValueError,TypeError)):model.classifier_trace(source)


def test_control_uses_own_completed_hour_and_missing_is_unknown():
    trace=model.classifier_trace(hours())
    t=trace.open_time.iloc[170]
    requests=pd.DataFrame(dict(event_id=['case','own-control','unknown'],signal_time=[t,t-pd.Timedelta(hours=100),t+pd.Timedelta(days=10)],
        decision_time=[t+pd.Timedelta(hours=1),t-pd.Timedelta(hours=99),t+pd.Timedelta(days=10,hours=1)],direction=[1,-1,1]))
    actual=model.attach_context(requests,trace)
    assert actual.classifier_count.tolist()==[171,71,0]
    assert actual.classifier_gate_state.iloc[1:].eq('unknown').all()
    assert actual.classifier_center.iloc[0]==trace.classifier_center.iloc[170]
    assert len(actual)==len(requests)
    with pytest.raises(ValueError,match='existing'):model.attach_context(actual,trace)
    wrong=requests.copy();wrong.loc[0,'decision_time']+=pd.Timedelta(hours=1)
    with pytest.raises(ValueError):model.attach_context(wrong,trace)


def test_frozen_runner_boundaries_and_no_outcome_paths():
    config=study.frozen_config()
    assert config['population']==dict(cases=251,controls=744,matched=248,unmatched=3)
    assert config['parameters']['center_length']==10 and config['parameters']['range_length']==100
    assert config['support']==dict(minimum_events=80,minimum_per_fold=12,minimum_active_months=12,minimum_months_per_fold=3)
    assert not config['outcomes_read_or_computed'] and not config['new_allocation']
    source=inspect.getsource(study.run)
    assert source.index('preflight_trace') < source.index('usecols=OHLC')
    assert source.count('verify_inputs(root)')==2
    assert source.index('committed_sources(root)') < source.index('directory.mkdir')
    assert 'case_labels' not in source and 'statistics.json' not in source

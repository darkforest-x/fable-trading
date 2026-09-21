"""Causality, label-maturity and denominator tests for finite 10R discovery."""
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation import spike_10r_search as s


def sample():
    times=pd.date_range('2024-10-01', '2025-10-01', freq='12h', tz='UTC', inclusive='left')
    c=pd.DataFrame({'available_at':times,'timeframe_min':60,'valid_entry':True,'censored':False,
                    'entry_time':times,'exit_time':times+pd.Timedelta(hours=1),
                    'asset':[f'A{i%30}' for i in range(len(times))],
                    'net_r':np.where(np.arange(len(times))%7==0,12.,-1.),'net_return':.01,'gross_return':.012})
    for j,f in enumerate(s.FEATURES):
        c[f]=1+(np.arange(len(c))%37)/37+j
    return c


def config():
    return dict(selection_min_closed=5,selection_min_gt10=1,selection_min_gt10_recall=.01,
                selection_min_positive_asset_months=1,selection_min_assets=1,
                selection_min_active_months=1,coverage_floors=[.01,.1])


def test_complete_rule_family_unique_distinct_feature_pairs():
    rules=s.rule_specs()
    assert len(rules)==6960 and len({name for _,name in rules})==6960
    assert sum(len(ids)==1 for ids,_ in rules)==120
    t=s.terms()
    for ids,_ in rules:
        if len(ids)==2:
            assert t[ids[0]]['feature']!=t[ids[1]]['feature']


def test_thresholds_ignore_current_future_and_all_labels():
    c=sample()
    m,r,t=s.calibrate(c)
    altered=c.copy()
    future=c.available_at.ge('2025-06-01')
    altered.loc[future,list(s.FEATURES)]=100000.
    altered['net_r']=999999.
    mm,rr,tt=s.calibrate(altered)
    prior=c.available_at.lt('2025-06-01')
    assert np.array_equal(m[prior],mm[prior])
    np.testing.assert_allclose(r[prior],rr[prior],equal_nan=True)
    assert_frame_equal(t.loc[t.month.le('2025-06')].reset_index(drop=True),tt.loc[tt.month.le('2025-06')].reset_index(drop=True))
    known=t.loc[t.known]
    assert (pd.to_datetime(known.history_end,utc=True)<=pd.to_datetime(known.month+'-01',utc=True)).all()


def test_no_lookback_borrowing_and_unknown_rejects():
    c=sample()
    c.loc[300,'reference_risk_fraction']=np.nan
    m,r,t=s.calibrate(c)
    assert not m[c.available_at.lt('2025-01-01')].any()
    assert not m[300,:6].any() and np.isnan(r[300,0])
    assert t.loc[t.month.eq('2024-10'),'known'].eq(False).all()


def test_selection_cannot_use_crossing_or_later_labels():
    c=sample()
    m,_,_=s.calibrate(c)
    crossing=c.index[c.available_at.lt(s.SPLIT)][-4:]
    c.loc[crossing,'exit_time']=s.SPLIT+pd.Timedelta(days=20)
    first,selected=s.search_earlier(c,m,config())
    changed=c.copy()
    forbidden=~s.early_mask(c)
    changed.loc[forbidden,'net_r']=1e6
    changed.loc[forbidden,'net_return']=1e6
    second,chosen=s.search_earlier(changed,m,config())
    assert_frame_equal(first,second)
    assert selected==chosen


def test_censored_not_negative_or_success_in_precision():
    c=sample().iloc[:4].copy()
    c['net_r']=[12.,-1.,1000.,1000.]
    c['valid_entry']=[True,True,True,False]
    c['censored']=[False,False,True,True]
    m=s.metrics(c,c)
    assert (m['closed'],m['gt10'],m['precision'],m['censored'],m['invalid'])==(2,1,.5,1,1)


def test_wilson_and_holm_not_raw_max_precision():
    assert s.wilson_lower(20,400)>s.wilson_lower(1,10)
    np.testing.assert_allclose(s.holm([.001,.02,.1]),[.003,.04,.1])
    np.testing.assert_allclose(s.holm([None,.04]),[1.,.08])


def test_rate_interval_empty_selection_and_identical_baseline():
    c=sample()
    assert np.isnan(s.rate_interval(c,c.iloc[:0])['precision_delta'])
    out=s.rate_interval(c,c)
    assert out==dict(precision_delta=0.,precision_ci_low=0.,precision_ci_high=0.)


def test_selection_rejects_terms_changed_after_discovery():
    import copy
    import pytest
    c=sample()
    m,_,_=s.calibrate(c)
    board,choices=s.search_earlier(c,m,config())
    selection=dict(selected=s.clean(choices),rule_count=len(board),earlier_closed=int(s.early_mask(c).sum()),
                   earlier_gt10=int(c.loc[s.early_mask(c)].net_r.gt(10).sum()),selection_uses_later_labels=False)
    s.validate_selection(c,m,config(),selection)
    altered=copy.deepcopy(selection)
    altered['selected'][0]['choice']['terms']=[119]
    with pytest.raises(ValueError,match='canonical'):
        s.validate_selection(c,m,config(),altered)


def test_config_cannot_claim_other_quantiles_than_implemented():
    import json
    import pytest
    cfg=json.loads((s.EXP/'config.json').read_text())
    s.validate_config(cfg)
    cfg['quantiles']=[.05,.2,.4]
    with pytest.raises(ValueError,match='frozen contract'):
        s.validate_config(cfg)


def test_completed_stream_coverage_keeps_legitimate_zero_long_streams():
    import pytest
    s.validate_stream_coverage(['nonempty','empty'],['nonempty','empty'],['nonempty'])
    with pytest.raises(ValueError,match='coverage'):
        s.validate_stream_coverage(['nonempty'],['nonempty','empty'],['nonempty'])
    with pytest.raises(ValueError,match='coverage'):
        s.validate_stream_coverage(['nonempty','empty'],['nonempty','empty'],['foreign'])

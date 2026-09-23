"""Behavioral checks for causal gate experiment reporting and matching."""
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v128_expansion_report import inference, matched_rows, holm, comparison


def test_week_signs_and_holm_are_exact():
    r=inference(pd.Series([1.,2.,3.]),pd.Series(['a','b','c']),1,100)
    assert r['p_one_sided']==1/8
    assert r['matched']==3 and r['weeks']==3
    np.testing.assert_allclose(holm([.01,.04,.03,1.]),[.04,.09,.09,1.])


def test_early_control_must_mature_and_failed_draw_is_not_replaced():
    split=pd.Timestamp('2026-08-23',tz='UTC')
    t=pd.DataFrame({'trade_key':['a','b','c'],'signal_close':pd.to_datetime(['2026-08-01']*3,utc=True),
                    'exit_time':pd.to_datetime(['2026-08-02']*3,utc=True),'censored':[False]*3})
    c=pd.DataFrame({'trade_key':['a','b','c'],'control_pool':['all']*3,'matched':[True,True,False],
                    'control_net_r':[1.,1.,np.nan],'control_net_return':[.01,.01,np.nan],
                    'control_exit_time':pd.to_datetime(['2026-08-02','2026-08-24',None],utc=True)})
    assert matched_rows(t,c,'all','earlier',split).trade_key.tolist()==['a']
    assert matched_rows(t,c,'all','all',split).trade_key.tolist()==['a','b']


def test_shared_week_bootstrap_identical_policies_have_zero_delta():
    d=pd.DataFrame({'week':['a','b','c'],'net_return':[.01,-.01,.02],'net_bp':[100.,-100.,200.],'net_r':[1.,-1.,2.]})
    r=comparison(d,d.copy(),4,100)
    for k,v in r.items():
        if k.startswith(('delta_','ci_low_','ci_high_')):assert v==0.


def test_matched_control_contract_rejects_censor_and_missing_outcome():
    import pytest
    split=pd.Timestamp('2026-08-23',tz='UTC')
    t=pd.DataFrame({'trade_key':['a'],'signal_close':pd.to_datetime(['2026-08-01'],utc=True),
                    'exit_time':pd.to_datetime(['2026-08-02'],utc=True),'censored':[False]})
    c=pd.DataFrame({'trade_key':['a'],'control_pool':['all'],'matched':[True],'control_censored':[True],
                    'control_net_r':[1.],'control_net_return':[.01],'control_exit_time':pd.to_datetime(['2026-08-02'],utc=True)})
    with pytest.raises(ValueError,match='censored'):matched_rows(t,c,'all','all',split)
    c['control_censored']=False;c['control_net_return']=np.nan
    with pytest.raises(ValueError,match='nonfinite'):matched_rows(t,c,'all','all',split)

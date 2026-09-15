"""Threshold boundaries, pooled weighting and causal blacklist invariance."""
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v9_asset_exclusions import asset_scores,blacklist,retained,UNKNOWN


def table(values,assets=None):
    return pd.DataFrame({'asset':assets or ['ETH']*len(values),'net_r':values,'censored':False,
        'entry_time':pd.Timestamp('2025-01-01',tz='UTC'),'exit_time':pd.Timestamp('2025-01-02',tz='UTC')})


def test_exact_user_inequalities_and_unknowns():
    score=pd.DataFrame({'win_rate':[.10,.100001,0.,0.], 'mean_r':[-.36,-.360001,0.,-1.],
        'pf_r':[.5,.499999,np.inf,np.nan], 'score_known':[True,True,True,False]},index=['A','B','C',UNKNOWN])
    assert blacklist(score,'win_le_10pct')=={'A','C'}
    assert blacklist(score,'mean_lt_neg036')=={'B'}
    assert blacklist(score,'pf_lt_05')=={'B'}


def test_asset_scores_pool_trades_and_pf_degenerate_cases():
    frame=table([1.]+[-1.]*9+[1.,0.,0.],['A']*10+['B','C','C'])
    result=asset_scores(frame)
    assert result.loc['A','win_rate']==.1
    assert result.loc['A','mean_r']==-.8
    assert result.loc['A','pf_r']==1/9
    assert np.isinf(result.loc['B','pf_r'])
    assert np.isnan(result.loc['C','pf_r'])
    assert blacklist(result,'win_le_10pct')=={'A','C'}


def test_earlier_blacklist_uses_only_already_closed_outcomes():
    cut=pd.Timestamp('2025-09-10',tz='UTC')
    frame=table([-1.,100.,100.,100.],['A','A','B','C'])
    frame.loc[1,'exit_time']=cut
    frame.loc[2,'entry_time']=cut;frame.loc[2,'exit_time']=cut+pd.Timedelta(days=1)
    frame.loc[3,'censored']=True
    a=asset_scores(frame,before=cut)
    assert a.index.tolist()==['A'] and a.loc['A','mean_r']==-1
    frame.loc[1:,'net_r']=-10000
    pd.testing.assert_frame_equal(a,asset_scores(frame,before=cut))


def test_whole_asset_exclusion_retains_all_other_stream_rows_unchanged():
    frame=table([-1.,4.,3.,-2.],['A','B','A','C'])
    frame['stream_key']=['exchange1_30','exchange1_60','exchange2_240','exchange2_30']
    pd.testing.assert_frame_equal(retained(frame,{'A'}),frame.iloc[[1,3]])

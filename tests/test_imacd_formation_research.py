"""Synthetic integration tests for V2; no exchange or historical inputs."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import imacd_formation_research as r
from yoyo.evaluation.imacd_startup_quality import build_features
from yoyo.evaluation.imacd_formation_memory import add_formation_memory
from yoyo.evaluation.imacd_startup_accounting import outcome_arrays


def test_timestamp_cutoff_stops_before_parsing_future_prices(tmp_path):
    path = tmp_path/'bars.csv'
    cutoff = int(r.END.timestamp()*1000)
    path.write_text('ts,open,high,low,close,volume\n'
                    f'{cutoff-1800000},100,101,99,100,1\n'
                    f'{cutoff-900000},100,101,99,100,1\n'
                    f'{cutoff},DO_NOT_PARSE_FUTURE_PRICES\n'
                    'not_even_a_timestamp,AFTER_CUTOFF\n')
    data = r.read_prefix(path)
    assert len(data) == 2
    assert (data.index + pd.Timedelta(minutes=15) <= r.END).all()


def test_frozen_policies_preserve_single_changed_variable():
    from itertools import product
    combos=list(product([False,True],repeat=4))
    f=pd.DataFrame(combos,columns=['release_side','keep_contraction','keep_proximity','keep_memory'])
    masks=r.policy_masks(f)
    assert list(masks)==['P00','P02','Q01','Q02']
    assert masks['P02'].equals(f.release_side & f.keep_contraction & f.keep_proximity)
    assert masks['Q02'].equals(f.release_side & f.keep_memory & f.keep_proximity)
    assert (masks['Q02'] & ~masks['Q01']).sum()==0
    assert r.FOLDS==[('development','2023-01-01','2025-01-01'),('validation','2025-01-01','2026-01-01')]


def test_future_prices_cannot_change_prior_candidate_flags():
    n=900
    c=np.full(n,100.)
    c[400:420]=np.linspace(100,125,20)
    c[420:480]=125
    c[480:]=100
    bars=pd.DataFrame(dict(open=c,high=c+1,low=c-1,close=c,volume=np.ones(n)),
                      index=pd.date_range('2024-01-01',periods=n,freq='h',tz='UTC'))
    full=add_formation_memory(build_features(bars))
    for end in (350,419,475,510,650):
        prefix=add_formation_memory(build_features(bars.iloc[:end]))
        pd.testing.assert_frame_equal(prefix,full.iloc[:end])
        for p,mask in r.policy_masks(prefix).items():
            pd.testing.assert_series_equal(mask,r.policy_masks(full)[p].iloc[:end])


def test_prefix_rejects_nonmonotonic_pre_cutoff(tmp_path):
    path=tmp_path/'bars.csv'
    path.write_text('ts,open,high,low,close,volume\n1800000,100,101,99,100,1\n900000,100,101,99,100,1\n')
    with pytest.raises(ValueError,match='nonmonotonic'):
        r.read_prefix(path)


def test_summarize_produces_all_arms_with_matched_diagnostics():
    e=pd.DataFrame(dict(fold=['validation']*10,minutes=[60]*10,
        event_id=[f'e{i}' for i in range(10)],symbol=['X']*10,
        month=['2025-01']*5+['2025-02']*5,signal_i=list(range(10)),
        net_bp=[-20,-10,0,5,15,25,35,45,55,100],gross_bp=[0,10,20,25,35,45,55,65,75,120],
        control_mean_net_bp=[-20]*10,excess_bp=[0,10,20,25,35,45,55,65,75,120],
        exit_kind=['natural']*10,near_zero_bars=[12]*10,contraction_ratio=[.5]*10,
        proximity_atr=[0]*10,separation_delta=[1]*10,formation_memory_ratio=[.5]*10,
        strength_score=np.arange(10),contraction_score=np.arange(10),
        proximity_score=np.arange(10),memory_score=np.arange(10),
        P00=[True]*10,P02=[True]*10,Q01=[True]*10,Q02=[False]*3+[True]*7))
    summary,rank,tails=r.summarize(e,1)
    q=summary.set_index('policy').loc['Q02']
    assert q['n']==7 and q.loss_count==0 and q.tail_profit_pct==100
    assert 'top_control_net_bp' in rank and 'top_p' in rank
    assert set(tails.policy)==set(r.POLICIES)


def test_daily_display_marks_retain_actual_source_timestamps_and_initial_nav():
    idx=pd.date_range('2025-01-01','2025-01-04',freq='15min',tz='UTC')
    full=pd.Series(np.linspace(1,2,len(idx)),index=idx)
    marks=r.daily_marks(full)
    assert marks.iloc[0]==1 and marks.index[0]==idx[0]
    assert marks.iloc[-1]==2 and marks.index[-1]==idx[-1]
    pd.testing.assert_series_equal(marks,full.loc[marks.index])
    assert pd.Timestamp('2025-01-01 23:45',tz='UTC') in marks.index
    assert pd.Timestamp('2025-01-02 00:00',tz='UTC') not in marks.index


def test_top_decile_matched_case_mean_does_not_include_unmatched_winner():
    g=pd.DataFrame(dict(event_id=['a','b'], net_bp=[1000,100],gross_bp=[1020,120],
        control_mean_net_bp=[np.nan,0],excess_bp=[np.nan,100],
        score=[2,1],month=['2025-01','2025-02']))
    # Build20 rows to get2 top-decile observations.
    rest=pd.DataFrame(dict(event_id=[str(i) for i in range(18)],net_bp=[-20]*18,
        gross_bp=[0]*18,control_mean_net_bp=[-20]*18,excess_bp=[0]*18,
        score=[0]*18,month=['2025-01']*18))
    out=r.ranking(pd.concat([g,rest],ignore_index=True),'score')
    assert out['top_net_bp']==550 and out['top_matched_n']==1
    assert out['top_matched_case_net_bp']==100
    assert out['top_excess_bp']==out['top_matched_case_net_bp']-out['top_control_net_bp']

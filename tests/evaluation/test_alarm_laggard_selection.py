"""Feature clocks, censoring bounds and contemporaneous-control contracts."""
import copy
import json

import numpy as np
import pandas as pd

from yoyo.evaluation.alarm_laggard_selection import OUT, STEP, causal_features, outcome_bounds, select_and_match
from yoyo.evaluation.alarm_laggard_statistics import interval_rates, summarize


def cfg():
    return json.loads((OUT/'config.json').read_text())


def test_boundary_touch_cannot_be_claimed_as_new_post_alarm_touch():
    times=np.arange(14)*STEP
    low=np.full(14,99.);close=np.full(14,100.)
    low[0]=94.;low[1]=93.
    result=outcome_bounds(times,low,close,60001,100.,cfg())
    assert result['deep_lower']==0 and result['deep_upper']==1
    assert result['joint_lower']==0 and result['joint_upper']==1
    assert result['initial_touch_ambiguous']==1


def test_contained_post_alarm_touch_then_later_close_proves_joint_lower():
    times=np.arange(14)*STEP
    low=np.full(14,99.);close=np.full(14,100.)
    low[1]=94.;close[1]=94.
    result=outcome_bounds(times,low,close,60001,100.,cfg())
    assert result['deep_lower']==result['deep_upper']==1
    assert result['joint_lower']==result['joint_upper']==1


def test_terminal_partial_bar_only_contributes_upper_bound():
    times=np.arange(14)*STEP
    low=np.full(14,99.);close=np.full(14,100.)
    low[3]=94.  # bar15..20minutes overlaps but is not contained in alarm+15m.
    result=outcome_bounds(times,low,close,60001,100.,cfg())
    assert result['deep_lower']==0 and result['deep_upper']==1


def test_missing_outcomes_are_censored_not_silently_negative():
    times=np.arange(14)*STEP
    low=np.full(14,np.nan);close=low.copy()
    result=outcome_bounds(times,low,close,60001,100.,cfg())
    assert result['deep_lower']==result['joint_lower']==0
    assert result['deep_upper']==result['joint_upper']==1
    assert result['future_censored']==1


def test_close_recovery_after_deadline_does_not_enter_lower_bound():
    times=np.arange(14)*STEP
    low=np.full(14,94.);low[0]=99.
    close=np.full(14,94.);close[12]=100.
    result=outcome_bounds(times,low,close,60001,100.,cfg())
    assert result['deep_lower']==1
    assert result['joint_lower']==0
    assert result['joint_upper']==1


def test_future_candles_cannot_change_prior_feature_values():
    c=cfg();c['features'].update(volatility_bars=12,history_bars=24,minimum_historical_tails=1)
    rng=np.random.default_rng(15);ret=rng.normal(0,.003,120)
    close=np.exp(ret.cumsum())*100
    f=pd.DataFrame({'open':close,'close':close,'high':close*1.01,'low':close*.99,'volume':100.})
    market=pd.Series(ret*.5+rng.normal(0,.001,120))
    before=causal_features(f,market,c)
    f.loc[90:,['open','close','high','low']]*=.5
    market.loc[90:]*=10
    after=causal_features(f,market,c)
    pd.testing.assert_frame_equal(before.iloc[:90],after.iloc[:90])


def candidates():
    n=90
    return pd.DataFrame(dict(alarm_id=0,symbol=[f'C{i:03}' for i in range(n)],
                             lag=np.arange(n),recovery=np.arange(n)[::-1],
                             sigma=np.tile(np.arange(9)+1,10),liquidity=np.tile(np.arange(10)+1,9),
                             not_deep=True,initial_touch_ambiguous=0,future_censored=0,
                             deep_lower=0,deep_upper=1,joint_lower=0,joint_upper=1))


def test_selection_is_invariant_to_future_labels_and_controls_match_same_alarm():
    c=cfg();f=candidates()
    records,selected=select_and_match(f,c)
    changed=f.copy();changed[['deep_lower','deep_upper','joint_lower','joint_upper']]=1
    changed_records,changed_selected=select_and_match(changed,c)
    pd.testing.assert_frame_equal(selected,changed_selected)
    assert (records.selected_n==9).all()
    assert (records.matched_n>0).all()
    assert (changed_records.control_joint_lower==changed_records.matched_n).all()


def test_conditional_recovery_interval_uses_deep_uncertainty():
    f=pd.DataFrame({'deep_lower':[1,0,0],'deep_upper':[1,1,1],
                    'joint_lower':[1,0,0],'joint_upper':[1,1,0]})
    r=interval_rates(f)['rates']
    assert r['conditional_recovery_lower']==1/3
    assert r['conditional_recovery_upper']==1.


def test_year_masks_do_not_mutate_alarm_membership(tmp_path):
    c=cfg();c['statistics'].update(bootstrap_repeats=10,permutation_repeats=9)
    frames=[]
    for i in range(3):
        f=candidates();f['alarm_id']=i;frames.append(f)
    f=pd.concat(frames,ignore_index=True);records,_=select_and_match(f,c)
    times=pd.to_datetime(['2025-06-01T00:00Z','2026-06-01T00:00Z','2026-08-22T00:00Z'])
    alarms=pd.DataFrame(dict(alarm_id=range(3),alarm_ms=times.astype('int64')//10**6,
                             six=True,btc=True,design_day=[False,False,True]))
    original=alarms.copy(deep=True)
    result=summarize(records,f,alarms,c,tmp_path)
    for method in ('six','btc'):
        assert result['results'][method]['2025']['all_alarms']==1
        assert result['results'][method]['2026']['all_alarms']==1
        assert result['results'][method]['pooled']['all_alarms']==2
        assert result['results'][method]['design_day']['all_alarms']==1
    pd.testing.assert_frame_equal(original,alarms)

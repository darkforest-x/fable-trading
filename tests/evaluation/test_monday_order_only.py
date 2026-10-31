"""The order-only treatment must not silently change targets or position rules."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_order_only import construct, select_track, cache_match, accounts, worse_paths

CFG=json.loads((Path(__file__).resolve().parents[2]/'experiments/active/exp-monday-order-only-20261031-v1/config.json').read_text())


def orders():
    rows=[]
    for event in ('low','high','missing','unselected'):
        for minutes in (5,60):
            for mode,target in [('near_monday_edge',105.),('range_0.618',115.),('opposite_1618',130.)]:
                for ctl in (0,1):
                    rows.append(dict(event_id=event,reclaim_minutes=minutes,exit_mode=mode,
                                     trade_id=f'{event}:m{minutes}:{mode}:c{ctl}',control_id=ctl,
                                     side=1,entry=100.,target_price=target,entry_i=0,expire_i=10,end_i=20))
    return pd.DataFrame(rows)


def selection():
    return pd.DataFrame({'score':[.1,.7,np.nan],'l2_order_score':[3,4,3]},index=['low','high','missing'])


def table():
    x=construct(orders(),selection(),np.array([110.,0.,999.]),.01,CFG)
    x['mode']=x.exit_mode;x['minutes']='m'+x.reclaim_minutes.astype(str)
    return x


def test_low_scores_enter_but_keep_original_target_and_controls():
    t=select_track(table(),'v3',CFG)
    assert set(t.event)=={'low','high'}
    assert set(t[t.event=='low']['mode'])=={'range_0.618'}
    assert set(t[t.event=='high']['mode'])=={'opposite_1618'}
    assert set(t.control_id)=={0,1}


def test_missing_scores_are_usable_only_without_score_dependent_targets():
    x=table()
    for track in ('v2_60','v2_5','spike','allin3'):
        assert 'missing' in set(select_track(x,track,CFG).event)
    for track in ('v3','v3lock','v3add','v3add20'):
        assert 'missing' not in set(select_track(x,track,CFG).event)


def test_primary_replacement_does_not_enable_low_score_adds():
    x=select_track(table(),'v3add',CFG)
    assert set(x[x.rule=='add'].event)=={'high'}
    assert (x[x.rule=='add'].entry==95.).all()
    assert x.rule.tolist()==sorted(x.rule.tolist(),key=lambda k:k=='add')
    assert set(x[x.rule=='add'].parent)<=set(x.trade_id)


def test_future_closes_cannot_change_orders_or_selection():
    a=construct(orders(),selection(),np.array([110.,0.,999.]),.01,CFG)
    b=construct(orders(),selection(),np.array([110.,9999.,-500.]),.01,CFG)
    pd.testing.assert_frame_equal(a,b)
    assert 'unselected' not in set(a.event)


def test_frozen_protection_and_one_tick_cache_rejection():
    x=table()
    assert set(x[x.rule=='spike'].stop)=={82.}
    assert set(x[x.rule=='p30'].stop)=={70.}
    assert set(x[x.rule=='lock'].be_offset_bp)=={50.}
    assert set(x[x.rule!='lock'].be_offset_bp)=={17.}
    r=x.iloc[0].to_dict()
    assert cache_match(r,r,.01)
    assert not cache_match(dict(r,stop=r['stop']+.01),r,.01)


def test_nonpositive_original_linear_size_is_unfunded():
    x=pd.DataFrame([dict(event='X:2026-01-05:+1',score=.1,entry_time='2026-01-06T00:00:00Z',
                         exit_time='2026-01-06T01:00:00Z',net=100.,mae_pct=0.,side=1,reason=4)])
    result,ledger,_=accounts('v3',x,CFG,'2026-01-01')
    assert result['funded']==0 and result['equity']==1000.
    assert len(ledger)==1


def test_allin_replenishment_remains_external_cash():
    x=pd.DataFrame([dict(event='X:2026-01-05:+1',score=np.nan,entry_time='2026-01-06T00:00:00Z',
                         exit_time='2026-01-06T01:00:00Z',net=-5000.,mae_pct=40.,side=1,reason=1)])
    result,ledger,_=accounts('allin3',x,CFG,'2026-01-01')
    assert result['external_cash']==1000. and result['net_pnl']==-1000.
    assert result['final_multiple'] is None and result['equity']==1000.


def test_one_path_child_keeps_original_ohlc_universe_and_outcome():
    o=pd.DataFrame({'rule':['add']*4,'filled':[True,True,False,True],'net':[100.,-100.,0.,100.]})
    l=pd.DataFrame({'rule':['add']*4,'filled':[False,False,True,True],'net':[0.,0.,80.,50.]})
    w=worse_paths(o,l)
    assert w.net.tolist()==[100.,-100.,0.,50.]
    assert w.filled.tolist()==[True,True,False,True]

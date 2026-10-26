"""Behavioral contracts for delayed entry and known pin risk, no market data."""
import ast
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_confirmed_r_engine import execute
from yoyo.evaluation.monday_confirmed_score import five_bars,hourly_bars,feature_row,score,MIN5,HOUR
from yoyo.evaluation.monday_confirmed_r_study import prepare


def frame(rows, freq='5min'):
    return pd.DataFrame(rows,columns=['open','high','low','close'],index=pd.date_range('2026-01-06',periods=len(rows),freq=freq,tz='UTC')).assign(volume=100.)


def event(entry=100.,stop=99.,side=1,end=1):
    return pd.DataFrame([dict(entry_i=0,end_i=end,entry=entry,entry_quote=entry,stop=stop,risk=side*(entry-stop),side=side)])


def test_static_target_and_fee_accounting():
    f=frame([[100,100.5,99.5,100],[100,102.5,99.5,102]])
    t,orders=execute(f,event(),[2],slip=0)
    assert len(orders)==4
    assert (t.reason==4).all()
    np.testing.assert_allclose(t.gross_r,2,atol=1e-9)
    np.testing.assert_allclose(t.net_bp,(2-.05-.0204)/100*10000,atol=1e-8)


def test_same_bar_stop_precedes_target():
    t,_=execute(frame([[100,102,98,100]]),event(end=0),[1],slip=0)
    assert t.reason.eq(1).all() and t.ambiguous.all()


def test_fee_protection_is_post_entry_close_only():
    f=frame([[100,100.2,99.9,100.2],[100.2,100.3,100.05,100.2],[100.2,102,100,101]])
    t,_=execute(f,event(end=2),[1],slip=.001)
    be=t[t.be_mode.eq('fee_close')].iloc[0]
    assert be.reason==5 and be.exit_i==1 and be.be_armed
    assert -10.02<be.net_bp<-9.98
    fixed=t[t.be_mode.eq('fixed_stop')].iloc[0]
    assert fixed.reason==4 and fixed.exit_i==2


def test_stop_before_qualifying_close_is_never_protected():
    f=frame([[100,100.5,98.5,100.3],[100.3,102,100,101]])
    t,_=execute(f,event(),[1],slip=0)
    assert t.reason.eq(1).all() and not t.be_armed.any()


def test_one_minute_requires_completed_five():
    f=frame([[100,100.3,99.9,100.2],[100.2,100.3,100.05,100.2],[100.2,100.3,100.05,100.2],
        [100.2,100.3,100.05,100.2],[100.2,100.3,100.05,100.2],[100.2,100.3,100.05,100.2]],freq='1min')
    t,_=execute(f,event(end=5),[2],slip=0,step=1)
    be=t[t.be_mode.eq('fee_close')].iloc[0]
    assert be.reason==5 and be.exit_i==5


def test_short_mirror_and_gap_loss():
    t,_=execute(frame([[100,100.5,99.5,100],[102,103,101,102]]),event(stop=101,side=-1),[1],slip=0)
    assert t.reason.eq(1).all()
    np.testing.assert_allclose(t.exit_price,102)
    np.testing.assert_allclose(t.gross_r,-2)


def test_broadcast_matches_scalar():
    f=frame([[100,100.5,99.5,100],[100,105,99.5,102]])
    batch,_=execute(f,event(),[1,2,3],slip=.001)
    for target in [1,2,3]:
        one,_=execute(f,event(),[target],slip=.001)
        np.testing.assert_allclose(batch.loc[batch.target_r.eq(target),'net_bp'],one.net_bp)


def test_censored_is_not_a_booked_win():
    t,_=execute(frame([[100,100.1,99.9,100]]),event(end=0),[10],slip=0)
    assert t.reason.eq(0).all() and t.net_bp.isna().all() and t.boundary_mark_bp.notna().all()


def test_score_matches_live_and_ignores_touch_close():
    idx=pd.date_range('2026-01-01',periods=12*24*12,freq='5min',tz='UTC')
    x=100+np.arange(len(idx))*.001
    f=pd.DataFrame(dict(open=x,high=x+1,low=x-1,close=x+.1,volume=100),index=idx)
    five=five_bars(f,5);h=hourly_bars(five);touch=idx[-30].value
    end=pd.Timestamp('2026-01-06',tz='UTC').value
    feat=feature_row(five,h,1,105,102,100,end,touch,False,5)
    source=Path('yoyo/monitor/monday_reclaim_paper.py').read_text();tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='features')
    ns=dict(math=math,MIN5=300000,HOUR=3600000)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<live features>','exec'),ns)
    k=np.searchsorted(five.index.as_unit("ns").asi8,touch);j=np.searchsorted(h.index.as_unit("ns").asi8+HOUR,touch,side='right')
    def rows(d):return [(int(i.value//1000000),r.open,r.high,r.low,r.close,r.qv) for i,r in d.iterrows()]
    live=ns['features'](rows(five.iloc[k-299:k+1]),rows(h.iloc[max(0,j-300):j]),1,
        dict(high=105,low=102,lower=100,upper=107,end=end//1000000),touch//1000000,False,5)
    for key in feat:np.testing.assert_allclose(feat[key],live[key],atol=1e-12)
    changed=five.copy();changed.loc[changed.index.as_unit("ns").asi8>=touch,['high','low','close','qv']]*=10
    feat2=feature_row(changed,hourly_bars(changed),1,105,102,100,end,touch,False,5)
    assert feat==feat2


def test_confirmation_then_next_open_and_pin_stop():
    f=frame([[101,102,98,99],[99,102,97,101],[102,103,101,102],[102,104,101,103]])
    old=pd.DataFrame([dict(event_id='X',trade_id='Xc1',symbol='X',week='2026-01-05',side=1,population='random',
        entry=100.,entry_i=0,entry_ns=f.index[0].value,confirm_i=1,end_i=3,monday_high=110,monday_low=105,level=100)])
    admitted,audit=prepare(f,old,dict(execution_minutes=5),pd.DataFrame(),{},.001)
    r=admitted.iloc[0]
    assert r.entry_i==2 and r.stop==98 and r.entry==102*1.001 and r.risk==r.entry-98
    f.loc[f.index[2],'open']=97
    admitted,audit=prepare(f,old,dict(execution_minutes=5),pd.DataFrame(),{},.001)
    assert admitted.empty and audit.admission.iloc[0]=='entry_open_beyond_pin_stop'

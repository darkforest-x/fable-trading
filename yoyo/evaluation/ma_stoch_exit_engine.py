"""Research exits for frozen MA Shift/Stoch entries, never a live executor.

See exp-ma-stoch-exit-optimization-20260915-v1/PROJECT_PLAN.md. Features read
current/prior high, low, close: Wilder ATR14 (SMA14 seed), SMA20 true-range
volatility, K5/SMA3/D3 and completed-15m hl2/SMA40. ATR at entry is frozen.
Future bars are used exclusively to simulate outcomes. New close-based stops
become active on the next bar; ambiguous OHLC stop/target hits use stop first.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd
from yoyo.evaluation.ma_shift_stoch import build_signals, BAR, _validate_frame
from yoyo.evaluation.spike_fanshen_exit import compute_signals


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    reference: str = 'baseline'
    stop_atr: float = 0.0
    target_r: float = 0.0
    partial_fraction: float = 1.0
    break_even_r: float = 0.0
    trail_atr: float = 0.0
    trail_start_r: float = 1.0
    reverse: str = 'arrow'
    max_bars: int = 0


POLICIES = {p.name:p for p in [
    ExitPolicy('baseline'),ExitPolicy('reverse_kd',reverse='kd'),ExitPolicy('reverse_ma',reverse='ma'),
    *[ExitPolicy(f'time_{n}',max_bars=n) for n in (6,12,24)],
    *[ExitPolicy(f'stop_{n}',stop_atr=n) for n in (1,2,3)],
    *[ExitPolicy(f'tp_{n}',reference='stop_2',stop_atr=2,target_r=n) for n in (1,2,3)],
    ExitPolicy('break_even',reference='stop_2',stop_atr=2,break_even_r=1),
    ExitPolicy('partial_1r',reference='stop_2',stop_atr=2,target_r=1,partial_fraction=.5),
    *[ExitPolicy(f'trail_{n}',reference='stop_2',stop_atr=2,trail_atr=n) for n in (1,2)],
]}


def prepare(frame):
    """Precompute closed-bar entry features once, using no future observations."""
    frame=_validate_frame(frame)
    sig=build_signals(frame); stoch=compute_signals(frame)
    prev=frame.close.shift(1)
    tr=pd.concat([frame.high-frame.low,(frame.high-prev).abs(),(frame.low-prev).abs()],axis=1).max(axis=1).to_numpy(float)
    atr=np.full(len(frame),np.nan)
    if len(frame)>=14:
        atr[13]=np.mean(tr[:14])
        for i in range(14,len(frame)):atr[i]=(13*atr[i-1]+tr[i])/14
    kd=np.where((stoch.k>stoch.d)&(stoch.k.shift(1)<=stoch.d.shift(1)),1,
                np.where((stoch.k<stoch.d)&(stoch.k.shift(1)>=stoch.d.shift(1)),-1,0))
    return dict(frame=frame,index=frame.index,open=frame.open.to_numpy(float),high=frame.high.to_numpy(float),
                low=frame.low.to_numpy(float),close=frame.close.to_numpy(float),atr=atr,
                vol=pd.Series(tr).rolling(20,min_periods=20).mean().to_numpy()/frame.close.to_numpy(),
                arrow=np.where(sig.arrow_long,1,np.where(sig.arrow_short,-1,0)),
                admission=np.where(sig.entry_long,1,np.where(sig.entry_short,-1,0)),
                direction=sig.direction_15m.fillna(0).to_numpy(int),kd=kd,signals=sig)


def _round(price,up,tick):
    return (math.ceil(price/tick-1e-9) if up else math.floor(price/tick+1e-9))*tick


def simulate(ctx,start,end,policy,*,forced=None,initial_equity=1000.,tick=.01,keep_curve=True):
    """Serial replay or one forced (signal_i, side) control with same exits.

    Intrabar exit_time is the bar close bound, not an invented tick timestamp;
    fills preserve the actual bar_open and timing='intrabar'. Target gaps fill
    at the observed open. The 10bp opening and closing costs use entry notional.
    """
    if isinstance(policy,str):policy=POLICIES[policy]
    start,end=pd.Timestamp(start),pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None or start>=end or start.value%BAR.value or end.value%BAR.value:
        raise ValueError('explicit timezone and aligned increasing 5m endpoints required')
    if end>pd.Timestamp('2026-05-01T00:00:00Z'):
        raise ValueError('exit optimisation cannot read/score holdout')
    idx=ctx['index'];first=int(idx.searchsorted(start));last=int(idx.searchsorted(end))
    if last==0 or idx[last-1]+BAR!=end:raise ValueError('complete source through endpoint required')
    if forced is not None:
        fi,fs=forced
        if fi<0 or fi+1>=last or fs not in (-1,1):raise ValueError('invalid forced entry')
        first=fi+1
    trades=[];fills=[];marks=[];equity=float(initial_equity);state=None;next_id=1;ambiguous=0
    if keep_curve:marks.append(dict(time=start,equity=equity,trade_id=None))

    def close_part(i,price,fraction,reason,timing):
        nonlocal state,equity
        fraction=min(fraction,state['remaining'])
        gross=fraction*state['side']*(price/state['entry_price']-1)
        cost=.001*fraction; net=gross-cost
        state['gross']+=gross;state['net']+=net;state['remaining']-=fraction
        state['exit_value']+=fraction*price
        fills.append(dict(trade_id=state['trade_id'],kind='exit',bar_open=idx[i],
                          time=idx[i] if timing=='open' else idx[i]+BAR,timing=timing,side=state['side'],
                          price=price,fraction=fraction,gross_return=gross,cost_return=cost,net_return=net,reason=reason))
        if state['remaining']<1e-10:
            r=dict(trade_id=state['trade_id'],policy=policy.name,signal_i=state['signal_i'],entry_i=state['entry_i'],
                   entry_time=idx[state['entry_i']],side=state['side'],entry_price=state['entry_price'],
                   entry_equity=state['entry_equity'],entry_atr=state['entry_atr'],initial_stop=state['initial_stop'],
                   initial_risk=state['risk'],exit_i=i,exit_time=idx[i] if timing=='open' else idx[i]+BAR,
                   exit_timing=timing,exit_price_weighted=state['exit_value'],exit_reason=reason,
                   gross_return=state['gross'],net_return=state['net'],cost_return=.002,
                   gross_r=state['gross']*state['entry_price']/state['risk'] if state['risk'] else np.nan,
                   net_r=state['net']*state['entry_price']/state['risk'] if state['risk'] else np.nan)
            equity=state['entry_equity']*(1+state['net']);trades.append(r);state=None

    def stop_hit(price):
        return state is not None and state['stop'] is not None and state['side']*(price-state['stop'])<=0

    def target_hit(price):
        return state is not None and state['target'] is not None and state['side']*(price-state['target'])>=0

    def hit_target(i,price,timing):
        amount=policy.partial_fraction
        close_part(i,price,amount,'partial_target' if amount<1 else 'target',timing)
        if state is not None:state['target']=None

    for i in range(first,last):
        o,h,l,c=ctx['open'][i],ctx['high'][i],ctx['low'][i],ctx['close'][i];prior=i-1
        was_forced_closed=bool(forced is not None and trades)
        if was_forced_closed:break
        if state is not None and stop_hit(o):close_part(i,o,state['remaining'],'stop_gap','open')
        if state is not None and target_hit(o):hit_target(i,o,'open')
        if state is not None and prior>=0:
            opposite=ctx['arrow'][prior]==-state['side']
            kd_exit=policy.reverse=='kd' and ctx['kd'][prior]==-state['side']
            ma_exit=policy.reverse=='ma' and ctx['direction'][prior]==-state['side']
            timed=policy.max_bars>0 and i-state['entry_i']>=policy.max_bars
            if opposite or kd_exit or ma_exit or timed:
                reason='reverse_arrow' if opposite else 'reverse_kd' if kd_exit else 'reverse_ma' if ma_exit else 'time'
                close_part(i,o,state['remaining'],reason,'open')
        candidate=int(ctx['admission'][prior]) if prior>=0 else 0
        if forced is not None:candidate=int(forced[1]) if prior==forced[0] and not trades else 0
        if state is None and candidate and equity>0:
            entry_atr=ctx['atr'][prior]
            if policy.stop_atr and (not np.isfinite(entry_atr) or entry_atr<=0):raise ValueError('ATR unavailable')
            stop=_round(o-candidate*policy.stop_atr*entry_atr,candidate==-1,tick) if policy.stop_atr else None
            if stop is not None and stop<=0:raise ValueError('invalid nonpositive stop')
            risk=abs(o-stop) if stop is not None else 0.
            target=_round(o+candidate*policy.target_r*risk,candidate==1,tick) if policy.target_r else None
            state=dict(trade_id=next_id,signal_i=prior,entry_i=i,side=candidate,entry_price=o,entry_equity=equity,
                       entry_atr=entry_atr,initial_stop=stop,stop=stop,risk=risk,target=target,
                       gross=0.,net=-.001,remaining=1.,exit_value=0.)
            fills.append(dict(trade_id=next_id,kind='entry',bar_open=idx[i],time=idx[i],timing='open',side=candidate,
                              price=o,fraction=1.,gross_return=0.,cost_return=.001,net_return=-.001,reason='arrow_next_open'))
            next_id+=1
        if state is not None:
            adverse=l if state['side']==1 else h;favorable=h if state['side']==1 else l
            sh,th=stop_hit(adverse),target_hit(favorable)
            if sh and th:ambiguous+=1
            if sh:close_part(i,state['stop'],state['remaining'],'stop','intrabar')
            elif th:hit_target(i,state['target'],'intrabar')
        if state is not None:
            side=state['side'];unreal=side*(c-state['entry_price'])
            proposals=[]
            if policy.break_even_r and unreal>=policy.break_even_r*state['risk']:
                proposals.append(_round(state['entry_price']*(1+side*.002),side==1,tick))
            if policy.trail_atr and unreal>=policy.trail_start_r*state['risk']:
                proposals.append(_round(c-side*policy.trail_atr*state['entry_atr'],side==-1,tick))
            # These assignments happen after all fills in bar i, so new stops
            # can execute only from bar i+1 onward, including its opening gap.
            for level in proposals:
                state['stop']=max(state['stop'],level) if side==1 else min(state['stop'],level)
        if keep_curve:
            marked=equity if state is None else state['entry_equity']*(1+state['net']+state['remaining']*(state['side']*(c/state['entry_price']-1)-.001))
            marks.append(dict(time=idx[i]+BAR,equity=marked,trade_id=state['trade_id'] if state else None))
        if forced is not None and state is None and trades:break
    opened=[]
    if state is not None:
        i=last-1;unreal=state['remaining']*state['side']*(ctx['close'][i]/state['entry_price']-1)
        net=state['net']+unreal-.001*state['remaining']
        opened=[dict(trade_id=state['trade_id'],entry_time=idx[state['entry_i']],side=state['side'],entry_price=state['entry_price'],remaining=state['remaining'],marked_time=end,marked_price=ctx['close'][i],marked_net_return=net)]
        final=state['entry_equity']*(1+net)
    else:final=equity
    tc=['trade_id','policy','signal_i','entry_i','entry_time','side','entry_price','entry_equity','entry_atr','initial_stop','initial_risk','exit_i','exit_time','exit_timing','exit_price_weighted','exit_reason','gross_return','net_return','cost_return','gross_r','net_r']
    result=dict(trades=pd.DataFrame(trades,columns=tc),fills=pd.DataFrame(fills),open_positions=pd.DataFrame(opened),curve=pd.DataFrame(marks),
                final_equity=final,closed_equity=equity,ambiguous_bars=ambiguous)
    return result

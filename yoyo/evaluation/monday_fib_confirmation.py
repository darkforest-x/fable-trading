"""First-touch observation, forming-bar Fanshen, and timed cost-aware protection.

Owner October22: touch starts285minutes; arrow APPEARANCE and completed15m
body are independent latched events. Features read only past complete higher
bars plus the current minute's cumulative high/low/close. One-minute sampling
cannot observe sub-minute flicker. Native Nautilus executes every exit; a
separate integer-tick oracle audits protection, targets and chronology.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_fib_limits as base
from yoyo.evaluation.pepe_range_exits import signals, inference
from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

SOURCE=ROOT/'experiments/active/exp-btc-monday-fib1618-okx-20261022-v1'
EXP=ROOT/'experiments/active/exp-btc-monday-fib-confirm-20261022-v1'
CODE=['yoyo/evaluation/monday_fib_confirmation.py',
      'tests/evaluation/test_monday_fib_confirmation.py',
      'yoyo/evaluation/pepe_range_exits.py',
      'yoyo/layers/l3_backtest/nautilus_independent.py']
ARMS=('15m','30m','60m','any')


def targets(high,low,side,tick):
    """Owner's four directional targets, each25percent of original notional."""
    fractions=(.5,.618,.705,1.) if side==1 else (.5,.382,.295,0.)
    return {f'tp{j+1}':base.rounded(low+f*(high-low),tick,side==1)
            for j,f in enumerate(fractions)}


def legs(entries):
    return pd.DataFrame([dict(r,parent_id=r['trade_id'],trade_id=f"{r['trade_id']}-tp{j}",
        target_price=r[f'tp{j}'],leg=f'tp{j}',weight=.25)
        for r in entries.to_dict('records') for j in (1,2,3,4)])


def aggregate(frame, minutes):
    """Only complete buckets of minute-close OHLC; no partial final bucket."""
    g=frame.resample(f'{minutes}min',closed='right',label='right',origin='epoch')
    out=g.agg(dict(open='first',high='max',low='min',close='last'))
    return out[g.close.count().eq(minutes)]


def forming(frame, minutes):
    """Current forming Stoch5/3/3, compared with PREVIOUS higher-bar K/D.

    Cumulative OHLC resets at each higher open. Prior rawK/K/D use only
    completed higher candles. The crossover is Pine's higher-bar crossover,
    not a crossover between adjacent minute samples. New appearances latch
    after false-to-true, including reappearance within a forming bar.
    """
    higher=aggregate(frame,minutes)
    lo=higher.low.rolling(5).min();hi=higher.high.rolling(5).max()
    raw=100*(higher.close-lo)/(hi-lo)
    k=raw.rolling(3).mean().round(10);d=k.rolling(3).mean().round(10)
    prior=pd.DataFrame(dict(lo4=higher.low.rolling(4).min(),
        hi4=higher.high.rolling(4).max(),raw2=raw.rolling(2).sum(),
        k2=k.rolling(2).sum(),k=k,d=d))
    opened=(frame.index-pd.Timedelta(minutes=1)).floor(f'{minutes}min')
    p=prior.reindex(opened).set_axis(frame.index)
    high=frame.high.groupby(opened).cummax();low=frame.low.groupby(opened).cummin()
    high=np.maximum(high,p.hi4);low=np.minimum(low,p.lo4)
    current_raw=100*(frame.close-low)/(high-low)
    current_k=((p.raw2+current_raw)/3).round(10)
    current_d=((p.k2+current_k)/3).round(10)
    bull=(current_k>current_d)&(p.k<=p.d)&(current_k<20)&(current_d<20)
    bear=(current_k<current_d)&(p.k>=p.d)&(current_k>80)&(current_d>80)
    state=bull.astype(int)-bear.astype(int)
    same_bar=pd.Series(opened,index=frame.index).eq(pd.Series(opened,index=frame.index).shift())
    event=state.where(~(state.eq(state.shift())&same_bar),0)
    final=signals(higher)
    np.testing.assert_array_equal(state.reindex(higher.index),final)
    return pd.DataFrame(dict(state=state,event=event,k=current_k,d=current_d,
                             higher_open=opened),index=frame.index)


def observe(frame, touch_i, expiry_i, side, level, events, quarter, window=285):
    """Two independent post-touch latches; neither is retroactively cancelled.

    A body is qualified only at its15m close, even if its open preceded touch.
    An arrow can appear before or after that body, and may disappear later.
    Equality at the key is not strictly inside. Deadline is inclusive.
    """
    end=min(touch_i+window,expiry_i,len(frame)-2)
    body_i=arrow_i=-1;lane=0
    q=quarter if quarter.index.equals(frame.index) else quarter.reindex(frame.index)
    for i in range(touch_i,end+1):
        if body_i<0 and np.isfinite(q.open.iloc[i]):
            if side*(q.open.iloc[i]-level)>1e-8 and side*(q.close.iloc[i]-level)>1e-8:
                body_i=i
        if arrow_i<0:
            candidates=[m for m,e in events.items() if e[i]==side]
            if candidates:
                arrow_i=i;lane=min(candidates)
        if body_i>=0 and arrow_i>=0:
            return dict(status='confirmed',entry_i=i,arrow_i=arrow_i,body_i=body_i,
                        arrow_minutes=lane,wait_minutes=i-touch_i)
    status='data_censored' if end==len(frame)-2 and end<touch_i+window else 'expired'
    return dict(status=status,entry_i=-1,arrow_i=arrow_i,body_i=body_i,
                arrow_minutes=lane,wait_minutes=-1)


def prepare(frame,cfg):
    weekly=pd.read_csv(SOURCE/'weekly_orders.csv',float_precision='round_trip')
    original=pd.read_csv(SOURCE/'signal_trades.csv',float_precision='round_trip')
    features={m:forming(frame,m) for m in (15,30,60)}
    events={m:f.event.to_numpy(int) for m,f in features.items()}
    quarter=aggregate(frame,15).reindex(frame.index)
    touch={};decisions=[];entries=[];boundary_changes=[]
    bars=frame[base.OHLC].to_numpy()
    for r in weekly.itertuples():
        first=int(r.entry_i)+1;last=int(r.expire_i)
        values=bars[first:last+1,2 if r.side==1 else 1]
        hits=np.flatnonzero(r.side*(values-r.entry)<=1e-8)
        if not len(hits):
            continue
        i=first+int(hits[0]);touch[r.trade_id]=i
        for arm in ARMS:
            subset=events if arm=='any' else {int(arm[:-1]):events[int(arm[:-1])]}
            result=observe(frame,i,last,r.side,r.entry,subset,quarter,cfg['wait_minutes'])
            conservative=observe(frame,i,last,r.side,r.entry,subset,quarter,cfg['wait_minutes']-1)
            if (result['status'],result['entry_i'])!=(conservative['status'],conservative['entry_i']):
                boundary_changes.append(dict(pair_id=r.pair_id,arm=arm))
            row=dict(pair_id=r.pair_id,week=r.week,side=r.side,arm=arm,
                touch_i=i,touch_time=str(frame.index[i]),level=r.entry,
                **targets(r.monday_high,r.monday_low,r.side,cfg['tick']),**result)
            if result['status']=='confirmed':
                j=result['entry_i'];entry=float(frame.close.iloc[j])
                if r.side*(row['tp1']-entry)<cfg['tick']-1e-8:
                    row['status']='target_already_passed'
                else:
                    stop=base.rounded(entry*(1-r.side*cfg['stop_fraction']),cfg['tick'],r.side==-1)
                    row.update(entry=entry,stop=stop,risk=r.side*(entry-stop),
                        entry_time=str(frame.index[j]),population='signal',
                        trade_id=f'{arm}-{r.pair_id}',end_i=len(frame)-1,
                        segment=int(frame.index[j]>=pd.Timestamp(cfg['split'])))
                    m=result['arrow_minutes'];a=result['arrow_i']
                    finish=features[m].higher_open.iloc[a]+pd.Timedelta(minutes=m)
                    row['arrow_disappeared_by_close']=bool(finish in features[m].index and features[m].state.loc[finish]!=r.side)
                    entries.append(row.copy())
            decisions.append(row)
    assert touch==dict(zip(original.parent_id,original.entry_i)), 'First-touch cohort changed'
    # Prefix reconstruction checks the forming candle with all future removed.
    prefix_checks=0
    for row in entries[::max(1,len(entries)//12)][:12]:
        cut=int(row['entry_i'])+1
        for m in (15,30,60):
            f=forming(frame.iloc[:cut],m)
            np.testing.assert_array_equal(f.event.iloc[-min(300,cut):],features[m].event.iloc[max(0,cut-300):cut])
            prefix_checks+=1
    return pd.DataFrame(entries),pd.DataFrame(decisions),dict(
        weeks=int(weekly.week.nunique()),weekly_sides=len(weekly),touched=len(touch),
        prefix_checks=prefix_checks,touch_minute_deadline_sensitivity=boundary_changes,
        forming_matches_all_completed_arrows=True)


def controls(frame,entry,cfg):
    """Random close-time null: same coin/week/side/half/causal15m ATR bucket.

    Includes identical target percentage geometry, SL and subsequent BE.
    These background controls do not require the strategy's confirmation.
    """
    q=aggregate(frame,15);vol=(atr(q,14)/q.close).reindex(frame.index,method='ffill')
    bucket=np.digitize(vol.to_numpy(),cfg['volatility_edges'])
    local=frame.index.tz_convert(cfg['timezone']);day=local.tz_localize(None).normalize()
    weeks=(day-pd.to_timedelta(local.dayofweek,unit='D')).strftime('%Y-%m-%d').to_numpy()
    segment=np.asarray(frame.index>=pd.Timestamp(cfg['split']))
    good=np.asarray((local.dayofweek>=1)&(frame.index>=pd.Timestamp(cfg['start']))
        &(frame.index<pd.Timestamp(cfg['end'])-pd.Timedelta(minutes=1)))&vol.notna().to_numpy()
    for i in entry.entry_i.unique(): good[max(0,i-5):i+6]=False
    rng=np.random.default_rng(cfg['seed']);used={};out=[]
    for r in entry.sort_values(['arm','entry_i','side']).itertuples():
        key=(r.arm,r.side);used.setdefault(key,set());b=int(bucket[r.entry_i])
        pool=np.flatnonzero(good&(weeks==r.week)&(segment==r.segment)&(bucket==b))
        pool=np.array([i for i in pool if i not in used[key]],dtype=int)
        choice=rng.choice(pool,min(cfg['controls_per_signal'],len(pool)),replace=False)
        assert len(choice)==cfg['controls_per_signal']
        for k,i in enumerate(choice):
            used[key].add(i);p=float(frame.close.iloc[i])
            stop=base.rounded(p*(1-r.side*cfg['stop_fraction']),cfg['tick'],r.side==-1)
            out.append(dict(trade_id=f'random-{r.trade_id}-{k}',pair_id=r.pair_id,
                arm=r.arm,week=r.week,population='random',side=r.side,entry_i=int(i),
                entry=p,stop=stop,risk=r.side*(p-stop),end_i=len(frame)-1,
                **{f'tp{j}':base.rounded(p*getattr(r,f'tp{j}')/r.entry,cfg['tick'],r.side==1) for j in (1,2,3,4)},
                entry_time=str(frame.index[i]),segment=r.segment,bucket=b))
    return pd.DataFrame(out)


def oracle(bars,r,delay,tick,cost,path='OHLC'):
    """Independent audit only; never supplies native trades or fills."""
    side=int(r.side);stop=round(r.stop/tick);target=round(r.target_price/tick)
    be=round(base.rounded(r.entry*(1+side*cost),tick,side==1)/tick)
    kind=1;trigger=effective=-1
    for i in range(int(r.entry_i)+1,int(r.end_i)+1):
        o,h,l,c=bars[i];points=[o,h,l,c] if path=='OHLC' else [o,l,h,c]
        if side*(o-stop)<=0: return i,o*tick,kind,trigger,effective
        if side*(o-target)>=0: return i,target*tick,4,trigger,effective
        for a,b in zip(points[:-1],points[1:]):
            hits=[(abs(p-a),p,reason) for p,reason in ((stop,kind),(target,4)) if min(a,b)<=p<=max(a,b)]
            if hits:
                _,p,reason=min(hits);return i,p*tick,reason,trigger,effective
        # Frame starts00:01 UTC, so index+1 follows the native5m clock.
        if delay and (i+1)%5==0 and i-r.entry_i>=delay:
            if trigger<0: trigger=i
            if effective<0 and side*(c-be)>=1:
                effective=i;stop=be;kind=5
        if i==r.end_i:return i,c*tick,0,trigger,effective
    raise AssertionError('No terminal mark')


def execute(frame,entries,delay,cfg,mode='market'):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    folder=EXP/f'native_{mode}_be_{delay}';folder.mkdir(exist_ok=True)
    prepared=legs(entries)
    identity=dict(config=cfg,delay=delay,mode=mode,inputs=[file_stamp(SOURCE/'candles.csv.gz')],
        entries=json.loads(prepared.to_json(orient='records',double_precision=15)),
        code=[file_stamp(ROOT/p) for p in CODE])
    receipt=folder/'identity.json'
    if receipt.exists():
        assert json.loads(receipt.read_text())==identity,'Changed execution identity'
        return pd.read_csv(folder/'parents.csv',float_precision='round_trip'),json.loads((folder/'audit.json').read_text())
    rows,fills,audit=run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),prepared,
        ExitPolicy(target_from_entry=True,opposite_hour_exit=False,
            time_breakeven_minutes=delay or None,round_trip_cost=cfg['round_trip_cost']),
        tick=cfg['tick'],symbol='BTC',execution_minutes=1,entry_order_mode=mode)
    bars=np.rint(frame[base.OHLC].to_numpy()/cfg['tick']).astype(np.int64)
    reversed_r=[]
    original={r.trade_id:r for r in prepared.itertuples()}
    for r in rows.itertuples():
        if mode=='limit':
            assert delay==0
            o=original[r.trade_id]
            value=base.path_oracle(bars,o,mode,cfg['tick'])
            expected=(*value[1:],-1,-1)
            reverse=base.path_oracle(bars,o,mode,cfg['tick'],'OLHC')[1:]
        else:
            expected=oracle(bars,r,delay,cfg['tick'],cfg['round_trip_cost'])
            reverse=oracle(bars,r,delay,cfg['tick'],cfg['round_trip_cost'],'OLHC')
        got=(r.exit_i,r.exit_price,r.reason,r.be_trigger_i,r.be_effective_i)
        assert got[0]==expected[0] and got[2:]==expected[2:] and abs(got[1]-expected[1])<1e-7,(r.trade_id,got,expected)
        reversed_r.append(r.side*(reverse[1]-r.entry)/r.risk-r.fee_r)
    rows['reverse_net_r']=reversed_r
    parents=[]
    for name,g in rows.groupby('parent_id'):
        r=g.iloc[0];net=float((g.weight*g.net_r).sum())
        # One tick of original notional is rounding residue, not a meaningful win.
        epsilon=cfg['tick']/r.risk+1e-9
        parents.append(dict(parent_id=name,pair_id=r.pair_id,arm=r.arm,population=r.population,
            week=r.week,side=r.side,segment=r.segment,entry_i=r.entry_i,entry=r.entry,risk=r.risk,
            closed=bool(g.reason.ne(0).all()),net_r=net,
            net_bp=float((g.weight*g.net_notional_bp).sum()),
            gross_r=float((g.weight*g.gross_r).sum()),
            reverse_net_r=float((g.weight*g.reverse_net_r).sum()),
            outcome='win' if net>epsilon else 'loss' if net < -epsilon else 'breakeven',
            **{f'tp{j}':bool(g.loc[g.leg.eq(f'tp{j}'),'reason'].iloc[0]==4) for j in (1,2,3,4)},
            be_exit=bool(g.reason.eq(5).any()),be_active=bool(g.be_effective_i.ge(0).any()),
            exit_i=int(g.exit_i.max())))
    result=pd.DataFrame(parents)
    audit.update(oracle_checked=len(rows),reverse_payoff_changes=int((abs(rows.net_r-rows.reverse_net_r)>1e-8).sum()))
    rows.to_csv(folder/'trades.csv',index=False);fills.to_csv(folder/'fills.csv',index=False)
    result.to_csv(folder/'parents.csv',index=False);dump(folder/'audit.json',audit);dump(receipt,identity)
    return result,audit


def metric(rows):
    c=rows[rows.closed];n=len(c)
    return dict(n=len(rows),closed=n,open=len(rows)-n,wins=int(c.outcome.eq('win').sum()),
        losses=int(c.outcome.eq('loss').sum()),breakeven=int(c.outcome.eq('breakeven').sum()),
        win_pct=float(100*c.outcome.eq('win').mean()) if n else None,
        nonloss_pct=float(100*c.outcome.ne('loss').mean()) if n else None,
        net_r=float(c.net_r.sum()),mean_bp=float(c.net_bp.mean()) if n else None,
        **{f'tp{j}':int(c[f'tp{j}'].sum()) for j in (1,2,3,4)},be_exit=int(c.be_exit.sum()),
        be_active=int(c.be_active.sum()),reverse_net_r=float(c.reverse_net_r.sum()),
        terminal_mark_r=float(rows.loc[~rows.closed,'net_r'].sum()))


def direct_baseline(frame,cfg):
    """Same four targets and fee, original native resting-limit entry."""
    old=pd.read_csv(SOURCE/'signal_trades.csv',float_precision='round_trip').set_index('parent_id')
    weekly=pd.read_csv(SOURCE/'weekly_orders.csv',float_precision='round_trip')
    entries=[]
    for r in weekly.itertuples():
        if r.trade_id not in old.index:continue
        row=r._asdict();row.pop('Index')
        row.update(targets(r.monday_high,r.monday_low,r.side,cfg['tick']))
        row.update(arm='touch',trade_id='touch-'+r.trade_id,segment=int(old.loc[r.trade_id,'segment']))
        entries.append(row)
    limits=pd.DataFrame(entries)
    s,a=execute(frame,limits,0,cfg,'limit')
    actual=limits.copy().set_index('pair_id')
    for r in s.itertuples():actual.loc[r.pair_id,'entry_i']=r.entry_i
    actual=actual.reset_index();actual['entry_i']=actual.entry_i.astype(int)
    random=controls(frame,actual,cfg)
    # Distinct folder prevents confusion with the confirmation market cohort.
    global EXP
    saved=EXP;EXP=saved/'direct_control';EXP.mkdir(exist_ok=True)
    try:r,b=execute(frame,random,0,cfg)
    finally:EXP=saved
    return pd.concat([s,r],ignore_index=True),[a,b]


def run():
    cfg=json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit builders first')
    frame=base.aligned(pd.read_csv(SOURCE/'candles.csv.gz',float_precision='round_trip'))
    assert frame.index[0].minute==1 and len(frame)==1080960
    entry,decisions,checks=prepare(frame,cfg)
    entry.to_csv(EXP/'entries.csv',index=False);decisions.to_csv(EXP/'decisions.csv',index=False)
    random=controls(frame,entry,cfg);random.to_csv(EXP/'controls.csv',index=False)
    allentries=pd.concat([entry,random],ignore_index=True)
    summary=[];audits=[];allrows=[]
    baseline,baseline_audits=direct_baseline(frame,cfg)
    baseline['delay']=0;allrows.append(baseline)
    for segment in (-1,0,1):
        b=baseline if segment<0 else baseline[baseline.segment.eq(segment)]
        summary.append(dict(arm='touch',delay=0,segment=segment,
            signal=metric(b[b.population.eq('signal')]),random=metric(b[b.population.eq('random')]),comparison={}))
    for delay in cfg['be_minutes']:
        rows,audit=execute(frame,allentries,delay,cfg);audits.append(dict(delay=delay,**audit))
        rows['delay']=delay;allrows.append(rows)
        for arm in ARMS:
            for segment in (-1,0,1):
                subset=rows[rows.arm.eq(arm)]
                if segment>=0:subset=subset[subset.segment.eq(segment)]
                s=subset[subset.population.eq('signal')];r=subset[subset.population.eq('random')]
                complete=r.groupby('pair_id').closed.all();ids=complete[complete].index
                paired=s[s.closed&s.pair_id.isin(ids)][['pair_id','week','net_r']]
                paired=paired.join(r.groupby('pair_id').net_r.mean().rename('net_r_random'),on='pair_id')
                summary.append(dict(arm=arm,delay=delay,segment=segment,signal=metric(s),
                    random=metric(r),comparison=inference(paired,cfg['seed'])))
        print('finished',delay,'minutes',json.dumps([x for x in summary[-12:] if x['segment']==-1]),flush=True)
    ledger=pd.concat(allrows,ignore_index=True);ledger.to_csv(EXP/'all_parents.csv',index=False)
    selections=[]
    for arm in ARMS:
        earlier=[x for x in summary if x['arm']==arm and x['segment']==0]
        for objective in ('win_pct','mean_bp'):
            best=max(earlier,key=lambda x:(x['signal'][objective],-x['delay']))
            later=next(x for x in summary if x['arm']==arm and x['segment']==1 and x['delay']==best['delay'])
            selections.append(dict(arm=arm,objective=objective,delay=best['delay'],earlier=best,later=later))
    dump(EXP/'summary.json',dict(config=cfg,admission=checks,comparisons=summary,
        selections=selections,native_audits=audits,baseline_audits=baseline_audits,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        training_eligible=False,production_eligible=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=EXP/'config.json')
    args=parser.parse_args();EXP=args.config.resolve().parent;run()

"""Notion task62: closed-confirmation ETH5m entries and minute-resolution exits.

Owner 2026-10-19 confirmed next-bar CLOSE admission and skipping structural
risk above8 USDT. Source is the existing Stoch5/3/3 Pine formula, verified in
TradingView. Feature windows: OHLC through signal+1 for confirmation and the
four-bar stop; TR20/close and K/D use completed5m OHLC only. Future minute
OHLC is used only for outcomes. New stops activate after the minute closes.
Research only: no execution, notifications, model or production imports.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_fanshen_exit import compute_signals

EXP = Path('experiments/active/exp-eth5m-stoch-confirm-exits-20261019-v1')
REASONS = ('boundary', 'stop', 'stop_gap', 'target', 'target_gap', 'opposite', 'time')


@dataclass(frozen=True)
class Policy:
    name: str
    target: float = 0.
    be: float = 0.
    net_be: bool = False
    trail: float = 0.
    minutes: int = 0
    kd: bool = False
    target_tick: float = 0.


def policy(name):
    if name == 'opposite_arrow': return Policy(name)
    if name == 'opposite_kd': return Policy(name, kd=True)
    if name.startswith('target_'): return Policy(name, target=float(name.split('_')[1][:-1]))
    if name.startswith('be_'): return Policy(name, be=float(name.split('_')[2][:-1]), net_be='_net_' in name)
    if name.startswith('trail_'): return Policy(name, trail=float(name.split('_')[1][:-1]))
    if name.startswith('time_'): return Policy(name, minutes=5*int(name.split('_')[1][:-4]))
    raise ValueError(name)


def load_minutes(cfg):
    """Read registered originals, validate overlap/continuity; never hash inputs."""
    paths = list(map(Path, cfg['raw_inputs'])) + sorted((EXP/'inputs').glob('okx_ETH_USDT_SWAP_1m_*.csv'))
    if len(paths) != 4: raise ValueError('exactly three reused sources and one completed recent input required')
    frames, facts = [], []
    for p in paths:
        d = pd.read_csv(p, usecols=['ts','open','high','low','close','volume'])
        facts.append(dict(path=str(p), resolved=str(p.resolve()), bytes=p.stat().st_size,
                          mtime_ns=p.stat().st_mtime_ns, rows=len(d), first_ts=int(d.ts.min()), last_ts=int(d.ts.max())))
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    dup = d[d.duplicated('ts', keep=False)]
    drift = dup.groupby('ts')[['open','high','low','close']].agg(lambda x: x.max()-x.min())
    if (drift.to_numpy() > 1e-7).any(): raise ValueError('overlapping source OHLC disagree')
    duplicate_rows = int(d.duplicated('ts').sum())
    d = d.drop_duplicates('ts').sort_values('ts')
    d.index = pd.to_datetime(d.pop('ts'), unit='ms', utc=True)
    d = d.loc[(d.index >= pd.Timestamp('2023-12-31T16:00:00Z')) & (d.index < pd.Timestamp(cfg['end']))]
    if d.empty or d.index[-1]+pd.Timedelta(minutes=1) != pd.Timestamp(cfg['end']):
        raise ValueError('source endpoint incomplete')
    if np.any(np.diff(d.index.asi8) != 60_000_000_000): raise ValueError('minute source has gaps')
    a=d[['open','high','low','close','volume']].to_numpy(float)
    if not np.isfinite(a).all() or (a[:,:4] <= 0).any() or (a[:,4] < 0).any(): raise ValueError('invalid OHLCV')
    if ((d.low > d[['open','close']].min(axis=1)) | (d.high < d[['open','close']].max(axis=1))).any():
        raise ValueError('OHLC envelope invalid')
    return d, dict(sources=facts, duplicate_rows=duplicate_rows, overlap_ohlc_max_error=float(drift.max().max()),
                   rows=len(d), start=str(d.index[0]), end_exclusive=cfg['end'], content_hashes_used=False)


def prepare(minutes):
    """Causal5m K/D and TR20/close; no future-filled feature values."""
    f=minutes.resample('5min', origin='epoch').agg(open=('open','first'), high=('high','max'),
        low=('low','min'), close=('close','last'), volume=('volume','sum'), count=('close','size'))
    if not f['count'].eq(5).all(): raise ValueError('incomplete5m bar')
    s=compute_signals(f)
    arrow=np.where(s.arrow_long,1,np.where(s.arrow_short,-1,0))
    kd=np.where(s.k.gt(s.d)&s.k.shift().le(s.d.shift()),1,
                np.where(s.k.lt(s.d)&s.k.shift().ge(s.d.shift()),-1,0))
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    return dict(frame=f, signals=s, arrow=arrow, kd=kd,
        vol=(tr.rolling(20,min_periods=20).mean()/f.close).to_numpy(),
        minute_index=minutes.index, o=minutes.open.to_numpy(), h=minutes.high.to_numpy(),
        l=minutes.low.to_numpy(), c=minutes.close.to_numpy())


def segment(ts,cfg):
    return 'development' if ts < pd.Timestamp(cfg['split']) else ('validation' if ts < pd.Timestamp(cfg['examples_start']) else 'examples')


def opportunities(ctx,cfg):
    """All hypothetical signal bars, with own causal confirmation/stop gates.

    A synthetic control anchor has the same four-bar structural stop rule as
    a true signal. Both require a same-direction confirmation body and actual
    next-open risk in(0,8]. Eligibility never depends on future outcomes.
    """
    f=ctx['frame']; h=f.high.to_numpy(); l=f.low.to_numpy(); o=f.open.to_numpy(); c=f.close.to_numpy()
    rows=[]
    for i in range(20,len(f)-2):
        stamp=f.index[i+2]
        if stamp < pd.Timestamp(cfg['start']): continue
        side=1 if c[i+1]>o[i+1] else (-1 if c[i+1]<o[i+1] else 0)
        entry=float(o[i+2]); raw=int(ctx['arrow'][i]); stop=np.nan; risk=np.nan
        if side:
            stop=(min(l[i-2:i+2])-1 if side==1 else max(h[i-2:i+2])+1)
            stop=(np.floor(stop*100+1e-8) if side==1 else np.ceil(stop*100-1e-8))/100
            risk=side*(entry-stop)
        eligible=bool(side and 0 < risk <= 8+1e-8)
        rows.append(dict(anchor_i=i, entry_i=(i+2)*5, signal_time=str(f.index[i]), entry_time=str(stamp),
            segment=segment(stamp,cfg), week=str(stamp.tz_localize(None).to_period('W-SUN')),
            bucket=int(np.searchsorted(cfg['volatility_bin_edges'],ctx['vol'][i+1])),
            side=side, raw_side=raw, entry=entry, stop=stop, risk=risk, eligible=eligible,
            is_signal=bool(raw and raw==side), confirmation_pass=bool(raw and raw==side)))
    return pd.DataFrame(rows)


def sample_controls(all_rows,cfg):
    """Fixed matches within segment/week/volatility/side; do not resample failures."""
    valid=all_rows[all_rows.eligible]
    signals=valid[valid.is_signal].copy().reset_index(drop=True)
    pool=valid[valid.raw_side.eq(0)]
    keys=['segment','week','bucket','side']
    groups={k:g.index.to_numpy() for k,g in pool.groupby(keys,sort=True)}
    rng=np.random.default_rng(cfg['control_seed']); controls=[]; missing=[]
    for pair_id,r in signals.iterrows():
        values=groups.get(tuple(r[k] for k in keys))
        if values is None or not len(values): missing.append(pair_id);continue
        z=all_rows.loc[int(rng.choice(values))].to_dict();z['pair_id']=pair_id;controls.append(z)
    signals['pair_id']=np.arange(len(signals))
    return signals,pd.DataFrame(controls),missing


def trace(ctx,entry_i,side,entry,initial_stop,p,cost=.002,end_i=None):
    """Minute path: old stop first; new close-observed protection next minute.

    For a net BE stop, a threshold below fees is not actionable until price
    also clears the fee-covering stop. Favorable target gaps execute at open;
    stops execute adversely at open. A still-open endpoint is censored.
    """
    o,h,l,c=ctx['o'],ctx['h'],ctx['l'],ctx['c']; arrows=ctx['kd'] if p.kd else ctx['arrow']
    risk=side*(entry-initial_stop)
    if not (risk>0 and side in (-1,1)): raise ValueError('invalid entry risk')
    stop=initial_stop; target=entry+side*p.target*risk; peak=entry
    if p.target and p.target_tick:
        target=round(target/p.target_tick)*p.target_tick
    fee=cost*entry; destination=entry+side*(fee if p.net_be else 0)
    destination=(np.ceil(destination*100-1e-8) if side==1 else np.floor(destination*100+1e-8))/100
    end_i=len(o) if end_i is None else min(int(end_i),len(o))
    ambiguity=0; protected=False; trail_active=False; x=end_i-1; price=c[x];reason=0
    for j in range(entry_i,end_i):
        if side*(o[j]-stop)<=0: x=j;price=o[j];reason=2;break
        if p.target and side*(o[j]-target)>=0: x=j;price=o[j];reason=4;break
        if j>entry_i and j%5==0 and arrows[j//5-1]==-side: x=j;price=o[j];reason=5;break
        if p.minutes and j-entry_i>=p.minutes: x=j;price=o[j];reason=6;break
        stopped=(l[j]<=stop if side==1 else h[j]>=stop)
        targeted=bool(p.target and (h[j]>=target if side==1 else l[j]<=target))
        if stopped:
            ambiguity+=int(targeted);x=j;price=stop;reason=1;break
        if targeted: x=j;price=target;reason=3;break
        if side*(c[j]-peak)>0: peak=c[j]
        profit=side*(c[j]-entry)
        if p.be and not protected and profit >= max(p.be*risk, side*(destination-entry)):
            stop=max(stop,destination) if side==1 else min(stop,destination);protected=True
        if p.trail and side*(peak-entry)>=2*risk:
            new=peak-side*p.trail*risk
            new=(np.floor(new*100+1e-8) if side==1 else np.ceil(new*100-1e-8))/100
            stop=max(stop,new) if side==1 else min(stop,new);trail_active=True
    gross=side*(price-entry); net=gross-fee
    return dict(exit_i=x,exit_price=float(price),reason=REASONS[reason],closed=bool(reason),
        gross_points=float(gross),net_points=float(net),cost_points=float(fee),net_r=float(net/risk),
        net_return=float(net/entry),exit_at_open=reason in (2,4,5,6),
        hold_minutes=x-entry_i+(0 if reason in (2,4,5,6) else 1),ambiguous_old_stop_target=ambiguity,
        be_activated=protected,trail_activated=trail_active)


def replay_paths(ctx,events,names,cfg):
    """Independent price paths, then each arm gets its own serial occupancy."""
    records=[]
    for name in names:
        p=policy(name)
        for r in events.itertuples(index=False):
            endpoint=cfg['split'] if r.segment=='development' else (cfg['examples_start'] if r.segment=='validation' else cfg['end'])
            end_i=ctx['minute_index'].searchsorted(pd.Timestamp(endpoint))
            z=trace(ctx,int(r.entry_i),int(r.side),float(r.entry),float(r.stop),p,end_i=end_i)
            records.append(dict(pair_id=int(r.pair_id),policy=name,entry_i=int(r.entry_i),
                entry_time=r.entry_time,side=int(r.side),entry=float(r.entry),stop=float(r.stop),
                risk=float(r.risk),segment=r.segment,week=r.week,**z))
        print('paths',name,len(events),flush=True)
    return pd.DataFrame(records)


def serial(rows):
    """One position total; each arm replays independently, including censorship."""
    chosen=[];free_at=-1
    for i,r in rows.sort_values(['entry_i','pair_id']).iterrows():
        if int(r.entry_i)>=free_at:
            chosen.append(i);free_at=int(r.exit_i)+(0 if bool(r.get('exit_at_open',False)) else 1)
    return rows.loc[chosen].copy()


def metrics(rows):
    closed=rows[rows.closed];n=closed.net_points.to_numpy(float);r=closed.net_r.to_numpy(float)
    cum=np.r_[0.,np.cumsum(n)];curve=np.maximum.accumulate(cum)-cum
    # Fee-covering tick stops can leave floating dust at mathematical zero.
    wins=n[n>1e-8].sum();loss=-n[n<-1e-8].sum()
    return dict(orders=len(rows),closed=len(closed),censored=int((~rows.closed).sum()),
        gross_points=float(closed.gross_points.sum()),net_points=float(n.sum()),
        net_r=float(r.sum()),mean_r=float(r.mean()) if len(r) else None,
        mean_net_bp=float(closed.net_return.mean()*10000) if len(r) else None,
        win_rate=float((n>1e-8).mean()) if len(n) else None,pf=float(wins/loss) if loss else None,
        closed_points_drawdown=float(curve.max()),cost_points=float(closed.cost_points.sum()),
        minus_best_points=float(n.sum()-n.max()) if len(n) else None,
        ambiguous_minutes=int(closed.ambiguous_old_stop_target.sum()),
        median_hold_minutes=float(closed.hold_minutes.median()) if len(n) else None)


def analyze(paths,controls,names):
    """Serial totals and matched independent opportunities are separate estimands."""
    summaries=[];picked=[];paired=[]
    for name in names:
        actual=serial(paths[paths.policy.eq(name)]);picked.append(actual)
        ctl=serial(controls[controls.policy.eq(name)])
        for seg in ('all','development','validation','examples'):
            a=actual if seg=='all' else actual[actual.segment.eq(seg)]
            b=ctl if seg=='all' else ctl[ctl.segment.eq(seg)]
            summaries.append(dict(policy=name,segment=seg,**metrics(a),random_serial_net_points=metrics(b)['net_points'],
                random_serial_closed=metrics(b)['closed']))
        a=paths[paths.policy.eq(name)];b=controls[controls.policy.eq(name)]
        m=a.merge(b,on=['pair_id','policy'],suffixes=('_signal','_random'),validate='one_to_one')
        m=m[m.closed_signal & m.closed_random]
        m['excess_points']=m.net_points_signal-m.net_points_random
        m['excess_r']=m.net_r_signal-m.net_r_random
        paired.append(m)
    return pd.DataFrame(summaries),pd.concat(picked,ignore_index=True),pd.concat(paired,ignore_index=True)


def weekly_test(values,seed=621019,reps=9999):
    """Two-sided weekly block sign-flip and percentile block bootstrap of totals."""
    v=np.asarray(values,float);rng=np.random.default_rng(seed)
    if len(v)<2:return dict(weeks=len(v),p=None,ci95=None)
    # Small batches avoid a large temporary matrix across every policy.
    null=[];boot=[]
    for start in range(0,reps,250):
        n=min(250,reps-start)
        null.extend((rng.choice([-1.,1.],size=(n,len(v)))*v).sum(axis=1))
        boot.extend(v[rng.integers(0,len(v),size=(n,len(v)))].sum(axis=1))
    return dict(weeks=len(v),p=float((1+(np.abs(null)>=abs(v.sum())-1e-12).sum())/(reps+1)),
        ci95=list(map(float,np.quantile(boot,[.025,.975]))))


def diagnostics(out):
    """Descriptive evidence only; never choose another arm or change admission."""
    out=Path(out)
    candidates=pd.read_csv(out/'signal_candidates.csv')
    entries=pd.read_csv(out/'entries.csv')
    summary=pd.read_csv(out/'summary.csv')
    selected=json.loads((out/'selection.json').read_text())['selected_on_development']
    trades=pd.read_csv(out/'serial_trades.csv.gz')
    drawings=[]
    observed=json.loads((EXP/'chart_observations.json').read_text())
    for drawing in observed['all_positions_read']:
        stamp=pd.Timestamp(drawing['matching_confirmation_close'][0]['signal_local']).tz_convert('UTC')
        r=candidates[pd.to_datetime(candidates.signal_time).eq(stamp)].iloc[0]
        drawings.append(dict(ordinal=drawing['ordinal'],signal_local=str(stamp.tz_convert('Asia/Shanghai')),
            side=int(r.raw_side),entry=float(r.entry),causal_stop=float(r.stop),risk=float(r.risk),
            passes_risk_gate=bool(r.eligible),confirmation_pass=bool(r.confirmation_pass),
            drawn_stop=drawing['stop_drawn'],drawn_entry=drawing['entry_drawn']))
    periods=[]
    selected_trades=trades[trades.policy.eq(selected)]
    years=pd.to_datetime(selected_trades.entry_time).dt.year
    for year,z in selected_trades.groupby(years):
        periods.append(dict(year=int(year),**metrics(z)))
    serial_break_evens={}
    for name,z in trades[trades.policy.str.startswith('be_')].groupby('policy'):
        serial_break_evens[name]=dict(activated=int(z.be_activated.sum()),
            mathematical_zero=int(z.net_points.abs().lt(1e-8).sum()),
            net_wins=int(z.net_points.gt(1e-8).sum()))
    data=dict(selected_on_development=selected,units='USDT points per fixed1ETH; not account returns',
        qualified_signals_by_segment=entries.segment.value_counts().to_dict(),
        drawings=drawings,drawings_qualified=sum(x['passes_risk_gate'] for x in drawings),
        risk_points_quantiles=entries.risk.quantile([.1,.5,.9]).to_dict(),
        cost_r_quantiles=(.002*entries.entry/entries.risk).quantile([.1,.5,.9]).to_dict(),
        selected_years=periods,break_even_counts=serial_break_evens,
        profitable_policy_count_by_segment=summary.groupby('segment').net_points.apply(lambda x:int(x.gt(0).sum())).to_dict(),
        all_history_descriptive_best=summary[summary.segment.eq('all')].sort_values('net_points',ascending=False).iloc[0].to_dict(),
        selection_caveat='All-history best is hindsight; development choice remains frozen; no profitable arm found.')
    (out/'diagnostics.json').write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    return data


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',default='results_v1');ap.add_argument('--smoke',action='store_true');args=ap.parse_args()
    cfg=json.loads((EXP/'config.json').read_text());out=EXP/args.out
    out.mkdir(parents=True,exist_ok=False)
    minute,facts=load_minutes(cfg)
    if args.smoke:minute=minute.loc['2026-10-16':]
    ctx=prepare(minute);all_rows=opportunities(ctx,cfg);signals,control,missing=sample_controls(all_rows,cfg)
    facts.update(git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        raw_arrows=int(all_rows.raw_side.ne(0).sum()),confirmed_arrows=int(all_rows.is_signal.sum()),
        eligible_signals=len(signals),control_pairs=len(control),unmatched_pair_ids=missing,
        arrow_positive_rate=float(all_rows.raw_side.ne(0).mean()),candidates=len(all_rows))
    (out/'input_receipt.json').write_text(json.dumps(facts,indent=2)+'\n')
    all_rows[all_rows.raw_side.ne(0)].to_csv(out/'signal_candidates.csv',index=False)
    signals.to_csv(out/'entries.csv',index=False);control.to_csv(out/'control_entries.csv',index=False)
    paths=replay_paths(ctx,signals,cfg['policies'],cfg);paths.to_csv(out/'paths.csv.gz',index=False)
    controls=replay_paths(ctx,control,cfg['policies'],cfg);controls.to_csv(out/'controls.csv.gz',index=False)
    sums,picked,paired=analyze(paths,controls,cfg['policies'])
    sums.to_csv(out/'summary.csv',index=False);picked.to_csv(out/'serial_trades.csv.gz',index=False);paired.to_csv(out/'pairs.csv.gz',index=False)
    dev=sums[sums.segment.eq('development')].sort_values(['net_points','closed_points_drawdown','policy'],ascending=[False,True,True])
    chosen=str(dev.iloc[0].policy)
    inference=[]
    for seg in ('development','validation','examples','all'):
        for name in cfg['policies']:
            z=paired[paired.policy.eq(name)];z=z if seg=='all' else z[z.segment_signal.eq(seg)]
            v=z.groupby('week_signal').excess_points.sum().to_numpy()
            inference.append(dict(policy=name,segment=seg,pairs=len(z),signal_net_points=float(z.net_points_signal.sum()),
                random_net_points=float(z.net_points_random.sum()),excess_points=float(z.excess_points.sum()),**weekly_test(v)))
    inf=pd.DataFrame(inference)
    for seg in inf.segment.unique():
        group=inf[inf.segment.eq(seg)].dropna(subset=['p']).sort_values('p');highest=0.
        for rank,(i,r) in enumerate(group.iterrows()):
            highest=max(highest,min(1.,r.p*(len(group)-rank)));inf.loc[i,'holm_p']=highest
    inf.to_csv(out/'matched_inference.csv',index=False)
    conclusion=dict(selected_on_development=chosen,selection_metric=cfg['selection_metric'],
        metrics_not_applicable='No prediction scores/classifier: AUC and top-decile ranking are not defined. Report complete deterministic signals and matched random economic null instead.',
        production_eligible=False,training_eligible=False)
    (out/'selection.json').write_text(json.dumps(conclusion,indent=2)+'\n')
    # Raw recent signal inputs permit independent chart parity without return selection.
    recent=ctx['frame'].join(ctx['signals'][['k','d','arrow_long','arrow_short']]).loc['2026-10-16':]
    recent.to_csv(out/'recent_chart_parity.csv')
    diagnostics(out)
    print(json.dumps(dict(output=str(out),eligible=len(signals),selected=chosen)))


if __name__=='__main__':main()

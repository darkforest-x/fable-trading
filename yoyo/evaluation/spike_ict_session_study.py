"""Frozen entry-session research for ETH3m V8; no production integration.

Session features use only the planned next-open timestamp and IANA New York
calendar. Current EDT session boundaries were observed in the owner's chart;
constant New York clock windows across winter are an explicitly stated model.
All price features/exits/costs reuse frozen V8. No holdout inputs are accepted.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_eth3m_recovery_study import load_context, bounds
from yoyo.evaluation.spike_recovery_exit import prepare, replay_entry
from yoyo.evaluation.spike_net_recovery_exit import replay_net_entry
from yoyo.evaluation.spike_net_recovery_cash import simulate_recovery
from yoyo.evaluation.spike_recovery_cash import eligible

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-spike-eth3m-ict-sessions-20260914-v1'
SESSIONS=['all','london','lunch','new_york','union','london_new_york']
ARMS=['original','next_bar','ohlc','olhc']


def save(path,value):
    def clean(v):
        if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [clean(x) for x in v]
        if isinstance(v,np.generic):return clean(v.item())
        if isinstance(v,float) and not np.isfinite(v):return None
        if isinstance(v,pd.Timestamp):return v.isoformat()
        return v
    path.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2)+'\n')


def session_mask(times,session):
    """Filter planned entry UTC timestamps; no OHLC, future, or weekday gate."""
    local=pd.DatetimeIndex(times).tz_convert('America/New_York')
    minute=local.hour*60+local.minute
    london=(minute>=120)&(minute<300)
    lunch=(minute>=300)&(minute<480)
    ny=(minute>=480)&(minute<660)
    values=dict(all=np.ones(len(local),dtype=bool),london=london,lunch=lunch,
                new_york=ny,union=london|lunch|ny,london_new_york=london|ny)
    if session not in values:raise ValueError('unknown session')
    return np.asarray(values[session],bool)


def serial(opportunities):
    """Serial admission after session exclusion; excluded entries never occupy."""
    accepted=[];previous=None
    for trade in sorted(opportunities,key=lambda t:t['signal_i']):
        if eligible(trade,previous):accepted.append(trade);previous=trade
    return accepted


def stats(rows):
    natural=[t for t in rows if not t['censored']]
    if any(not np.isfinite(t['net_r']) for t in natural):raise ValueError('unresolved natural outcome')
    r=np.array([t['net_r'] for t in natural]);gross=np.array([t['gross_r'] for t in natural])
    initial=[t['exit_reason'] in ['initial_stop','initial_stop_gap'] for t in natural]
    be=[str(t['exit_reason']).startswith('cost_be') and t['net_r']>=-1e-9 for t in natural]
    def longest(flags):
        best=run=0
        for yes in flags:run=run+1 if yes else 0;best=max(best,run)
        return best
    losses=-r[r<0].sum()
    return dict(trades=len(rows),natural=len(natural),open=len(rows)-len(natural),
        wins=int(sum((r>1e-9)&~np.array(be,dtype=bool))),strict_net_positive=int(sum(r>1e-9)),
        be=int(sum(be)),losses=int(sum(r<-1e-9)),net_zero=int(sum(abs(r)<=1e-9)),
        win_rate=float(np.mean(r>1e-9)) if len(r) else None,
        mean_net_r=float(r.mean()) if len(r) else None,sum_net_r=float(r.sum()),
        mean_gross_r=float(gross.mean()) if len(r) else None,
        profit_factor=float(r[r>0].sum()/losses) if losses else None,
        initial_stops=sum(initial),max_initial_stop_streak=longest(initial),
        max_net_loss_streak=longest(r<-1e-9),gross3r=int(sum(gross>=3)),net3r=int(sum(r>=3)))


def replay(prepared,i,arm,side=None):
    if arm=='original':
        return replay_entry(prepared,int(i),take_profit_r=None,protection_mode='none',side_override=side)
    return replay_net_entry(prepared,int(i),net_take_profit_r=1.,timing=arm,side_override=side)


def controls_for(frame,prepared,rows,arm,cfg,cache):
    """Same asset/UTC month/NY entry hour/weekend/prior-relative-vol bucket.

    Controls are independent natural outcomes, not a tradable random account.
    Pool eligibility consumes only signal ATR/close and preceding 120 bars.
    """
    vol=frame.atr/frame.close;prior=vol.shift().rolling(120,min_periods=120)
    low,high=prior.quantile(1/3),prior.quantile(2/3)
    bucket=np.where(vol<=low,0,np.where(vol<=high,1,2))
    entry_time=frame.index+pd.Timedelta(minutes=3)
    local=entry_time.tz_convert('America/New_York')
    months=entry_time.strftime('%Y-%m').to_numpy();hours=local.hour.to_numpy();weekend=(local.dayofweek>=5)
    pools={};details=[]
    for trade in rows:
        if trade['censored']:continue
        i,side=int(trade['signal_i']),int(trade['side'])
        key=(i,side)
        if key not in cache:
            group=(months[i],int(hours[i]),bool(weekend[i]),int(bucket[i]))
            if group not in pools:
                candidates=np.flatnonzero((months==group[0])&(hours==group[1])&(weekend==group[2])&(bucket==group[3])&low.notna().to_numpy())
                pools[group]=candidates[candidates+1<len(frame)]
            rng=np.random.default_rng(np.random.SeedSequence([cfg['seed'],i,side+1]))
            draws=[];attempts=censored=invalid=0
            for j in rng.permutation(pools[group]):
                if j==i:continue
                attempts+=1;t=replay(prepared,j,arm,side)
                if t is None or not np.isfinite(t['net_r']):invalid+=1;continue
                if t['censored']:censored+=1;continue
                draws.append(dict(signal_i=int(j),net_r=float(t['net_r'])))
                if len(draws)==cfg['controls_per_trade']:break
            cache[key]=dict(draws=draws,attempts=attempts,censored=censored,invalid=invalid)
        result=cache[key];draws=result['draws']
        mean=float(np.mean([d['net_r'] for d in draws])) if draws else np.nan
        details.append(dict(signal_i=i,month=months[i],ny_hour=int(hours[i]),weekend=bool(weekend[i]),
            vol_bucket=int(bucket[i]),actual_net_r=trade['net_r'],random_net_r=mean,
            excess_net_r=trade['net_r']-mean,matched=len(draws),attempts=result['attempts'],
            rejected_censored=result['censored'],rejected_invalid=result['invalid'],draws=json.dumps(draws)))
    df=pd.DataFrame(details)
    if not len(df):return df,dict(matched_trades=0,random_mean_net_r=None,excess_net_r=None,p=None)
    full=df.loc[df.matched==cfg['controls_per_trade']]
    blocks=full.groupby('month').excess_net_r.sum().to_numpy()
    rng=np.random.default_rng(cfg['seed'])
    perm=(rng.choice([-1,1],size=(cfg['permutations'],len(blocks)))*blocks).sum(axis=1)
    p=float((1+(perm>=blocks.sum()).sum())/(1+len(perm))) if len(blocks) else None
    return df,dict(matched_trades=len(full),unmatched=len(df)-len(full),months=len(blocks),
        random_mean_net_r=float(full.random_net_r.mean()),excess_net_r=float(full.excess_net_r.mean()),p=p)


def run():
    cfg=json.loads((EXP/'config.json').read_text())
    for path in [Path(__file__),EXP/'config.json',EXP/'PROJECT_PLAN.md']:
        rel=str(path.relative_to(ROOT))
        subprocess.run(['git','cat-file','-e','HEAD:'+rel],cwd=ROOT,check=True)
        subprocess.run(['git','diff','--exit-code','HEAD','--',rel],cwd=ROOT,check=True)
    out=EXP/'results';out.mkdir(exist_ok=False)
    ctx=load_context(cfg);summaries=[];cash_rows=[];control_rows=[]
    for period in ['development','validation','preholdout','continuous_pre']:
        frame,raw,indices=bounds(ctx,cfg,period);prepared=prepare(frame,raw)
        for arm in ARMS:
            opportunities=[]
            for i in indices:
                t=replay(prepared,i,arm)
                if t is not None:
                    if not np.isfinite(t['net_r']):raise ValueError('unresolved source gap')
                    opportunities.append(t)
            pd.DataFrame(opportunities).to_csv(out/(period+'_'+arm+'_opportunities.csv.gz'),index=False)
            cache={}
            for session in SESSIONS:
                mask=session_mask([t['entry_time'] for t in opportunities],session)
                filtered=[t for t,keep in zip(opportunities,mask) if keep]
                accepted=serial(filtered);tag=period+'_'+arm+'_'+session
                pd.DataFrame(accepted).to_csv(out/(tag+'_serial.csv.gz'),index=False)
                summary=dict(period=period,arm=arm,session=session,candidates=len(filtered),**stats(accepted))
                summaries.append(summary)
                # Fixed-session random matching on three disjoint periods. The
                # continuous account is a path check, not another independent test.
                if period!='continuous_pre':
                    matched,metrics=controls_for(frame,prepared,accepted,arm,cfg,cache)
                    matched.to_csv(out/(tag+'_matched.csv.gz'),index=False)
                    control_rows.append(dict(period=period,arm=arm,session=session,**metrics))
                for cash_name,schedule in [('fixed1','fixed'),('double','double')]:
                    result=simulate_recovery(filtered,schedule=schedule,leverage_cap=10.)
                    cash_rows.append(dict(period=period,arm=arm,session=session,cash=cash_name,**result['summary']))
                    pd.DataFrame(result['ledger']).to_csv(out/(tag+'_'+cash_name+'_ledger.csv.gz'),index=False)
                print(period,arm,session,'n',summary['natural'],'mean',round(summary['mean_net_r'] or 0,4),'run',summary['max_net_loss_streak'],flush=True)
        if period=='development':
            candidates=[r for r in summaries if r['period']==period and r['arm']=='next_bar' and r['session']!='all' and r['natural']>=50]
            chosen=max(candidates,key=lambda r:(r['mean_net_r'],r['natural']))
            save(EXP/'selection.json',dict(chosen_session=chosen['session'],development=chosen,
                objective='highest development mean net R among five fixed session choices,minimum50 natural trades',
                positive_development=chosen['mean_net_r']>0,holdout_consumptions=0,
                primary_user_hypothesis='union; always evaluated regardless of selection'))
    for name,rows in [('summary',summaries),('cash',cash_rows),('controls',control_rows)]:
        pd.DataFrame(rows).to_csv(out/(name+'.csv'),index=False)
    save(out/'receipt.json',dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        holdout_consumptions=0,context_sha256=cfg['context_sha256'],session_groups=SESSIONS,
        source_end=str(ctx['frame'].index[-1]),selection_before_validation=True,
        entry_clock='next observed open',weekends=True,cost=.002,
        matched_control_scope='same asset,month,NY hour,weekend,vol bucket; independent natural exits'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.parse_args();run()

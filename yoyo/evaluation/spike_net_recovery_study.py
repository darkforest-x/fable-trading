"""Frozen ETH3m net-1R recovery verification with three execution scenarios.

No input features are added: closed V8 masks, original initial stops and V6
reverse signals come from the SHA-bound pre-May context. Random controls use
same asset/month/side and signal-bar ATR/close bucket relative to preceding
120 bars. O-H-L-C and O-L-H-C are assumptions, not reconstructed ticks.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_eth3m_recovery_study import load_context, bounds
from yoyo.evaluation.spike_net_recovery_exit import prepare, replay_net_entry
from yoyo.evaluation.spike_net_recovery_cash import simulate_recovery
from yoyo.evaluation.spike_recovery_exit import replay_entry

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-spike-eth3m-net-recovery-20260914-v3'
CONFIG=EXP/'config.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identities():
    names=['yoyo/evaluation/'+n for n in ['spike_net_recovery_study.py',
        'spike_net_recovery_exit.py','spike_net_recovery_cash.py','spike_recovery_exit.py',
        'spike_recovery_cash.py','spike_eth3m_recovery_study.py']]
    names.append(str(CONFIG.relative_to(ROOT)))
    return {n:sha(ROOT/n) for n in names}


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def replay(prepared,i,arm,side=None):
    if arm=='legacy_gross1':
        return replay_entry(prepared,int(i),take_profit_r=1.,protection_mode='none',side_override=side)
    return replay_net_entry(prepared,int(i),net_take_profit_r=1.,timing=arm,side_override=side)


def matching(frame,prepared,accepted,arm,cfg,cache):
    """Matched natural-exit diagnostics; controls do not form a cash account."""
    vol=frame.atr/frame.close
    prior=vol.shift(1).rolling(120,min_periods=120)
    lo,hi=prior.quantile(1/3),prior.quantile(2/3)
    bucket=np.where(vol<=lo,0,np.where(vol<=hi,1,2))
    months=frame.index.strftime('%Y-%m').to_numpy()
    pools={}
    details=[]
    attempts=censored=invalid=0
    for t in accepted:
        i,side=int(t['signal_i']),int(t['side'])
        key=(i,side)
        if key not in cache:
            group=(months[i],int(bucket[i]))
            if group not in pools:
                pools[group]=np.flatnonzero((months==group[0])&(bucket==group[1])&lo.notna().to_numpy())
            candidates=pools[group]
            candidates=candidates[(candidates!=i)&(candidates+1<len(frame))]
            rng=np.random.default_rng(np.random.SeedSequence([cfg['seed'],i,side+1]))
            draws=[];tries=nc=ni=0
            for j in rng.permutation(candidates):
                tries+=1
                r=replay(prepared,j,arm,side)
                if r is None or not np.isfinite(r['net_r']):
                    ni+=1;continue
                if r['censored']:
                    nc+=1;continue
                draws.append(dict(signal_i=int(j),net_r=float(r['net_r'])))
                if len(draws)==cfg['controls_per_trade']:
                    break
            if len(draws)!=cfg['controls_per_trade']:
                raise ValueError('insufficient matched controls')
            cache[key]=dict(draws=draws,attempts=tries,rejected_censored=nc,rejected_invalid=ni)
        matched=cache[key]
        mean=float(np.mean([x['net_r'] for x in matched['draws']]))
        attempts+=matched['attempts'];censored+=matched['rejected_censored'];invalid+=matched['rejected_invalid']
        details.append(dict(signal_i=i,side=side,month=months[i],bucket=int(bucket[i]),
            actual_net_r=t['net_r'],random_net_r=mean,excess_net_r=t['net_r']-mean,
            risk=t['risk'],excess_cash=t['risk']*(t['net_r']-mean),
            controls_json=json.dumps(matched['draws'])))
    data=pd.DataFrame(details)
    if not len(data):
        return data,dict(n=0,months=0,attempts=attempts,rejected_censored=censored,
                         rejected_invalid=invalid,random_mean_net_r=None,excess_net_r=None,p=None)
    blocks=data.groupby('month').excess_net_r.sum().to_numpy()
    rng=np.random.default_rng(cfg['seed'])
    permuted=(rng.choice([-1,1],size=(cfg['permutation_draws'],len(blocks)))*blocks).sum(axis=1)
    return data,dict(n=len(data),months=len(blocks),attempts=attempts,rejected_censored=censored,
        rejected_invalid=invalid,random_mean_net_r=float(data.random_net_r.mean()),
        excess_net_r=float(data.excess_net_r.mean()),excess_cash=float(data.excess_cash.sum()),
        p=float((1+(permuted>=blocks.sum()).sum())/(1+len(permuted))))


def longest_run_detail(ledger,mode):
    natural=[r for r in ledger if r['accepted'] and not r['censored']]
    runs=[];run=[]
    for r in natural:
        yes=r['full_initial_stop'] if mode=='initial_stop' else r['net_r'] < -1e-9
        if yes:
            run.append(r)
        else:
            if run:runs.append(run)
            run=[]
    if run:runs.append(run)
    return max(runs,key=len,default=[])


def run():
    cfg=json.loads(CONFIG.read_text())
    frozen=identities()
    for path,digest in frozen.items():
        committed=subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT)
        if hashlib.sha256(committed).hexdigest()!=digest:
            raise ValueError('commit builders and frozen configuration before data replay')
    out=EXP/'results'/'evaluation';out.mkdir(parents=True,exist_ok=False)
    source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    save(out/'frozen.json',dict(code_commit=source_commit,identities=frozen,config=cfg,holdout_consumptions=0))
    ctx=load_context(cfg);summaries=[];controls=[];streaks=[];halts=[]
    for period in cfg['periods']:
        frame,raw,indices=bounds(ctx,cfg,period);prepared=prepare(frame,raw)
        for arm in cfg['arms']:
            opportunities=[]
            for i in indices:
                row=replay(prepared,i,arm)
                if row is not None:
                    if not np.isfinite(row['net_r']):raise ValueError('unresolved gap outcome')
                    opportunities.append(row)
            tag=period+'_'+arm
            pd.DataFrame(opportunities).to_csv(out/(tag+'_opportunities.csv.gz'),index=False)
            cashes=cfg['cash'] if arm!='legacy_gross1' else {'fixed10':cfg['cash']['fixed10']}
            cache={}
            for cash_name,cash in cashes.items():
                result=simulate_recovery(opportunities,**cash)
                if period=='development' and arm=='legacy_gross1':
                    assert result['summary']['n_natural']==809
                    np.testing.assert_allclose(result['summary']['final_balance'],437.586101046302,atol=1e-6)
                full=tag+'_'+cash_name
                pd.DataFrame(result['ledger']).to_csv(out/(full+'_ledger.csv.gz'),index=False)
                pd.DataFrame(result['cycles']).to_csv(out/(full+'_cycles.csv'),index=False)
                summaries.append(dict(period=period,arm=arm,cash=cash_name,**result['summary']))
                accepted=[r for r in result['ledger'] if r['accepted'] and not r['censored']]
                detail,stats=matching(frame,prepared,accepted,arm,cfg,cache)
                detail.to_csv(out/(full+'_matched.csv.gz'),index=False)
                controls.append(dict(period=period,arm=arm,cash=cash_name,**stats))
                for mode in ['initial_stop','net_loss']:
                    longest=longest_run_detail(result['ledger'],mode)
                    for rank,r in enumerate(longest,1):
                        streaks.append(dict(period=period,arm=arm,cash=cash_name,streak_type=mode,
                                            streak_length=len(longest),rank=rank,**r))
                if result['summary']['halt_reason']:
                    cid=result['ledger'][-1]['cycle_id']
                    halts.extend(dict(period=period,arm=arm,cash=cash_name,**r)
                                 for r in result['ledger'] if r['cycle_id']==cid)
                print(full,'cash',round(result['summary']['final_balance'],4),
                      'initial/net/level',result['summary']['max_initial_stop_streak'],
                      result['summary']['max_net_loss_streak'],result['summary']['max_unrecovered_loss_events'],
                      'halt',result['summary']['halt_time'],flush=True)
    for name,rows in [('summary',summaries),('controls',controls),('longest_streaks',streaks),('halt_cycles',halts)]:
        pd.DataFrame(rows).to_csv(out/(name+'.csv'),index=False)
    save(out/'receipt.json',dict(code_commit=source_commit,identities=frozen,holdout_consumptions=0,
        n_accounts=len(summaries),summary_sha256=sha(out/'summary.csv'),controls_sha256=sha(out/'controls.csv')))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.parse_args();run()

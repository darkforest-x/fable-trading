"""ETH3m partial exits: fixed development arms then frozen capital diagnostics.

No new signal features. Matching uses current closed-bar ATR/close against
the preceding 120 bars, same UTC month and side; no holdout loader exists.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_eth3m_recovery_study import load_context, bounds
from yoyo.evaluation.spike_recovery_exit import prepare
from yoyo.evaluation.spike_recovery_cash import simulate
from yoyo.evaluation.spike_partial_exit import replay_partial

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-spike-eth3m-partial-tp-20260914-v1'
RESULTS=EXP/'results'
CONFIG=EXP/'config.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,obj):
    path.parent.mkdir(exist_ok=True,parents=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=str)+'\n')


def identities():
    paths=[Path(__file__),ROOT/'yoyo/evaluation/spike_partial_exit.py',
           ROOT/'yoyo/evaluation/spike_recovery_exit.py',ROOT/'yoyo/evaluation/spike_recovery_cash.py',CONFIG]
    return {str(p.relative_to(ROOT)):sha(p) for p in paths}


def max_run(flags):
    run=best=0
    for flag in flags:
        run=run+1 if flag else 0
        best=max(best,run)
    return best


def generate(prepared,indices,policy):
    result=[]
    for i in indices:
        r=replay_partial(prepared,int(i),**policy)
        if r is not None:
            if not np.isfinite(r['net_r']):
                raise ValueError('unresolved outcome; gaps cannot disappear')
            result.append(r)
    return result


def account(opportunities,cash=None):
    cash=cash or dict(schedule='fixed',reset_mode='recovery',factor=1.)
    result=simulate(opportunities,**cash)
    by_signal={r['signal_i']:r for r in opportunities}
    for row in result['ledger']:
        if row['accepted']:
            r=by_signal[row['signal_i']]
            row['partial_executed']=bool(r.get('partial_executed',False))
            row['full_initial_stop']=bool(r.get('full_initial_stop',str(r['exit_reason']).startswith('initial_stop') and not r.get('partial_executed',False)))
    natural=[r for r in result['ledger'] if r['accepted'] and not r['censored']]
    result['summary'].update(n_partial=sum(r['partial_executed'] for r in natural),
        n_full_initial_stop=sum(r['full_initial_stop'] for r in natural),
        max_full_initial_stop=max_run(r['full_initial_stop'] for r in natural),
        max_price_loss=max_run(r['gross_r'] < -1e-9 for r in natural),
        mean_net_r=float(np.mean([r['net_r'] for r in natural])),
        mean_gross_r=float(np.mean([r['gross_r'] for r in natural])))
    assert abs(1000+sum(r['pnl'] for r in result['ledger'] if r['accepted'])-result['summary']['final_balance'])<1e-7
    assert abs(sum(r['net_pnl'] for r in result['cycles'])-(result['summary']['final_balance']-1000))<1e-7
    return result


def matched(frame,prepared,accepted,policy,cfg):
    vol=frame.atr/frame.close
    roll=vol.shift(1).rolling(120,min_periods=120)
    lo,hi=roll.quantile(1/3),roll.quantile(2/3)
    bucket=np.where(vol<=lo,0,np.where(vol<=hi,1,2))
    month=frame.index.strftime('%Y-%m').to_numpy()
    pools={}
    rng=np.random.default_rng(cfg['seed'])
    rows=[]
    attempts=censored=invalid=0
    for t in accepted:
        i=int(t['signal_i']);key=month[i],int(bucket[i])
        if key not in pools:
            pools[key]=np.flatnonzero((month==key[0])&(bucket==key[1])&lo.notna().to_numpy())
        candidates=pools[key]
        candidates=candidates[(candidates!=i)&(candidates+1<len(frame))]
        draws=[]
        for j in rng.permutation(candidates):
            attempts+=1
            r=replay_partial(prepared,int(j),side_override=int(t['side']),**policy)
            if r is None or not np.isfinite(r['net_r']):
                invalid+=1
                continue
            if r['censored']:
                censored+=1
                continue
            draws.append(float(r['net_r']))
            if len(draws)==cfg['controls_per_trade']:
                break
        if len(draws)!=cfg['controls_per_trade']:
            raise ValueError('incomplete control pool')
        mean=float(np.mean(draws))
        rows.append(dict(signal_i=i,month=key[0],side=int(t['side']),risk=t['risk'],
            actual_net_r=t['net_r'],random_net_r=mean,excess_net_r=t['net_r']-mean,
            excess_cash=t['risk']*(t['net_r']-mean)))
    table=pd.DataFrame(rows)
    blocks=table.groupby('month').excess_net_r.sum().to_numpy()
    simulated=(rng.choice([-1,1],size=(cfg['permutation_draws'],len(blocks)))*blocks).sum(axis=1)
    return table,dict(n=len(rows),months=len(blocks),attempts=attempts,rejected_censored=censored,rejected_invalid=invalid,
        random_mean_net_r=float(table.random_net_r.mean()),excess_net_r=float(table.excess_net_r.mean()),
        excess_cash=float(table.excess_cash.sum()),p=float((1+(simulated>=blocks.sum()).sum())/(1+len(simulated))))


def export(out,tag,opportunities,result):
    flattened=[]
    for r in opportunities:
        row=dict(r)
        if 'fills' in row:
            row['fills_json']=json.dumps(row.pop('fills'),default=str)
        flattened.append(row)
    pd.DataFrame(flattened).to_csv(out/(tag+'_opportunities.csv.gz'),index=False)
    pd.DataFrame(result['ledger']).to_csv(out/(tag+'_ledger.csv.gz'),index=False)
    pd.DataFrame(result['cycles']).to_csv(out/(tag+'_cycles.csv'),index=False)


def develop(cfg):
    out=RESULTS/'development';out.mkdir(parents=True,exist_ok=False)
    ctx=load_context(cfg);frame,raw,indices=bounds(ctx,cfg,'development');prepared=prepare(frame,raw)
    stats=[]
    for name,policy in cfg['arms'].items():
        rows=generate(prepared,indices,policy);result=account(rows)
        export(out,name,rows,result)
        s=result['summary']
        if name=='whole_3r':
            assert s['n_natural']==751
            np.testing.assert_allclose(s['final_balance'],479.82100723452197,atol=1e-8)
        stats.append(dict(name=name,**s,hard_pass=s['final_balance']>1000 and s['max_full_initial_stop']<=6 and s['max_consecutive_net_loss']<=6))
        print(name,'cash',s['final_balance'],'netwin',s['win_rate'],'full/net streak',s['max_full_initial_stop'],s['max_consecutive_net_loss'],flush=True)
    table=pd.DataFrame(stats);table.to_csv(out/'summary.csv',index=False)
    eligible=table[table.n_natural.ge(100)]
    best=eligible.sort_values(['final_balance','max_full_initial_stop','name'],ascending=[False,True,True]).iloc[0]['name']
    selected=list(dict.fromkeys([best,'whole_3r','whole_3r_be1','p50_1_be','p70_1_be']))
    save(RESULTS/'selection.json',dict(best=best,selected=selected,policies={n:cfg['arms'][n] for n in selected},
        identities=identities(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        development_summary_sha256=sha(out/'summary.csv'),hard_pass_count=int(table.hard_pass.sum()),holdout_consumed=False))


def evaluate(cfg):
    selection=RESULTS/'selection.json'
    assert subprocess.check_output(['git','show','HEAD:'+str(selection.relative_to(ROOT))],cwd=ROOT)==selection.read_bytes()
    freeze=json.loads(selection.read_text());assert freeze['identities']==identities()
    out=RESULTS/'evaluation';out.mkdir(exist_ok=False)
    ctx=load_context(cfg);summaries=[];controls=[]
    cashes={'fixed':dict(schedule='fixed',factor=1.,reset_mode='recovery')}
    for factor in [1.5,2.]:
        for reset in ['net_win','recovery']:
            cashes[f'x{factor}_{reset}']=dict(schedule='double',factor=factor,reset_mode=reset)
    for period in cfg['periods']:
        frame,raw,indices=bounds(ctx,cfg,period);prepared=prepare(frame,raw)
        names=list(cfg['arms']) if period=='development' else freeze['selected']
        for name in names:
            rows=generate(prepared,indices,cfg['arms'][name])
            selected_cashes=cashes if name in freeze['selected'] else {'fixed':cashes['fixed']}
            for cash_name,cash in selected_cashes.items():
                result=account(rows,cash);tag=period+'_'+name+'_'+cash_name
                # Opportunities depend only on exits; store once per arm/window.
                if cash_name=='fixed':
                    export(out,tag,rows,result)
                else:
                    pd.DataFrame(result['ledger']).to_csv(out/(tag+'_ledger.csv.gz'),index=False)
                    pd.DataFrame(result['cycles']).to_csv(out/(tag+'_cycles.csv'),index=False)
                summaries.append(dict(period=period,name=name,cash=cash_name,**result['summary']))
                accepted=[r for r in result['ledger'] if r['accepted'] and not r['censored']]
                detail,c=matched(frame,prepared,accepted,cfg['arms'][name],cfg)
                detail.to_csv(out/(tag+'_matched.csv.gz'),index=False)
                controls.append(dict(period=period,name=name,cash=cash_name,**c))
                print('evaluated',tag,result['summary']['final_balance'],flush=True)
    pd.DataFrame(summaries).to_csv(out/'summary.csv',index=False)
    pd.DataFrame(controls).to_csv(out/'controls.csv',index=False)
    save(out/'receipt.json',dict(identities=identities(),selection_sha256=sha(selection),holdout_consumed=False,
        summary_sha256=sha(out/'summary.csv'),controls_sha256=sha(out/'controls.csv')))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['develop','evaluate']);args=parser.parse_args()
    for relative,digest in identities().items():
        saved=subprocess.check_output(['git','show','HEAD:'+relative],cwd=ROOT)
        if hashlib.sha256(saved).hexdigest()!=digest:
            raise ValueError('commit every builder and config before any real replay')
    cfg=json.loads(CONFIG.read_text())
    {'develop':develop,'evaluate':evaluate}[args.phase](cfg)


if __name__=='__main__':
    main()

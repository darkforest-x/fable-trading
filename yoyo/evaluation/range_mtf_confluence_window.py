"""Allow the already-qualified MTF arrow before the key-entry confirmation.

Owner2026-10-21 permits MTF first. Read v1 causal admission events only;
the key's own paired arrow must have been MTF-eligible at its close. The
existing entry is unchanged and must be0/15/30minutes later. Reuse every
native outcome, including screenshot management and matched controls.
This does not backdate entry, borrow another arrow or optimize a time window.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation.range_morphology import read,association,cluster_design
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.range_mtf_confluence import summarize

CONFIG=ROOT/'experiments/active/exp-range-v231-mtf-confluence-20261021-v2/config.json'


def window_gate(admission):
    """Only the paired completed arrow and existing entry clock are consulted."""
    entry=pd.to_datetime(admission.entry_time,utc=True)
    arrow=pd.to_datetime(admission.arrow_close,utc=True)
    minutes=(entry-arrow).dt.total_seconds()/60
    if not minutes.isin([0.,15.,30.]).all():
        raise ValueError('Frozen key pairing must be zero to two15m bars, never future or expired')
    if not admission.paired_arrow_was_mtf.isin([True,False]).all():
        raise ValueError('Admission must contain explicit causal booleans')
    allowed=admission.paired_arrow_was_mtf.astype(bool)
    if (admission.strict_both_now & ~allowed).any():
        raise ValueError('Strict intersection must be a subset of the same-arrow window')
    return allowed


def verify_completed():
    """Audit restored winners and both historical definitions as one test family."""
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent;old=ROOT/cfg['previous']
    current=json.loads((dest/'summary.json').read_text());prior=json.loads((old/'summary.json').read_text())
    family=[dict(definition=label,symbol=r['symbol'],wild_p=r['wild_p'])
        for label,summary in [('strict_same_close',prior),('paired_arrow_window',current)]
        for r in summary['primary_tests']]
    for row,p in zip(family,holm([r['wild_p'] for r in family])):row['holm_four']=float(p)
    checks=[]
    for coin in ['BTC','ETH']:
        g=read(dest/f'{coin}_admission.csv')
        before=read(old/coin/'admission.csv')
        pd.testing.assert_frame_equal(g[before.columns],before)
        rows=read(ROOT/prior['config']['source']/coin/'pivot/trades.csv')
        s=rows[rows.population=='signal'].set_index('pair_id')
        recent=pd.to_datetime(g.entry_time,utc=True)>=pd.Timestamp(cfg['recent_start'])
        r=g[recent];kept=r.loc[r.window_both,'pair_id'];removed=r.loc[~r.window_both,'pair_id']
        checks.append(dict(symbol=coin,recent_kept_wins=int((s.loc[kept,'net_r']>1e-9).sum()),
            recent_removed_wins=int((s.loc[removed,'net_r']>1e-9).sum()),
            recent_removed_net_r=float(s.loc[removed,'net_r'].sum())))
    dump(dest/'verification.json',dict(unchanged_admission_fields=True,new_executions=0,
        family_sensitivity=family,recent_checks=checks,
        explanation='Both definitions were inspected on the same known history. Holm4 is a conservative sensitivity across both coins and definitions, not independent validation.'))


def main():
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    if (dest/'summary.json').exists():
        print('Reuse completed '+str(dest/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit window-gate builder first')
    old=ROOT/cfg['previous'];prior=json.loads((old/'summary.json').read_text());oldcfg=prior['config']
    for stamp in prior['sources']:assert stamp==file_stamp(stamp['path'])
    source=ROOT/oldcfg['source'];arms=list(dict.fromkeys(r['arm'] for r in prior['rows']))
    results=[];checks=[];tests=[];stability=[];inputs=[file_stamp(old/'summary.json')]
    partitions=0;baseline_checks=0
    for coin in ['BTC','ETH']:
        path=old/coin/'admission.csv';g=read(path);inputs.append(file_stamp(path))
        allow=window_gate(g);g['window_both']=allow
        g.to_csv(dest/f'{coin}_admission.csv',index=False)
        times=pd.to_datetime(g.entry_time,utc=True)
        periods=dict(all=np.ones(len(g),bool),first_half=times<pd.Timestamp(cfg['midpoint']),
            second_half=times>=pd.Timestamp(cfg['midpoint']),recent2m=times>=pd.Timestamp(cfg['recent_start']))
        additional=g[g.window_both&~g.strict_both_now]
        checks.append(dict(symbol=coin,all=len(g),strict=int(g.strict_both_now.sum()),
            window=int(allow.sum()),added=len(additional),
            added15m=int(((pd.to_datetime(additional.entry_time)-pd.to_datetime(additional.arrow_close)).dt.total_seconds()==900).sum()),
            added30m=int(((pd.to_datetime(additional.entry_time)-pd.to_datetime(additional.arrow_close)).dt.total_seconds()==1800).sum()),
            added_opposing_watch_at_entry=int((additional.entry_watch_side!=additional.side).sum())))
        for arm in arms:
            ledger=old/coin/'native/screenshot/trades.csv' if arm=='screenshot' else source/coin/arm/'trades.csv'
            rows=read(ledger)
            for period,valid in periods.items():
                current=g[valid]
                groups=dict(all=current.pair_id.tolist(),strict=current.loc[current.strict_both_now,'pair_id'].tolist(),
                    both=current.loc[current.window_both,'pair_id'].tolist(),excluded=current.loc[~current.window_both,'pair_id'].tolist())
                stats={name:summarize(rows,ids) for name,ids in groups.items()}
                for col in ['n','wins','net_r','random_net_r']:
                    np.testing.assert_allclose(stats['both'][col]+stats['excluded'][col],stats['all'][col],atol=1e-7,rtol=0)
                partitions+=1
                for group,previous_group in [('all','all'),('strict','both')]:
                    saved=next(r for r in prior['rows'] if r['symbol']==coin and r['arm']==arm and r['period']==period and r['group']==previous_group)
                    for col in ['n','wins','net_r','random_net_r']:
                        np.testing.assert_allclose(saved[col],stats[group][col],atol=1e-7,rtol=0)
                    baseline_checks+=1
                results.extend(dict(symbol=coin,arm=arm,period=period,group=name,**stat) for name,stat in stats.items())
            if arm=='pivot':
                s=rows[rows.population=='signal'].set_index('pair_id').loc[g.pair_id]
                local=times.dt.tz_convert('America/New_York').dt.tz_localize(None)
                week=(local.dt.normalize()-pd.to_timedelta(local.dt.dayofweek,unit='D')).dt.strftime('%Y-%m-%d')
                d=pd.DataFrame(dict(net_win=(s.net_r.to_numpy()>1e-9).astype(int),week=week,
                    side=g.side,risk_bp=s.side.to_numpy()*(s.entry-s.stop).to_numpy()/s.entry.to_numpy()*10000,
                    entry_time=g.entry_time))
                tests.append(dict(symbol=coin,**association(d,allow,cluster_design(d,cfg),cfg)))
        same=[r for r in results if r['symbol']==coin and r['period']=='all' and r['group']=='both' and r['arm']!='screenshot']
        baseline={r['arm']:r for r in results if r['symbol']==coin and r['period']=='all' and r['group']=='all'}
        stability.append(dict(symbol=coin,higher_win_arms=sum(r['win_rate']>baseline[r['arm']]['win_rate'] for r in same),
            total_arms=len(same),positive_net_arms=sum(r['net_r']>0 for r in same)))
    for row,p in zip(tests,holm([r['wild_p'] for r in tests])):row['holm_p']=float(p)
    for stamp in inputs:assert stamp==file_stamp(stamp['path'])
    pd.DataFrame(results).to_csv(dest/'comparison.csv',index=False)
    dump(dest/'summary.json',dict(config=cfg,rows=results,checks=checks,primary_tests=tests,stability=stability,
        partitions_reconciled=partitions,prior_aggregates_reproduced=baseline_checks,new_native_paths=0,
        inputs=inputs,builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        limitations='User-revised historical comparison, not an independent validation. Shared15m oscillator; bounded paired-arrow window only. Independent netR and equal-notionalbp; frozen20bp costs, original matching and boundary treatment. No Pine edit.'))
    print(json.dumps([r for r in results if r['arm'] in ['pivot','screenshot'] and r['period'] in ['all','recent2m'] and r['group']=='both'],indent=2))


if __name__=='__main__':main()

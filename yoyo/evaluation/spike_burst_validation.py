"""Evaluate frozen SPIKE entries with shared execution and causal cashbooks.

Source and decision protocol: exp-spike-burst-validation-20260910-v1/PROJECT_PLAN.md.
Only previously frozen event/control records are evaluated. Outcome columns are
labels, never inputs to signal selection or account priority. Calendar NAV slices
inherit positions; entry-cohort trade statistics may exit after a slice boundary.
Permutation units are asset means of asset/week excesses, not exchange copies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.altseason_portfolio import run_portfolio, summarize_portfolio
from yoyo.evaluation.altseason_paired_portfolio import matched_sets
from yoyo.evaluation.altseason_research import (
    START, END, PERIODS, SCOPES, scope_rows, paired_test, holm, rank_auc,
    portfolio_periods, read_events,
)
from yoyo.evaluation.launch_quality_accounts import load_prices, write_csv

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT/'experiments/active/exp-spike-burst-validation-20260910-v1'
ARMS = ('burst_trail', 'focus_trail')


def primary_holm(values):
    """Keep all sixteen preregistered hypotheses even when some lack power."""
    p=np.asarray(values,dtype=float)
    if len(p)!=16 or np.any((p[np.isfinite(p)]<0)|(p[np.isfinite(p)]>1)):
        raise ValueError('Exactly sixteen valid-or-missing primary p values required')
    adjusted=holm(np.where(np.isfinite(p),p,1.0))
    adjusted[~np.isfinite(p)]=np.nan
    return adjusted


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def event_summary(actual, controls):
    """Paired means use identical actual identities; unmatched is not zero."""
    full, paired, zero = matched_sets(actual, controls)
    g = full.copy()
    for col in ('net_bp','gross_bp','net_r','net_return','peak_r','initial_risk_frac','relative_volume','tr_expansion'):
        g[col]=pd.to_numeric(g[col],errors='coerce')
    control_valid=controls.loc[controls.valid.eq(True)].copy()
    control_valid['net_bp']=pd.to_numeric(control_valid.net_bp,errors='coerce')
    mean = control_valid.groupby('matched_event_id').net_bp.mean()
    g['control_mean_net_bp'] = g.event_id.map(mean)
    g['excess_bp'] = g.net_bp - g.control_mean_net_bp
    p, units, balanced = paired_test(g)
    natural = g.loc[g.natural_exit.eq(True)]
    paired_any = g.loc[g.excess_bp.notna()]
    row = dict(candidates=len(actual), valid=len(g), paired0=len(paired),
        matched_fraction=len(paired_any)/len(g) if len(g) else np.nan,
        paired0_fraction=len(paired)/len(g) if len(g) else np.nan,
        mean_gross_bp=g.gross_bp.mean(), mean_net_bp=g.net_bp.mean(),
        median_net_r=g.net_r.median(), win_rate=g.net_return.gt(0).mean(),
        paired_actual_net_bp=paired_any.net_bp.mean(),
        paired_random_net_bp=paired_any.control_mean_net_bp.mean(),
        mean_excess_bp=paired_any.excess_bp.mean(), permutation_p=p,
        permutation_assets=units, asset_balanced_excess_bp=balanced,
        initial_stop_rate=g.exit_reason.astype(str).str.startswith('initial_stop').mean(),
        natural_exits=len(natural), boundary_marks=int(g.censored.sum()),
        natural_20pct=int(natural.net_return.ge(.2).sum()),
        natural_50pct=int(natural.net_return.ge(.5).sum()),
        natural_5r=int(natural.net_r.ge(5).sum()),
        peak_r_max=g.peak_r.max(), net_r_max=g.net_r.max(),
        risk_pct_median=100*g.initial_risk_frac.median())
    scores=[]
    for feature in ('relative_volume','tr_expansion'):
        h=g.loc[np.isfinite(pd.to_numeric(g[feature],errors='coerce'))].copy()
        if h.empty:
            continue
        h=h.sort_values([feature,'event_id'],ascending=[False,True])
        top=h.head(max(1,int(np.ceil(len(h)*.1))))
        scores.append(dict(feature=feature,n=len(h),top_n=len(top),
            descriptive_auc=rank_auc(h[feature],h.net_bp.gt(0)),
            top_gross_bp=top.gross_bp.mean(),top_net_bp=top.net_bp.mean(),
            top_win_rate=top.net_bp.gt(0).mean(),
            top_matched_actual_bp=top.loc[top.excess_bp.notna(),'net_bp'].mean(),
            top_random_bp=top.control_mean_net_bp.mean(),top_excess_bp=top.excess_bp.mean()))
    return row,scores


def evaluate_account(events, prices, minutes):
    curve, ledger=run_portfolio(events,prices,START,END,minutes)
    summary=summarize_portfolio(curve,ledger)
    filled=ledger.loc[ledger.portfolio_selected.eq(True)] if len(ledger) else ledger
    natural=filled.loc[filled.natural_exit.eq(True)] if len(filled) else filled
    extra_fee=float((filled.quantity*(filled.exit_price-filled.entry_price)*.001).sum()) if len(filled) else 0.
    summary.update(candidates=len(events),natural_big_winners=int(natural.net_return.ge(.5).sum()) if len(natural) else 0,
        natural_5r=int(natural.net_r.ge(5).sum()) if len(natural) else 0,
        initial_stop_rate=float(filled.exit_reason.astype(str).str.startswith('initial_stop').mean()) if len(filled) else np.nan,
        stress40_static_pct=summary['return_pct']-float(filled.notional.sum()*.002/1000) if len(filled) else summary['return_pct'],
        stress60_static_pct=summary['return_pct']-float(filled.notional.sum()*.004/1000) if len(filled) else summary['return_pct'],
        actual_turnover_fee_static_pct=summary['return_pct']-extra_fee/1000)
    return summary,curve,ledger


def _sources():
    names=['spike_burst_validation','spike_burst_dataset','spike_burst_replay','spike_burst_execution',
           'altseason_portfolio','altseason_research','altseason_paired_portfolio','launch_quality_accounts']
    files=[ROOT/('yoyo/evaluation/'+name+'.py') for name in names]
    files += [EXPERIMENT/'PROJECT_PLAN.md',ROOT/'yoyo/evaluation/pine/spike_burst_v1.pine']
    for path in files:
        committed=subprocess.check_output(['git','show','HEAD:'+str(path.relative_to(ROOT))],cwd=ROOT)
        if committed!=path.read_bytes():
            raise ValueError('Commit exact source before evaluation: '+str(path))
    return {str(p.relative_to(ROOT)):sha(p) for p in files}


def run(folder=EXPERIMENT/'results'):
    folder=Path(folder)
    sources=_sources()
    receipt_path=folder/'validation_manifest.json'
    if receipt_path.exists() or (folder/'accounts').exists() or (folder/'validation_started.json').exists():
        raise ValueError('Refusing frozen result overwrite')
    inputs=json.loads((folder/'dataset_manifest.json').read_text())
    for item in inputs['artifacts']:
        if sha(item['path'])!=item['sha256']:
            raise ValueError('Changed dataset artifact: '+item['path'])
    hashes={str(Path(x['path']).resolve()):x['sha256'] for x in inputs['feature_sources']}
    events=read_events(folder/'events.csv.gz')
    controls=read_events(folder/'controls.csv.gz')
    receipt=dict(status='running',generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        input_manifest_sha256=sha(folder/'dataset_manifest.json'),builder_sha256=sources,
        holdout_consumption=1,retrospective=True,optimization=False)
    (folder/'validation_started.json').write_text(json.dumps(receipt,indent=2)+'\n')
    rows=[];event_rows=[];score_rows=[];period_rows=[];artifacts=[]
    for minutes in (60,240):
        e=events.loc[events.minutes.eq(minutes)].copy()
        c=controls.loc[controls.minutes.eq(minutes)].copy()
        prices=load_prices(e,c,hashes)
        for scope in SCOPES:
            for arm in ARMS:
                a=scope_rows(e.loc[e.arm.eq(arm)],scope)
                random=scope_rows(c.loc[c.arm.eq(arm)],scope)
                context=dict(scope=scope,minutes=minutes,arm=arm)
                stats, scores=event_summary(a,random)
                event_rows.append(dict(context,**stats))
                score_rows.extend(dict(context,**row) for row in scores)
                for population in (('all_assets','without_useless') if scope=='combined' else ('all_assets',)):
                    aa=a if population=='all_assets' else a.loc[~a.asset.eq('USELESS')]
                    cc=random if population=='all_assets' else random.loc[~random.asset.eq('USELESS')]
                    _,paired,zero=matched_sets(aa,cc)
                    for account,frame in (('full',aa),('paired_actual',paired),('paired_random',zero)):
                        key=dict(context,population=population,account=account)
                        summary,curve,ledger=evaluate_account(frame,prices,minutes)
                        rows.append(dict(key,**summary))
                        periods=portfolio_periods(curve,ledger,scope,minutes,arm)
                        period_rows.extend(dict(row,population=population,account=account) for row in periods)
                        name='_'.join(str(v) for v in key.values())
                        for suffix,table in (('curve',curve),('ledger',ledger)):
                            path=folder/'accounts'/(name+'_'+suffix+'.csv.gz')
                            write_csv(path,table);artifacts.append(path)
                print(minutes,scope,arm,'accounts verified',flush=True)
    summary=pd.DataFrame(event_rows)
    summary['holm_p']=primary_holm(summary.permutation_p.to_numpy(float))
    for name,table in (('accounts_summary.csv',pd.DataFrame(rows)),('event_summary.csv',summary),
                       ('score_summary.csv',pd.DataFrame(score_rows)),('period_summary.csv',pd.DataFrame(period_rows))):
        path=folder/name;write_csv(path,table);artifacts.append(path)
    receipt.update(status='complete',completed_at=pd.Timestamp.now(tz='UTC').isoformat(),
        account_count=len(rows),primary_tests=len(summary),
        costs='20bp entry-notional round trip; funding/impact excluded from base account; static stress does not reallocate',
        artifacts=[dict(path=str(p.resolve()),sha256=sha(p),size_bytes=p.stat().st_size) for p in artifacts])
    receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--results',type=Path,default=EXPERIMENT/'results')
    run(parser.parse_args().results)

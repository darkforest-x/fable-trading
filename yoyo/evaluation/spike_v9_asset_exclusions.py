"""Read saved V8/V9 outcomes to quantify fixed, whole-asset exclusions.

No prices or indicators are read. Pooled per-asset scores use closed trades:
full-history hindsight or entry and exit strictly before the frozen split.
Later outcomes cannot change an earlier blacklist. Independent retained stream
paths and their original random controls remain unchanged; R is not cash PnL.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, periods, START, SPLIT, END
from yoyo.evaluation.spike_v9_full_report import boolean, control_metrics
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v9-asset-exclusions-20260915-v1')
DEPENDENCIES = [Path(__file__), EXP/'config.json', EXP/'PROJECT_PLAN.md',
    Path('tests/evaluation/test_spike_v9_asset_exclusions.py'),
    Path('yoyo/evaluation/spike_be05_report.py'), Path('yoyo/evaluation/spike_v9_full_report.py')]
UNKNOWN = '__UNKNOWN_ASSET__'
POLICIES = ('win_le_10pct', 'mean_lt_neg036', 'pf_lt_05')
TRADE_COLS = ['stream_key','asset','timeframe_min','venue','arm','event_key','entry_time',
              'exit_time','net_r','gross_r','net_return','censored']
CONTROL_COLS = ['arm','event_key','asset','entry_time','control_entry_time','control_exit_time',
                'matched','target_net_r','target_net_return','control_net_r','control_net_return']


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str)+'\n')


def asset_scores(trades, *, before=None):
    """Pool individual closed R outcomes, optionally known strictly before cut."""
    closed=trades.loc[~trades.censored].copy()
    if before is not None:
        closed=closed.loc[(closed.entry_time<before)&(closed.exit_time<before)]
    closed['win']=closed.net_r.gt(0)
    closed['positive_r']=closed.net_r.clip(lower=0)
    closed['negative_r']=-closed.net_r.clip(upper=0)
    result=closed.groupby('asset',sort=True).agg(closed=('net_r','size'),wins=('win','sum'),
        total_r=('net_r','sum'),positive_r=('positive_r','sum'),negative_r=('negative_r','sum'))
    result['win_rate']=result.wins/result.closed
    result['mean_r']=result.total_r/result.closed
    result['pf_r']=result.positive_r/result.negative_r
    result['score_known']=result.index!=UNKNOWN
    return result


def blacklist(scores, policy):
    """Exact owner boundaries; undefined PF/unknown assets do not prove failure."""
    if policy=='win_le_10pct': fail=scores.win_rate.le(.10)
    elif policy=='mean_lt_neg036': fail=scores.mean_r.lt(-.36)
    elif policy=='pf_lt_05': fail=scores.pf_r.lt(.5)
    else: raise ValueError(policy)
    return set(scores.index[fail & scores.score_known])


def retained(trades, excluded):
    """An entire asset is removed across all venues and timeframes."""
    return trades.loc[~trades.asset.isin(excluded)].copy()


def load(cfg):
    root=Path(cfg['source'])
    if digest(root/'statistics_receipt.json')!=cfg['source_receipt_sha256']:
        raise ValueError('source receipt drift')
    receipt=json.loads((root/'statistics_receipt.json').read_text())
    if receipt['streams']!=3531 or receipt['trades']!=177473 or receipt['controls']!=177473:
        raise ValueError('source coverage drift')
    for name,sha in cfg['files'].items():
        if receipt['files'][name]!=sha or digest(root/name)!=sha:raise ValueError(f'source changed: {name}')
    trades=pd.read_csv(root/'trades.csv.gz',usecols=TRADE_COLS,keep_default_na=False)
    controls=pd.read_csv(root/'controls.csv.gz',usecols=CONTROL_COLS,keep_default_na=False)
    for frame in (trades,controls):
        for col in ('entry_time','exit_time','control_entry_time','control_exit_time'):
            if col in frame:frame[col]=pd.to_datetime(frame[col],utc=True,errors='raise')
        for col in ('net_r','gross_r','net_return','target_net_r','target_net_return','control_net_r','control_net_return'):
            if col in frame:frame[col]=pd.to_numeric(frame[col],errors='coerce')
        for col in ('censored','matched'):
            if col in frame:frame[col]=boolean(frame[col])
        if len(frame)!=177473 or frame.duplicated(['arm','event_key']).any():raise ValueError('source identity drift')
    for frame in (trades,controls):
        if frame.asset.eq(UNKNOWN).any():raise ValueError('sentinel collision')
        frame['asset']=frame.asset.mask(frame.asset.eq(''),UNKNOWN)
    actual=pd.MultiIndex.from_frame(trades[['arm','event_key']])
    expected=pd.MultiIndex.from_frame(controls[['arm','event_key']])
    if len(actual.difference(expected)) or len(expected.difference(actual)):raise ValueError('controls coverage drift')
    paired=trades[['arm','event_key','asset','net_r']].merge(
        controls[['arm','event_key','asset','target_net_r']],on=['arm','event_key'],validate='one_to_one')
    if not paired.asset_x.eq(paired.asset_y).all() or not np.allclose(paired.net_r,paired.target_net_r,equal_nan=True):
        raise ValueError('controls target identity drift')
    if not np.isfinite(trades.loc[~trades.censored,['net_r','gross_r','net_return']]).all().all():
        raise ValueError('closed outcomes undefined')
    if (trades.entry_time.isna().any() or trades.exit_time.isna().any()
            or not set(trades.arm)=={'v8','v9'} or not set(trades.timeframe_min)<={30,60,240}):
        raise ValueError('source clock/arm/timeframe drift')
    return trades,controls,pd.read_csv(root/'summary.csv')


def summarize(part, controls, period, baseline, excluded, fit):
    stat=metrics(part); original=metrics(baseline)
    removed=baseline.loc[baseline.asset.isin(excluded)]
    if len(part)+len(removed)!=len(baseline):raise ValueError('selection count does not reconcile')
    if not np.isclose(stat['total_r']+metrics(removed)['total_r'],original['total_r'],atol=1e-7,rtol=1e-12):
        raise ValueError('R attribution does not reconcile')
    closed=part.loc[~part.censored]; baseclosed=baseline.loc[~baseline.censored]
    tail=baseclosed.loc[baseclosed.net_r.ge(10)]
    retained_tail=int(tail.event_key.isin(closed.event_key).sum())
    dropped=fit.loc[fit.index.isin(excluded)]
    return dict(**stat,**control_metrics(part,controls,period),assets_with_events=part.asset.nunique(),
        known_assets_with_events=part.loc[part.asset.ne(UNKNOWN),'asset'].nunique(),
        blacklist_assets=len(excluded),excluded_assets_present=removed.asset.nunique(),
        removed_closed=int((~removed.censored).sum()),net_r_delta=stat['total_r']-original['total_r'],
        gross_r=closed.gross_r.sum(),gross_r_delta=closed.gross_r.sum()-baseclosed.gross_r.sum(),
        cost_r_reduction=(baseclosed.gross_r-baseclosed.net_r).sum()-(closed.gross_r-closed.net_r).sum(),
        original_ge10=len(tail),retained_original_ge10=retained_tail,lost_original_ge10=len(tail)-retained_tail,
        excluded_fit_under10=int(dropped.closed.lt(10).sum()),excluded_fit_under30=int(dropped.closed.lt(30).sum()),
        unscored_retained_assets=int((~part.loc[part.asset.ne(UNKNOWN),'asset'].drop_duplicates().isin(fit.index)).sum()),
        unknown_asset_closed=int(baseclosed.asset.eq(UNKNOWN).sum()))


def run(output):
    if not _committed(tuple(DEPENDENCIES)):raise ValueError('commit unchanged builder/config/tests before aggregation')
    cfg=json.loads((EXP/'config.json').read_text())
    if (pd.Timestamp(cfg['start'])!=START or pd.Timestamp(cfg['split'])!=SPLIT or pd.Timestamp(cfg['end'])!=END
            or cfg['round_trip_cost']!=.002):raise ValueError('frozen calendar/cost drift')
    expected={'win_le_10pct':{'metric':'win_rate','operator':'<=','threshold':.10},
        'mean_lt_neg036':{'metric':'mean_r','operator':'<','threshold':-.36},
        'pf_lt_05':{'metric':'pf_r','operator':'<','threshold':.5}}
    if cfg['policies']!=expected:raise ValueError('owner threshold drift')
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'evaluation_started.json',dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        started_at=datetime.now(timezone.utc),saved_outcome_consumption_per_policy_mode=1,
        new_ohlc_reads=0,new_replay_runs=0,history='explicit what-if on already-exposed outcomes',
        dependencies={str(p):digest(p) for p in DEPENDENCIES}))
    trades,controls,oracle=load(cfg)
    summaries=[];scores_list=[];coverage=[]
    for arm in cfg['arms']:
        frame=trades.loc[trades.arm.eq(arm)];armcontrols=controls.loc[controls.arm.eq(arm)]
        slices=dict(periods(frame))
        for period,part in slices.items():
            got=metrics(part);target=oracle.loc[oracle.arm.eq(arm)&oracle.period.eq(period)].iloc[0]
            for col in ('events','closed','censored','total_r','realized_ge10','pf_r'):
                if not np.isclose(got[col],target[col],atol=1e-7,rtol=1e-12):raise ValueError(f'baseline mismatch {arm} {period} {col}')
        for mode in cfg['selection_modes']:
            fit=asset_scores(frame,before=None if mode=='full_history_posthoc' else SPLIT)
            selected={p:blacklist(fit,p) for p in POLICIES}
            score=fit.copy();score['arm']=arm;score['selection_mode']=mode
            for policy,names in selected.items():score[policy]=score.index.isin(names)
            scores_list.append(score.reset_index())
            periods_to_score=('full','earlier','later') if mode=='full_history_posthoc' else ('later',)
            for period in periods_to_score:
                base=slices[period]
                for policy,excluded in [('baseline',set()),*selected.items()]:
                    part=retained(base,excluded)
                    summaries.append(dict(arm=arm,selection_mode=mode,period=period,policy=policy,
                        **summarize(part,armcontrols,period,base,excluded,fit)))
            coverage.append(dict(arm=arm,selection_mode=mode,fit_known_assets=int(fit.score_known.sum()),
                                 fit_closed=int(fit.closed.sum()),asset_blacklists={p:sorted(v) for p,v in selected.items()}))
    summary=pd.DataFrame(summaries);summary.to_csv(output/'summary.csv',index=False)
    pd.concat(scores_list,ignore_index=True).to_csv(output/'asset_scores.csv',index=False)
    write_json(output/'blacklists.json',coverage)
    write_json(output/'manifest.json',dict(complete=True,source_receipt_sha256=cfg['source_receipt_sha256'],
        source_files=cfg['files'],configuration=cfg,baseline_period_checks=6,source_events=len(trades),
        rows=len(summary),files={p.name:digest(p) for p in output.iterdir() if p.is_file()}))
    print(summary.loc[(summary.selection_mode.eq('full_history_posthoc')&summary.period.eq('full'))|
        summary.selection_mode.eq('earlier_frozen_later'),['arm','selection_mode','policy','blacklist_assets','closed',
        'total_r','mean_r','pf_r','realized_ge10','paired_excess_r','p_month_signflip']].to_string(index=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)

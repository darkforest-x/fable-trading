"""Frozen three-year SPIKE validation; no parameter fitting or live imports.

The history adapter authenticates Binance monthly 15m quote turnover and the
native recent 1H tail. Every continuous segment restarts the existing features.
Current/prior OHLCV only feed the unchanged SPIKE replay and old focus baseline.
All candidates and same-asset/week/causal-volatility controls are frozen before
any execution. Existing next-open, cost and cashbook engines are reused intact.

The only experiment changes are the longer calendar and documented universe.
BTC/ETH diagnostics are separate from the primary altcoin pool. Annual NAV
slices inherit positions; annual trade counts use exit times, not future labels
assigned to earlier entry cohorts. Model validation AUC is not applicable.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.altseason_sources import atomic_json, utc_now
from yoyo.evaluation.altseason_engine import build_features
from yoyo.evaluation.altseason_portfolio import run_portfolio, summarize_portfolio
from yoyo.evaluation.altseason_paired_portfolio import matched_sets
from yoyo.evaluation.altseason_research import read_events, holm
from yoyo.evaluation.launch_quality_accounts import load_prices, write_csv
from yoyo.evaluation.launch_quality_dataset import _id, artifact, _check
from yoyo.evaluation.spike_burst_dataset import prepare_job, schedule_rows, score_jobs
from yoyo.evaluation.spike_burst_validation import event_summary

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-burst-three-year-20260910-v1'
START, END = pd.Timestamp('2023-09-09T00:00:00Z'), pd.Timestamp('2026-09-09T00:00:00Z')
CUTS = [START, pd.Timestamp('2024-09-09T00:00:00Z'), pd.Timestamp('2025-09-09T00:00:00Z'), END]
ARMS = ('burst_trail', 'focus_trail')
PINE_SHA = '18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2'
SCHEDULE_COLUMNS = ['event_id', 'matched_event_id', 'control_number', 'instrument',
                    'minutes', 'arm', 'decision_i', 'features_path']
CONFIG = dict(schema='spike-burst-three-year-v1', start=START.isoformat(), end=END.isoformat(),
              annual_cuts=[x.isoformat() for x in CUTS], minutes=[60, 240], arms=list(ARMS),
              direction='long_only', primary_cohort='altcoins', diagnostic_cohorts=['BTC', 'ETH'],
              cost_roundtrip=.002, primary_hypotheses=4, holdout_consumption=2,
              retrospective=True, optimization=False, production_eligible=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources():
    """All executable study sources must be committed before real artifacts."""
    paths = [Path(__file__), ROOT/'yoyo/data/spike_burst_history.py',
             ROOT/'yoyo/data/binance_um_archives.py', EXP/'PROJECT_PLAN.md',
             ROOT/'yoyo/evaluation/pine/spike_burst_v1.pine', ROOT/'yoyo/data/altcoin_features.py']
    names = ['spike_burst_dataset', 'spike_burst_execution', 'spike_burst_replay',
             'spike_burst_validation', 'altseason_engine', 'altseason_portfolio',
             'altseason_paired_portfolio', 'altseason_research',
             'launch_quality_accounts', 'launch_quality_dataset']
    paths.extend(ROOT/('yoyo/evaluation/'+name+'.py') for name in names)
    for path in paths:
        committed = subprocess.check_output(['git', 'show', 'HEAD:'+str(path.relative_to(ROOT))], cwd=ROOT)
        if committed != path.read_bytes():
            raise ValueError('Commit exact builder before evaluation: '+str(path))
    if sha(ROOT/'yoyo/evaluation/pine/spike_burst_v1.pine') != PINE_SHA:
        raise ValueError('Frozen Pine changed')
    return {str(p.relative_to(ROOT)):sha(p) for p in paths}


def _prepare(segment, output):
    """Existing 1H/4H baseline builder supplies only causal input semantics."""
    output = Path(output)
    path = _check(segment['source_features_path'], segment['source_features_sha256'])
    bars = pd.read_pickle(path)
    tables = build_features(bars)
    jobs, coverage = [], []
    for minutes, frame in tables.items():
        info = dict(segment, minutes=minutes)
        status = 'included' if len(frame) >= 2 else 'fewer_than_two_complete_bars'
        if len(frame) >= 2:
            baseline = output/'baseline'/(_id(segment['instrument'], minutes)+'.pkl.gz')
            baseline.parent.mkdir(parents=True, exist_ok=True)
            frame.to_pickle(baseline, compression={'method':'gzip', 'mtime':0})
            info.update(source_features_path=str(baseline.resolve()), source_features_sha256=sha(baseline))
            job = prepare_job(info, output, START, END)
            if job is not None:
                jobs.append(job)
            else:
                status = 'no_complete_bars_before_end'
        coverage.append(dict(venue=segment['venue'], symbol=segment['symbol'], asset=segment['asset'],
                             instrument=segment['instrument'], minutes=minutes, bars=len(frame), status=status))
    return jobs, coverage


def _score(job, folder, global_matching_sha):
    """A worker gets a verified subset of the already frozen global schedule.

    This is a partition for compute only. A worker cannot invent a candidate:
    its job must equal the parent manifest entry, including decision indices.
    Parent schedule artifacts remain checked by the unchanged score_jobs.
    """
    folder = Path(folder)
    matching_path = _check(folder/'matching.json', global_matching_sha)
    parent = json.loads(matching_path.read_text())
    key = (job['instrument'], job['minutes'])
    matches = [r for r in parent['jobs'] if (r['instrument'], r['minutes']) == key]
    if len(matches) != 1 or matches[0] != job:
        raise ValueError('Score partition is not an exact frozen parent job')
    sub = folder/'scored'/(_id(*key))
    sub.mkdir(parents=True, exist_ok=False)
    atomic_json(sub/'matching.json', dict(jobs=[job], schedule_artifacts=parent['schedule_artifacts'],
                                        parent_matching_sha256=global_matching_sha))
    actual, random = score_jobs([job], sub, END)
    for name, frame in [('events.csv.gz',actual), ('controls.csv.gz',random)]:
        write_csv(sub/name, frame)
    return dict(events=str(sub/'events.csv.gz'), controls=str(sub/'controls.csv.gz'))


def prepare(history_path=EXP/'data_tradable/history_manifest.json', folder=EXP/'results', workers=2):
    folder, history_path = Path(folder), Path(history_path)
    frozen_sources = sources()
    if (folder/'dataset_started.json').exists():
        raise ValueError('Refusing to overwrite existing history dataset')
    history_artifact = artifact(history_path)
    history = json.loads(history_path.read_text())
    if history.get('schema') != 'spike-burst-history-v1' or history.get('status') != 'complete':
        raise ValueError('Complete authenticated history manifest required')
    history_start = pd.Timestamp(history.get('start'))
    history_end = pd.Timestamp(history.get('exclusive_end'))
    if (pd.isna(history_start) or pd.isna(history_end) or
            history_start > pd.Timestamp('2023-05-01T00:00:00Z') or history_end < END):
        raise ValueError('History request does not cover the frozen calendar and warmup')
    for source in history.get('builder_sources', []):
        key = str(Path(source['path']).resolve().relative_to(ROOT))
        if frozen_sources.get(key) != source['sha256']:
            raise ValueError('History adapter/parser source differs from this study')
    if len(history.get('builder_sources', [])) != 2:
        raise ValueError('Missing history adapter/parser source receipt')
    if {Path(s['path']).name for s in history['builder_sources']} != {'spike_burst_history.py','binance_um_archives.py'}:
        raise ValueError('History adapter/parser receipt identities missing or duplicated')
    if history.get('tradability_audit', {}).get('status') != 'complete':
        raise ValueError('Listing/delivery and zero-volume boundary audit required before scoring')
    folder.mkdir(parents=True, exist_ok=True)
    started = dict(status='running', generated_at=utc_now(), config=CONFIG,
                   code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                   source_hashes=frozen_sources, history=history_artifact)
    atomic_json(folder/'dataset_started.json', started)
    segments = [s for s in history['segments'] if not s.get('exclude_reason')]
    if len({s['instrument'] for s in segments}) != len(segments):
        raise ValueError('History instrument identity collision')
    jobs, coverages = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_prepare, s, folder):s for s in segments}
        for future in as_completed(pending):
            js, cs = future.result()
            jobs.extend(js); coverages.extend(cs)
            print('prepared', pending[future]['instrument'], 'jobs', len(jobs), flush=True)
    jobs.sort(key=lambda j:(j['instrument'],j['minutes']))
    coverages.sort(key=lambda j:(j['instrument'],j['minutes']))
    if not jobs:
        raise ValueError('No authenticated evaluable history jobs; no return report can be built')
    actual_schedule, control_schedule = schedule_rows(jobs)
    schedules = []
    for name, rows in [('candidate_schedule.csv.gz',actual_schedule),('control_schedule.csv.gz',control_schedule)]:
        path = folder/name
        write_csv(path,pd.DataFrame(rows,columns=SCHEDULE_COLUMNS)); schedules.append(artifact(path))
    write_csv(folder/'coverage.csv', pd.DataFrame(coverages))
    reused = Counter((r['instrument'],r['minutes'],r['decision_i']) for r in control_schedule)
    matching = dict(config=CONFIG, jobs=jobs, schedule_artifacts=schedules, generated_at=utc_now(),
                    candidate_rows=len(actual_schedule), control_rows=len(control_schedule),
                    unique_control_positions=len(reused), reused_control_positions=sum(v>1 for v in reused.values()),
                    maximum_control_reuse=max(reused.values(), default=0))
    atomic_json(folder/'matching.json',matching)
    frozen_matching = sha(folder/'matching.json')
    # No score worker starts before the complete global matching is immutable.
    scored = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(_score,job,folder,frozen_matching) for job in jobs]
        for future in as_completed(pending):
            scored.append(future.result())
    for kind in ('events','controls'):
        tables = [read_events(row[kind]) for row in sorted(scored,key=lambda x:x['events'])]
        data = pd.concat(tables,ignore_index=True) if tables else pd.DataFrame()
        if len(data) and data.event_id.duplicated().any():
            raise ValueError('Duplicate scored event identities')
        write_csv(folder/(kind+'.csv.gz'),data)
    if frozen_sources != sources():
        raise ValueError('Study source changed during preparation')
    _check(history_artifact['path'],history_artifact['sha256'])
    _check(folder/'matching.json',frozen_matching)
    for segment in segments:
        _check(segment['source_features_path'], segment['source_features_sha256'])
    for job in jobs:
        _check(job['source_features_path'], job['source_features_sha256'])
        _check(job['features_path'], job['features_sha256'])
    feature_sources = [artifact(j['features_path']) for j in jobs]
    artifacts = [artifact(folder/name) for name in ('events.csv.gz','controls.csv.gz','coverage.csv',
                 'matching.json','candidate_schedule.csv.gz','control_schedule.csv.gz')]
    result = dict(started,status='complete',completed_at=utc_now(),prepared_jobs=len(jobs),
                  feature_sources=feature_sources,artifacts=artifacts+feature_sources,
                  event_count=len(actual_schedule),control_count=len(control_schedule))
    atomic_json(folder/'dataset_manifest.json',result)
    return result


def cohort_rows(frame, cohort):
    if cohort == 'altcoins':
        return frame.loc[~frame.asset.isin(['BTC','ETH'])].copy()
    return frame.loc[frame.asset.eq(cohort)].copy()


def annual_rows(curve, ledger, cuts=CUTS):
    """Continuous NAV, local high-water drawdown, trades by EXIT clock.

    The opening equity is the previous bar's known close. Existing positions
    are neither closed nor reopened at annual boundaries. Boundary-value
    events count only where their actual recorded close/mark is located.
    At a cut, a close belongs to the ending period; an open belongs to the
    new period, matching the cashbook's close-then-next-open sequence.
    """
    c = curve.copy()
    c['time'] = pd.to_datetime(c.time,utc=True)
    rows = []
    for i,(start,end) in enumerate(zip(cuts[:-1],cuts[1:])):
        prior = c.loc[c.time.le(start)]
        opening = float(prior.equity.iloc[-1]) if len(prior) else 100000.
        segment = c.loc[c.time.gt(start)&c.time.le(end)]
        if segment.empty:
            raise ValueError('Missing annual NAV slice')
        values = np.r_[opening,segment.equity.to_numpy(float)]
        dd = values/np.maximum.accumulate(values)-1
        selected = ledger.loc[ledger.portfolio_selected.eq(True)].copy() if len(ledger) else ledger
        if len(selected):
            clock = pd.to_datetime(selected.exit_time, utc=True)
            at_open = selected.exit_timing.eq('open')
            in_period = ((at_open & clock.ge(start) & clock.lt(end)) |
                         (~at_open & clock.gt(start) & clock.le(end)))
            exits = selected.loc[in_period]
        else:
            exits = selected
        natural = exits.loc[exits.natural_exit.eq(True)] if len(exits) else exits
        rows.append(dict(period='year'+str(i+1), start=start.isoformat(),end=end.isoformat(),
                         opening_equity=opening,ending_equity=values[-1],
                         return_pct=(values[-1]/opening-1)*100,max_drawdown_pct=-dd.min()*100,
                         exits=len(exits),win_rate=exits.realized_net_pnl.gt(0).mean() if len(exits) else np.nan,
                         natural_exits=len(natural),natural_win_rate=natural.realized_net_pnl.gt(0).mean() if len(natural) else np.nan))
    return rows


def evaluate(folder=EXP/'results'):
    folder=Path(folder)
    pins=sources()
    if (folder/'validation_started.json').exists():
        raise ValueError('Refusing existing validation overwrite')
    data=json.loads((folder/'dataset_manifest.json').read_text())
    if data.get('status')!='complete' or data.get('config')!=CONFIG:
        raise ValueError('Wrong dataset protocol')
    if data.get('source_hashes')!=pins:
        raise ValueError('Dataset was built with different study sources')
    for item in data['artifacts']:
        _check(item['path'],item['sha256'])
    events,controls=read_events(folder/'events.csv.gz'),read_events(folder/'controls.csv.gz')
    hashes={str(Path(x['path']).resolve()):x['sha256'] for x in data['feature_sources']}
    receipt=dict(status='running',generated_at=utc_now(),config=CONFIG,source_hashes=pins,
                 input_manifest_sha256=sha(folder/'dataset_manifest.json'))
    atomic_json(folder/'validation_started.json',receipt)
    accounts,stats,scores,periods,artifacts=[],[],[],[],[]
    for minutes in (60,240):
        e=events.loc[events.minutes.eq(minutes)].copy()
        c=controls.loc[controls.minutes.eq(minutes)].copy()
        prices=load_prices(e,c,hashes)
        for cohort in ('altcoins','BTC','ETH'):
            for arm in ARMS:
                a=cohort_rows(e.loc[e.arm.eq(arm)],cohort)
                zero=cohort_rows(c.loc[c.arm.eq(arm)],cohort)
                key=dict(minutes=minutes,cohort=cohort,arm=arm)
                row,feature_rows=event_summary(a,zero)
                stats.append(dict(key,**row))
                scores.extend(dict(key,**r) for r in feature_rows)
                _,paired,random=matched_sets(a,zero)
                for account,frame in [('full',a),('paired_actual',paired),('paired_random',random)]:
                    curve,ledger=run_portfolio(frame,prices,START,END,minutes)
                    summary=summarize_portfolio(curve,ledger)
                    filled=ledger.loc[ledger.portfolio_selected.eq(True)] if len(ledger) else ledger
                    natural=filled.loc[filled.natural_exit.eq(True)] if len(filled) else filled
                    summary.update(natural_win_rate=natural.realized_net_pnl.gt(0).mean() if len(natural) else np.nan,
                                   natural_5r=int(natural.net_r.ge(5).sum()) if len(natural) else 0,
                                   natural_50pct=int(natural.net_return.ge(.5).sum()) if len(natural) else 0,
                                   stress40_static_pct=summary['return_pct']-float(filled.notional.sum()*.002/1000) if len(filled) else summary['return_pct'],
                                   stress60_static_pct=summary['return_pct']-float(filled.notional.sum()*.004/1000) if len(filled) else summary['return_pct'])
                    context=dict(key,account=account)
                    accounts.append(dict(context,**summary))
                    periods.extend(dict(context,**r) for r in annual_rows(curve,ledger))
                    name='_'.join(str(v) for v in context.values())
                    for suffix,table in [('curve',curve),('ledger',ledger)]:
                        path=folder/'accounts'/(name+'_'+suffix+'.csv.gz')
                        write_csv(path,table);artifacts.append(artifact(path))
                print('accounts',minutes,cohort,arm,flush=True)
    table=pd.DataFrame(stats)
    table['holm_p']=np.nan
    primary=table.cohort.eq('altcoins')
    if primary.sum()!=4:
        raise ValueError('Exactly four preregistered primary hypotheses')
    ps=table.loc[primary,'permutation_p'].to_numpy(float)
    adjusted=holm(np.where(np.isfinite(ps),ps,1.))
    adjusted[~np.isfinite(ps)]=np.nan
    table.loc[primary,'holm_p']=adjusted
    for name,rows in [('accounts_summary.csv',accounts),('event_summary.csv',table),
                      ('score_summary.csv',scores),('annual_summary.csv',periods)]:
        path=folder/name
        write_csv(path,pd.DataFrame(rows));artifacts.append(artifact(path))
    if sources()!=pins:
        raise ValueError('Source changed during account evaluation')
    _check(folder/'dataset_manifest.json', receipt['input_manifest_sha256'])
    for item in data['artifacts']:
        _check(item['path'],item['sha256'])
    receipt.update(status='complete',completed_at=utc_now(),artifacts=artifacts,account_count=len(accounts))
    atomic_json(folder/'validation_manifest.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','evaluate'])
    parser.add_argument('--history',type=Path,default=EXP/'data_tradable/history_manifest.json')
    parser.add_argument('--results',type=Path,default=EXP/'results')
    parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args()
    if args.phase=='prepare':
        prepare(args.history,args.results,args.workers)
    else:
        evaluate(args.results)


if __name__=='__main__':
    main()

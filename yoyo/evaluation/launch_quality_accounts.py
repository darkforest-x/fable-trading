"""Evaluate preregistered 1H launch filters without changing entry or exit rules.

Inputs are the source-verified, cutoff-specific focus/md event ledgers produced
by launch_quality_dataset. A filter reads only closed decision-bar volume/TR.
Matched controls are linked by original event identity, never filtered by their
own score. All accounts use the existing causal cashbook. Removing USELESS
removes its whole asset on every venue before cash is reallocated from scratch.
Random thinning is an offline same-asset/week null, not an online scheduler.
No API, monitor, model, notification, fitting or execution code is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.altseason_paired_portfolio import matched_sets
from yoyo.evaluation.altseason_portfolio import run_portfolio, summarize_portfolio
from yoyo.evaluation.altseason_research import read_events, scope_rows, rank_auc
from yoyo.evaluation.launch_quality_statistics import (
    filter_variants, random_thinning, paired_event_statistics, holm_adjust,
)

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-launch-quality-20260910-v1'
OLD = ROOT / 'experiments/active/exp-altseason-multivenue-20260910-v1'
PERIODS = {
    'earlier': (pd.Timestamp('2026-05-15T00:00Z'), pd.Timestamp('2026-07-10T00:00Z')),
    'known': (pd.Timestamp('2026-07-10T00:00Z'), pd.Timestamp('2026-09-09T00:00Z')),
}
SCOPES = ('okx', 'binance', 'gate', 'combined')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path, frame):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0} if path.name.endswith('.gz') else None)


def without_asset(events, asset='USELESS'):
    """Delete all venue appearances before allocation, including losing events."""
    return events.loc[~events.asset.eq(asset)].copy()


def load_prices(events, controls, source_hashes):
    """Use verified feature files solely as the open/close mark source."""
    pairs = pd.concat([events, controls], ignore_index=True)[['instrument', 'features_path']].drop_duplicates()
    if pairs.instrument.duplicated().any():
        raise ValueError('One instrument points to multiple feature sources')
    prices = {}
    for row in pairs.itertuples(index=False):
        path=str(Path(row.features_path).resolve())
        if path not in source_hashes or sha(path)!=source_hashes[path]:
            raise ValueError('Unverified price feature source: '+path)
        frame = pd.read_pickle(row.features_path)
        prices[row.instrument] = frame[['open', 'close']].copy()
    return prices


def evaluate_account(events, prices, start, end):
    """Return the real cash-reallocation result and its boundary-mark caveat."""
    curve, ledger = run_portfolio(events, prices, start, end, 60)
    summary = summarize_portfolio(curve, ledger)
    filled = ledger.loc[ledger.portfolio_selected.eq(True)] if len(ledger) else ledger
    natural = filled.loc[filled.natural_exit.eq(True)] if len(filled) else filled
    summary.update(candidates=len(events), valid_candidates=int(events.valid.eq(True).sum()),
        initial_stop_rate=float(filled.exit_reason.isin(['initial_stop', 'initial_stop_gap']).mean()) if len(filled) else np.nan,
        natural_win_rate=float(natural.net_return.gt(0).mean()) if len(natural) else np.nan,
        natural_big_winners=int(natural.net_return.ge(.5).sum()) if len(natural) else 0,
        extra_exit_notional_fee_pct=float((filled.quantity*(filled.exit_price-filled.entry_price)*.001).sum()/100000*100) if len(filled) else 0,
        stress40_static_pct=summary['return_pct']-float(filled.notional.sum()*.002/100000*100) if len(filled) else summary['return_pct'],
        stress60_static_pct=summary['return_pct']-float(filled.notional.sum()*.004/100000*100) if len(filled) else summary['return_pct'])
    summary['actual_turnover_fee_static_pct']=summary['return_pct']-summary['extra_exit_notional_fee_pct']
    return summary, curve, ledger


def candidate_summary(actual, controls):
    """Natural exits and censored marks stay distinct; matched means are paired."""
    full, paired, random = matched_sets(actual, controls)
    indexed = controls.loc[controls.valid.eq(True)].groupby('matched_event_id').net_bp.mean()
    valid = full.copy()
    valid['control_mean_net_bp'] = valid.event_id.map(indexed)
    valid['excess_bp'] = valid.net_bp-valid.control_mean_net_bp
    stats = paired_event_statistics(valid)
    natural = valid.loc[valid.natural_exit.eq(True)]
    row = dict(candidates=len(actual), valid=len(valid), paired=len(paired),
        paired0_fraction=len(paired)/len(valid) if len(valid) else np.nan,
        mean_gross_bp=float(valid.gross_bp.mean()), mean_net_bp=float(valid.net_bp.mean()),
        matched_actual_mean_bp=float(valid.loc[valid.excess_bp.notna(), 'net_bp'].mean()),
        matched_random_mean_bp=float(valid.control_mean_net_bp.mean()),
        mean_excess_bp=float(valid.excess_bp.mean()), win_rate=float(valid.net_bp.gt(0).mean()),
        initial_stop_rate=float(valid.exit_reason.isin(['initial_stop', 'initial_stop_gap']).mean()),
        natural_exits=len(natural), censored=int(valid.censored.sum()),
        natural_win_rate=float(natural.net_bp.gt(0).mean()),
        natural_big_winners=int(natural.net_return.ge(.5).sum()))
    row.update(stats)
    row['any_control_fraction']=row.pop('matched_fraction')
    return row


def score_diagnostics(events, controls):
    """Descriptive ranks use future labels only for scoring, never for entry."""
    valid = events.loc[events.valid.eq(True)].copy()
    mean_control = controls.loc[controls.valid.eq(True)].groupby('matched_event_id').net_bp.mean()
    valid['control_mean_net_bp'] = valid.event_id.map(mean_control)
    rows = []
    for feature in ('relative_volume', 'tr_expansion'):
        g = valid.loc[np.isfinite(pd.to_numeric(valid[feature], errors='coerce'))].copy()
        if g.empty:
            continue
        count=max(1,int(np.ceil(len(g)*.1)))
        top=g.sort_values([feature,'event_id'],ascending=[False,True]).head(count)
        threshold=float(top[feature].min())
        rows.append(dict(feature=feature, auc_positive_net=rank_auc(g[feature], g.net_bp.gt(0)),
            events=len(g), top_decile_threshold=threshold, top_decile_n=len(top),
            top_decile_gross_bp=float(top.gross_bp.mean()), top_decile_net_bp=float(top.net_bp.mean()),
            top_decile_matched_actual_bp=float(top.loc[top.control_mean_net_bp.notna(), 'net_bp'].mean()),
            top_decile_random_bp=float(top.control_mean_net_bp.mean()),
            top_decile_win_rate=float(top.net_bp.gt(0).mean()),
            status='retrospective score diagnostic; no train/validation model or deployed decile'))
    return rows


def run(folder=EXPERIMENT/'results'):
    folder = Path(folder)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    sources = [Path(__file__), ROOT/'yoyo/evaluation/launch_quality_statistics.py',
        ROOT/'yoyo/evaluation/altseason_portfolio.py', ROOT/'yoyo/evaluation/altseason_paired_portfolio.py',
        ROOT/'yoyo/evaluation/altseason_research.py']
    for source in sources:
        committed = subprocess.check_output(['git', 'show', 'HEAD:'+str(source.relative_to(ROOT))], cwd=ROOT)
        if committed != source.read_bytes():
            raise ValueError('Commit builder before evaluation: '+str(source))
    inputs = json.loads((folder/'dataset_manifest.json').read_text())
    for item in inputs['artifacts']:
        if sha(item['path']) != item['sha256']:
            raise ValueError('Dataset artifact changed: '+item['path'])
    manifest_path = folder/'accounts_manifest.json'
    if manifest_path.exists() or (folder/'accounts_started.json').exists() or (folder/'accounts').exists():
        raise ValueError('Frozen result exists; use a new explicitly recorded verification directory')
    receipt = dict(code_commit=head, generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        holdout_consumption=1, optimization=False, live_changes=False,
        input_manifest_sha256=sha(folder/'dataset_manifest.json'),
        feature_sources=inputs['feature_sources'],
        builder_sha256={str(s.relative_to(ROOT)):sha(s) for s in sources}, status='running')
    (folder/'accounts_started.json').write_text(json.dumps(receipt, indent=2)+'\n')
    account_rows, event_rows, score_rows, thinning_rows, thinning_group_rows = [], [], [], [], []
    evidence = []
    parity = {}
    for period, (start, end) in PERIODS.items():
        events = read_events(folder/(period+'_events.csv.gz'))
        controls = read_events(folder/(period+'_controls.csv.gz'))
        if len(events) and (not events.arm.eq('focus_md').all() or not events.minutes.eq(60).all()):
            raise ValueError('Unexpected original entry or exit rule')
        prices = load_prices(events, controls, {str(Path(a['path']).resolve()):a['sha256'] for a in inputs['feature_sources']})
        variants = filter_variants(events)
        # Freeze thinning identities before inspecting any outcome summaries.
        schedules = {}
        base = scope_rows(variants['baseline'], 'combined')
        for variant in ('volume4', 'expansion3'):
            filtered = scope_rows(variants[variant], 'combined')
            schedules[variant] = random_thinning(base, filtered)
            schedule = schedules[variant]
            ids = {str(k):v for k,v in schedule['selections'].items()}
            destination = folder/(period+'_'+variant+'_thinning_ids.json')
            destination.write_text(json.dumps(dict(selections=ids, summary=schedule['summary']), indent=2)+'\n')
            evidence.append(destination)
            groups = schedule['groups'].copy(); groups['period']=period; groups['variant']=variant
            thinning_group_rows.append(groups)
        for scope in SCOPES:
            for variant, pool in variants.items():
                actual = scope_rows(pool, scope)
                random_pool = scope_rows(controls, scope)
                random_pool = random_pool.loc[random_pool.matched_event_id.isin(actual.event_id)]
                event_rows.append(dict(period=period, scope=scope, variant=variant, **candidate_summary(actual, random_pool)))
                if variant == 'baseline':
                    score_rows.extend(dict(period=period, scope=scope, **r) for r in score_diagnostics(actual, random_pool))
                for population in ('all_assets', 'without_useless'):
                    a = actual if population=='all_assets' else without_asset(actual)
                    c = random_pool if population=='all_assets' else without_asset(random_pool)
                    _, paired, random = matched_sets(a, c)
                    for account, frame in (('full', a), ('paired_actual', paired), ('paired_random', random)):
                        summary, curve, selected = evaluate_account(frame, prices, start, end)
                        context = dict(period=period, scope=scope, variant=variant, population=population, account=account)
                        account_rows.append(dict(context, **summary))
                        name='_'.join(context.values())
                        for suffix, table in (('curve', curve), ('ledger', selected)):
                            path=folder/'accounts'/(name+'_'+suffix+'.csv.gz');write_csv(path, table);evidence.append(path)
                        if period=='known' and variant=='baseline' and population=='all_assets' and account=='full':
                            old=pd.read_csv(OLD/'SUMMARY.csv')
                            ref=old.loc[old.scope.eq(scope)&old.minutes.eq(60)&old.arm.eq('focus_md')&old.period.eq('full')].iloc[0]
                            parity[scope]={key:float(summary[key])-float(ref[key]) for key in ('return_pct','max_drawdown_pct','trades')}
                            if any(abs(v)>1e-7 for v in parity[scope].values()):raise ValueError('Known baseline parity failed: '+scope)
                print(period, scope, variant, 'account checks complete', flush=True)
        for variant, schedule in schedules.items():
            for seed, ids in schedule['selections'].items():
                sample=base.loc[base.event_id.isin(ids)].copy()
                summary, curve, selected=evaluate_account(sample, prices, start, end)
                thinning_rows.append(dict(period=period, variant=variant, seed=int(seed), **summary))
                path=folder/'thinning'/(period+'_'+variant+'_'+str(seed)+'.csv.gz');write_csv(path,curve);evidence.append(path)
            print(period,variant,'19 random thinning accounts complete',flush=True)
    e=pd.DataFrame(event_rows)
    pcol=next((x for x in ('permutation_p','p_value','p') if x in e),None)
    if pcol is None:raise ValueError('Missing paired permutation statistic')
    e['holm_p']=holm_adjust(e[pcol].to_numpy(float))
    for name, table in [('accounts_summary.csv',pd.DataFrame(account_rows)),('event_summary.csv',e),
        ('score_summary.csv',pd.DataFrame(score_rows)),('thinning_summary.csv',pd.DataFrame(thinning_rows)),
        ('thinning_groups.csv',pd.concat(thinning_group_rows,ignore_index=True))]:
        path=folder/name;write_csv(path,table);evidence.append(path)
    receipt.update(status='complete', completed_at=pd.Timestamp.now(tz='UTC').isoformat(), known_baseline_parity=parity,
        accounts=len(account_rows), thinning_accounts=len(thinning_rows),
        costs='fixed 20bp of entry notional; full funding/impact unknown; stress attribution does not reallocate cash',
        artifacts=[dict(path=str(p.resolve()),sha256=sha(p),size_bytes=p.stat().st_size) for p in evidence])
    manifest_path.write_text(json.dumps(receipt,indent=2,default=str)+'\n')
    print(json.dumps(dict(accounts=len(account_rows),thinning_accounts=len(thinning_rows),parity=parity)),flush=True)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--results',type=Path,default=EXPERIMENT/'results')
    run(parser.parse_args().results)

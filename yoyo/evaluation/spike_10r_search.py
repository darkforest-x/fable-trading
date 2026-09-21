"""Finite, time-separated discovery of high-10R-precision admission rules.

Source: authenticated all-original-V9-long candidate paths, unchanged execution.
Twenty signal-close features each admit low/high 10/20/40 percent tails using
only the previous three complete UTC months per timeframe (>=100 observations).
The 120 single terms and 6,840 distinct-feature two-term conjunctions are a
predeclared offline rule experiment, not ML training. Selection reads only
closed paths whose entry AND exit precede the fixed split. Later outcomes are
consumed only in a separate evaluation invocation after selection is persisted.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_six_filter_statistics import strict_bool
from yoyo.evaluation.spike_high_r_entry_report import auc_score, block_test
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-10r-discovery-20260921-v3')
FEATURES = (
    'reference_risk_fraction', 'atr_fraction', 'risk_atr', 'volume_ratio',
    'expansion', 'body_fraction', 'close_location', 'rope_width_atr',
    'past_width_atr', 'rope_distance_atr', 'breakout20_atr', 'momentum20_atr',
    'momentum60_atr', 'trend_slope20_atr', 'trend_alignment', 'atr_relative100',
    'volatility_ratio20_100', 'efficiency20', 'prior_range20_atr', 'volume_trend20',
)
QUANTILES = (.1, .2, .4)
SEED = 921310
SPLIT = pd.Timestamp('2025-09-10T00:00:00Z')
START = pd.Timestamp('2024-09-10T00:00:00Z')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str, allow_nan=False)+'\n')


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def load_candidates(folder):
    """Read only aggregate files certified by the dataset receipt."""
    receipt = json.loads((folder/'receipt.json').read_text())
    for name in ('candidates.csv.gz', 'controls.csv.gz'):
        expected = receipt['files'][name]
        if isinstance(expected, dict):
            expected = expected['sha256']
        if digest(folder/name) != expected:
            raise ValueError(f'dataset aggregate hash drift: {name}')
    c, control = (pd.read_csv(folder/name) for name in ('candidates.csv.gz','controls.csv.gz'))
    if c.event_key.duplicated().any() or control.event_key.duplicated().any():
        raise ValueError('duplicate candidate or control')
    for frame in (c, control):
        for name in ('available_at','entry_time','exit_time','signal_bar_open',
                     'control_entry_time','control_exit_time','control_signal_time'):
            if name in frame:
                frame[name] = pd.to_datetime(frame[name], utc=True)
        for name in ('valid_entry','censored','matched','control_censored','target_censored'):
            if name in frame:
                frame[name] = strict_bool(frame[name])
    if len(c) != 49207 or c.stream_key.nunique() > 3531:
        raise ValueError('all-original-long universe mismatch')
    closed = c.valid_entry & ~c.censored
    if not np.isfinite(c.loc[closed, ['net_r','net_return','gross_return']].to_numpy(float)).all():
        raise ValueError('nonfinite closed outcome')
    return c, control, receipt


def terms():
    return [dict(id=f'{f}__{direction}{int(q*100)}', feature=f, direction=direction, q=q)
            for f in FEATURES for direction in ('low','high') for q in QUANTILES]


def calibrate(c):
    """Unlabelled feature thresholds: [month-3 months,month), grouped by TF.

    Uses only available_at, timeframe_min and the twenty documented causal
    features. Quantile comparison includes equality. Unknowns always reject.
    Percentile scores use historical mid-ranks and are diagnostic only.
    """
    ts = pd.to_datetime(c.available_at, utc=True)
    months = ts.dt.strftime('%Y-%m')
    matrix = np.zeros((len(c), len(terms())), dtype=bool)
    ranks = np.full((len(c),len(FEATURES)), np.nan)
    rows = []
    for month in sorted(months.unique()):
        end = pd.Timestamp(month+'-01', tz='UTC')
        start = end-pd.DateOffset(months=3)
        for tf in (30,60,240):
            current = np.flatnonzero(months.eq(month) & c.timeframe_min.eq(tf))
            past = ts.ge(start) & ts.lt(end) & c.timeframe_min.eq(tf)
            for j, feature in enumerate(FEATURES):
                a = c.loc[past, feature].to_numpy(float)
                a = np.sort(a[np.isfinite(a)])
                known = start >= START and len(a) >= 100
                values = c.iloc[current][feature].to_numpy(float)
                if known:
                    ranks[current,j] = (np.searchsorted(a,values,'left')+np.searchsorted(a,values,'right'))/(2*len(a))
                    ranks[current[~np.isfinite(values)],j] = np.nan
                for d, direction in enumerate(('low','high')):
                    for k,q in enumerate(QUANTILES):
                        cutoff = float(np.quantile(a,q if direction=='low' else 1-q)) if known else np.nan
                        col = j*6+d*3+k
                        if known:
                            matrix[current,col] = np.isfinite(values) & ((values<=cutoff) if direction=='low' else (values>=cutoff))
                        rows.append(dict(month=month,timeframe_min=tf,feature=feature,direction=direction,q=q,
                            history_start=start,history_end=end,n_history=len(a),known=known,cutoff=cutoff))
    return matrix,ranks,pd.DataFrame(rows)


def rule_specs():
    t = terms()
    singles = [(i,) for i in range(len(t))]
    pairs = [(i,j) for i in range(len(t)) for j in range(i+1,len(t)) if t[i]['feature'] != t[j]['feature']]
    return [(tuple(ids),' & '.join(t[i]['id'] for i in ids)) for ids in singles+pairs]


def mask_for(matrix, ids):
    return matrix[:,ids[0]] if len(ids)==1 else matrix[:,ids[0]] & matrix[:,ids[1]]


def wilson_lower(hits, n):
    if n <= 0:
        return 0.
    z = 1.959963984540054
    p = hits/n
    return (p+z*z/(2*n)-z*np.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)


def early_mask(c):
    return c.valid_entry & ~c.censored & c.entry_time.lt(SPLIT) & c.exit_time.lt(SPLIT)


def search_earlier(c, matrix, cfg):
    """Search earlier labels only; no future or unresolved labels enter choice."""
    early = early_mask(c).to_numpy()
    e = c.loc[early]
    m = matrix[early]
    label = e.net_r.to_numpy(float)>10
    net = e.net_return.to_numpy(float)
    asset = e.asset.astype(str).to_numpy()
    month = e.entry_time.dt.strftime('%Y-%m').to_numpy()
    asset_month = np.char.add(np.char.add(asset.astype(str),'|'),month.astype(str))
    rows = []
    for ids, rid in rule_specs():
        take = mask_for(m,ids)
        n,h = int(take.sum()),int((take & label).sum())
        precision = h/n if n else 0.
        recall = h/label.sum() if label.sum() else 0.
        sufficient = n>=cfg['selection_min_closed'] and h>=cfg['selection_min_gt10'] and recall>=cfg['selection_min_gt10_recall']
        am = len(np.unique(asset_month[take & label])) if sufficient else 0
        assets = len(np.unique(asset[take])) if sufficient else 0
        months = len(np.unique(month[take])) if sufficient else 0
        eligible = sufficient and am>=cfg['selection_min_positive_asset_months'] and assets>=cfg['selection_min_assets'] and months>=cfg['selection_min_active_months']
        rows.append(dict(rule=rid,terms=list(ids),term_count=len(ids),closed=n,gt10=h,precision=precision,
                         coverage=n/len(e),recall=recall,wilson_lower=wilson_lower(h,n),
                         mean_net_bp=float(net[take].mean()*1e4) if n else np.nan,
                         active_months=months,assets=assets,positive_asset_months=am,eligible=eligible))
    result = pd.DataFrame(rows).sort_values(['wilson_lower','precision','gt10','rule'],ascending=[False,False,False,True])
    selected = []
    for coverage in cfg['coverage_floors']:
        eligible = result.loc[result.eligible & result.coverage.ge(coverage)]
        selected.append(dict(name=f'coverage_{int(coverage*100)}pct',coverage_floor=coverage,
                             choice=clean(eligible.iloc[0].to_dict()) if len(eligible) else None))
    singles = result.loc[result.eligible & result.term_count.eq(1)]
    selected.append(dict(name='best_single',coverage_floor=None,
                         choice=clean(singles.iloc[0].to_dict()) if len(singles) else None))
    return result,selected


def validate_selection(c, matrix, cfg, selection):
    """Recompute only earlier choices so edited selected terms cannot pass."""
    board, choices = search_earlier(c, matrix, cfg)
    if (selection['selected'] != clean(choices) or selection['rule_count'] != len(board)
            or selection['earlier_closed'] != int(early_mask(c).sum())
            or selection['earlier_gt10'] != int(c.loc[early_mask(c)].net_r.gt(10).sum())
            or selection.get('selection_uses_later_labels') is not False):
        raise ValueError('selected rules differ from canonical earlier-only selection')


def control_statistics(part, controls, period):
    """Same-stream/month/ATR-bucket random timing; censored pairs never redraw."""
    closed = part.loc[part.valid_entry & ~part.censored]
    pair = closed[['event_key','entry_time','net_r','net_return']].merge(controls,on='event_key',how='left',validate='one_to_one',suffixes=('','_controlmeta'))
    if len(pair) and not np.allclose(pair.net_r,pair.target_net_r,equal_nan=True):
        raise ValueError('missing control or target drift')
    ok = pair.matched.fillna(False) & ~pair.control_censored.fillna(True)
    if period=='earlier':
        ok &= pair.control_entry_time.lt(SPLIT) & pair.control_exit_time.lt(SPLIT)
    elif period=='later':
        ok &= pair.control_entry_time.ge(SPLIT)
    p = pair.loc[ok].copy()
    p['r_delta'] = p.net_r-p.control_net_r
    p['tail_delta'] = p.net_r.gt(10).astype(int)-p.control_net_r.gt(10).astype(int)
    month = p.entry_time.dt.strftime('%Y-%m')
    rtest = block_test(p.groupby(month).r_delta.sum())
    ttest = block_test(p.groupby(month).tail_delta.sum())
    return dict(matched_pairs=len(p),unmatched_closed=len(pair)-len(p),
        random_gt10=int(p.control_net_r.gt(10).sum()),paired_gt10=int(p.net_r.gt(10).sum()),
        random_precision=float(p.control_net_r.gt(10).mean()),
        random_mean_net_bp=float(p.control_net_return.mean()*1e4),
        paired_excess_r=float(p.r_delta.mean()),paired_excess_net_bp=float((p.net_return-p.control_net_return).mean()*1e4),
        random_tail_p=ttest['p'],random_net_p=rtest['p'])


def metrics(part, universe):
    closed = part.loc[part.valid_entry & ~part.censored]
    all_closed = universe.loc[universe.valid_entry & ~universe.censored]
    r = closed.net_r
    h, base_h = int(r.gt(10).sum()),int(all_closed.net_r.gt(10).sum())
    return dict(candidates=len(part),invalid=int((~part.valid_entry).sum()),censored=int((part.valid_entry & part.censored).sum()),
        closed=len(closed),gt10=h,precision=float(r.gt(10).mean()),
        confirmed_gt10_per_candidate=h/len(part) if len(part) else np.nan,
        candidate_coverage=len(part)/len(universe) if len(universe) else np.nan,coverage=len(closed)/len(all_closed) if len(all_closed) else np.nan,
        recall=h/base_h if base_h else np.nan,win_rate=float(r.gt(0).mean()),
        mean_r=float(r.mean()),mean_gross_bp=float(closed.gross_return.mean()*1e4),mean_net_bp=float(closed.net_return.mean()*1e4),
        assets=closed.asset.nunique(),positive_asset_months=closed.loc[r.gt(10)].assign(month=closed.entry_time.dt.strftime('%Y-%m')).groupby(['asset','month']).ngroups)


def rate_interval(universe, selected):
    """Pair-month bootstrap of ratio differences, retaining empty selected months."""
    u = universe.loc[universe.valid_entry & ~universe.censored].copy()
    s = selected.loc[selected.valid_entry & ~selected.censored].copy()
    for frame in (u,s):
        frame['month'] = frame.entry_time.dt.strftime('%Y-%m')
        frame['tail'] = frame.net_r.gt(10).astype(int)
    months = sorted(u.month.unique())
    if not months or not len(s):
        return dict(precision_delta=np.nan,precision_ci_low=np.nan,precision_ci_high=np.nan)
    count = []
    for frame in (u,s):
        count.append(frame.groupby('month').agg(n=('tail','size'),h=('tail','sum')).reindex(months,fill_value=0).to_numpy(float))
    a = np.array(count)
    rng = np.random.default_rng(SEED)
    draws = a[:,rng.integers(len(months),size=(4000,len(months))),:].sum(axis=2)
    valid = (draws[:,:,0]>0).all(axis=0)
    difference = draws[1,valid,1]/draws[1,valid,0]-draws[0,valid,1]/draws[0,valid,0]
    lo,hi = np.quantile(difference,[.025,.975])
    return dict(precision_delta=float(s['tail'].mean()-u['tail'].mean()),precision_ci_low=float(lo),precision_ci_high=float(hi))


def holm(values):
    a = np.array([float(x) if x is not None and np.isfinite(x) else 1. for x in values])
    order = np.argsort(a,kind='stable')
    result = np.empty(len(a))
    result[order] = np.minimum(1.,np.maximum.accumulate(a[order]*np.arange(len(a),0,-1)))
    return result


def periods(c):
    yield 'full',np.ones(len(c),bool)
    yield 'earlier',(c.available_at.lt(SPLIT) & c.exit_time.lt(SPLIT)).to_numpy()
    yield 'later',c.available_at.ge(SPLIT).to_numpy()
    yield 'crossing_or_early_unresolved',(c.available_at.lt(SPLIT) & ~c.exit_time.lt(SPLIT)).to_numpy()


def rule_score(ranks,ids):
    scores=[]
    for i in ids:
        term=terms()[i]
        r=ranks[:,FEATURES.index(term['feature'])]
        scores.append(1-r if term['direction']=='low' else r)
    return np.minimum.reduce(scores)


def evaluate(c, controls, matrix, ranks, selection):
    chosen = {x['choice']['rule']:tuple(x['choice']['terms']) for x in selection['selected'] if x['choice']}
    masks = {'original_all':np.ones(len(c),bool),'prior_v2_breakout':c.breakout20_atr.gt(0).to_numpy(),
             'prior_v21_risk_decile':matrix[:,0]}
    masks.update({name:mask_for(matrix,ids) for name,ids in chosen.items()})
    rows=[]
    for period, clock in periods(c):
        u=c.loc[clock]
        for name, mask in masks.items():
            s=c.loc[clock & mask]
            rows.append(dict(period=period,rule=name,**metrics(s,u),**control_statistics(s,controls,period),**rate_interval(u,s)))
    table=pd.DataFrame(rows)
    later=(table.period.eq('later') & table.rule.isin(chosen))
    for what in ('tail','net'):
        table.loc[later,'random_'+what+'_holm_p']=holm(table.loc[later,'random_'+what+'_p'])
    table.loc[later,'historical_gate_passed']=(table.loc[later,'precision_ci_low'].gt(0) &
        table.loc[later,'random_tail_holm_p'].lt(.01) & table.loc[later,'random_net_holm_p'].lt(.01) &
        table.loc[later,'mean_net_bp'].gt(0) & table.loc[later,'recall'].ge(.1))
    diagnostics=[]
    closed=c.valid_entry & ~c.censored
    score_list=[(f'{feature}_{direction}', 1-ranks[:,j] if direction=='low' else ranks[:,j])
                for j,feature in enumerate(FEATURES) for direction in ('low','high')]
    score_list += [(name,rule_score(ranks,ids)) for name,ids in chosen.items()]
    for period,clock in periods(c):
        if period not in ('earlier','later'):
            continue
        for name,score in score_list:
            valid=clock & closed.to_numpy() & np.isfinite(score)
            indices=np.flatnonzero(valid)
            order=indices[np.argsort(-score[indices],kind='stable')]
            top=c.iloc[order[:int(np.ceil(len(order)*.1))]]
            diagnostics.append(dict(period=period,score=name,scored=len(indices),auc_gt10=auc_score(c.loc[valid].net_r.gt(10),score[valid]),
                **metrics(top,c.loc[clock]),**control_statistics(top,controls,period)))
    # All retained/missed positives remain auditable, including original WIF.
    kept=c[['event_key','stream_key','asset','timeframe_min','available_at','valid_entry','censored','net_r']].copy()
    for name,mask in masks.items():
        kept[name]=mask
    return table,pd.DataFrame(diagnostics),kept,masks


def check_committed():
    paths=[Path(__file__),Path('tests/evaluation/test_spike_10r_search.py'),EXP/'config.json',EXP/'PROJECT_PLAN.md',
           Path('yoyo/evaluation/spike_high_r_entry_report.py'),Path('yoyo/evaluation/spike_six_filter_statistics.py')]
    if not _committed(paths):
        raise ValueError('commit search, tests, config and plan before market analysis')
    return {str(p):digest(p) for p in paths}


def run(dataset,output,phase):
    dependencies=check_committed()
    cfg=json.loads((EXP/'config.json').read_text())
    c,controls,_=load_candidates(dataset)
    matrix,ranks,thresholds=calibrate(c)
    if phase=='select':
        if output.exists():
            raise ValueError('refuse to overwrite search')
        output.mkdir(parents=True)
        leaderboard,selected=search_earlier(c,matrix,cfg)
        leaderboard.to_csv(output/'earlier_search.csv',index=False)
        thresholds.to_csv(output/'thresholds.csv',index=False)
        selection=dict(selected=selected,rule_count=len(leaderboard),feature_count=len(FEATURES),dependencies=dependencies,
            dataset=str(dataset),dataset_receipt_sha256=digest(dataset/'receipt.json'),
            search_code_sha256=digest(Path(__file__)),config_sha256=digest(EXP/'config.json'),
            thresholds_sha256=digest(output/'thresholds.csv'),
            source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            selected_at=pd.Timestamp.now(tz='UTC').isoformat(),earlier_closed=int(early_mask(c).sum()),
            earlier_gt10=int(c.loc[early_mask(c)].net_r.gt(10).sum()),
            selection_uses_later_labels=False)
        dump(output/'selection.json',clean(selection))
        print(json.dumps(clean(dict(selected=selected,rules=len(leaderboard))),ensure_ascii=False))
    elif phase=='evaluate':
        selection=json.loads((output/'selection.json').read_text())
        if (selection['dependencies']!=dependencies or selection['dataset_receipt_sha256']!=digest(dataset/'receipt.json') or selection['search_code_sha256']!=digest(Path(__file__))
                or selection['config_sha256']!=digest(EXP/'config.json') or selection['thresholds_sha256']!=digest(output/'thresholds.csv')):
            raise ValueError('frozen selection identity drift')
        validate_selection(c,matrix,cfg,selection)
        if (output/'evaluation_receipt.json').exists():
            raise ValueError('refuse to overwrite evaluation')
        table,diagnostics,kept,masks=evaluate(c,controls,matrix,ranks,selection)
        table.to_csv(output/'comparison.csv',index=False)
        diagnostics.to_csv(output/'score_diagnostics.csv',index=False)
        kept.to_csv(output/'decisions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        rows=[]
        for name,mask in masks.items():
            s=c.loc[mask & c.valid_entry.to_numpy() & ~c.censored.to_numpy()].copy()
            s['month']=s.entry_time.dt.strftime('%Y-%m')
            for dimensions in (['month'],['timeframe_min'],['venue'],['asset']):
                for key,part in s.groupby(dimensions):
                    rows.append(dict(rule=name,dimension=dimensions[0],value=str(key[0] if isinstance(key,tuple) else key),
                        **metrics(part,c),**control_statistics(part,controls,'full')))
        pd.DataFrame(rows).to_csv(output/'groups.csv',index=False)
        files={p.name:digest(p) for p in output.iterdir() if p.is_file()}
        dump(output/'evaluation_receipt.json',dict(selection_sha256=digest(output/'selection.json'),files=files,
            evaluated_at=pd.Timestamp.now(tz='UTC').isoformat(),warning='exposed-history temporal replication, not blind validation'))
        print(table.loc[table.period.eq('later')].to_string(index=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--phase',choices=['select','evaluate'],required=True)
    a=parser.parse_args()
    run(a.dataset,a.output,a.phase)

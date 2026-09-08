"""Frozen V3 independent launch and HTF comparisons; no live mutations.

Source: exp-imacd-launch-context-20260908-v3/PROJECT_PLAN.md. Formation
features use OHLCV through decision only (see imacd_startup_quality). The
matching volatility bucket uses ATR/close ranks over the preceding 240
observations including the closed decision, never future ranking. This file
separates outcome-blind selection from neutral-exit labels, calendar-block
inference and cash-funded portfolio accounting. It never imports execution,
reads credentials, sends notifications, trains or changes monitor settings.
"""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from .imacd_startup_quality import build_features
from .imacd_formation_memory import add_formation_memory
from .imacd_launch_context import add_context, HIGHER
from .imacd_startup_accounting import outcome_arrays, compound_portfolio

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-imacd-launch-context-20260908-v3'
DATA = ROOT / 'data/imacd_launch_context_20260908_v3'
END = pd.Timestamp('2026-01-01', tz='UTC')
FOLDS = [('development', '2023-01-01', '2025-01-01'),
         ('validation', '2025-01-01', '2026-01-01')]
POLICIES = ['P00', 'S01', 'H00', 'H01']
PERIODS = [15, 60, 240]
SEED = 20260908
SCORES = ['strength_score', 'breakout_score', 'htf_score']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(raw, minutes):
    """Aggregate only complete UTC-aligned 15m groups; never manufacture bars."""
    step = pd.Timedelta(minutes=15)
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError('duplicate/nonmonotonic source')
    if not raw.index.equals(raw.index.floor('15min')):
        raise ValueError('misaligned source')
    if len(raw) > 1 and not raw.index.to_series().diff().iloc[1:].eq(step).all():
        raise ValueError('source gap: explicit separate segments required')
    grouped = raw.groupby(raw.index.floor(f'{minutes}min'))
    b = grouped.agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    return b.loc[grouped.size().eq(minutes // 15)]


def policy_masks(f):
    """Two separate candidate gates; H00 isolates higher-data availability."""
    base=f.release_side.ne(0)
    return {'P00':base,'S01':base & f.box_breakout,
            'H00':base & f.htf_known,'H01':base & f.htf_same_direction}


def read_prefix(path, end=END):
    """Parse OHLCV strictly before end; inspect only cutoff timestamp after it.

    Source files are ascending, ts-first CSV. The first excluded row's price
    fields are never parsed. This does not read a full CSV then filter away
    post-cutoff prices. Byte hashing is separate source-identity metadata.
    """
    cutoff = int(end.timestamp()*1000)
    rows = []
    with path.open() as handle:
        header = handle.readline()
        if not header.startswith('ts,'):
            raise ValueError('expected ts-first CSV')
        for line in handle:
            timestamp = int(line.partition(',')[0])
            if timestamp >= cutoff:
                break
            rows.append(line)
    if not rows:
        raise ValueError('no pre-cutoff source rows')
    raw = pd.read_csv(io.StringIO(header + ''.join(rows)),
                      usecols=['ts', 'open', 'high', 'low', 'close', 'volume'])
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop('ts'), unit='ms', utc=True))
    if not raw.index.is_monotonic_increasing or raw.index.has_duplicates:
        raise ValueError('nonmonotonic source prefix')
    if not (raw.index + pd.Timedelta(minutes=15) <= end).all():
        raise ValueError('partial bar straddles cutoff')
    return raw


def matched_indexes(f, indexes, seed):
    """Same symbol/TF call, month, causal volatility quintile and md sign.

    All focus releases are removed from the control pool. A control is used
    at most once in this call, without reading any post-decision observations.
    """
    month = f.index.strftime('%Y-%m').to_numpy()
    zone = np.sign(f.md.to_numpy()).astype(int)
    buckets = f.volbin.fillna(-1).to_numpy(int)
    release = f.release_side.to_numpy(int)
    pools = {}
    for i in indexes:
        if not release[i]:
            pools.setdefault((month[i], buckets[i], zone[i]), []).append(int(i))
    rng = np.random.default_rng(seed)
    for v in pools.values():
        rng.shuffle(v)
    result = {}
    for i in indexes:
        if release[i]:
            p = pools.get((month[i], buckets[i], zone[i]), [])
            result[int(i)] = [p.pop() for _ in range(min(3, len(p)))]
    return result


def inference(values, months):
    """Calendar-month clustered sign randomization plus bootstrap, not an RCT.

    All coins in one month share a block. Few months limit p resolution and
    are explicitly reported. The null assumes exchangeable signs of monthly
    aggregate matched excess; it does not establish causal market prediction.
    """
    a = pd.DataFrame({'v': values, 'month': months}).dropna()
    if a.empty:
        return dict(excess_bp=None, p=None, ci_low=None, ci_high=None, months=0)
    g = a.groupby('month').v.agg(['sum', 'count'])
    s, n = g['sum'].to_numpy(float), g['count'].to_numpy(float)
    k = len(s)
    rng = np.random.default_rng(SEED)
    ix = rng.integers(0, k, size=(9999, k))
    boot = s[ix].sum(axis=1) / n[ix].sum(axis=1)
    if k <= 16:
        signs = np.asarray(list(itertools.product([-1, 1], repeat=k)))
        p = np.mean((signs * s).sum(axis=1) >= s.sum() - 1e-10)
        resolution = 1 / 2 ** k
    else:
        signs = rng.choice([-1, 1], size=(9999, k))
        p = (1 + np.sum((signs * s).sum(axis=1) >= s.sum() - 1e-10)) / 10000
        resolution = .0001
    return dict(excess_bp=float(a.v.mean()), p=float(p), months=k,
                p_resolution=resolution, ci_low=float(np.quantile(boot, .025)),
                ci_high=float(np.quantile(boot, .975)))


def ranking(g, score):
    """Rank by an ex-ante scalar; outcomes are used exclusively for evaluation."""
    g = g.loc[np.isfinite(g[score])].copy()
    if g.empty:
        return dict(n=0, auc=None)
    y = g.net_bp.gt(0)
    n1, n0 = int(y.sum()), int((~y).sum())
    auc = ((g[score].rank().loc[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
           if n1 and n0 else None)
    top = g.sort_values([score, 'event_id'], ascending=[False, True]).head(max(1, int(np.ceil(len(g) / 10))))
    return dict(n=len(g), auc=auc, top_n=len(top), top_gross_bp=top.gross_bp.mean(),
                top_net_bp=top.net_bp.mean(), top_win_pct=top.net_bp.gt(0).mean() * 100,
                top_matched_n=int(top.excess_bp.notna().sum()),
                top_matched_case_net_bp=top.loc[top.excess_bp.notna(), 'net_bp'].mean(),
                top_control_net_bp=top.control_mean_net_bp.mean(),
                top_excess_bp=top.excess_bp.mean(),
                **{'top_'+k:v for k,v in inference(top.excess_bp, top.month).items() if k != 'excess_bp'})


def monthly_increment(candidate, baseline):
    """Close-stamped changes belong to the bar that ends at that stamp.

    In particular Jan 1 00:00 is the final December bar close, not an extra
    independent January block. Both curves must share the full calendar.
    """
    if not candidate.index.equals(baseline.index):
        raise ValueError('candidate and baseline calendars differ')
    delta = (candidate.diff().fillna(candidate.iloc[0]-1)
             - baseline.diff().fillna(baseline.iloc[0]-1))*10000
    return delta.groupby((delta.index-pd.Timedelta(nanoseconds=1)).strftime('%Y-%m')).sum()


def summarize(events, universe_count):
    """Report every policy, including empty outcomes, and deleted right tails."""
    summary, ranks, tails = [], [], []
    for (fold, minutes), g in events.groupby(['fold', 'minutes'], sort=False):
        tail = g.sort_values(['net_bp', 'event_id'], ascending=[False, True]).head(max(1, int(np.ceil(len(g) / 10))))
        tail_positive = tail.net_bp.clip(lower=0).sum()
        for p in POLICIES:
            q = g.loc[g[p]]
            matched = q.loc[q.excess_bp.notna()]
            kept_tail = tail.loc[tail[p]]
            row = dict(fold=fold, minutes=minutes, policy=p, baseline_n=len(g), n=len(q),
                       retained_pct=100 * len(q) / len(g), symbols=q.symbol.nunique(),
                       universe_symbols=universe_count, matched_n=len(matched),
                       matched_pct=100 * len(matched) / len(q) if len(q) else None,
                       mean_gross_bp=q.gross_bp.mean(), mean_net_bp=q.net_bp.mean(),
                       median_net_bp=q.net_bp.median(), win_pct=q.net_bp.gt(0).mean() * 100,
                       loss_count=int(q.net_bp.le(0).sum()),
                       loss_removed_pct=(1-q.net_bp.le(0).sum()/g.net_bp.le(0).sum())*100 if g.net_bp.le(0).any() else None,
                       winner_retained_pct=q.net_bp.gt(0).sum()/g.net_bp.gt(0).sum()*100 if g.net_bp.gt(0).any() else None,
                       control_net_bp=matched.control_mean_net_bp.mean(),
                       matched_case_net_bp=matched.net_bp.mean(),
                       boundary_marks=int(q.exit_kind.eq('boundary_mark').sum()),
                       tail_n=len(tail), tail_retained_n=len(kept_tail),
                       tail_count_pct=len(kept_tail) / len(tail) * 100,
                       tail_profit_pct=kept_tail.net_bp.clip(lower=0).sum() / tail_positive * 100 if tail_positive else None,
                       **inference(matched.excess_bp, matched.month))
            summary.append(row)
            for score in SCORES:
                ranks.append(dict(fold=fold, minutes=minutes, policy=p, score=score, **ranking(q, score)))
            for r in tail.to_dict('records'):
                tails.append(dict(event_id=r['event_id'], symbol=r['symbol'], fold=fold,
                                  minutes=minutes, policy=p, kept=bool(r[p]),
                                  net_bp=r['net_bp'], signal_i=r['signal_i'],
                                  near_zero_bars=r['near_zero_bars'],
                                  contraction_ratio=r['contraction_ratio'], proximity_atr=r['proximity_atr'],
                                  separation_delta=r['separation_delta'],
                                  formation_memory_ratio=r['formation_memory_ratio']))
    summary = pd.DataFrame(summary)
    ix = summary.loc[(summary.fold == 'validation') & summary.p.notna()].sort_values('p').index
    summary.loc[ix, 'p_holm_validation'] = np.maximum.accumulate(np.minimum(
        1, summary.loc[ix, 'p'].to_numpy() * (len(ix) - np.arange(len(ix)))))
    return summary, pd.DataFrame(ranks), pd.DataFrame(tails)



def daily_marks(full):
    """Thin display points without relabeling a close or losing initial NAV."""
    sampled = full.groupby(full.index.floor('D')).tail(1)
    combined = pd.concat([full.iloc[:1], sampled]).sort_index()
    return combined.loc[~combined.index.duplicated(keep='first')]


def run():
    if (EXP / 'results/manifest.json').exists() or (DATA / 'events.csv.gz').exists():
        raise RuntimeError('Do not overwrite prior experiment outcomes')
    paths = sorted((ROOT / 'data/kline_deep').glob('okx_*_USDT_SWAP_15m_*.csv'))
    if len(paths) != 54:
        raise RuntimeError(f'Preregistered universe changed: {len(paths)} files')
    symbols = [p.name.split('_USDT_SWAP_')[0].removeprefix('okx_') for p in paths]
    if len(set(symbols)) != 54:
        raise RuntimeError('Duplicate instruments')
    DATA.mkdir(parents=True, exist_ok=True)
    results = EXP / 'results'
    results.mkdir(exist_ok=True)
    builder = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    watched = [Path(__file__), ROOT/'yoyo/evaluation/imacd_startup_quality.py',
               ROOT/'yoyo/evaluation/imacd_startup_accounting.py',
               ROOT/'yoyo/evaluation/imacd_formation_memory.py',
               ROOT/'yoyo/evaluation/imacd_launch_context.py',
               ROOT/'yoyo/monitor/signals.py', EXP/'PROJECT_PLAN.md']
    for p in watched:
        frozen = subprocess.check_output(['git', 'show', f'HEAD:{p.relative_to(ROOT)}'], cwd=ROOT)
        if frozen != p.read_bytes():
            raise RuntimeError(f'Builder not frozen: {p}')
    (results/'started.json').write_text(json.dumps(dict(builder_commit=builder,
        started_at=pd.Timestamp.now(tz='UTC').isoformat(), policies=POLICIES,
        holdout_consumptions={p: 0 for p in POLICIES},
        authorization='Owner: 任何时间段数据都可以使用不要有任何限制；2026-09-08 你去做吧'), ensure_ascii=False, indent=2))
    events, controls, coverage, sources, portfolio_rows, accepted = [], [], [], [], [], []
    pool_equities = {}
    for fold, start, end in FOLDS:
        for minutes in PERIODS:
            clock = pd.date_range(start, end, freq=f'{minutes}min', tz='UTC', inclusive='right')
            for policy in POLICIES:
                pool_equities[fold, minutes, policy] = pd.Series(0., index=clock)
    for symbol_number, (symbol, path) in enumerate(zip(symbols, paths)):
        raw = read_prefix(path)
        sources.append(dict(symbol=symbol, path=str(path.relative_to(ROOT)), sha256=digest(path),
                            rows=len(raw), start=raw.index[0].isoformat(), end_close=(raw.index[-1]+pd.Timedelta(minutes=15)).isoformat()))
        for minutes in PERIODS:
            duration = pd.Timedelta(minutes=minutes)
            bars = aggregate(raw, minutes)
            f = add_formation_memory(build_features(bars))
            higher_bars = aggregate(raw, HIGHER[minutes])
            higher_features = build_features(higher_bars)
            f = add_context(bars, f, higher_bars, higher_features, minutes)
            f['volbin'] = np.ceil((f.atr/bars.close).rolling(240, min_periods=240).rank(pct=True)*5).clip(1, 5)
            masks = policy_masks(f)
            for fold_number, (fold, start, end) in enumerate(FOLDS):
                start, end = pd.Timestamp(start, tz='UTC'), pd.Timestamp(end, tz='UTC')
                in_fold = np.flatnonzero((bars.index >= start) & (bars.index + duration <= end))
                if not len(in_fold):
                    for policy in POLICIES:
                        pool_equities[fold, minutes, policy] += 1/len(symbols)
                    coverage.append(dict(symbol=symbol, minutes=minutes, fold=fold, bars=0, signals=0, status='not_listed_or_no_data'))
                    continue
                first, last = int(in_fold[0]), int(in_fold[-1])
                indexes = in_fold[(in_fold >= 340) & (in_fold < last) & f.volbin.iloc[in_fold].notna().to_numpy()]
                mapping = matched_indexes(f, indexes, SEED+symbol_number*100+minutes+fold_number)
                decisions = list(mapping)
                labels = outcome_arrays(bars, f, decisions, f.release_side.iloc[decisions].to_numpy(int), last)
                ci = [c for i in decisions for c in mapping[i]]
                cs = [int(f.release_side.iloc[i]) for i in decisions for c in mapping[i]]
                clabels = outcome_arrays(bars, f, ci, cs, last)
                cursor, fold_rows = 0, []
                for i, label in zip(decisions, labels):
                    eid = f'{symbol}_{minutes}_{int(bars.index[i].timestamp())}'
                    nctrl = len(mapping[i])
                    cr = clabels[cursor:cursor+nctrl]
                    cursor += nctrl
                    for c, value in zip(mapping[i], cr):
                        controls.append(dict(event_id=eid, control_i=c, symbol=symbol, minutes=minutes,
                                             fold=fold, **value))
                    r = f.iloc[i]
                    row = dict(**label, event_id=eid, symbol=symbol, minutes=minutes, fold=fold,
                               month=bars.index[i].strftime('%Y-%m'), control_n=nctrl,
                               control_mean_net_bp=float(np.mean([x['net_bp'] for x in cr])) if nctrl == 3 else np.nan,
                               near_zero_bars=int(r.near_zero_bars), focus_start_i=int(r.focus_start_i),
                               contraction_ratio=r.contraction_ratio, proximity_atr=r.proximity_atr,
                               separation_delta=r.separation_delta,
                               formation_memory_ratio=r.formation_memory_ratio,
                               formation_memory_recent_width=r.formation_memory_recent_width,
                               formation_memory_background_width=r.formation_memory_background_width,
                               strength_score=abs(r.md)/r.atr, contraction_score=-r.contraction_ratio,
                               proximity_score=-r.proximity_atr, memory_score=-r.formation_memory_ratio,
                               prior_box_high=r.prior_box_high, prior_box_low=r.prior_box_low,
                               box_breakout=bool(r.box_breakout), breakout_score=r.breakout_score,
                               htf_known=bool(r.htf_known), htf_index=int(r.htf_index), htf_md=r.htf_md, htf_atr=r.htf_atr,
                               htf_close_ms=r.htf_close_ms, htf_score=r.htf_score,
                               signal_close_price=float(bars.close.iloc[i]),
                               signal_close_time=(bars.index[i]+duration).isoformat(),
                               **{p: bool(mask.iloc[i]) for p, mask in masks.items()})
                    row['excess_bp'] = row['net_bp']-row['control_mean_net_bp']
                    fold_rows.append(row)
                events.extend(fold_rows)
                coverage.append(dict(symbol=symbol, minutes=minutes, fold=fold, bars=len(in_fold),
                                     eligible_decisions=len(indexes), signals=len(fold_rows),
                                     status='ok' if len(indexes) else 'insufficient_warmup'))
                ef = pd.DataFrame(fold_rows)
                for policy in POLICIES:
                    selected = ef.loc[ef[policy]].copy() if len(ef) else ef.copy()
                    pm, eq, ids = compound_portfolio(bars, selected, first, last)
                    portfolio_rows.append(dict(symbol=symbol, minutes=minutes, fold=fold, policy=policy, **pm))
                    accepted.extend(dict(event_id=eid, policy=policy) for eid in ids)
                    series = pool_equities[fold, minutes, policy]
                    close_eq = eq.loc[eq.kind.eq('close')].set_index('time').equity
                    if close_eq.index.has_duplicates:
                        raise ValueError('duplicate portfolio close')
                    aligned = close_eq.reindex(series.index).ffill().fillna(1.)
                    pool_equities[fold, minutes, policy] += aligned / len(symbols)
        print(f'{symbol_number+1}/54 {symbol}: {len(events)} canonical events', flush=True)
    ef = pd.DataFrame(events)
    summary, ranks, tails = summarize(ef, len(symbols))
    equity_rows, pool_metrics, incremental = [], [], []
    for (fold, minutes, policy), eq in pool_equities.items():
        initial = pd.Series([1.], index=[eq.index[0]-pd.Timedelta(minutes=minutes)])
        full = pd.concat([initial, eq])
        drawdown = (1-full/full.cummax()).max()*100
        pool_metrics.append(dict(fold=fold, minutes=minutes, policy=policy,
                                 portfolio_net_pct=(eq.iloc[-1]-1)*100, portfolio_mdd_pct=drawdown,
                                 portfolio_min_equity=full.min()))
        # Persist daily marks and the final boundary, keeping reports compact.
        daily = daily_marks(full)
        equity_rows.extend(dict(fold=fold, minutes=minutes, policy=policy, time=t.isoformat(), equity=v)
                           for t, v in daily.items())
        if policy != 'P00':
            base = pool_equities[fold, minutes, 'P00']
            # Month-end change measured in identical initial-capital units,
            # not averages of per-coin returns/drawdowns or overlapping events.
            grouped = monthly_increment(eq, base)
            inc = inference(grouped.to_numpy(), grouped.index.to_numpy())
            incremental.append(dict(fold=fold, minutes=minutes, policy=policy,
                                    incremental_net_pct=(eq.iloc[-1]-base.iloc[-1])*100,
                                    positive_delta_months=int(grouped.gt(0).sum()),
                                    **{'incremental_'+k:v for k,v in inc.items()}))
    summary = summary.merge(pd.DataFrame(pool_metrics), how='left', on=['fold', 'minutes', 'policy'])
    inc = pd.DataFrame(incremental)
    ix = inc.loc[(inc.fold=='validation') & inc.policy.isin(['S01','H01']) & inc.incremental_p.notna()].sort_values('incremental_p').index
    inc.loc[ix, 'incremental_p_holm_validation'] = np.maximum.accumulate(np.minimum(
        1, inc.loc[ix, 'incremental_p'].to_numpy()*(len(ix)-np.arange(len(ix)))))
    summary = summary.merge(inc, how='left', on=['fold','minutes','policy'])
    outputs = [('events', ef, DATA), ('controls', pd.DataFrame(controls), DATA),
               ('accepted', pd.DataFrame(accepted), DATA), ('summary', summary, results),
               ('rankings', ranks, results), ('right_tail', tails, results),
               ('coverage', pd.DataFrame(coverage), results), ('portfolios', pd.DataFrame(portfolio_rows), results),
               ('equity_daily', pd.DataFrame(equity_rows), results)]
    files = []
    for name, frame, directory in outputs:
        target = directory / (name+('.csv.gz' if directory == DATA else '.csv'))
        frame.to_csv(target, index=False)
        files.append(dict(path=str(target.relative_to(ROOT)), rows=len(frame), sha256=digest(target), bytes=target.stat().st_size))
    manifest = dict(builder_commit=builder, completed_at=pd.Timestamp.now(tz='UTC').isoformat(),
                    sources=sources, policies=POLICIES, folds=FOLDS, holdout_consumptions={p:0 for p in POLICIES},
                    files=files, training_eligible=False, production_eligible=False,
                    universe='54 existing deep USDT perpetual datasets; not a point-in-time complete market universe',
                    cost_bp=20, funding_included=False, exit='IMACD neutral next open or fold boundary mark',
                    portfolio='Equal initial cash sleeves across 54 symbols, each 1x equity-funded single position; no margin model',
                    authorization='Owner allowed all dates; 2026-09-08 authorized startup quality research',
                    live_changes=False)
    (results/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(summary.loc[summary.fold=='validation', ['minutes','policy','n','mean_net_bp','excess_bp','portfolio_net_pct','portfolio_mdd_pct','tail_profit_pct']].to_string(index=False), flush=True)


if __name__ == '__main__':
    run()

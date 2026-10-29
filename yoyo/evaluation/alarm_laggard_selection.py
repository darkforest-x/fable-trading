"""Frozen-alarm, prospective coin ranking with honest five-minute outcome bounds.

Sources: registered Binance5m OHLCV cohort and the saved market_crash_alarm
timestamps. No trades are simulated. Features use completed coin/major bars at
the same time; only label bounds see subsequent lows/closes. Missing future bars
remain censored, not negative examples. The touch-day1m cache is never a feature
or a sampling frame. Rolling beta is a descriptive past-return statistic, not a
new trained classifier. See the frozen experiment config for all windows.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.market_crash_alarm import save

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-alarm-laggard-selection-20261029-v1'
STEP = 300_000
FIELDS = ['open', 'high', 'low', 'close', 'volume']
_CFG = _ALARMS = _MARKET = None


def frozen_alarms(cfg):
    """Read exact stored alarms, including false alarms; do not rerun thresholds."""
    root = ROOT / cfg['alarm_source']
    parts = []
    for method in ('six', 'btc'):
        for period in ('2025_2026', 'design_day'):
            f = pd.read_csv(root / f'{period}_{method}_alarms.csv')
            t = pd.to_datetime(f.available_at, utc=True).astype('int64') // 10**6
            parts.append(pd.DataFrame({'alarm_ms': t, method: True}))
    a = pd.concat(parts).groupby('alarm_ms')[['six', 'btc']].max().fillna(False).astype(bool).reset_index()
    start = pd.Timestamp(cfg['start']).value // 10**6
    end = pd.Timestamp(cfg['end_exclusive']).value // 10**6
    a = a[a.alarm_ms.between(start, end - 1)].copy().reset_index(drop=True)
    a['alarm_id'] = np.arange(len(a))
    a['available_at'] = pd.to_datetime(a.alarm_ms, unit='ms', utc=True).astype(str)
    a['design_day'] = a.available_at.str.startswith(cfg['design_day'])
    # Both 1m alarm and 5m bars have the frozen one-second arrival delay.
    a['feature_close_ms'] = ((a.alarm_ms - 1000) // STEP) * STEP
    return a


def build_market(cfg):
    """Median-major5m log returns using synchronized completed closes only."""
    z = np.load(ROOT / cfg['alarm_source'] / 'panel.npz')
    take = (z['times'] + 60000) % STEP == 0
    closes = z['close'][take]
    returns = np.median(np.diff(np.log(closes), axis=0, prepend=np.full((1, 6), np.nan)), axis=1)
    np.savez_compressed(OUT / 'market_5m.npz', times=z['times'][take] - 4 * 60000, returns=returns)


def causal_features(frame, market, cfg):
    """OHLCV; prior288return sigma, prior2016return beta,2016past tail recoveries.

    All inputs end at the row's bar CLOSE. Sigma/beta shift1 excludes that row's
    return. Tail recovery includes the just-completed bar, whose close is known.
    Volume uses close*base_volume and is only an approximate liquidity proxy.
    """
    c = cfg['features']; close = frame.close
    r = np.log(close).diff()
    sigma = r.rolling(c['volatility_bars']).std(ddof=0).shift(1).clip(lower=c['volatility_floor'])
    beta = r.rolling(c['history_bars']).cov(market, ddof=0).shift(1) / market.rolling(c['history_bars']).var(ddof=0).shift(1)
    retreat = 1 - frame.low / close.shift(1)
    tail = retreat >= c['historical_tail_sigma'] * sigma
    recovery = ((close - frame.low) / (close.shift(1) - frame.low)).clip(0, 1).where(tail)
    count = tail.astype(float).rolling(c['history_bars']).sum()
    resilience = recovery.fillna(0).rolling(c['history_bars']).sum() / count
    resilience = resilience.where(count >= c['minimum_historical_tails'])
    r30 = np.log(close / close.shift(6))
    m30 = market.rolling(6).sum()
    lag = (r30 - beta * m30) / (sigma * np.sqrt(6))
    liquidity = (close * frame.volume).rolling(c['volatility_bars']).sum()
    complete = frame[FIELDS].notna().all(axis=1)
    warmup = c['volatility_bars'] + c['history_bars'] + 2
    valid = complete.astype(int).rolling(warmup).min().eq(1)
    active = frame.volume.gt(0).astype(int).rolling(cfg['eligibility']['recent_positive_volume_bars']).min().eq(1)
    prior_low = frame.low.rolling(6).min() / close.shift(6) - 1
    known_return = close / close.shift(6) - 1
    not_deep = (known_return > cfg['eligibility']['close_decline_limit']) & (prior_low > cfg['eligibility']['low_decline_limit'])
    result = pd.DataFrame(dict(reference=close, lag=lag, recovery=resilience, sigma=sigma,
                               liquidity=liquidity, beta=beta, return30=known_return,
                               low30=prior_low, historical_tail_count=count,
                               not_deep=not_deep, valid_history=valid, active=active))
    result['rankable'] = valid & active & np.isfinite(result[['lag', 'recovery', 'sigma', 'liquidity']]).all(axis=1) & liquidity.gt(0)
    return result


def outcome_bounds(times, low, close, alarm_ms, reference, cfg):
    """Interval-censored labels, not fills. Times are5m opens on an intact grid.

    A lower-bound first NEW touch requires the alarm-straddling bar to remain
    above the deep level; otherwise pre-alarm touch cannot be excluded. Full
    contained bars prove subsequent touches. Recovery uses a later bar close.
    Unknown bars retain the widest label bounds and never remove a candidate.
    """
    oc = cfg['outcomes']; deep_end = alarm_ms + oc['deep_horizon_minutes'] * 60000
    recovery_end = alarm_ms + oc['recovery_horizon_minutes'] * 60000
    level = reference * (1 - oc['deep_fraction'])
    target = reference * (1 - oc['recovery_fraction'])
    overlap = (times < deep_end) & (times + STEP > alarm_ms)
    full = (times >= alarm_ms) & (times + STEP <= deep_end)
    initial = (times <= alarm_ms) & (times + STEP > alarm_ms)
    deep_known = np.isfinite(low[overlap]).all() and overlap.any() and initial.any()
    observed_hits = overlap & (low <= level)
    upper_deep = bool(observed_hits.any() or not deep_known)
    initial_clear = initial.any() and np.isfinite(low[initial]).all() and bool((low[initial] > level).all())
    definite = np.flatnonzero(full & (low <= level))
    lower_deep = bool(initial_clear and len(definite))
    # All closes strictly later than the first definite crossing bar; excludes
    # a possibly already recovered same-bar move and so is a conservative lower.
    lower_joint = False
    if lower_deep:
        first = definite[0]
        later = (times > times[first]) & (times + STEP <= recovery_end)
        lower_joint = bool((close[later] >= target).any())
    possible = np.flatnonzero(observed_hits)
    upper_joint = False
    if len(possible):
        first = possible[0]
        later = (times >= times[first]) & (times < recovery_end)
        # A close after the horizon does not prove recovery by that horizon,
        # but can only contribute to the upper bound.
        upper_joint = bool((close[later] >= target).any() or (~np.isfinite(close[later])).any())
    future = (times + STEP > alarm_ms) & (times < recovery_end)
    censored = not deep_known or not np.isfinite(close[future]).all()
    if censored and upper_deep:
        upper_joint = True
    return dict(deep_lower=int(lower_deep), deep_upper=int(upper_deep),
                joint_lower=int(lower_joint), joint_upper=int(upper_joint),
                initial_touch_ambiguous=int(bool((low[initial] <= level).any()) or not np.isfinite(low[initial]).all()),
                future_censored=int(censored))


def worker_init(cfg, alarms):
    global _CFG, _ALARMS, _MARKET
    _CFG, _ALARMS = cfg, alarms
    z = np.load(OUT / 'market_5m.npz')
    _MARKET = pd.Series(z['returns'], index=z['times'])


def process_symbol(job):
    """One full source read per symbol, cached causal snapshots and label bounds."""
    from yoyo.evaluation.monday_ma6_retest import source_frame
    cfg, alarms = _CFG, _ALARMS
    symbol, paths = job
    folder = OUT / 'symbols'; folder.mkdir(exist_ok=True)
    done = folder / f'{symbol}.json'; target = folder / f'{symbol}.csv.gz'
    receipts = [dict(path=str(Path(p).relative_to(ROOT)), bytes=Path(p).stat().st_size,
                     mtime_ns=Path(p).stat().st_mtime_ns) for p in paths]
    if done.exists():
        old = json.loads(done.read_text())
        if old['sources'] != receipts:
            raise ValueError(f'Changed input cache: {symbol}')
        return old
    frame, source_audit = source_frame(symbol, paths)
    frame.index = frame.index.astype('int64') // 10**6
    first = int(alarms.feature_close_ms.min()) - (cfg['features']['history_bars'] + cfg['features']['volatility_bars'] + 4) * STEP
    last = int(alarms.alarm_ms.max()) + 65 * 60000
    grid = np.arange(first, last // STEP * STEP + STEP, STEP, dtype=np.int64)
    frame = frame.reindex(grid)
    feat = causal_features(frame, _MARKET.reindex(grid), cfg)
    loc = np.searchsorted(grid, alarms.feature_close_ms.to_numpy() - STEP)
    snapshots = feat.iloc[loc].reset_index(drop=True)
    common = snapshots.rankable.to_numpy(bool)
    rows = []
    low, close = frame.low.to_numpy(), frame.close.to_numpy()
    for ai in np.flatnonzero(common):
        a = alarms.iloc[ai]; f = snapshots.iloc[ai]
        ix = int(loc[ai]) + 1
        stop = min(len(grid), ix + 14)
        labels = outcome_bounds(grid[ix:stop], low[ix:stop], close[ix:stop], int(a.alarm_ms), float(f.reference), cfg)
        rows.append(dict(alarm_id=int(a.alarm_id), symbol=symbol,
                         feature_age_seconds=(int(a.alarm_ms) - int(a.feature_close_ms)) / 1000,
                         **{k: f[k] for k in ['reference','lag','recovery','sigma','liquidity','beta','return30','low30','historical_tail_count','not_deep']},
                         **labels))
    columns = ['alarm_id','symbol','feature_age_seconds','reference','lag','recovery','sigma','liquidity','beta','return30','low30','historical_tail_count','not_deep',
               'deep_lower','deep_upper','joint_lower','joint_upper','initial_touch_ambiguous','future_censored']
    pd.DataFrame(rows, columns=columns).to_csv(target, index=False, compression='gzip')
    result = dict(symbol=symbol, sources=receipts, source_audit=source_audit,
                  alarm_count=len(alarms), rankable=int(common.sum()),
                  not_deep=int((common & snapshots.not_deep.to_numpy(bool)).sum()),
                  missing_history=int((~snapshots.valid_history).sum()),
                  inactive=int((snapshots.valid_history & ~snapshots.active).sum()),
                  insufficient_feature=int((snapshots.valid_history & snapshots.active & ~snapshots.rankable).sum()))
    save(done, result)
    return result


def select_and_match(candidates, cfg):
    """Fixed causal ranks and exact expected random control within each cell.

    Control labels are pool means because a uniform sample without replacement
    of K rows has that exact expectation. No simulated trade or fitted model.
    """
    rows, selection_rows = [], []
    labels = ['deep_lower','deep_upper','joint_lower','joint_upper']
    for alarm_id, group in candidates[candidates.not_deep].groupby('alarm_id', sort=True):
        g = group.copy().sort_values('symbol').reset_index(drop=True)
        if len(g) < 20:
            continue
        g['vol_bin'] = np.minimum((g.sigma.rank(method='average', pct=True) * 3).astype(int), 2)
        g['liq_bin'] = np.minimum((g.liquidity.rank(method='average', pct=True) * 3).astype(int), 2)
        g['cell'] = g.vol_bin * 3 + g.liq_bin
        k = int(np.ceil(cfg['selection']['fraction'] * len(g)))
        lag_order = g.sort_values(['lag', 'symbol'], ascending=[False, True])
        rules = dict(lag=lag_order.head(k).index,
                     lag_then_recovery=lag_order.head(2*k).sort_values(['recovery','symbol'], ascending=[False,True]).head(k).index)
        for name, selected in rules.items():
            chosen = g.loc[selected]
            matched, control_sum = [], {label: 0. for label in labels}
            for cell, portion in chosen.groupby('cell'):
                pool = g[(g.cell == cell) & ~g.index.isin(selected)]
                if len(pool) < len(portion):
                    continue
                matched.extend(portion.index.tolist())
                for label in labels:
                    control_sum[label] += len(portion) * float(pool[label].mean())
            entry = dict(alarm_id=int(alarm_id), rule=name, candidate_n=len(g), selected_n=k,
                         matched_n=len(matched), unmatched_n=k-len(matched),
                         selected_ambiguous=int(chosen.initial_touch_ambiguous.sum()),
                         selected_censored=int(chosen.future_censored.sum()))
            for label in labels:
                entry['selected_' + label] = int(chosen[label].sum())
                entry['matched_' + label] = int(g.loc[matched, label].sum())
                entry['control_' + label] = control_sum[label]
            rows.append(entry)
            for idx in selected:
                selection_rows.append(dict(alarm_id=int(alarm_id), rule=name, symbol=g.loc[idx,'symbol'],
                                           matched=idx in matched))
    return pd.DataFrame(rows), pd.DataFrame(selection_rows)


def main():
    from yoyo.evaluation.monday_ma6_retest import source_jobs
    from yoyo.evaluation.alarm_laggard_statistics import summarize
    start_clock = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument('--summarize-only',action='store_true',help='Reuse persisted candidates and matched records; no market/source replay.')
    args = parser.parse_args()
    cfg = json.loads((OUT / 'config.json').read_text())
    code = ['yoyo/evaluation/alarm_laggard_selection.py','yoyo/evaluation/alarm_laggard_statistics.py',str((OUT/'config.json').relative_to(ROOT))]
    subprocess.run(['git','diff','--exit-code','HEAD','--',*code], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    if args.summarize_only:
        alarm_table = pd.read_csv(OUT/'alarms.csv')
        candidates = pd.read_csv(OUT/'candidates.csv.gz',dtype={'not_deep':bool})
        records = pd.read_csv(OUT/'matched_alarm_records.csv')
        result = summarize(records,candidates,alarm_table,cfg,OUT)
        result.update(experiment_id=cfg['experiment_id'],
                      builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                      candidate_builder_commit=json.loads((OUT/'candidate_build.json').read_text())['builder_commit'],
                      symbols=len(json.loads((OUT/'input_receipts.json').read_text())),
                      raw_candidate_snapshots=len(candidates),summary_elapsed_seconds=time.monotonic()-start_clock,
                      source_read_errors=[],resumed_from_cached_candidates=True,training_eligible=False,production_eligible=False)
        save(OUT/'summary.json',result)
        print('Completed summary from cached candidates; no source replay.',flush=True)
        return
    alarm_table = frozen_alarms(cfg)
    alarm_table.to_csv(OUT / 'alarms.csv', index=False)
    if not (OUT / 'market_5m.npz').exists():
        build_market(cfg)
    source_cfg = json.loads((ROOT / cfg['source_config']).read_text())
    jobs = [(s,p) for s,p in source_jobs(source_cfg) if s != 'XAUUSD']
    save(OUT/'source_jobs.json',jobs)
    print(f'Frozen alarms: {len(alarm_table)} union; source symbols: {len(jobs)}', flush=True)
    receipts, errors = [], []
    with ProcessPoolExecutor(cfg['workers'], initializer=worker_init, initargs=(cfg, alarm_table)) as pool:
        work = {pool.submit(process_symbol, job): job[0] for job in jobs}
        for i, future in enumerate(as_completed(work), 1):
            symbol = work[future]
            try:
                receipt = future.result(); receipts.append(receipt)
                print(f'{i}/{len(jobs)} {symbol}: {receipt["rankable"]} rankable snapshots', flush=True)
            except Exception as exc:
                errors.append(dict(symbol=symbol,error=repr(exc)))
                save(OUT/'errors.json',errors)
                print(f'{i}/{len(jobs)} {symbol}: ERROR {exc!r}', flush=True)
    save(OUT/'input_receipts.json',receipts)
    if errors:
        raise RuntimeError(f'{len(errors)} source errors; do not silently discard symbols')
    frames = [pd.read_csv(OUT/'symbols'/f'{s}.csv.gz') for s,_ in jobs]
    candidates = pd.concat([f for f in frames if not f.empty],ignore_index=True)
    candidates['not_deep'] = candidates.not_deep.astype('boolean').astype(bool)
    candidates.to_csv(OUT/'candidates.csv.gz',index=False,compression='gzip')
    save(OUT/'candidate_build.json',dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                                        source_read_errors=errors,source_symbols=len(jobs),rows=len(candidates)))
    records, selected = select_and_match(candidates,cfg)
    records.to_csv(OUT/'matched_alarm_records.csv',index=False)
    selected.to_csv(OUT/'selections.csv.gz',index=False,compression='gzip')
    result = summarize(records, candidates, alarm_table, cfg, OUT)
    result.update(experiment_id=cfg['experiment_id'],builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  symbols=len(jobs),raw_candidate_snapshots=len(candidates),elapsed_seconds=time.monotonic()-start_clock,
                  source_read_errors=errors,training_eligible=False,production_eligible=False)
    save(OUT/'summary.json',result)
    print('Completed fixed-selection study; summary.json saved.',flush=True)


if __name__ == '__main__':
    main()

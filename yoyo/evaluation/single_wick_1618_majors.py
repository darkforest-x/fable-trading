"""Notion #72: causal single-5m-wick entries with owner-confirmed candle stops.

Reuse registered Binance candles and native Nautilus orders. The signal only
reads frozen completed NY Monday anchors and OHLC through its own close.
Features read OHLCV through that close: previous 14 true ranges, previous 20
volumes, previous 12 closes, and the signal candle's wick/body geometry. Future
prices belong only to native exits. No trained model or production change.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_ma6_retest import finite
from yoyo.evaluation.monday_limit_reclaim_inputs import load_job, weekly_context
from yoyo.evaluation.monday_limit_reclaim import segment_ends

EXP = ROOT / 'experiments/active/exp-5m-single-wick-1618-majors-20261029-v1'
OHLC = ['open', 'high', 'low', 'close']
FEATURES = ['wick_fraction', 'volume_ratio20', 'range_atr14', 'approach12_bp',
            'risk_bp', 'planned_rr', 'penetration_range', 'reclaim_range']


def five_minute(frame, step):
    """Keep only exact complete 5m grids; never fill a missing source minute."""
    if step == 5:
        return frame.copy()
    g = frame.resample('5min', closed='left', label='left')
    bars = g.agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    return bars[g.close.count().eq(5)].copy()


def prepare(frame, job, cfg):
    """Build causal signals and matched nulls, not a price-path/PnL engine.

    Controls use only earlier closes in the trigger NY week. Their maximum
    observation duration equals the signal's available continuous duration.
    Signal/control barriers share percentage geometry. Missing controls stay
    missing; nothing is resampled using future profit or loss.
    """
    step = job['execution_minutes']
    bars = five_minute(frame, step)
    bars.index = bars.index.as_unit('ns')
    bars.attrs['group'] = 'crypto_5m'
    ctx = weekly_context(bars, 5)
    decimals = max(2, min(9, int(np.ceil(-np.log10(frame[OHLC].to_numpy().min()))) + 7))
    tick = 10. ** -decimals
    p = frame[OHLC].round(decimals)
    b = bars.copy(); b[OHLC] = b[OHLC].round(decimals)
    o, h, l, c = (b[k].to_numpy() for k in OHLC)
    tr = pd.concat([b.high-b.low, (b.high-b.close.shift()).abs(),
                    (b.low-b.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift().to_numpy()
    volume_ref = b.volume.rolling(20).median().shift().to_numpy()
    bucket = np.floor(np.log2(np.where(atr > 0, atr / b.close.shift().to_numpy(), np.nan))*4)
    ends = segment_ends(frame, job)
    # Signal execution is the last minute of a complete 5m bar, or the 5m bar.
    execution = frame.index.get_indexer(b.index+pd.Timedelta(minutes=5-step))
    assert (execution >= 0).all()
    rng = np.random.default_rng(cfg['seed']+zlib.crc32(job['symbol'].encode()))
    rows, audit = [], []
    for w in ctx:
        if not w.get('valid'):
            audit.append(dict(week=w['week'], status='unobservable_week'))
            continue
        a, stop = int(w['monitor_start_i']), int(w['monitor_end_i_exclusive'])
        hi, lo = w['monday_high'], w['monday_low']; width = hi-lo
        if width <= 0:
            continue
        for side in (1, -1):
            level = lo-.618*width if side == 1 else hi+.618*width
            target = round(lo+.618*width if side == 1 else hi-.618*width, decimals)
            touched = l <= level if side == 1 else h >= level
            hits = np.flatnonzero(touched[a:stop])+a
            first = int(hits[0]) if len(hits) else -1
            for i in hits:
                if i < 20 or i == 0 or touched[i-1]:
                    continue
                if not (side*(o[i]-level) > 0 and side*(c[i]-level) > 0):
                    continue
                j = int(execution[i]); end = int(ends[j])
                sl = l[i] if side == 1 else h[i]
                risk = side*(c[i]-sl)
                if target <= 0 or side*(target-c[i]) < tick*.999 or risk < tick*.999 or end <= j:
                    audit.append(dict(week=w['week'], status='no_target_room_or_observation', bar_i=int(i)))
                    continue
                if not np.isfinite(bucket[i]) or atr[i] <= 0 or volume_ref[i] <= 0:
                    audit.append(dict(week=w['week'], status='unwarmed_feature', bar_i=int(i)))
                    continue
                ident = f"{job['symbol']}:{w['week']}:{side:+d}:{b.index[i].value}"
                candle_range = h[i]-l[i]
                wick = min(o[i], c[i])-l[i] if side == 1 else h[i]-max(o[i], c[i])
                common = dict(event_id=ident, symbol=job['symbol'], week=w['week'], side=side,
                    monday_high=hi, monday_low=lo, level=level, first_weekly_touch=bool(i == first),
                    signal_time=str(b.index[i]+pd.Timedelta(minutes=5)), signal_i=j,
                    signal_bar_i=int(i), bucket=int(bucket[i]),
                    wick_fraction=wick/candle_range, volume_ratio20=b.volume.iloc[i]/volume_ref[i],
                    range_atr14=candle_range/atr[i], approach12_bp=-side*(c[i-1]/c[i-13]-1)*10000,
                    risk_bp=risk/c[i]*10000, planned_rr=side*(target-c[i])/risk,
                    penetration_range=-side*(sl-level)/width, reclaim_range=side*(c[i]-level)/width)
                pool = np.arange(a, i)
                pool = pool[np.isfinite(bucket[pool]) & (bucket[pool] == bucket[i])]
                duration = end-j
                pool = pool[(ends[execution[pool]]-execution[pool]) >= duration]
                chosen = rng.choice(pool, cfg['controls_per_signal'], replace=False) if len(pool) >= cfg['controls_per_signal'] else []
                common['matched_controls_available'] = len(chosen)
                rows.append(dict(common, trade_id=ident+':c0', population='signal', control_id=0,
                    entry_i=j, end_i=end, entry=c[i], stop=sl, risk=risk, target_price=target,
                    entry_time=str(frame.index[j]+pd.Timedelta(minutes=step))))
                for k, ri in enumerate(chosen, 1):
                    rj = int(execution[ri]); entry = float(c[ri]); scale = entry/c[i]
                    rstop = round(sl*scale, decimals); rtarget = round(target*scale, decimals)
                    rrisk = side*(entry-rstop)
                    if rrisk < tick*.999 or side*(rtarget-entry) < tick*.999 or rstop <= 0:
                        continue
                    rows.append(dict(common, trade_id=ident+f':c{k}', population='random', control_id=k,
                        entry_i=rj, end_i=rj+duration, entry=entry, stop=rstop, risk=rrisk,
                        target_price=rtarget, entry_time=str(frame.index[rj]+pd.Timedelta(minutes=step))))
    return p, b, pd.DataFrame(rows), audit, tick


def policy(path, cfg):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy
    return ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
        target_from_entry=True, intrabar_path=path, monotone_segments=True,
        fill_maker_fee=cfg['maker_fee'], fill_taker_fee=cfg['taker_fee'])


def execute(frame, entries, job, cfg, tick, path='OHLC'):
    from yoyo.layers.l3_backtest.nautilus_independent import run_native
    return run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries,
        policy(path, cfg), tick=tick, symbol=job['symbol'].removesuffix('USDT'),
        execution_minutes=job['execution_minutes'], batch_bars=50000)


def process(job, cfg):
    start = time.monotonic(); dest = EXP/'symbols'/job['symbol']; dest.mkdir(parents=True, exist_ok=True)
    if (dest/'done.json').exists():
        return json.loads((dest/'done.json').read_text())
    frame, quality = load_job(job)
    p, bars, entries, opportunities, tick = prepare(frame, job, cfg)
    dump(dest/'identity.json', dict(job=job, inputs=[dict(path=x, bytes=Path(x).stat().st_size,
        mtime_ns=Path(x).stat().st_mtime_ns) for x in job['paths']], config=cfg,
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()))
    entries.to_parquet(dest/'entries.parquet', index=False)
    bars.to_parquet(dest/'candles5m.parquet')
    dump(dest/'opportunities.json', opportunities)
    if entries.empty:
        raise ValueError('No entries; inspect source and scope')
    p.index = p.index+pd.Timedelta(minutes=job['execution_minutes'])
    rows, fills, native = execute(p, entries, job, cfg, tick)
    assert len(rows) == len(entries) == native['positions']
    assert len(fills) == len(rows)*2
    assert (rows.exit_i > rows.entry_i).all(), 'Entry-bar extrema must not exit a close-time entry'
    rows.to_parquet(dest/'trades_ohlc.parquet', index=False)
    fills.to_parquet(dest/'fills_ohlc.parquet', index=False)
    # Only a first-hit bar containing BOTH barriers needs a path sensitivity.
    ids = set(rows.loc[rows.ambiguous.eq(1), 'trade_id'])
    other = []
    for trade_id in sorted(ids):
        e = entries[entries.trade_id.eq(trade_id)].copy()
        original = rows[rows.trade_id.eq(trade_id)].iloc[0]
        left, right = int(e.entry_i.iloc[0]), int(original.exit_i)
        e['entry_i'] = 0; e['end_i'] = right-left
        alternate, _, _ = execute(p.iloc[left:right+1], e, job, cfg, tick, 'OLHC')
        alternate['entry_i'] += left; alternate['exit_i'] += left
        other.append(alternate)
    conservative = rows.copy().set_index('trade_id')
    if other:
        alt = pd.concat(other, ignore_index=True).set_index('trade_id')
        alt.to_parquet(dest/'ambiguous_olhc.parquet')
        for trade_id, row in alt.iterrows():
            if row.net_r < conservative.loc[trade_id, 'net_r']:
                conservative.loc[trade_id, row.index] = row
    t = conservative.reset_index()
    t['closed'] = t.reason.ne(0)
    t['target_hit'] = t.reason.eq(4)
    t['gross_bp'] = t.gross_r*t.risk/t.entry*10000
    # Stress only; not a fill/decision engine. Charge 10bp of each market-fill notional.
    t['slip_stress_bp'] = 10.+np.where(t.target_hit, 0., 10.*t.exit_price/t.entry)
    t['net_stress_bp'] = t.net_notional_bp-t.slip_stress_bp
    t['net_stress_r'] = t.net_r-t.slip_stress_bp/10000*t.entry/t.risk
    t.to_parquet(dest/'trades.parquet', index=False)
    done = dict(symbol=job['symbol'], source_first=str(frame.index[0]), source_last=str(frame.index[-1]),
        source_rows=len(frame), five_minute_rows=len(bars), signals=int(entries.population.eq('signal').sum()),
        controls=int(entries.population.eq('random').sum()), positions=native['positions'], fills=native['fills'],
        engine=native.get('engine'), version=native.get('version'), ambiguity_n=len(ids),
        gap_count=quality.get('gap_count'), seconds=time.monotonic()-start)
    dump(dest/'done.json', finite(done)); return done


def metrics(g):
    c = g[g.closed]; v = c.net_r
    return finite(dict(n=len(g), closed_n=len(c), censored_n=len(g)-len(c), target_n=int(c.target_hit.sum()),
        target_pct=100*c.target_hit.mean(), win_pct=100*v.gt(0).mean(), gross_mean_r=c.gross_r.mean(),
        net_mean_r=v.mean(), net_sum_r=v.sum(), net_mean_bp=c.net_notional_bp.mean(),
        stress_mean_r=c.net_stress_r.mean(), stress_mean_bp=c.net_stress_bp.mean(),
        pf=v[v>0].sum()/-v[v<0].sum() if (v<0).any() else None,
        planned_rr_median=c.planned_rr.median(), risk_bp_median=c.risk_bp.median(),
        holding_hours_median=((pd.to_datetime(c.exit_time, utc=True)-pd.to_datetime(c.entry_time, utc=True)).dt.total_seconds()/3600).median()))


def paired(g):
    s = g[g.population.eq('signal') & g.closed]
    r = g[g.population.eq('random') & g.closed].groupby('event_id').agg(
        control_n=('net_r', 'size'), random_r=('net_r', 'mean'), random_bp=('net_notional_bp', 'mean'))
    z = s.merge(r[r.control_n.eq(3)], left_on='event_id', right_index=True)
    d = z.net_r-z.random_r
    if len(z):
        weeks = pd.DataFrame(dict(week=z.week, delta=d)).groupby('week').delta.agg(['sum', 'count'])
        rng = np.random.default_rng(20261029)
        ix = rng.integers(len(weeks), size=(3000, len(weeks)))
        boot = weeks['sum'].to_numpy()[ix].sum(1)/weeks['count'].to_numpy()[ix].sum(1)
        null = (rng.choice([-1, 1], size=(3000, len(weeks)))*weeks['sum'].to_numpy()).sum(1)
        p = (1+(np.abs(null) >= abs(weeks['sum'].sum())).sum())/3001
        ci = np.quantile(boot, [.025, .975]).tolist()
    else:
        p, ci = None, None
    return finite(dict(matched_n=len(z), signal_mean_r=z.net_r.mean(), random_mean_r=z.random_r.mean(),
        excess_r=d.mean(), random_mean_bp=z.random_bp.mean(), excess_r_week_bootstrap95=ci,
        paired_week_signflip_p=p))


def feature_readout(t):
    """Single-feature ranks only; orientation/quartiles chosen before 2025."""
    def auc_value(labels, scores):
        # Mann-Whitney AUC with midranks for ties; no estimator is fitted.
        positive = np.asarray(labels, dtype=bool)
        npos = int(positive.sum()); nneg = len(positive)-npos
        if not npos or not nneg:
            return None
        ranks = pd.Series(np.asarray(scores)).rank(method='average').to_numpy()
        return float((ranks[positive].sum()-npos*(npos+1)/2)/(npos*nneg))

    s = t[t.population.eq('signal') & t.closed].copy()
    et = pd.to_datetime(s.entry_time, utc=True); xt = pd.to_datetime(s.exit_time, utc=True)
    all_et = pd.to_datetime(t.entry_time, utc=True); all_xt = pd.to_datetime(t.exit_time, utc=True)
    within_period = {
        'pre2025': (all_et < '2025-01-01') & (all_xt < '2025-01-01'),
        'y2025': (all_et >= '2025-01-01') & (all_et < '2026-01-01') & (all_xt < '2026-01-01'),
        'y2026': all_et >= '2026-01-01',
    }
    train = s[(et < '2025-01-01') & (xt < '2025-01-01')]
    records = []
    for feat in FEATURES:
        z = train.dropna(subset=[feat])
        auc = auc_value(z.target_hit, z[feat])
        auc = .5 if auc is None else auc
        direction = 1 if auc >= .5 else -1
        quartiles = z[feat].quantile([.25, .5, .75]).to_numpy()
        top_cut = (direction*z[feat]).quantile(.9)
        for period, mask in [('pre2025', (et < '2025-01-01') & (xt < '2025-01-01')),
                             ('y2025', (et >= '2025-01-01') & (et < '2026-01-01') & (xt < '2026-01-01')),
                             ('y2026', et >= '2026-01-01')]:
            q = s[mask].dropna(subset=[feat]); score = q[feat]*direction
            val_auc = auc_value(q.target_hit, score)
            best = q[score >= top_cut]
            period_rows = t[within_period[period]]
            groups = []
            for k in range(4):
                g = q[np.searchsorted(quartiles, q[feat], side='right') == k]
                ids = set(g.event_id)
                groups.append(dict(quartile=k+1, **metrics(g), control=paired(period_rows[period_rows.event_id.isin(ids)])))
            records.append(finite(dict(feature=feat, period=period, direction=direction, auc=val_auc,
                training_quartiles=quartiles.tolist(), frozen_top_decile_cut=float(top_cut),
                frozen_top_decile=metrics(best), top_control=paired(period_rows[period_rows.event_id.isin(set(best.event_id))]),
                quartiles=groups)))
    return records


def summarize():
    cfg = json.loads((EXP/'config.json').read_text())
    done = [json.loads((EXP/'symbols'/j['symbol']/'done.json').read_text()) for j in cfg['jobs']]
    t = pd.concat([pd.read_parquet(EXP/'symbols'/j['symbol']/'trades.parquet') for j in cfg['jobs']], ignore_index=True)
    t.to_parquet(EXP/'trades.parquet', index=False)
    et = pd.to_datetime(t.entry_time, utc=True); xt = pd.to_datetime(t.exit_time, utc=True)
    table = []
    for period, pm in [('all', np.ones(len(t), bool)), ('pre2025', (et < '2025-01-01') & (xt < '2025-01-01')),
                       ('y2025', (et >= '2025-01-01') & (et < '2026-01-01') & (xt < '2026-01-01')),
                       ('y2026', et >= '2026-01-01')]:
        for cohort, cm in [('all', np.ones(len(t), bool)), ('first_touch', t.first_weekly_touch),
                           ('repeat', ~t.first_weekly_touch), ('short', t.side.eq(-1)), ('long', t.side.eq(1))]:
            g = t[pm & cm]
            table.append(dict(period=period, cohort=cohort, **metrics(g[g.population.eq('signal')]), control=paired(g)))
    symbols = [dict(symbol=sym, **metrics(g[g.population.eq('signal')]), control=paired(g)) for sym, g in t.groupby('symbol')]
    ohlc = pd.concat([pd.read_parquet(EXP/'symbols'/j['symbol']/'trades_ohlc.parquet') for j in cfg['jobs']])
    signal = t[t.population.eq('signal')]
    good = signal[signal.closed & signal.target_hit].sort_values(['symbol', 'entry_time'])
    good.to_csv(EXP/'target_examples.csv', index=False)
    signal.to_csv(EXP/'signals.csv', index=False)
    feats = feature_readout(t)
    dump(EXP/'features.json', feats)
    summary = dict(experiment_id=EXP.name, coverage=done, metrics=table, by_symbol=symbols,
        ohlc_signal_net_r=float(ohlc.loc[ohlc.population.eq('signal') & ohlc.reason.ne(0), 'net_r'].sum()),
        conservative_signal_net_r=float(signal.loc[signal.closed, 'net_r'].sum()),
        signal_ambiguous_n=int(signal.ambiguous.sum()),
        caution='Independent original-risk R, not compounded account returns. Retrospective fixed major universe. '
                'Feature comparisons exploratory; no fitted model or promote. Censored native boundary valuations excluded. '
                'Current bar close entries assume instantaneous close fills. Funding omitted; slippage reported separately.')
    dump(EXP/'summary.json', finite(summary))
    print(json.dumps(finite(dict(metrics=table, by_symbol=symbols)), ensure_ascii=False))


def run(workers):
    paths = ['yoyo/evaluation/single_wick_1618_majors.py', str((EXP/'config.json').relative_to(ROOT))]
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or subprocess.run(
                ['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and configuration before history execution')
    cfg = json.loads((EXP/'config.json').read_text())
    errors = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, j, cfg): j['symbol'] for j in cfg['jobs']}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as exc:
                error = dict(symbol=futures[future], error=repr(exc)); errors.append(error)
                dump(EXP/'symbols'/futures[future]/'error.json', error)
                print(json.dumps(error), flush=True)
    if errors:
        raise RuntimeError(errors)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=['run', 'summarize'])
    parser.add_argument('--workers', type=int, default=2); args = parser.parse_args()
    run(args.workers) if args.command == 'run' else summarize()

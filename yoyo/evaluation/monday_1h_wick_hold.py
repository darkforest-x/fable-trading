"""Owner case3: hourly wick-only 1618 touch and three untouched hourly bars.

Use completed NY Monday anchors and the existing five-minute Nautilus adapter.
The pin open AND close must be strictly inside the level; only its wick touches.
The next three consecutive, fully observed hours must have their entire range
strictly inside. Decide only after the third close; enter at the following open.
No volume, color, or wick/body-size filter. Stop is original pin extreme; target
is the near Monday edge. The first-touch option is fixed in the experiment config.

Features use pin/three CLOSED confirmation OHLCs and prior14 completed five-minute
TR for control matching. Hour bars require12 observed five-minute intervals and
60 underlying minutes. Crypto first touch requires an uninterrupted weekly prefix.
Spot inherits the existing >=240 observed five-minute Monday anchor and ordinary
market closures; its first touch means first OBSERVED touch. Conflicting minutes
remain excluded, segment boundaries censor positions, and spot costs stay unknown.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_5m_wick_gold as quality
from yoyo.evaluation import monday_5m_wick_gold_clock as gold_clock
from yoyo.evaluation.monday_15m_volume_rejection import compare, week_keys
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, runtime_versions

EXP = ROOT / 'experiments/active/exp-monday-1h-wick-hold-20261023-v1'
CODE = list(dict.fromkeys([*gold_clock.CODE, 'yoyo/evaluation/monday_1h_wick_hold.py',
                          'tests/evaluation/test_monday_1h_wick_hold.py']))
STEP = pd.Timedelta(hours=1)


def aggregate(f):
    group = f.resample('1h', closed='left', label='left')
    h = group.agg(dict(open='first', high='max', low='min', close='last')).dropna()
    h['bars'] = group.close.count().reindex(h.index)
    h['minutes'] = group['count'].sum().reindex(h.index)
    h.index = h.index.as_unit('ns')
    return h


def pattern_mask(h, level, side):
    """Pin-indexed masks, usable only at pin open +four hours."""
    complete = h.bars.eq(12) & h.minutes.eq(60)
    if side == 1:
        pin = h.low.le(level) & h[['open', 'close']].min(axis=1).gt(level)
        untouched = h.low
    else:
        pin = h.high.ge(level) & h[['open', 'close']].max(axis=1).lt(level)
        untouched = h.high
    confirms = complete.copy()
    for k in (1, 2, 3):
        confirms &= complete.shift(-k, fill_value=False)
        confirms &= (h.index.to_series().shift(-k)-h.index.to_series()).eq(STEP*k)
        confirms &= untouched.shift(-k).gt(level) if side == 1 else untouched.shift(-k).lt(level)
    return pin & complete, confirms


def candidates(f, symbol, cfg, blocked=()):
    h = aggregate(f)
    local, week = week_keys(h.index)
    l5, w5 = week_keys(f.index)
    mon = f[l5.dayofweek == 0].assign(week=w5[l5.dayofweek == 0])
    anchors = mon.groupby('week').agg(high=('high', 'max'), low=('low', 'min'), n=('close', 'size'))
    anchors = anchors[anchors.n.ge(240 if symbol == 'XAUUSD' else 288)]
    anchors = anchors[~anchors.index.isin(blocked)]
    hi = pd.Series(week, index=h.index).map(anchors.high)
    lo = pd.Series(week, index=h.index).map(anchors.low)
    if symbol == 'XAUUSD':
        known_prefix = pd.Series(True, index=h.index)
    else:
        start = pd.DatetimeIndex(pd.to_datetime(week)).tz_localize(old.TZ).tz_convert('UTC').as_unit('ns')
        expected_slot = (h.index.asi8-start.asi8)//STEP.value
        seen_slot = pd.Series(np.arange(len(h)), index=h.index).groupby(week).cumcount()
        known_prefix = seen_slot.eq(expected_slot) & (h.bars.eq(12) & h.minutes.eq(60)).groupby(week).cummin()
    tr = pd.concat([f.high-f.low, (f.high-f.close.shift()).abs(),
                    (f.low-f.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().shift()/f.open
    bucket = np.floor(np.log2(atr.where(atr > 0))*4).fillna(-999).to_numpy(int)
    rows = []; funnel = {}; rejected = 0
    for side in (1, -1):
        level = lo-.618*(hi-lo) if side == 1 else hi+.618*(hi-lo)
        touched = (h.low.le(level) if side == 1 else h.high.ge(level)) & hi.notna() & (hi > lo) & (local.dayofweek >= 1)
        admission = touched & touched.astype(int).groupby(week).cumsum().eq(1) if cfg['first_touch_only'] else touched
        unknown = admission & ~known_prefix
        if cfg['first_touch_only']:
            admission &= known_prefix
        pin, confirms = pattern_mask(h, level, side)
        mask = admission & pin & confirms
        funnel[str(side)] = dict(touches=int(touched.sum()), admitted_touches=int(admission.sum()),
            unknown_prefix=int(unknown.sum()), wick=int((admission & pin).sum()), three_range_holds=int(mask.sum()))
        for j in np.flatnonzero(mask.to_numpy()):
            when = h.index[j]+STEP*4
            i = f.index.get_indexer([when])[0]
            if i < 0:
                rejected += 1; continue
            entry = float(f.open.iloc[i]); stop = float(h.low.iloc[j] if side == 1 else h.high.iloc[j])
            target = float(lo.iloc[j] if side == 1 else hi.iloc[j]); risk = side*(entry-stop)
            if risk <= 0 or side*(target-entry) <= 0 or bucket[i] == -999:
                rejected += 1; continue
            pair = f'{symbol}-{i}-{side}'
            rows.append(dict(trade_id=pair, pair_id=pair, population='signal', symbol=symbol,
                entry_i=int(i), pin_i=int(f.index.get_indexer([h.index[j]])[0]), side=side,
                entry=entry, stop=stop, target_price=target, risk=risk, level=float(level.iloc[j]),
                week=week[j], bucket=int(bucket[i]), entry_time=str(when), pin_time=str(h.index[j]),
                confirmation_close_time=str(when), segment=int(when >= pd.Timestamp(cfg['split'])),
                monday_high=float(hi.iloc[j]), monday_low=float(lo.iloc[j])))
    signals = pd.DataFrame(rows)
    detail = dict(valid_mondays=len(anchors), funnel=funnel, rejected_geometry=rejected, unmatched=0)
    if signals.empty:
        return signals, detail
    eligible = (l5.dayofweek >= 1) & (bucket != -999) & (f.index.minute == 0) & np.r_[False, np.diff(f.index.asi8) == old.STEP.value]
    pools = {}
    for i in np.flatnonzero(eligible):
        pools.setdefault((w5[i], bucket[i]), []).append(i)
    rng = np.random.default_rng(cfg['seed']); controls = []
    for r in signals.itertuples():
        pool = np.array(pools.get((r.week, r.bucket), []), dtype=int)
        pool = pool[pool != r.entry_i]
        if len(pool) < cfg['controls_per_signal']:
            detail['unmatched'] += 1; continue
        for k, i in enumerate(rng.choice(pool, cfg['controls_per_signal'], replace=False)):
            entry = float(f.open.iloc[i]); scale = entry/r.entry
            controls.append(dict(trade_id=f'{r.pair_id}-c{k}', pair_id=r.pair_id, population='random', symbol=symbol,
                entry_i=int(i), side=r.side, entry=entry, stop=entry-r.side*r.risk*scale,
                target_price=entry+(r.target_price-r.entry)*scale, risk=r.risk*scale,
                week=r.week, bucket=r.bucket, entry_time=str(f.index[i]), segment=int(f.index[i] >= pd.Timestamp(cfg['split']))))
    return pd.concat([signals, pd.DataFrame(controls)], ignore_index=True), detail


def process(job, cfg):
    symbol, paths = job
    folder = EXP / 'symbols' / symbol
    folder.mkdir(parents=True, exist_ok=True)
    identity = dict(config=cfg, inputs=[file_stamp(p) for p in paths],
                    code=[file_stamp(ROOT/p) for p in CODE], runtime=runtime_versions())
    if (folder/'done.json').exists():
        assert json.loads((folder/'identity.json').read_text()) == identity, 'Changed completed input/code'
        return json.loads((folder/'done.json').read_text())
    blocked = set()
    if symbol == 'XAUUSD':
        raw, _ = gold_clock.read_minutes(paths)
        f, q = quality.clean_minutes(raw)
        blocked = quality.invalid_monday_weeks(q)
        q['invalid_monday_weeks'] = sorted(blocked)
        dump(folder/'data-quality.json', q)
    else:
        f = old.load_crypto(paths)
        f['quality_segment'] = 0
    if not np.isfinite(f[old.OHLC]).all().all() or (f[old.OHLC] <= 0).any().any():
        raise ValueError('Invalid source OHLC')
    if (f.high < f[old.OHLC].max(axis=1)).any() or (f.low > f[old.OHLC].min(axis=1)).any():
        raise ValueError('Invalid price range')
    ledgers = []; audits = []; details = []
    for segment, chunk in f.groupby('quality_segment', sort=True):
        entries, detail = candidates(chunk, symbol, cfg, blocked)
        details.append(detail)
        if not len(entries):
            continue
        for col in ['trade_id', 'pair_id']:
            entries[col] = f'q{segment}-'+entries[col]
        dest = folder/f'segment-{segment}'
        dest.mkdir(exist_ok=True)
        entries.to_csv(dest/'candidates.csv.gz', index=False)
        rows, audit = old.execute(chunk, entries, symbol, dest, cfg)
        ledgers.append(rows); audits.append(audit)
    if ledgers:
        pd.concat(ledgers, ignore_index=True).to_csv(folder/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, bars=len(f), first=str(f.index[0]), last=str(f.index[-1]),
        signals=sum(int(x.population.eq('signal').sum()) for x in ledgers),
        positions=sum(a['positions'] for a in audits), fills=sum(a['fills'] for a in audits),
        oracle_checked=sum(a['oracle_checked'] for a in audits),
        valid_mondays=sum(d['valid_mondays'] for d in details),
        rejected_geometry=sum(d['rejected_geometry'] for d in details),
        unmatched=sum(d['unmatched'] for d in details), segments=details)
    dump(folder/'identity.json', identity); dump(folder/'done.json', done)
    return done


def summarize(cfg):
    f = pd.concat([pd.read_csv(p) for p in (EXP/'symbols').glob('*/trades.csv.gz')], ignore_index=True)
    major = f.symbol.isin(['BTCUSDT', 'ETHUSDT', 'XAUUSD'])
    sets = {'BTC': f[f.symbol.eq('BTCUSDT')], 'ETH': f[f.symbol.eq('ETHUSDT')],
            'XAUUSD': f[f.symbol.eq('XAUUSD')], 'GROUP1_BTC_ETH_GOLD': f[major], 'ALTCOINS': f[~major]}
    tables = {}; comparisons = {}
    for name, g in sets.items():
        tables[name] = {}
        for segment in ['all', 'earlier', 'later']:
            x = g if segment == 'all' else g[g.segment.eq(int(segment == 'later'))]
            tables[name][segment] = {p: old.stats(x[x.population.eq(p)]) for p in ['signal', 'random']}
        c = g.copy()
        gross = name in ['XAUUSD', 'GROUP1_BTC_ETH_GOLD']
        if gross:
            c['net_bp'] = c.gross_bp
        comparisons[name] = compare(c, cfg)
        comparisons[name]['metric'] = 'gross_bp' if gross else 'net_bp'
    done = [json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    f.to_csv(EXP/'all_trades.csv.gz', index=False)
    out = dict(config=cfg, tables=tables, comparisons=comparisons,
        by_symbol={s: old.stats(g[g.population.eq('signal')]) for s, g in f.groupby('symbol')},
        completed_symbols=len(done), signals=sum(d['signals'] for d in done),
        native_positions=sum(d['positions'] for d in done), native_fills=sum(d['fills'] for d in done),
        coverage=done, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', out)
    print(json.dumps(dict(completed=len(done), tables=tables, comparisons=comparisons)), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='*'); parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--summary', action='store_true'); args = parser.parse_args()
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    if args.summary:
        summarize(cfg); return
    selected, excluded = old.jobs(cfg)
    # Start the long spot archive alongside crypto, rather than after the pool.
    selected.sort(key=lambda j: (j[0] not in ['BTCUSDT', 'ETHUSDT', 'XAUUSD'], j[0]))
    dump(EXP/'universe.json', dict(selected=[s for s, _ in selected], excluded=excluded))
    if args.symbols:
        selected = [j for j in selected if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, j, cfg): j[0] for j in selected}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as e:
                print('FAILED '+futures[future]+': '+repr(e), flush=True); raise
    if not args.symbols:
        summarize(cfg)


if __name__ == '__main__':
    main()

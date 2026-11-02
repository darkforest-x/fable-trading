"""One-variable EMA20/50 confirmation study on the frozen H1 glyph universe.

Only new delayed entries go through the existing native VectorBT execution
adapter. Original entry results are read from the immutable prior ledger.
All EMA features use current/past closed 5m closes, reset at gaps, and need
50 continuous bars. ATR matching uses the latest fully closed H1 ATR14.
The 24h setup lifetime is fixed before results, not an optimized parameter.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import warnings

import numpy as np
import pandas as pd

from yoyo.evaluation.h1_outside_glyph_signals import prepare
from yoyo.evaluation.h1_outside_glyph_study import (
    ROOT, STEP, compare, describe, dump, execute, stamp,
)
from yoyo.evaluation.monday_15m_volume_rejection import load

EXP = ROOT / 'experiments/active/exp-h1-ema-cloud-20261102-v1'
BASE = ROOT / 'experiments/active/exp-h1-outside-glyph-stops-20261102-v1'


def cloud_features(f, fast=20, slow=50):
    """Close EMA20/50 and new crossings; no high/low or future input.

    EMA uses alpha=2/(period+1), seeded with the first segment close, as in
    the inspected Pine script. Fifty continuous 5m bars warm up both lines.
    Values belong to bar close (index+5m), never to the indexed bar open.
    """
    out = pd.DataFrame(index=f.index, columns=['ema_fast', 'ema_slow'], dtype=float)
    out['flip_long'] = False
    out['flip_short'] = False
    group = (~f.index.to_series().diff().eq(STEP)).cumsum().to_numpy()
    for _, part in f.groupby(group, sort=False):
        a = part.close.ewm(span=fast, adjust=False).mean()
        b = part.close.ewm(span=slow, adjust=False).mean()
        ready = np.arange(len(part)) >= slow - 1
        out.loc[part.index, 'ema_fast'] = a.where(ready)
        out.loc[part.index, 'ema_slow'] = b.where(ready)
        out.loc[part.index, 'flip_long'] = ((a > b) & (a.shift(1) <= b.shift(1)) & ready)
        out.loc[part.index, 'flip_short'] = ((a < b) & (a.shift(1) >= b.shift(1)) & ready)
    return out


def schedule(f, base_signal_rows, cloud, cfg):
    """Arm each frozen H1 event and locate its first newly closed cloud flip.

    Same-time H1/5m close confirmation is permitted; old cloud state is not.
    Target touches in [H1 confirmation, cloud confirmation) cancel the setup,
    including the confirming candle's high/low. No pending setup crosses a
    missing 5m bar. No position exists while pending: fixed percentage stops
    are not applied before entry. Original common wick geometry is rechecked
    against the actual entry, without looking at the entry bar's high/low.
    """
    rows = []
    n = len(f)
    times = f.index
    gaps = np.r_[np.flatnonzero(np.diff(times.as_unit('ns').asi8) != STEP.value), n-1]
    flips = {1: np.flatnonzero(cloud.flip_long.to_numpy()),
             -1: np.flatnonzero(cloud.flip_short.to_numpy())}
    opens, highs, lows = (f[x].to_numpy(float) for x in ('open', 'high', 'low'))
    for base in base_signal_rows.itertuples(index=False):
        start = int(base.entry_i)
        side = int(base.side)
        armed = pd.Timestamp(base.entry_time)
        assert times[start] == armed
        assert start > 0 and times[start-1] + STEP == armed
        deadline = armed + pd.Timedelta(hours=cfg['wait_hours'])
        # The available contiguous segment may end before the nominal TTL.
        boundary = int(gaps[np.searchsorted(gaps, start-1)])
        positions = flips[side]
        k = int(np.searchsorted(positions, start-1))
        confirm = int(positions[k]) if k < len(positions) else n
        in_time = confirm < n and times[confirm] + STEP <= deadline
        confirm = confirm if in_time else n
        # Check only candles observable before candidate entry / TTL / gap.
        deadline_last = int(times.searchsorted(deadline, side='left')) - 1
        last = min(confirm, boundary, deadline_last)
        touched = False
        if last >= start:
            touched = bool(np.any(highs[start:last+1] >= base.target_price) if side == 1
                           else np.any(lows[start:last+1] <= base.target_price))
        item = dict(pair_id=base.pair_id, signal_time=str(armed), side=side,
                    status='', entry_i=-1, confirm_i=-1, wait_minutes=np.nan)
        if touched:
            item['status'] = 'target_before_flip'
        elif confirm < n and confirm < boundary:
            entry_i = confirm + 1
            px = opens[entry_i]
            extreme = base.signal_low if side == 1 else base.signal_high
            stop = extreme - side * px * cfg['wick_buffer']
            item.update(entry_i=entry_i, confirm_i=confirm,
                        wait_minutes=(times[entry_i]-armed).total_seconds()/60)
            item['status'] = ('entered' if side*(base.target_price-px) > 0 and
                              side*(px-stop) > 0 else 'invalid_geometry')
        elif times[boundary] + STEP <= deadline:
            item['status'] = 'wait_censored'
        else:
            item['status'] = 'no_flip_24h'
        rows.append(item)
    return pd.DataFrame(rows)


def random_pools(f, h, cloud):
    """Eligible 5m opens matched on month and causal H1 ATR14/close log bucket.

    H1 data are mapped only after their close and for <1h thereafter, so a
    missing hour cannot supply stale volatility. Pool requires a completed
    Monday anchor, positive preceding H1 volume and warmed-up EMA history.
    No cloud crossing/location condition is imposed on random controls.
    """
    known = h.index + pd.Timedelta(hours=1)
    at = known.searchsorted(f.index, side='right')-1
    safe = np.maximum(at, 0)
    atr = h.atr14.to_numpy()[safe]
    close = h.close.to_numpy()[safe]
    bucket = np.full(len(f), -999, dtype=int)
    good = (at >= 0) & np.isfinite(atr) & (atr > 0) & (close > 0)
    good &= (f.index.as_unit('ns').asi8-known.as_unit('ns').asi8[safe] < pd.Timedelta(hours=1).value)
    bucket[good] = np.floor(4*np.log2(atr[good]/close[good])).astype(int)
    valid = good & h.monday_high.notna().to_numpy()[safe] & (h.volume.to_numpy()[safe] > 0)
    valid &= cloud.ema_slow.shift(1).notna().to_numpy()
    month = f.index.strftime('%Y-%m').to_numpy()
    pool = pd.DataFrame(dict(i=np.flatnonzero(valid), month=month[valid], bucket=bucket[valid]))
    pools = {key: part.i.to_numpy(int) for key, part in pool.groupby(['month', 'bucket'], sort=False)}
    return pools, month, bucket


def candidates(f, h, cloud, baseline, scheduled, cfg):
    """Reprice unchanged stop formulas at new opens and generate matched controls."""
    accepted = scheduled[scheduled.status.eq('entered')].set_index('pair_id')
    if accepted.empty:
        return pd.DataFrame(), dict(unmatched=0)
    pools, months, buckets = random_pools(f, h, cloud)
    rng = np.random.default_rng(cfg['seed'])
    rows = []
    unmatched = 0
    source = baseline.drop_duplicates('pair_id').set_index('pair_id')
    original_rr = baseline.set_index(['pair_id', 'stop_id']).planned_rr
    for pair, item in accepted.iterrows():
        b = source.loc[pair]
        i = int(item.entry_i)
        side = int(b.side)
        entry = float(f.open.iloc[i])
        extreme = float(b.signal_low if side == 1 else b.signal_high)
        wick_stop = extreme-side*entry*cfg['wick_buffer']
        target = float(b.target_price)
        pool = pools.get((months[i], int(buckets[i])), np.empty(0, dtype=int))
        pool = pool[pool != i]
        choices = rng.choice(pool, min(cfg['controls'], len(pool)), replace=False)
        if len(choices) < cfg['controls']:
            unmatched += 1
        for stop_id, pct in cfg['stops'].items():
            distance = side*(entry-wick_stop)/entry if pct is None else pct
            reward = side*(target-entry)/entry
            for number, ix in enumerate([i, *choices]):
                ix = int(ix)
                px = float(f.open.iloc[ix])
                rows.append(dict(pair_id=pair, trade_key=f'{pair}:{stop_id}:cloud:{number}',
                    symbol=b.symbol, population='signal' if number == 0 else 'random', stop_id=stop_id,
                    entry_i=ix, entry_time=str(f.index[ix]), signal_time=b.signal_time,
                    cloud_confirm_time=str(f.index[int(item.confirm_i)]+STEP),
                    side=side, entry=px, stop=px-side*px*distance,
                    target_price=px+side*px*reward, risk=px*distance,
                    planned_rr=reward/distance, stop_pct=distance*100,
                    wait_minutes=float(item.wait_minutes), month=months[ix], bucket=int(buckets[ix]),
                    source_entry=float(b.entry), source_entry_time=b.entry_time,
                    source_planned_rr=float(original_rr.loc[(pair, stop_id)]),
                    ema_fast=float(cloud.ema_fast.iloc[int(item.confirm_i)]),
                    ema_slow=float(cloud.ema_slow.iloc[int(item.confirm_i)])))
    return pd.DataFrame(rows), dict(unmatched=unmatched)


def process(job, cfg):
    warnings.filterwarnings('ignore', category=FutureWarning)
    symbol, paths = job['symbol'], job['inputs']
    folder = EXP/'symbols'/symbol
    folder.mkdir(parents=True, exist_ok=True)
    old = BASE/'symbols'/symbol
    receipt = dict(inputs=[stamp(Path(p)) for p in paths], config=cfg,
                   baseline=stamp(old/'trades.csv.gz') if (old/'trades.csv.gz').exists() else None)
    if (folder/'done.json').exists():
        assert json.loads((folder/'identity.json').read_text()) == receipt
        return json.loads((folder/'done.json').read_text())
    # Reject modified input; no hashes or historical data rewriting.
    assert receipt['inputs'] == json.loads((old/'identity.json').read_text())['inputs']
    coverage = json.loads((old/'done.json').read_text())
    details = dict(symbol=symbol, baseline_signals=coverage['signals'], positions=0,
                   accepted=0, statuses={}, prefix_checks=[], unmatched=0)
    if receipt['baseline'] is not None:
        base = pd.read_csv(old/'trades.csv.gz')
        base = base[base.population.eq('signal')]
        f = load(paths)
        # Verify exact old entry identity, rather than silently rebuilding signals.
        check = base.drop_duplicates('pair_id')
        assert (f.index[check.entry_i.to_numpy(int)] == pd.to_datetime(check.entry_time, utc=True)).all()
        np.testing.assert_allclose(f.open.iloc[check.entry_i].to_numpy(), check.entry, rtol=1e-13)
        cloud = cloud_features(f, cfg['ema_fast'], cfg['ema_slow'])
        planned = schedule(f, check, cloud, cfg)
        planned.to_csv(folder/'schedule.csv.gz', index=False)
        h = prepare(f)
        e, more = candidates(f, h, cloud, base, planned, cfg)
        ledger, fills = execute(f, e, cfg)
        if len(ledger):
            ledger.to_csv(folder/'trades.csv.gz', index=False)
            fills.to_csv(folder/'fills.csv.gz', index=False)
        if symbol in ('BTCUSDT', 'ETHUSDT', 'LPTUSDT'):
            cut = len(f)//2
            short = cloud_features(f.iloc[:cut], cfg['ema_fast'], cfg['ema_slow'])
            pd.testing.assert_frame_equal(short, cloud.iloc[:cut])
            details['prefix_checks'].append(dict(source_rows=cut, matched_rows=len(short)))
        details.update(positions=len(ledger), accepted=int(planned.status.eq('entered').sum()),
                       statuses=planned.status.value_counts().to_dict(), **more)
    assert receipt['inputs'] == [stamp(Path(p)) for p in paths]
    dump(folder/'identity.json', receipt)
    dump(folder/'done.json', details)
    return details


def periodize(t, split):
    t = t.copy()
    entry = pd.to_datetime(t.entry_time, utc=True)
    exits = pd.to_datetime(t.exit_time, utc=True)
    split = pd.Timestamp(split)
    t['period'] = np.where(entry >= split, 'later', np.where(exits < split, 'earlier', 'cross_split'))
    return t


def paired_change(new, old):
    q = new.set_index('pair_id')[['net_r', 'net_bp', 'planned_rr']].join(
        old.set_index('pair_id')[['net_r', 'net_bp', 'planned_rr']], rsuffix='_old', how='inner')
    if q.empty:
        return dict(n=0)
    return dict(n=len(q), mean_delta_net_r=float((q.net_r-q.net_r_old).mean()),
                mean_delta_net_bp=float((q.net_bp-q.net_bp_old).mean()),
                median_planned_rr_change=float((q.planned_rr-q.planned_rr_old).median()),
                improved_pct=float((q.net_r > q.net_r_old).mean()*100))


def analyze(cfg):
    done = [json.loads(p.read_text()) for p in sorted((EXP/'symbols').glob('*/done.json'))]
    new = pd.concat([pd.read_csv(p) for p in sorted((EXP/'symbols').glob('*/trades.csv.gz'))], ignore_index=True)
    new = periodize(new, cfg['split'])
    new.to_csv(EXP/'trades.csv.gz', index=False)
    old = periodize(pd.read_csv(BASE/'trades.csv.gz'), cfg['split'])
    scheduled = pd.concat([pd.read_csv(p) for p in sorted((EXP/'symbols').glob('*/schedule.csv.gz'))], ignore_index=True)
    scheduled.to_csv(EXP/'schedule.csv.gz', index=False)
    rows, changes = [], []
    for period in ('all', 'earlier', 'later'):
        a = new[~new.censored]
        b = old[~old.censored]
        if period != 'all':
            a = a[a.period.eq(period)]
            b = b[b.period.eq(period)]
        for stop in cfg['stops']:
            x, y = a[a.stop_id.eq(stop)], b[b.stop_id.eq(stop)]
            filled_pairs = new.loc[new.stop_id.eq(stop) & new.population.eq('signal'), 'pair_id']
            for label, data in [('baseline_all', y), ('baseline_filled_subset', y[y.pair_id.isin(filled_pairs)]), ('cloud_flip', x)]:
                sig, ctl = data[data.population.eq('signal')], data[data.population.eq('random')]
                row = dict(period=period, stop_id=stop, method=label, **describe(sig), **compare(sig, ctl))
                if label == 'cloud_flip' and len(sig):
                    row.update(median_wait_minutes=float(sig.wait_minutes.median()),
                               planned_rr3_pct=float((sig.planned_rr >= 3).mean()*100))
                rows.append(row)
            changes.append(dict(period=period, stop_id=stop,
                **paired_change(x[x.population.eq('signal')], y[y.population.eq('signal')])))
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'metrics.csv', index=False)
    pd.DataFrame(changes).to_csv(EXP/'paired_changes.csv', index=False)
    signal = new[new.population.eq('signal') & ~new.censored]
    for field, keys in [('by_symbol', ['symbol', 'stop_id']), ('by_side', ['side', 'stop_id']),
                        ('by_year', ['year', 'stop_id'])]:
        signal = signal.assign(year=pd.to_datetime(signal.entry_time, utc=True).dt.year)
        breakdown = [dict(zip(keys, k), **describe(q)) for k, q in signal.groupby(keys)]
        pd.DataFrame(breakdown).to_csv(EXP/(field+'.csv'), index=False)
    statuses = scheduled.status.value_counts().to_dict()
    primary = table[(table.stop_id == cfg['primary_stop']) & (table.period == 'later')]
    result = dict(experiment_id=cfg['experiment_id'], symbols=len(done),
        baseline_signals=int(sum(d['baseline_signals'] for d in done)),
        accepted_signals=int(sum(d['accepted'] for d in done)), statuses=statuses,
        acceptance_pct=float(scheduled.status.eq('entered').mean()*100),
        native_positions=len(new), primary_stop=cfg['primary_stop'],
        censored_signal_positions=int((new.population.eq('signal') & new.censored).sum()),
        primary_later=primary.astype(object).where(pd.notna(primary), None).to_dict('records'),
        baseline_not_replayed=True, source_commit=json.loads((EXP/'run.json').read_text())['builder_commit'],
        versions={x: version(x) for x in ['vectorbt','pandas','numpy','numba']},
        training_eligible=False, production_eligible=False, limitations=cfg['limitations'])
    dump(EXP/'summary.json', result)
    print(table[table.period.eq('later')].to_string(index=False), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--analyze-only', action='store_true')
    args = p.parse_args()
    cfg = json.loads((EXP/'config.json').read_text())
    if not args.analyze_only:
        if not (EXP/'run.json').exists():
            dump(EXP/'run.json', dict(builder_commit=subprocess.check_output(
                ['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                started_at=pd.Timestamp.now(tz='UTC'), reproduction=cfg['reproduction']))
        work = json.loads((BASE/'universe.json').read_text())
        dump(EXP/'universe.json', work)
        failed = []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process, job, cfg): job['symbol'] for job in work}
            for k, future in enumerate(as_completed(futures), 1):
                symbol = futures[future]
                try:
                    r = future.result()
                    print(k, len(work), symbol, r['accepted'], r['positions'], flush=True)
                except Exception as exc:
                    failed.append(dict(symbol=symbol, error=repr(exc)))
                    print('ERROR', symbol, repr(exc), flush=True)
        dump(EXP/'errors.json', failed)
        if failed:
            raise RuntimeError(f'{len(failed)} symbols failed; results retained')
    analyze(cfg)


if __name__ == '__main__':
    main()

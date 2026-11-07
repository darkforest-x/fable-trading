"""Check Capital41's 5m relative-volume gate on frozen live- candidates and native fills."""
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import live_ema_cloud_filter as accounts
from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT / "experiments/active/exp-live-capital41-volume-20261107-v1"
STEP = pd.Timedelta(minutes=5)


def tag_symbol(args):
    """At each decision use 50 completed 5m OHLCV bars, including the last; levels exclude it."""
    symbol, entries, october = args
    job = json.loads((CODEX / 'symbols' / symbol / 'identity.json').read_text())['job']
    if job['execution_minutes'] != 5:
        raise ValueError(f'{symbol}: expected a frozen native 5m series')
    extra = october / symbol / 'new_bars.csv.gz'
    if extra.exists() and max(r[1] for r in entries) >= pd.Timestamp('2026-09-23', tz='UTC'):
        job = dict(job, paths=[*job['paths'], str(extra)])
    frame, _ = load_job(job)
    rows = []
    for event, decision, side in entries:
        row = dict(event=event, decision=decision, side=side, symbol=symbol)
        bars = frame.loc[decision-50*STEP:decision-STEP]
        row['bars'] = len(bars)
        if len(bars) != 50 or not bars.index.equals(pd.date_range(decision-50*STEP, periods=50, freq=STEP)):
            rows.append(row)
            continue
        last, prior = bars.iloc[-1], bars.iloc[-21:-1]
        prior_low, prior_high = prior.low.min(), prior.high.max()
        reclaim = (last.low < prior_low and last.close >= prior_low if side == 1
                   else last.high > prior_high and last.close <= prior_high)
        vol_mean = bars.volume.mean()
        ratio = last.volume / vol_mean if vol_mean > 0 else 0.
        tr = pd.concat([bars.high-bars.low, (bars.high-bars.close.shift()).abs(),
                        (bars.low-bars.close.shift()).abs()], axis=1).max(axis=1)
        vol = tr.iloc[-15:-1].mean() / bars.close.iloc[-2]
        rows.append(dict(row, last_open=bars.index[-1], current_volume=last.volume, sma50=vol_mean,
                         ratio=ratio, high_volume=ratio >= 1.2, reclaim=reclaim, sweep=reclaim and ratio >= 1.2,
                         close=last.close, prior_low=prior_low, prior_high=prior_high,
                         vol_bucket=int(np.floor(2*np.log2(vol))) if vol > 0 else -999))
    return rows


def returns(rows):
    return dict(n=len(rows), mean_net_bp=float(rows.net.mean()) if len(rows) else None,
                median_net_bp=float(rows.net.median()) if len(rows) else None,
                wins=int((rows.net > 0).sum()))


def summarize(x, spec, start, saved, cfg):
    """Run archived allocation rules on selected native trades; draw conditional equal-count candidate subsets."""
    base = accounts.replay(x, spec, start)
    if abs(base['end']-saved['end']) > 1e-6 or x.duplicated('event').any():
        raise ValueError('Unfiltered allocation or unique event parity failed')
    known = x.ratio.notna()
    keep = known & (x.ratio >= 1.2)
    chosen = accounts.replay(x[keep].reset_index(drop=True), spec, start)
    stats = {}
    for period, mask in [('all', np.ones(len(x), bool)), *[(str(y), x.entry_t.dt.year == y) for y in sorted(set(x.entry_t.dt.year))]]:
        stats[period] = {k: returns(x[m & mask]) for k, m in [('baseline', known), ('pass', keep), ('fail', known & ~keep)]}
    metric = 'trading_pnl' if spec['kind'] == 'allin' else 'end'
    rng = np.random.default_rng(cfg['seed'])
    pool = x[known].reset_index(drop=True)
    selected = pool.ratio.to_numpy() >= 1.2
    # Within-week/side/volatility controls, drawn from originally admissible symbols.
    strata = pool.groupby([pool.event.str.split(':').str[1], pool.side, pool.vol_bucket]).indices
    variable, fixed = [], np.zeros(len(pool), dtype=bool)
    for idx in strata.values():
        n = int(selected[idx].sum())
        if n == len(idx):
            fixed[idx] = True
        elif n:
            variable.append((idx, n))
    values = []
    for _ in range(cfg['control_draws']):
        draw = fixed.copy()
        for idx, n in variable:
            draw[rng.choice(idx, n, replace=False)] = True
        values.append(accounts.replay(pool[draw].reset_index(drop=True), spec, start)[metric])
    return dict(base=base, selected=chosen, missing=int((~known).sum()), same_direction_full_sweep=int(x.sweep.eq(True).sum()),
                price_reclaim_before_volume=int(x.reclaim.eq(True).sum()), per_candidate=stats, control=dict(
                    metric=metric, movable_candidates=sum(len(idx) for idx, _ in variable), movable_selected=sum(n for _, n in variable),
                    fixed_selected=int(fixed.sum()), median=float(np.median(values)), p05=float(np.quantile(values, .05)),
                    p95=float(np.quantile(values, .95)), p_ge=(1+int((np.array(values)>=chosen[metric]).sum()))/(len(values)+1)))


def run(workers=5):
    cfg = json.loads((EXP/'config.json').read_text())
    accounts.cr.committed('yoyo/evaluation/live_capital41_volume.py', str((EXP/'config.json').relative_to(ROOT)))
    src = ROOT/cfg['accounts']
    parent = json.loads(src.read_text())
    old = pd.read_csv(src.parent/'candidates.csv.gz', parse_dates=['entry_t'])
    old['decision'] = old.entry_t.where(old.account != 'live-allin3', old.entry_t-STEP)
    jobs = [(s, list(g[['event','decision','side']].drop_duplicates().itertuples(index=False, name=None)),
             ROOT/parent['october_bars']) for s,g in old.groupby('symbol')]
    blocks = []
    with ProcessPoolExecutor(workers) as ex:
        for i, block in enumerate(ex.map(tag_symbol, jobs), 1):
            blocks.extend(block)
            if i % 80 == 0:
                print(f'{i}/{len(jobs)} symbols', flush=True)
    tags = pd.DataFrame(blocks)
    if tags.duplicated(['event','decision']).any():
        raise ValueError('duplicate tags')
    tags.to_csv(EXP/'tags.csv.gz', index=False)
    specs = accounts.candidates(parent, workers)
    results = {}
    for (name, start), (spec, x, saved) in specs.items():
        key = f'{name}|{start}'
        x['decision'] = pd.to_datetime(x.decision_ns, unit='ns', utc=True)
        x = x.merge(tags, on=['event', 'decision', 'symbol', 'side'], validate='one_to_one', how='left')
        check = old[(old.account == name) & (old.start == start)].set_index('event')
        if len(check) != len(x) or x.bars.isna().any() or not np.allclose(x.net, check.loc[x.event, 'net'], atol=1e-8, rtol=0):
            raise ValueError(f'{key}: frozen candidate mismatch')
        results[key] = summarize(x, spec, start, saved, cfg)
        print(key, 'complete', flush=True)
    dump(EXP/'summary.json', dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  source_config=cfg, results=results))


if __name__ == '__main__':
    run()

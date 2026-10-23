"""Prepare the requested altcoin N5 HTML evidence from frozen framework fills.

No trade execution or strategy selection occurs here. OHLC is used only for
retrospective excursions and seeded illustrations, never to change admission,
entry, exit, costs or the unresolved status of an original trade. Sources are
the registered case4-n5-v20 ledger and existing Binance five-minute archives.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation.monday_body_hold import excursions
from yoyo.evaluation.monday_cases_report_data import compact_bars, records
from yoyo.evaluation.monday_report_update import query

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/active/exp-monday-vectorbt-parameters-20261023-v1/alt-case4-n5-detail-20261024'
OUT = ROOT / 'artifacts/monday-alt-n5-report-20261024'
APP = ROOT / 'reports/monday-1618-cases-20261023'
SEED = 20261024


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def sample_rows(frame):
    """Three uniform draws in each direction/outcome stratum, before OHLC reads."""
    rng = np.random.default_rng(SEED)
    chosen = []
    closed = frame[frame.closed].sort_values('trade_id')
    for side in (1, -1):
        for win in (True, False):
            pool = closed[(closed.side == side) & (closed.net_r.gt(0) == win)]
            for index in rng.choice(pool.index, min(3, len(pool)), replace=False):
                row = pool.loc[index].to_dict()
                row.update(sample_id=f'alt-n5-{len(chosen)+1:02}', sampling_pool=len(pool))
                chosen.append(row)
    return pd.DataFrame(chosen)


def coin_evidence(job):
    symbol, paths, trade_rows, picked_rows = job
    cache = OUT / 'coins' / f'{symbol}.json'
    stamps = [dict(path=str(p), bytes=Path(p).stat().st_size, mtime_ns=Path(p).stat().st_mtime_ns) for p in paths]
    if cache.exists():
        saved = json.loads(cache.read_text())
        if saved['source_stamps'] != stamps:
            raise RuntimeError(f'Changed input for completed evidence: {symbol}')
        return saved
    f = volume.load(paths)
    g = pd.DataFrame(trade_rows)
    g = g[g.closed].copy()
    g['entry_i'] = f.index.get_indexer(pd.to_datetime(g.entry_time, utc=True))
    g['exit_original_i'] = f.index.get_indexer(pd.to_datetime(g.exit_interval_start, utc=True))
    if (g[['entry_i', 'exit_original_i']] < 0).any().any():
        raise ValueError(f'Missing original fill interval: {symbol}')
    np.testing.assert_allclose(f.open.iloc[g.entry_i].to_numpy(), g.entry, rtol=1e-10)
    audits = excursions(f, g)
    # The shared diagnostic appends these names; keep the original fields and
    # explicitly choose only new diagnostics to avoid duplicate JSON keys.
    new_names = ['mfe_r', 'mae_r', 'mfe_bp', 'mfe_net_taker_r', 'mfe_before_exit_bar_r',
                 'mfe_time', 'mae_time', 'holding_hours', 'exit_phase_audited', 'exit_at_open']
    audit_rows = records(audits[['trade_id', *new_names]])
    samples = []
    for r in picked_rows:
        pin, entry, end = map(pd.Timestamp, [r['pin_time'], r['entry_time'], r['exit_interval_end']])
        start = pd.Timestamp(r['week']).tz_localize('America/New_York').tz_convert('UTC')
        monday = f.loc[start:start + pd.Timedelta(days=1) - pd.Timedelta(minutes=5)]
        assert len(monday) == 288
        hi, lo = float(monday.high.max()), float(monday.low.min())
        level = lo - .618*(hi-lo) if r['side'] == 1 else hi + .618*(hi-lo)
        np.testing.assert_allclose([level, lo if r['side'] == 1 else hi], [r['level'], r['target_price']], rtol=1e-9)
        step = pd.Timedelta(hours=4)
        window = f.loc[pin-step*24:max(end+step*8, entry+step*24)]
        h = window.resample('4h').agg(dict(open='first', high='max', low='min', close='last', volume='sum')).dropna()
        assert entry == pin + step*6
        np.testing.assert_allclose(h.loc[pin, 'low' if r['side'] == 1 else 'high'], r['stop'], rtol=1e-9)
        checks = []
        for k in range(1, 6):
            t = pin + step*k
            b = h.loc[t]
            assert (b.close >= r['level'] if r['side'] == 1 else b.close <= r['level'])
            checks.append(dict(number=k, open_time=str(t), close_time=str(t+step),
                               **{c: float(b[c]) for c in old.OHLC}, passed=True))
        s = dict(r, case=4, timeframe_minutes=240, confirmation_count=5,
                 monday_high=hi, monday_low=lo, direction='多' if r['side'] == 1 else '空',
                 outcome='盈利' if r['net_r'] > 0 else '亏损', target=r['target_price'],
                 risk_pct=r['risk_bp']/100, holding_h=r['holding_h_upper'], ambiguous=bool(r['ambiguous_bar']),
                 detail_bars=compact_bars(h.loc[pin-step*24:entry+step*24]),
                 full_bars=compact_bars(h.loc[pin-step*24:end+step*8]), checks_json=json.dumps(checks))
        samples.extend(records(pd.DataFrame([s])))
    result = dict(symbol=symbol, source_stamps=stamps, excursions=audit_rows, samples=samples)
    dump(cache, result)
    return result


def weekly_intervals(g):
    """Descriptive cluster bootstrap of explored history, not a fresh test."""
    r = g[g.closed].copy()
    r['positive'] = r.net_r.gt(0).astype(int)
    blocks = r.groupby('actual_entry_week').agg(total=('net_r', 'sum'), n=('net_r', 'size'), wins=('positive', 'sum'))
    x = blocks.to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws = x[rng.integers(0, len(x), size=(2000, len(x)))].sum(axis=1)
    means, wins = draws[:, 0]/draws[:, 1], draws[:, 2]/draws[:, 1]
    return dict(weeks=len(x), mean_r_low=float(np.quantile(means, .025)), mean_r_high=float(np.quantile(means, .975)),
                win_low=float(np.quantile(wins, .025)), win_high=float(np.quantile(wins, .975)))


def assemble(ledger, evidence):
    extra = pd.DataFrame([r for e in evidence for r in e['excursions']])
    assert len(extra) == 9041 and not extra.trade_id.duplicated().any()
    complete = ledger.merge(extra, on='trade_id', how='left', validate='one_to_one')
    assert len(complete) == 9065 and complete.loc[complete.closed, 'mfe_r'].notna().all()
    assert complete.loc[~complete.closed, 'mfe_r'].isna().all()
    assert (complete.loc[complete.closed, 'mfe_r'] + 1e-8 >= complete.loc[complete.closed, 'mfe_before_exit_bar_r']).all()
    complete.to_csv(OUT / '全部9065笔含浮盈浮亏.csv', index=False, encoding='utf-8-sig')
    stats = pd.read_csv(SOURCE / '分组统计.csv')
    additions = []
    for period in ('all', 'before2023', 'from2023'):
        g = complete if period == 'all' else complete[(complete.segment == int(period == 'from2023')) & ~complete.crosses_selection_cut]
        c = g[g.closed]; sl = c[c.reason == 1]
        additions.append(dict(period=period, **weekly_intervals(g), closed=len(c), stops=len(sl),
            sl_after_positive_mfe=int(sl.mfe_r.gt(1e-10).sum()),
            sl_after_positive_net_mfe=int(sl.mfe_net_taker_r.gt(1e-10).sum()),
            sl_after_half_r=int(sl.mfe_r.ge(.5).sum()), sl_after_one_r=int(sl.mfe_r.ge(1).sum()),
            sl_after_one_r_prior_bar=int(sl.mfe_before_exit_bar_r.ge(1).sum()),
            median_mfe_r=float(c.mfe_r.median()), median_mae_r=float(c.mae_r.median())))
    pd.DataFrame(additions).to_csv(OUT / '浮盈回吐与周聚类区间.csv', index=False, encoding='utf-8-sig')
    distributions = []
    for name, edges in [('planned_rr', [-np.inf, .25, .5, 1, 2, np.inf]),
                        ('net_r', [-np.inf, -1.1, -1, 0, .25, .5, 1, 2, np.inf])]:
        c = complete[complete.closed]
        bins = pd.cut(c[name], edges, right=False)
        for i, (interval, g) in enumerate(c.groupby(bins, observed=False)):
            distributions.append(dict(metric=name, bin=str(interval), order=i, n=len(g), mean_net_r=float(g.net_r.mean()) if len(g) else None))
    samples = sorted([s for e in evidence for s in e['samples']], key=lambda s:s['sample_id'])
    assert len(samples) == 12
    dump(OUT / 'samples.json', samples)
    from yoyo.evaluation.monday_cases_report_charts import draw
    dest = APP / 'src/content/assets/alt-n5'
    dest.mkdir(parents=True, exist_ok=True)
    imports, pairs = [], []
    for s in samples:
        for mode in ('detail', 'full'):
            name = f"{s['sample_id']}-{mode}"
            path = OUT / draw(s, mode, OUT)
            shutil.copy2(path, dest / path.name)
            variable = f'image{len(imports)}'
            imports.append(f'import {variable} from "../assets/alt-n5/{path.name}";')
            pairs.append(f'"{name}":{variable}')
    (APP / 'src/content/report/alt-n5-images.js').write_text('\n'.join(imports)+'\nexport const altN5Images={'+','.join(pairs)+'};\n')
    queries = {
        'alt-n5-stats': query(records(stats), [SOURCE / '分组统计.csv'], 'Frozen N5 periods, directions and calendar cohorts'),
        'alt-n5-ledger': query(records(complete), [SOURCE / '全部9065笔交易.csv', OUT / '全部9065笔含浮盈浮亏.csv'], 'All9065 original signals, including24 unresolved'),
        'alt-n5-excursions': query(additions, [OUT / '浮盈回吐与周聚类区间.csv'], 'Post-exit descriptive excursions and2000 NY-week cluster bootstrap draws'),
        'alt-n5-distributions': query(distributions, [SOURCE / '全部9065笔交易.csv'], 'Descriptive bins; not an optimized admission filter'),
        'alt-n5-quantiles': query(records(pd.read_csv(SOURCE / '分布与持仓时间.csv')), [SOURCE / '分布与持仓时间.csv'], 'Frozen distribution quantiles'),
        'alt-n5-streaks': query(records(pd.read_csv(SOURCE / '连续止损及连亏区间.csv')), [SOURCE / '连续止损及连亏区间.csv'], 'Exit-order versus per-coin streaks'),
        'alt-n5-samples': query([{k:v for k,v in s.items() if k not in ('detail_bars','full_bars')} for s in samples], [OUT / 'samples.json'], 'Seed20261024: three draws per direction and outcome stratum'),
    }
    dump(OUT / 'reviewed-queries.json', queries)
    receipt = dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(), no_engine_replay=True,
        builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, cwd=ROOT).strip(),
        signals=len(complete), closed=int(complete.closed.sum()), unresolved=int((~complete.closed).sum()),
        symbols=len(evidence), samples=len(samples), images=24, seed=SEED,
        source=str(SOURCE.relative_to(ROOT)), validation='Original IDs, outcomes, PnL, costs and unresolved status retained;60confirmation candles verified',
        limitations=['MFE is descriptive under frozen OHLC path, not a simulated partial/BE strategy.',
                     'Exit-bar path uncertainty exposed by completed-prior-bar MFE.',
                     'Weekly bootstrap describes already-explored history, not untouched validation.'])
    dump(OUT / 'receipt.json', receipt)
    print(json.dumps(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    subprocess.run(['git', 'ls-files', '--error-unmatch', str(Path(__file__).relative_to(ROOT))], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', __file__], cwd=ROOT, check=True)
    (OUT / 'coins').mkdir(parents=True, exist_ok=True)
    ledger = pd.read_csv(SOURCE / '全部9065笔交易.csv', float_precision='round_trip')
    picked = sample_rows(ledger)
    dump(OUT / 'sample-selection.json', records(picked[['sample_id', 'trade_id', 'symbol', 'side', 'outcome_cn', 'sampling_pool']]))
    # Use the registered per-symbol input receipts. Enumerating every external
    # directory and probing all tail paths is unnecessary and can stall on a
    # busy removable disk. Keep using the original repository symlink paths.
    paths = {}
    for symbol in ledger.symbol.unique():
        receipt = SOURCE.parent / 'symbols' / symbol / 'identity.json'
        inputs = json.loads(receipt.read_text())['inputs']
        paths[symbol] = [str(ROOT / p['path'].split('/fable-trading/', 1)[1]) for p in inputs]
    jobs = [(symbol, paths[symbol], records(g), records(picked[picked.symbol == symbol])) for symbol, g in ledger.groupby('symbol')]
    evidence = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(coin_evidence, job) for job in jobs]
        for result in as_completed(futures):
            evidence.append(result.result())
            if len(evidence) % 20 == 0:
                print(f'Excursion evidence {len(evidence)}/{len(jobs)} symbols', flush=True)
    assemble(ledger, evidence)


if __name__ == '__main__':
    main()

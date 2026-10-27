"""Export missing account paths from existing native fills and existing sizing code.

No candles are replayed and no entry/exit decisions or costs are changed. The
original capital allocators emit a trace through optional observers. Every
series is one deterministic seed, not a splice of independent period medians.
MAE for the original liquidation scenario is read with the original extractor.
Inputs use only the saved fields/window of each registered historical study.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_limit_return_opt as capital
from yoyo.evaluation import monday_limit_spike_opt as source
from yoyo.evaluation import monday_limit_reclaim_allexits as five
from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
from yoyo.evaluation.monday_limit_spike_hw import slot_replay
from yoyo.evaluation.monday_limit_langlang import path_run
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, RUN

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / 'experiments/active'
OUT = A / 'exp-strategy-booklet-paths-20261027-v1'


def read(name):
    return json.loads((A / name).read_text())


def dated(t):
    t = t.copy()
    for k in ['entry_t', 'exit_t']:
        t[k] = pd.to_datetime(t[k], utc=True)
        t[k.replace('_t', '_ns')] = t[k].astype('int64')
    return t


class Trace:
    """Observe funded realized events; cash additions are distinct from P&L."""
    def __init__(self, start):
        self.rows = [dict(time_ms=int(start)//1_000_000, equity=1000., pnl=0., cashflow=0., stake_budget=1000., notional=0.)]

    def __call__(self, row, ts, equity, pnl, notional, cashflow=0., stake_budget=0.):
        event = str(row.get('event', row.get('trade_id', row.get('tid', row.name))))
        entry = row.get('entry_ns', row.get('entry', ts))
        self.rows.append(dict(time_ms=int(ts)//1_000_000, equity=float(equity), pnl=float(pnl),
                             cashflow=float(cashflow), stake_budget=float(stake_budget), notional=float(notional),
                             trade_id=event, symbol=str(row.get('symbol', event.split(':')[0])),
                             side=int(row.get('side', 0)), net_bp=float(row.get('net', 0)),
                             entry_ms=int(entry)//1_000_000, reason=str(row.get('reason', ''))))


def lock_rows(symbol):
    frames = []
    for path in ['ohlc', 'olhc']:
        f = A / 'exp-monday-limit-be-lock-20261027-v1' / path / 'symbols' / symbol / 'trades.csv.gz'
        t = pd.read_csv(f)
        frames.append(t[t.variant == 'v3_be50'].set_index('trade_id'))
    o, l = frames[0], frames[1].reindex(frames[0].index)
    no = o.net_notional_bp - np.where(o.reason == 4, 0., 10.)
    nl = l.net_notional_bp - np.where(l.reason == 4, 0., 10.)
    worse = nl < no
    t = pd.DataFrame(dict(event=o.index.str.split(':').str[:3].str.join(':'), score=o.score,
                         side=o.side, net=np.where(worse, nl, no), reason=np.where(worse, l.reason, o.reason),
                         entry_t=o.entry_time, exit_t=np.where(worse, l.exit_time, o.exit_time), mae_pct=0.))
    return t[~t.event.isin(BAD_EVENTS)].reset_index(drop=True)


def inputs():
    cache = OUT / 'native-fills.csv.gz'
    if cache.exists():
        print('Reuse extracted native fills', flush=True)
        return dated(pd.read_csv(cache))
    news = pd.read_csv(A / 'exp-monday-limit-time-news-20261026-v1/trades.csv.gz')
    events = set(news[news.variant == 'v3_be17'].event) - BAD_EVENTS
    wanted = {RUN.name: ['v3_be0', 'v3_add5'], 'exp-monday-limit-v3-levels-20261025-v1': ['opp1618_P30']}
    symbols = sorted({e.split(':')[0] for e in events})
    parts = []
    with ProcessPoolExecutor(4) as pool:
        for i, frame in enumerate(pool.map(source.legs, [(s, wanted, events) for s in symbols], chunksize=1)):
            if len(frame):
                parts.append(frame)
            if i % 25 == 0:
                print(f'Reading saved fills/MAE {i+1}/{len(symbols)}', flush=True)
    # No MAE is needed at 1x; reuse the already projected, cost-adjusted ledger.
    base = news[news.variant.isin(['v2_be17', 'v3_be17']) & ~news.event.isin(BAD_EVENTS)].copy()
    base = base.rename(columns={'t': 'entry_t'}).assign(mae_pct=0.)
    parts.append(base)
    lock_symbols = read('exp-monday-limit-be-lock-20261027-v1/config.json')['symbols']
    with ProcessPoolExecutor(4) as pool:
        locks = pd.concat(pool.map(lock_rows, lock_symbols, chunksize=8), ignore_index=True)
    parts.append(locks.assign(variant='v3_be50'))
    t = dated(pd.concat(parts, ignore_index=True))
    t['symbol'] = t.event.str.split(':').str[0]
    t['tid'] = t.event + ':' + t.variant
    t = t[['event', 'tid', 'variant', 'symbol', 'side', 'score', 'net', 'reason', 'entry_t', 'exit_t', 'entry_ns', 'exit_ns', 'mae_pct']]
    t.to_csv(cache, index=False, compression='gzip')
    return t


def five_rows():
    cache = OUT / 'five-minute-fills.csv.gz'
    if cache.exists():
        return dated(pd.read_csv(cache))
    symbols = sorted(d.name for d in five.ALL.iterdir() if (d/'done.json').exists() and (five.V2/d.name/'done.json').exists())
    print('Read saved 5m native fills and frozen scores', flush=True)
    with ProcessPoolExecutor(4) as pool:
        rows = pd.concat([frame for frame, _ in pool.map(five.load, symbols, chunksize=8)], ignore_index=True)
    scores, cutoff = five.scores()
    r = rows[(rows.m == 5) & (rows.leg == 'near')].set_index('event')
    t = five.combine({'near': r}, ('near',)).join(scores.rename('score'))
    t = t[t.score >= cutoff].reset_index().rename(columns={'event': 'event'})
    t['entry_t'] = pd.to_datetime(t.entry, utc=True)
    t['exit_t'] = pd.to_datetime(t.exit, utc=True)
    t['tid'] = t.event
    t['reason'] = ''
    t.to_csv(cache, index=False, compression='gzip')
    return dated(t)


def export(track, t, allocator, note, source_paths, start, seed=None):
    t = t[t.entry_t >= pd.Timestamp(start, tz='UTC')].reset_index(drop=True)
    if t.empty:
        raise ValueError(f'No saved fills for {track}')
    recorder = Trace(pd.Timestamp(start, tz='UTC').value)
    result = allocator(t, recorder)
    frame = pd.DataFrame(recorder.rows)
    if not frame.time_ms.is_monotonic_increasing:
        raise ValueError(f'Unordered events: {track}')
    error = np.abs(frame.equity - (1000 + frame.pnl.cumsum() + frame.cashflow.cumsum())).max()
    if error > 1e-5 * max(1., frame.equity.max()):
        raise ValueError(f'Cash-flow reconciliation failed: {track} {error}')
    frame.to_csv(OUT / f'{track}-ledger.csv.gz', index=False, compression='gzip')
    meta = dict(track=track, start=start, end=str(t.exit_t.max()), seed=seed, candidates=len(t),
                funded=int((frame.notional > 0).sum()), note=note, sources=source_paths,
                allocator_result=result, reconciliation_error=float(error))
    print(f'{track}: {meta["funded"]} funded events, ending {frame.equity.iloc[-1]:.6f}', flush=True)
    return meta


def run():
    files = ['yoyo/evaluation/strategy_booklet_paths.py', 'yoyo/evaluation/monday_limit_return_opt.py',
             'yoyo/evaluation/monday_limit_spike_hw.py', 'yoyo/evaluation/monday_limit_reclaim_optimize.py',
             'yoyo/evaluation/monday_limit_langlang.py', str((OUT/'config.json').relative_to(ROOT))]
    for file in files:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', file], cwd=ROOT).returncode or subprocess.run(['git', 'ls-files', '--error-unmatch', file], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit exporter/config/observer hooks before exporting')
    t = inputs()
    cutoff = read(RUN.name + '/scores.json')['threshold']
    mean_excess = read('exp-monday-limit-v3-compare-20261025-v1/summary.json')['results']['linear_mean_excess']
    fraction = lambda x: np.minimum(.05 * (x.score.to_numpy() - cutoff) / mean_excess, .5)
    linear = lambda x, trace: capital.linear(x, fraction(x), 1, 20261103, trace=trace)
    meta = {}
    for track, variant in [('v3', 'v3_be17'), ('v3lock', 'v3_be50'), ('v3add', 'v3_be0')]:
        variants = ['v3_be0', 'v3_add5'] if track == 'v3add' else [variant]
        note = '历史Binance成交；评分线性分配，总保证金≤净值，1倍；固定原研究首个随机种子，非10次中位数。'
        note += '历史BE0，当前+0.17%。' if track == 'v3add' else '保护偏移与当前策略相同。'
        meta[track] = export(track, t[t.variant.isin(variants)], linear, note,
                             ['exp-monday-limit-be-lock-compare-20261027-v1' if track == 'v3lock' else 'exp-monday-limit-return-opt-20261027-v2'], '2020-01-01', 20261103)
    meta['60'] = export('60', t[t.variant == 'v2_be17'],
                        lambda x, trace: replay_w(x.rename(columns={'entry_ns':'entry','exit_ns':'exit'}), np.full(len(x), .1), 1., trace=trace)[0],
                        '历史Binance 60m/+0.17%成交；按当前每笔10%净值、总敞口1倍核算。仅补出资金路径，未重放K线成交。',
                        ['exp-monday-limit-time-news-20261026-v1', 'exp-monday-limit-be-scalein-20261026-v1'], '2020-01-01')
    f = five_rows()
    meta['5'] = export('5', f, lambda x, trace: replay_w(x.sort_values('entry').reset_index(drop=True), np.full(len(x), .1), 1., trace=trace)[0],
                       '原5m近侧目标基线、BE0、每笔10%净值。当前保护偏移为+0.17%；曲线不是当前版本精确回测。',
                       ['exp-monday-limit-reclaim-allexits-20261025-v1'], '2025-01-01')
    s = dated(pd.read_csv(A / 'exp-monday-limit-spike-hw-20261026-v1/trades.csv')).sort_values(['symbol', 'entry_ns'], kind='stable').reset_index(drop=True)
    meta['spike'] = export('spike', s, lambda x, trace: slot_replay(x, 10, 5, 19., 20261102, trace=trace)[0],
                           '原高胜率研究：BE0、10份×5倍、19%浮亏强平情景；当前+0.17%且保证金封顶。固定原研究首个种子，不是20次中位数。',
                           ['exp-monday-limit-spike-hw-20261026-v1'], '2020-01-01', 20261102)
    meta['v3add20'] = export('v3add20', t[t.variant.isin(['v3_be0', 'v3_add5'])],
                            lambda x, trace: capital.frac(x, 20, 3, 20261103, trace=trace),
                            '修正后的20份动态保证金×3倍；历史BE0/MAE强平，当前+0.17%/保证金封顶。固定原研究首个种子，不是10次中位数。',
                            ['exp-monday-limit-return-opt-20261027-v2'], '2020-01-01', 20261103)
    meta['allin3'] = export('allin3', t[(t.variant == 'opp1618_P30') & (t.score >= .6)],
                           lambda x, trace: path_run(x, 'allin', 3, 20261105, trace=trace),
                           '原研究首个2023年月度起点和首个种子，非最优起点。历史BE0/MAE强平，当前+0.17%/保证金封顶；补充本金单列，不计收益。',
                           ['exp-monday-limit-langlang-20261027-v1'], '2023-01-01', 20261105)
    payload = dict(accounts=meta, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT,text=True).strip(),
                   training_eligible=False, production_eligible=False)
    (OUT/'manifest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                               default=lambda value: value.item()))


if __name__ == '__main__':
    run()

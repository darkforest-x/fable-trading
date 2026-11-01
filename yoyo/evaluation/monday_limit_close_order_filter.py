"""EMA order filter on the live-wick rule (owner 2026-11-01).

Owner: "这套策略没用上排列过滤吗？能用吗？能提高胜率吗？能提高收益吗？". The spike account requires the 15m EMA
8/13/21/34/55 order score >= 3 (side-aligned count of ordered pairs on the 15m candles closed before the touch 5m bar,
`l2_order_score` of features_v2, known before the touch). This adds only that filter to the live-wick rule: the E20
near-edge rows of exp-monday-limit-close-stop-grid-20261101-v1 (touch-close survivors, entry at the close, stop 20
percent from the entry; NautilusTrader, worse path, taker + 10bp entry, 10bp non-target exits), minus the closes 30
percent or more past the level (as -v5). No new fills. The question fixed before looking: min_order 3 (the spike
value) against no filter; 1, 2 and 4 are reported as context. Per period: trades, win rate, mean; frac replays at
6 x 3 and 3 x 3 (2020-2024 one seed; 2025 onward 10 tie seeds, median final and worst drawdown).
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_close_reclaim as cr

PER = {'y2020_2024': ('2020-01-01', '2025-01-01'), 'y2025': ('2025-01-01', '2026-01-01'), 'y2026': ('2026-01-01', '2100-01-01'),
       'from2025': ('2025-01-01', '2100-01-01'), 'all': ('2020-01-01', '2100-01-01')}


def window(x, p):
    a, b = PER[p]
    return x[(x.entry_t >= pd.Timestamp(a, tz='UTC')) & (x.entry_t < pd.Timestamp(b, tz='UTC'))].reset_index(drop=True)


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_return_opt import frac
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_order_filter.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['fills']
    syms = sorted({p.parent.name for p in (src/'ohlc'/'symbols').glob('*/done.json')})
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(cr.load_symbol, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    excluded = set(json.loads((ROOT/cfg['paths']/'manifest.json').read_text())['excluded_beyond'])
    t = t[(t.variant == cfg['variant']) & (t['mode'] == cfg['target']) & ~t.event.isin(BAD_EVENTS | excluded)
          & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    order = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    t['order'] = t.event.map(order)
    t = t.assign(entry_ns=t.entry_t.astype('int64'), exit_ns=t.exit_t.astype('int64'))
    res = dict(checks=dict(events=int(t.event.nunique()), missing_order=int(t.order.isna().sum()),
                           order_counts={str(int(k)): int(v) for k, v in t.order.value_counts().sort_index().items()},
                           issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20]), variants={})
    for k in cfg['min_order']:
        x = t[t.order >= k].reset_index(drop=True)
        stats = {}
        for p in PER:
            y = window(x, p)
            stats[p] = dict(n=int(len(y)), win_pct=float(100*(y.net > 1).mean()) if len(y) else None,
                            mean_bp=float(y.net.mean()) if len(y) else None, sum_pct=float(y.net.sum()/100))
        acc = {}
        for n, lev in cfg['sizings']:
            a = frac(window(x, 'y2020_2024'), n, lev, cfg['seed'])
            reps = [frac(window(x, 'from2025'), n, lev, cfg['seed']+r) for r in range(cfg['ties'])]
            full = frac(window(x, 'all'), n, lev, cfg['seed'])
            acc[f'{n}x{lev}'] = dict(y2020_2024=dict(final=a['final_multiple'], dd=a['max_drawdown_pct']),
                                     from2025=dict(final_median=float(np.median([q['final_multiple'] for q in reps])),
                                                   dd_max=float(np.max([q['max_drawdown_pct'] for q in reps]))),
                                     all=dict(final=full['final_multiple'], dd=full['max_drawdown_pct']))
        res['variants'][str(k)] = dict(stats=stats, accounts=acc)
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)

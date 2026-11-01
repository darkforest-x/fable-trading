"""Distance past the 1.618 at the touch close as a filter on the live-wick rule (owner 2026-11-01).

Owner: "测离1.618≥2%过滤". The live-wick ledger showed a higher win rate when the touch 5m close was further past the
level (beyond_pct = side x (level - close) / level, known at the close): >= 2 percent won about 84 percent in 2020-2024
and 75 in 2025-2026 against about 58 below. The cut was read off those trades, so this is post hoc. Rows: the E20
near-edge survivors of exp-monday-limit-close-stop-grid-20261101-v1 (saved NautilusTrader fills, worse path, costs as
the live-wick accounts), minus closes 30 percent or more past the level; no new fills.
Per cut (primary 2, robustness 1, 1.5, 3, 4): per-period trades, win rate and mean; frac replays at 6 x 3, 4 x 3 and
3 x 3 (2020-2024 one seed; 2025 onward 10 tie seeds; full history one seed). Control: 300 random subsets of the same size
drawn from all survivors in the same period (win rate and the 6 x 3 multiple), to separate the distance from having
fewer trades.
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
from yoyo.evaluation.monday_limit_close_survivor_paths import symbol_rows
from yoyo.evaluation.monday_limit_close_order_filter import PER, window


def evaluate(args):
    t, cut, cfg = args
    from yoyo.evaluation.monday_limit_return_opt import frac
    x = t[t.beyond_pct >= cut].reset_index(drop=True)
    stats, acc, ctrl = {}, {}, {}
    for p in PER:
        y = window(x, p)
        stats[p] = dict(n=int(len(y)), win_pct=float(100*(y.net > 0).mean()) if len(y) else None,
                        mean_bp=float(y.net.mean()) if len(y) else None)
    for n, lev in cfg['sizings']:
        a = frac(window(x, 'y2020_2024'), n, lev, cfg['seed'])
        reps = [frac(window(x, 'from2025'), n, lev, cfg['seed']+r) for r in range(cfg['ties'])]
        full = frac(window(x, 'all'), n, lev, cfg['seed'])
        acc[f'{n}x{lev}'] = dict(y2020_2024=dict(final=a['final_multiple'], dd=a['max_drawdown_pct']),
                                 from2025=dict(final_median=float(np.median([q['final_multiple'] for q in reps])),
                                               dd_max=float(np.max([q['max_drawdown_pct'] for q in reps]))),
                                 all=dict(final=full['final_multiple'], dd=full['max_drawdown_pct']))
    rng = np.random.default_rng(cfg['control_seed'])
    n, lev = cfg['control_sizing']
    for p in ('y2020_2024', 'from2025'):
        pool, k = window(t, p), stats[p]['n']
        wins, mult = [], []
        for _ in range(cfg['control_draws']):
            y = pool.iloc[np.sort(rng.choice(len(pool), k, replace=False))].reset_index(drop=True)
            wins.append(100*(y.net > 0).mean()); mult.append(frac(y, n, lev, cfg['seed'])['final_multiple'])
        mine = frac(window(x, p), n, lev, cfg['seed'])['final_multiple']
        ctrl[p] = dict(win_median=float(np.median(wins)), win_p95=float(np.percentile(wins, 95)),
                       win_share_below=float(100*(np.array(wins) < stats[p]['win_pct']).mean()),
                       multiple=mine, multiple_median=float(np.median(mult)), multiple_share_below=float(100*(np.array(mult) < mine).mean()))
    return str(cut), dict(stats=stats, accounts=acc, control=ctrl)


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_beyond_filter.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['fills']
    surv = pd.read_csv(ROOT/cfg['survivors'])
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(src), s, slip['market_entry'], slip['non_target_exit'], cfg['variant'])
                                        for s in sorted(set(surv.symbol))], chunksize=4))
    t = pd.concat([g[0] for g in got if len(g[0])], ignore_index=True)
    t = t[(t['mode'] == cfg['target']) & t.event.isin(set(surv.event)) & ~t.event.isin(BAD_EVENTS)
          & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['beyond_pct'] = 100*t.side*(t.level-t.entry)/t.level
    t = t[t.beyond_pct < cfg['max_beyond_pct']]
    t = t.assign(entry_ns=t.entry_t.astype('int64'), exit_ns=t.exit_t.astype('int64')).reset_index(drop=True)
    res = dict(checks=dict(events=int(t.event.nunique()), issues=[i for g in got for i in g[1]][:20]), cuts={})
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(evaluate, [(t, c, cfg) for c in cfg['cuts']]):
            res['cuts'][name] = out
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)

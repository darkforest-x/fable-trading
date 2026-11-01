"""EMA order score as a position-size booster on the live-wick rule (owner 2026-11-01).

Owner: "在排列分高时开大一点的仓位 … 测试一下吧". Same rows as monday_limit_close_order_filter (E20 near-edge survivors,
saved NautilusTrader fills, minus closes 30 percent or more past the level); no trade is dropped. Sizing is
monday_limit_return_opt.frac with one change: a fill whose 15m EMA order score (known before the touch) is >= k uses
boost x the normal margin (realized equity / n), still at most n open and margin in use <= equity; a loss never
exceeds its margin, a fill whose MAE reaches the leverage's liquidation line loses it.
Fixed before looking: k 3 with boost 1.5 and 2.0 at 6 x 3 (the live-wick6x3 base); k 2 and 3 x 3 are context.
A boost adds exposure to positive-mean trades, so any boost may raise the multiple. The control boosts the same number of
randomly chosen trades in the same window (draws seeded), and the order-based result is placed in that distribution.
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
from yoyo.evaluation.monday_limit_close_order_filter import PER, window


def weighted_frac(t, n, lev, seed, weights):
    """frac with a margin multiplier per fill (weights aligned to t's rows)."""
    rng = np.random.default_rng(seed)
    o = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t, w = t.iloc[o].reset_index(drop=True), np.asarray(weights, float)[o]
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    liq = 100./lev-1. if lev > 1 else 1e9
    eq, peak, dd, used, skipped = 1000., 1000., 0., 0., 0
    margin = {}
    net, mae = t.net.to_numpy()/1e4, t.mae_pct.to_numpy()
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            m = eq/n*w[j]
            if len(margin) >= n or m <= 0 or eq-used < m-1e-9:
                skipped += 1; continue
            margin[j] = m; used += m
        elif j in margin:
            m = margin.pop(j); used -= m
            eq += -m if mae[j] >= liq else max(-m, m*lev*net[j])
            peak = max(peak, eq); dd = max(dd, 1-eq/peak)
            if eq <= 1e-6:
                return dict(final_multiple=0., max_drawdown_pct=100., skipped=skipped)
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, skipped=skipped)


def evaluate(args):
    """One (k, boost, sizing): order-based boost per window, the random-boost control and the unboosted base."""
    x, k, boost, n, lev, cfg = args
    out = {}
    for p in ('y2020_2024', 'from2025', 'all'):
        y = window(x, p)
        hit = (y.order >= k).to_numpy()
        ties = cfg['ties'] if p == 'from2025' else 1
        def run(weights):
            reps = [weighted_frac(y, n, lev, cfg['seed']+r, weights) for r in range(ties)]
            return float(np.median([q['final_multiple'] for q in reps])), float(np.max([q['max_drawdown_pct'] for q in reps]))
        base, dd0 = run(np.ones(len(y)))
        mine, dd1 = run(np.where(hit, boost, 1.))
        rng = np.random.default_rng(cfg['control_seed'])
        ctrl = []
        for _ in range(cfg['control_draws']):
            pick = np.zeros(len(y), bool); pick[rng.choice(len(y), int(hit.sum()), replace=False)] = True
            ctrl.append(weighted_frac(y, n, lev, cfg['seed'], np.where(pick, boost, 1.))['final_multiple'])
        ctrl = np.array(ctrl)
        out[p] = dict(trades=int(len(y)), boosted=int(hit.sum()), base=base, base_dd=dd0, boosted_final=mine, boosted_dd=dd1,
                      control_median=float(np.median(ctrl)), control_p95=float(np.percentile(ctrl, 95)),
                      share_of_controls_below=float(100*(ctrl < mine).mean()))
    return f'k{k}|x{boost}|{n}x{lev}', out


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_order_boost.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['fills']
    syms = sorted({p.parent.name for p in (src/'ohlc'/'symbols').glob('*/done.json')})
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(cr.load_symbol, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    excluded = set(json.loads((ROOT/cfg['paths']/'manifest.json').read_text())['excluded_beyond'])
    t = t[(t.variant == cfg['variant']) & (t['mode'] == cfg['target']) & ~t.event.isin(BAD_EVENTS | excluded)
          & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['order'] = t.event.map(pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score)
    t = t.assign(entry_ns=t.entry_t.astype('int64'), exit_ns=t.exit_t.astype('int64')).reset_index(drop=True)
    jobs = [(t, k, b, n, lev, cfg) for k, b in cfg['boosts'] for n, lev in cfg['sizings']]
    res = dict(checks=dict(events=int(t.event.nunique()), missing_order=int(t.order.isna().sum())), variants={})
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(evaluate, jobs):
            res['variants'][name] = out
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)

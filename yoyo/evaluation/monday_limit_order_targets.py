"""Score >= 0.6 + EMA order score > 0: backstop and larger targets, on existing framework fills (owner 2026-10-26).

Owner: "闪电插针也要有兜底止损  2026年有多少单？胜率多少？止盈是否能更大点位？". Read-only, no new backtest: the
NautilusTrader fills of exp-monday-limit-v3-grid-20261025-v1 (near Monday edge target x backstop) and
exp-monday-limit-v3-levels-20261025-v1 (range 0.5 / range 0.618 / far Monday edge / opposite 1618 x backstop), both
intrabar paths, breakeven at the entry (the +0.17 percent offset changed results by less than tie-order noise in
exp-monday-limit-be-scalein-compare-20261026-v1). Worse path per trade, 10bp slippage on non-target exits, the two bad
source bars dropped. Population: score >= 0.6 and l2_order_score > 0 (causal feature table v2); all score >= 0.6
as the reference. Backstops: none ('far'), -20 and -30 percent from the fill until the confirmation (a reclaim
replaces it with the breakeven stop). Per target x backstop and period (2020-2024, 2025, 2026, last five months):
trades, win (net > 1bp) / flat / loss shares, backstop exits, mean, worst, same-5m-bar fill-and-target share, and a
1000 USDT replay with linear score sizing (gross <= 1x, medians over TIES random orders of same-time fills).
The order-score filter was found on the same history; these numbers are in-sample.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, RUN, clean, worse
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-order-targets-20261026-v1'
A = ROOT/'experiments/active'


def load(args):
    symbol, variants = args
    out = []
    for run in ('exp-monday-limit-v3-grid-20261025-v1', 'exp-monday-limit-v3-levels-20261025-v1'):
        t = {}
        for path in ('ohlc', 'olhc'):
            x = pd.read_csv(A/run/path/'symbols'/symbol/'trades.csv.gz')
            t[path] = x[x.variant.isin(variants) & (x.score >= .6)] if len(x) else x
        if len(t['ohlc']) or len(t['olhc']):
            out.append(worse(t['ohlc'], t['olhc']))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def stats(x):
    n = len(x)
    if not n:
        return dict(n=0)
    return dict(n=int(n), win_pct=float(100*x.win.mean()), flat_pct=float(100*x.flat.mean()), loss_pct=float(100*(1-x.win-x.flat).mean()),
                backstop=int((x.reason == 1).sum()), mean_bp=float(x.net.mean()), worst_bp=float(x.net.min()),
                same_bar_target_pct=float(100*((x.exit == x.entry) & (x.reason == 4)).mean()))


def replay(y, cut, mexcess, ties, rng):
    if not len(y):
        return dict(final_median=None, dd_median=None)
    y = y.sort_values(['entry', 'tid'], kind='mergesort').reset_index(drop=True)
    k = np.minimum(.05*(y.score.to_numpy()-cut)/mexcess, .5)
    rep = []
    for _ in range(ties):
        o = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
        rep.append(opt.replay_w(y.iloc[o].reset_index(drop=True), k[o], 1.)[0])
    return dict(final_median=float(np.median([r['final_multiple'] for r in rep])), dd_median=float(np.median([r['max_drawdown_pct'] for r in rep])))


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_order_targets.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    variants = [f'{tg}_{st}' for tg in cfg['targets'] for st in cfg['stops']]
    syms = json.loads((RUN/'config.json').read_text())['symbols']
    with ProcessPoolExecutor(4) as ex:
        m = pd.concat(ex.map(load, [(s, variants) for s in syms], chunksize=8), ignore_index=True)
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':')
    m = m[~m.event.isin(BAD_EVENTS)].copy()
    w = (m.net_l < m.net_o).to_numpy()
    m['net'] = np.where(w, m.net_l, m.net_o); m['reason'] = np.where(w, m.reason_l, m.reason_o)
    m['entry'] = pd.to_datetime(m.entry_time_o.fillna(m.entry_time_l), utc=True).astype('int64')
    m['exit'] = pd.to_datetime(pd.Series(np.where(w, m.exit_time_l, m.exit_time_o)), utc=True).astype('int64').to_numpy()
    m['t'] = pd.to_datetime(m.entry, utc=True); m['tid'] = m.trade_id
    m['win'] = (m.net > cfg['win_bp']).astype(float); m['flat'] = (m.net.abs() <= cfg['win_bp']).astype(float)
    f = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    m = m.merge(f, on='event', how='left')
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': lambda t: t < B[0], 'y2025': lambda t: (t >= B[0]) & (t < B[1]), 'y2026': lambda t: t >= B[1], 'last5m': lambda t: t >= window}
    rng = np.random.default_rng(cfg['seed'])
    res = {}
    for pop, sel in (('order_pos', m.order > 0), ('all_ge06', m.score >= .6)):
        res[pop] = {}
        for v in variants:
            g = m[sel & (m.variant == v)]
            res[pop][v] = {per: dict(**stats(g[f(g.t)]), **replay(g[f(g.t)], cut, mexcess, cfg['ties'], rng)) for per, f in pers.items()}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    for pop in res:
        print('==', pop)
        for v, r in res[pop].items():
            print(f'{v:14s}', *(f"{per}: n{x['n']} win {x.get('win_pct') or 0:.1f} bs {x.get('backstop', 0)} mean {x.get('mean_bp') or 0:.0f} worst {x.get('worst_bp') or 0:.0f} x{x['final_median'] or 0:.2f} dd {x['dd_median'] or 0:.0f}" for per, x in r.items()))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

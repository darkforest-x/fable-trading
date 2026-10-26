"""1000 USDT equity replays of v3 with the 15m EMA order score as a filter or size multiplier (owner 2026-10-26).

Owner: "用现有框架成交做资金曲线重放：v3 原样、只做排列分>0、排列分>0 仓位加倍，分时段给倍数和回撤".
Read-only arithmetic on existing framework fills, no new backtest: the v3_be17 trades (score >= 0.5, range 0.618 /
opposite 1618 targets, -30 percent backstop, breakeven at entry +0.17 percent) of exp-monday-limit-time-news-20261026-v1
(worse intrabar path per trade of the NautilusTrader fills in exp-monday-limit-be-scalein-20261026-v1, 10bp slippage on
non-target exits), joined to l2_order_score of the causal feature table exp-monday-limit-score-features-v2-20261025-v1
(side-aligned 15m EMA order, completed bars before the touch; missing counts as not > 0).
Plans, all with linear sizing k = min(0.05 * (score - cut) / mean_excess, 0.5) and gross <= 1x equity:
  v3          every trade at k;
  order_only  only trades with order score > 0, at k;
  order_x2    every trade, 2k when order score > 0 (single-trade cap 1.0);
  order_x2c   every trade, min(2k, 0.5) when order score > 0 (single-trade cap kept), a sensitivity.
Added after the owner asked about the near-target subset (same day), on the v2_be17 fills of the same events
(near Monday edge target, no backstop, breakeven +0.17 percent), same linear sizing:
  near_ge05       score >= 0.5 (the v3 events with the near target);
  near_ge06       score >= 0.6;
  near_ge06_order score >= 0.6 and order score > 0 (89.5 / 77.5 percent win rate in the lever study).
These run after the first four plans so the earlier tie draws are unchanged.
Periods restart at 1000 USDT: 2020-2024, 2025, 2026, last five months. Medians over TIES random orders of same-time
fills (opt.replay_w books exits before entries at the same timestamp except a trade's own same-bar exit).
The order score was found on the same 2020-2026 history (exp-monday-limit-winrate-levers-20261026-v1), so these
multiples are in-sample for the filter; forward paper data is the test. Parity: the v3 plan must reproduce the
v3_be17 replay of exp-monday-limit-be-scalein-compare-20261026-v1 within tie-order noise.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation.monday_limit_be_scalein_compare import RUN, B, clean

EXP = ROOT/'experiments/active/exp-monday-limit-order-replay-20261026-v1'
A = ROOT/'experiments/active'


def plans(t, cut, mexcess):
    k = np.minimum(.05*(t.score.to_numpy()-cut)/mexcess, .5)
    pos = (t.order > 0).to_numpy()
    return dict(v3=(np.ones(len(t), bool), k), order_only=(pos, k),
                order_x2=(np.ones(len(t), bool), np.where(pos, 2*k, k)),
                order_x2c=(np.ones(len(t), bool), np.where(pos, np.minimum(2*k, .5), k)))


def replay(t, keep, k, within, ties, rng):
    sel = keep & within
    y = t[sel].reset_index(drop=True); kk = k[sel]
    rep = []
    for _ in range(ties):
        order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
        rep.append(opt.replay_w(y.iloc[order].reset_index(drop=True), kk[order], 1.)[0])
    return dict(n=int(len(y)), win_pct=float(100*(y.net > 1).mean()) if len(y) else None, mean_bp=float(y.net.mean()) if len(y) else None,
                final_median=float(np.median([r['final_multiple'] for r in rep])),
                final_min=float(np.min([r['final_multiple'] for r in rep])), final_max=float(np.max([r['final_multiple'] for r in rep])),
                dd_median=float(np.median([r['max_drawdown_pct'] for r in rep])), dd_max=float(np.max([r['max_drawdown_pct'] for r in rep])))


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_order_replay.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    t_all = pd.read_csv(ROOT/cfg['trades'], parse_dates=['t', 'exit_t'])
    f = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    t_all = t_all.merge(f, on='event', how='left')
    t_all['entry'] = t_all.t.astype('int64'); t_all['exit'] = t_all.exit_t.astype('int64'); t_all['tid'] = t_all.trade_id
    t_all = t_all.sort_values(['entry', 'tid'], kind='mergesort').reset_index(drop=True)
    t = t_all[t_all.variant == cfg['variant']].reset_index(drop=True)
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': t.t < B[0], 'y2025': (t.t >= B[0]) & (t.t < B[1]), 'y2026': t.t >= B[1], 'last5m': t.t >= window}
    rng = np.random.default_rng(cfg['seed'])
    res = dict(trades=int(len(t)), order_missing=int(t.order.isna().sum()), order_positive=int((t.order > 0).sum()),
               cut=cut, mean_excess=mexcess, window_start=str(window), replays={})
    for name, (keep, k) in plans(t, cut, mexcess).items():
        res['replays'][name] = {per: replay(t, keep, k, w.to_numpy(), cfg['ties'], rng) for per, w in pers.items()}
    near = t_all[t_all.variant == cfg['near_variant']].reset_index(drop=True)
    kn = np.minimum(.05*(near.score.to_numpy()-cut)/mexcess, .5)
    pn = {'y2020_2024': near.t < B[0], 'y2025': (near.t >= B[0]) & (near.t < B[1]), 'y2026': near.t >= B[1], 'last5m': near.t >= window}
    for name, keep in (('near_ge05', near.score >= .5), ('near_ge06', near.score >= .6), ('near_ge06_order', (near.score >= .6) & (near.order > 0))):
        res['replays'][name] = {per: replay(near, keep.to_numpy(), kn, w.to_numpy(), cfg['ties'], rng) for per, w in pn.items()}
    prev = json.loads((A/'exp-monday-limit-be-scalein-compare-20261026-v1/summary.json').read_text())['results']['replays']['v3_be17']
    res['parity_v3_vs_be_scalein_compare'] = {per: dict(here=res['replays']['v3'][per]['final_median'], before=prev[per]['final_median'],
                                                        n_here=res['replays']['v3'][per]['n'], n_before=prev[per]['n']) for per in pers}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps({k: v for k, v in res.items() if k != 'replays'}))
    for name, r in res['replays'].items():
        print(f'{name:15s}', *(f"{per}: n{x['n']} win {x['win_pct']:.1f} mean {x['mean_bp']:.0f} x{x['final_median']:.2f} [{x['final_min']:.2f}-{x['final_max']:.2f}] dd {x['dd_median']:.0f}/{x['dd_max']:.0f}" for per, x in r.items()))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

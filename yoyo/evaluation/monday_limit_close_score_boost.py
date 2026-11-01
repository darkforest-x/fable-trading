"""Entry score vs win rate on the live-wick rule, and a larger margin for high scores (owner 2026-11-01).

Owner: "我看评分大于0.76的单子全赢了？能否动态针对高评分加大仓位？详细分析一下评分和胜率的关系？". Same rows as
monday_limit_close_order_filter (E20 near-edge survivors, saved NautilusTrader fills, minus closes 30 percent or more
past the level). Score = the frozen touch-bar score (`stored`, the paper accounts' score, known at the touch close).
1. Relation: fixed score bands per period (trades, wins, mean net), and the rank correlation of score with net and with
   win, against 2000 within-period shuffles of the scores (one-sided p for a positive relation).
2. Sizing: margin x boost when score >= cut (monday_limit_close_order_boost.weighted_frac; every trade kept). Fixed before
   looking: cut 0.6 (the existing high-score line of allin3 / spike) with boost 1.5 and 2.0 at 6 x 3; cut 0.7 is
   context; cut 0.76 is the owner's observation on these same trades (post hoc, reported as such). Control: the same
   number of randomly chosen trades boosted in the same window.
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
from yoyo.evaluation.monday_limit_close_order_filter import window
from yoyo.evaluation.monday_limit_close_order_boost import weighted_frac


def ranks(v):
    return pd.Series(v).rank().to_numpy()


def relation(y, draws, seed):
    """Spearman of score with net and with win, and one-sided shuffle p-values."""
    s, net, win = y.score.to_numpy(), y.net.to_numpy(), (y.net > 1).to_numpy(float)
    rs, rn, rw = ranks(s), ranks(net), ranks(win)
    obs = (np.corrcoef(rs, rn)[0, 1], np.corrcoef(rs, rw)[0, 1])
    rng = np.random.default_rng(seed)
    perm = np.array([(np.corrcoef(p, rn)[0, 1], np.corrcoef(p, rw)[0, 1]) for p in (rng.permutation(rs) for _ in range(draws))])
    return dict(n=int(len(y)), spearman_net=float(obs[0]), p_net=float((perm[:, 0] >= obs[0]).mean()),
                spearman_win=float(obs[1]), p_win=float((perm[:, 1] >= obs[1]).mean()))


def evaluate(args):
    x, cut, boost, n, lev, cfg = args
    out = {}
    for p in ('y2020_2024', 'from2025', 'all'):
        y = window(x, p)
        hit = (y.score >= cut).to_numpy()
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
    return f'score{cut}|x{boost}|{n}x{lev}', out


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_score_boost.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['fills']
    syms = sorted({p.parent.name for p in (src/'ohlc'/'symbols').glob('*/done.json')})
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(cr.load_symbol, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    excluded = set(json.loads((ROOT/cfg['paths']/'manifest.json').read_text())['excluded_beyond'])
    t = t[(t.variant == cfg['variant']) & (t['mode'] == cfg['target']) & ~t.event.isin(BAD_EVENTS | excluded)
          & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['score'] = t.event.map(pd.read_csv(ROOT/cfg['survivors']).set_index('event').score)
    t = t.assign(entry_ns=t.entry_t.astype('int64'), exit_ns=t.exit_t.astype('int64')).reset_index(drop=True)
    names = {1: 'target', 11: 'not_reclaimed_60m', 5: 'breakeven_stop'}
    bands = {}
    edges = cfg['bands']
    for p in ('y2020_2024', 'y2025', 'y2026', 'from2025', 'all'):
        y = window(t, p)
        b = pd.cut(y.score, edges, right=False)
        rows = []
        for iv, g in y.groupby(b, observed=True):
            rows.append(dict(band=f'{iv.left:.2f}-{iv.right:.2f}', n=int(len(g)), wins=int((g.net > 1).sum()),
                             win_pct=float(100*(g.net > 1).mean()), mean_bp=float(g.net.mean()), median_bp=float(g.net.median()),
                             worst_bp=float(g.net.min()), stops=int((g.reason == 1).sum())))
        bands[p] = rows
    hi = t[t.score >= .76].sort_values('entry_t')
    top = [dict(event=r.event, entry=str(r.entry_t), score=float(r.score), net_bp=float(r.net), reason=names.get(int(r.reason), 'target' if int(r.reason) == 4 else str(int(r.reason))))
           for r in hi.itertuples()]
    rel = {p: relation(window(t, p), cfg['draws'], cfg['control_seed']) for p in ('y2020_2024', 'from2025', 'all')}
    jobs = [(t, c, b, n, lev, cfg) for c, b in cfg['boosts'] for n, lev in cfg['sizings']]
    res = dict(checks=dict(events=int(t.event.nunique()), missing_score=int(t.score.isna().sum())), bands=bands,
               score_ge_076=top, relation=rel, variants={})
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

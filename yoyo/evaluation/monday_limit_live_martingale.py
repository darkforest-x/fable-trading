"""Loss doubling on top of the live-wick account sizing (owner 2026-11-01).

Owner: "倍投为什么用每单基础风险 不动原来的仓位和杠杆逻辑不行吗 只不过是基于原有的实际持仓量翻倍啊". The earlier study
(exp-monday-limit-live-streaks-20261101-v1) used the ETH study's fixed base risk and ignored overlaps. Here the account
is the live-wick one itself (monday_limit_return_opt.frac: margin = realized equity / n per fill, at most n open,
margin in use <= equity, 3x, a loss never exceeds its margin) and only the margin is multiplied: x 2^level, where level
counts the account's consecutive losing closes (in exit order) and resets after a winning close; at max_level a further
loss keeps the top level until a win (variant reset_at_cap resets instead, as the ETH rule). A fill whose doubled margin
does not fit the free equity is skipped and leaves the level unchanged. Rows: the saved E20 near-edge survivor fills
(live-wick) and the >= 1.5 percent subset (live-wick3x3far); no new fills.
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
from yoyo.evaluation.monday_limit_close_order_filter import window


def martingale_frac(t, n, lev, seed, mult=2., max_level=3, reset_at_cap=False):
    """frac with margin x mult^level; level = consecutive losing closes so far (exit order)."""
    rng = np.random.default_rng(seed)
    t = t.iloc[np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))].reset_index(drop=True)
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    liq = 100./lev-1. if lev > 1 else 1e9
    eq, peak, dd, used, skipped, level, top, worst = 1000., 1000., 0., 0., 0, 0, 0, 0.
    margin = {}
    net, mae = t.net.to_numpy()/1e4, t.mae_pct.to_numpy()
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            m = eq/n*mult**level
            if len(margin) >= n or m <= 0 or eq-used < m-1e-9:
                skipped += 1; continue
            margin[j] = m; used += m; top = max(top, level)
        elif j in margin:
            m = margin.pop(j); used -= m
            pnl = -m if mae[j] >= liq else max(-m, m*lev*net[j])
            worst = min(worst, pnl/eq)
            eq += pnl
            peak = max(peak, eq); dd = max(dd, 1-eq/peak)
            if pnl > 0:
                level = 0
            elif level >= max_level:
                level = 0 if reset_at_cap else max_level
            else:
                level += 1
            if eq <= 1e-6:
                return dict(final_multiple=0., max_drawdown_pct=100., skipped=skipped, top_level=top, worst_trade_pct=100*worst)
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, skipped=skipped, top_level=top, worst_trade_pct=100*worst)


def evaluate(args):
    name, x, n, lev, cfg = args
    out = {}
    for p in ('y2020_2024', 'from2025', 'all'):
        y = window(x, p)
        ties = cfg['ties'] if p == 'from2025' else 1
        res = {}
        for label, mult, cap, reset in [('none', 1., 0, False)] + [(f'x2_L{c}{"_reset" if r else ""}', 2., c, r)
                                                                 for c in cfg['max_levels'] for r in (False, True)]:
            reps = [martingale_frac(y, n, lev, cfg['seed']+k, mult, cap, reset) for k in range(ties)]
            res[label] = dict(final_median=float(np.median([q['final_multiple'] for q in reps])),
                              dd_max=float(np.max([q['max_drawdown_pct'] for q in reps])),
                              skipped_median=float(np.median([q['skipped'] for q in reps])),
                              top_level=int(max(q['top_level'] for q in reps)),
                              worst_trade_pct=float(min(q['worst_trade_pct'] for q in reps)))
        out[p] = dict(trades=int(len(y)), variants=res)
    return f'{name}|{n}x{lev}', out


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_live_martingale.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['fills']
    surv = pd.read_csv(ROOT/cfg['survivors'])
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(src), s, 10, 10, cfg['variant']) for s in sorted(set(surv.symbol))], chunksize=4))
    t = pd.concat([g[0] for g in got if len(g[0])], ignore_index=True)
    t = t[(t['mode'] == cfg['target']) & t.event.isin(set(surv.event)) & ~t.event.isin(BAD_EVENTS)
          & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['beyond_pct'] = 100*t.side*(t.level-t.entry)/t.level
    t = t[t.beyond_pct < 30].assign(entry_ns=lambda y: y.entry_t.astype('int64'), exit_ns=lambda y: y.exit_t.astype('int64')).reset_index(drop=True)
    jobs = [(name, x, n, lev, cfg) for name, x in (('live-wick', t), ('live-wick-far', t[t.beyond_pct >= 1.5].reset_index(drop=True)))
            for n, lev in cfg['sizings']]
    res = dict(checks=dict(events=int(len(t)), far=int((t.beyond_pct >= 1.5).sum())), accounts={})
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(evaluate, jobs):
            res['accounts'][name] = out
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)

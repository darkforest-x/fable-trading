"""Matched random controls for the spike subsets of the Monday1618 limit-reclaim rule (owner 2026-10-27, overfitting).

Owner asked whether the strategy is overfit. The Codex ledger (exp-monday-limit-reclaim-20261024-v1, NautilusTrader,
O-H-L-C path) carries up to three matched random controls per signal: same symbol, NY week, direction and prior
14-bar volatility bucket, entry at a random pre-entry close with the signal's limit distance and target percentage,
same reclaim, fee and monitoring rules. Here: 60m confirmation, near Monday edge target, no backstop, breakeven at
entry, closed rows only. For each subset (all signals, score >= 0.6, + EMA order >= 1, + EMA order >= 3) and period,
the signal net and win rate are compared with the mean of its filled controls (paired by event), with a 95 percent
NY-week block bootstrap of the paired difference. Only the O-H-L-C path exists for controls, so absolute numbers are
the optimistic path (the worse-path signal results are reported elsewhere); the paired difference is the point.
Scores: frozen model (scores.csv.gz of exp-monday-limit-be-scalein-20261026-v1); order: feature table v2.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, clean
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC

EXP = ROOT/'experiments/active/exp-monday-limit-spike-controls-20261027-v1'


def rows(symbol):
    f = SRC/symbol/'trades.csv.gz'
    if not f.exists():
        return pd.DataFrame()
    x = pd.read_csv(f, usecols=['event_id', 'population', 'reclaim_minutes', 'exit_mode', 'closed', 'net_bp', 'entry_time', 'week'])
    x = x[(x.reclaim_minutes == 60) & (x.exit_mode == 'near_monday_edge') & x.closed & x.net_bp.notna()]
    return x[['event_id', 'population', 'net_bp', 'entry_time', 'week']]


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_spike_controls.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    syms = sorted(d.name for d in SRC.iterdir() if (d/'trades.csv.gz').exists())
    with ProcessPoolExecutor(4) as ex:
        x = pd.concat([d for d in ex.map(rows, syms, chunksize=8) if len(d)], ignore_index=True)
    x = x[~x.event_id.isin(BAD_EVENTS)]
    sig = x[x.population == 'signal'].drop_duplicates('event_id').set_index('event_id')
    ctl = x[x.population == 'random'].groupby('event_id').agg(ctl_net=('net_bp', 'mean'), ctl_win=('net_bp', lambda v: (v > cfg['win_bp']).mean()),
                                                              ctl_n=('net_bp', 'size'))
    d = sig.join(ctl, how='inner')
    d['t'] = pd.to_datetime(d.entry_time, utc=True)
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event').score
    fo = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    d['score'] = d.index.map(sc); d['order'] = d.index.map(fo)
    d['diff'] = d.net_bp-d.ctl_net
    rng = np.random.default_rng(cfg['seed'])
    pers = {'y2020_2024': d.t < B[0], 'y2025_2026': d.t >= B[0], 'all': d.t.notna()}
    subsets = {'all_signals': d.score.notna() | d.score.isna(), 'score_ge06': d.score >= .6,
               'score_ge06_order_ge1': (d.score >= .6) & (d.order >= 1), 'score_ge06_order_ge3': (d.score >= .6) & (d.order >= 3)}
    res = dict(signals=int(len(sig)), paired=int(len(d)))
    for name, m in subsets.items():
        res[name] = {}
        for per, pm in pers.items():
            y = d[m & pm]
            if not len(y):
                res[name][per] = dict(n=0); continue
            wk = pd.factorize(y.week)[0]; nw = wk.max()+1
            s = np.bincount(wk, y['diff'].to_numpy(), nw); c = np.bincount(wk, minlength=nw).astype(float)
            W = rng.multinomial(nw, np.full(nw, 1/nw), size=cfg['boot']).astype(float)
            with np.errstate(all='ignore'):
                bs = (W@s)/(W@c)
            lo, hi = np.nanpercentile(bs, [2.5, 97.5])
            res[name][per] = dict(n=int(len(y)), weeks=int(nw), signal_mean_bp=float(y.net_bp.mean()), control_mean_bp=float(y.ctl_net.mean()),
                                  diff_bp=float(y['diff'].mean()), diff_ci95=[float(lo), float(hi)], p_le0=float((bs <= 0).mean()),
                                  signal_win_pct=float(100*(y.net_bp > cfg['win_bp']).mean()), control_win_pct=float(100*y.ctl_win.mean()))
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(res['signals'], res['paired'])
    for name in subsets:
        for per, v in res[name].items():
            if v.get('n'):
                print(f"{name:22s} {per:11s} n{v['n']:6d} wk{v['weeks']:4d} sig {v['signal_mean_bp']:7.1f} ctl {v['control_mean_bp']:7.1f} diff {v['diff_bp']:7.1f} [{v['diff_ci95'][0]:.1f},{v['diff_ci95'][1]:.1f}] p {v['p_le0']:.4f} win {v['signal_win_pct']:.1f} vs {v['control_win_pct']:.1f}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

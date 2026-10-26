"""Spike subset: further win-rate levers inside score >= 0.6 + EMA order > 0 (owner 2026-10-26).

Owner: "还能继续优化吗，还能继续提高胜率吗" after plan A (near target, -20 percent backstop, 10 slots, 5x). Read-only on
existing NautilusTrader fills, no replay:
  A. inside the subset, near target 60m (v2_be17 rows of exp-monday-limit-time-news-20261026-v1, breakeven +0.17
     percent, no backstop): win rate and mean by EMA order score (1-4), score band and side, per period;
  B. 5m confirmation with the near target for the same events: Codex ledger rows (O-H-L-C, reclaim_minutes 5) and
     exp-monday-limit-reclaim-olhc-allexits-20261025-v1 (monotone O-L-H-C), worse path per trade, 10bp slippage on
     non-target exits, breakeven at entry, no backstop;
  C. plan A against the same plan restricted to order score >= 3: slot replays (monday_limit_spike_opt.simulate) on
     the near_P20 fills with MAE from Codex 5m bars, 10 / 5 slots at 3x / 5x.
Win = net > 1bp. Everything here was looked at after the subset was chosen on the same history (in-sample).
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
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-spike-more-20261026-v1'
A = ROOT/'experiments/active'


def five(args):
    """5m confirmation, near target: both paths for the wanted events of one symbol."""
    symbol, events = args
    o = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'population', 'reclaim_minutes', 'exit_mode', 'closed', 'side',
                                                        'reason', 'entry_time', 'exit_time', 'net_bp'])
    o = o[(o.population == 'signal') & (o.reclaim_minutes == 5) & (o.exit_mode == 'near_monday_edge') & o.closed]
    o['event'] = o.trade_id.str.split(':').str[:3].str.join(':')
    o = o[o.event.isin(events)]
    lp = A/'exp-monday-limit-reclaim-olhc-allexits-20261025-v1/symbols'/symbol/'trades.csv.gz'
    if o.empty or not lp.exists():
        return pd.DataFrame()
    l = pd.read_csv(lp, usecols=['trade_id', 'reason', 'exit_time', 'net_notional_bp'])
    m = o.merge(l, on='trade_id', suffixes=('_o', '_l'))
    no = m.net_bp-np.where(m.reason_o == 4, 0., opt.SLIP); nl = m.net_notional_bp-np.where(m.reason_l == 4, 0., opt.SLIP)
    w = (nl < no).to_numpy()
    return pd.DataFrame(dict(event=m.event, side=m.side, net=np.where(w, nl, no), reason=np.where(w, m.reason_l, m.reason_o),
                             t=pd.to_datetime(m.entry_time, utc=True), exit_t=pd.to_datetime(pd.Series(np.where(w, m.exit_time_l, m.exit_time_o)), utc=True)))


def stats(x):
    n = len(x)
    if not n:
        return dict(n=0)
    return dict(n=int(n), win_pct=float(100*(x.net > 1).mean()), flat_pct=float(100*(x.net.abs() <= 1).mean()),
                mean_bp=float(x.net.mean()), worst_bp=float(x.net.min()))


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_spike_more.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    t = pd.read_csv(ROOT/cfg['trades'], parse_dates=['t', 'exit_t'])
    f = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    t = t[t.variant == 'v2_be17'].merge(f, on='event')
    sub = t[(t.score >= cfg['min_score']) & (t.order > 0) & ~t.event.isin(BAD_EVENTS)].copy()
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': lambda x: x.t < B[0], 'y2025': lambda x: (x.t >= B[0]) & (x.t < B[1]), 'y2026': lambda x: x.t >= B[1],
            'last5m': lambda x: x.t >= window, 'all': lambda x: x.t.notna()}
    per = lambda x: {k: stats(x[f(x)]) for k, f in pers.items()}
    res = dict(n=int(len(sub)))
    res['order'] = {str(int(o)): per(sub[sub.order == o]) for o in (1, 2, 3, 4)}
    res['order_ge3'] = per(sub[sub.order >= 3])
    res['score'] = {f'{lo}-{hi}': per(sub[(sub.score >= lo) & (sub.score < hi)]) for lo, hi in ((.6, .7), (.7, .8), (.8, 1.01))}
    res['side'] = {('long' if s == 1 else 'short'): per(sub[sub.side == s]) for s in (1, -1)}
    res['same_bar'] = dict(yes=per(sub[sub.exit_t == sub.t]), no=per(sub[sub.exit_t != sub.t]))
    events = set(sub.event)
    syms = sorted({e.split(':')[0] for e in events})
    with ProcessPoolExecutor(4) as ex:
        fm = pd.concat([d for d in ex.map(five, [(s, events) for s in syms], chunksize=8) if len(d)], ignore_index=True)
    fm = fm.merge(sub[['event', 'order', 'score']], on='event')
    res['confirm_5m'] = dict(all=per(fm), order_ge3=per(fm[fm.order >= 3]))
    res['confirm_60m'] = dict(all=per(sub[sub.event.isin(set(fm.event))]), order_ge3=per(sub[sub.event.isin(set(fm.event)) & (sub.order >= 3)]))
    # C. plan A versus order >= 3, slot replays on near_P20 fills.
    src = {'exp-monday-limit-v3-grid-20261025-v1': ['near_P20']}
    with ProcessPoolExecutor(4) as ex:
        g = pd.concat([d for d in ex.map(so.legs, [(s, src, events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    g = g.merge(sub[['event', 'order']], on='event')
    g['entry_ns'] = g.entry_t.astype('int64'); g['exit_ns'] = g.exit_t.astype('int64')
    pers_g = {k: f for k, f in pers.items() if k != 'all'}
    res['slots'] = {}
    for name, x in (('order_ge1', g), ('order_ge3', g[g.order >= 3])):
        for n in (10, 5):
            for L in (3, 5):
                for k, f in pers_g.items():
                    y = x[f(x.rename(columns={'entry_t': 't'}))].reset_index(drop=True)
                    reps = [so.simulate(y, L, n, True, True, seed=cfg['seed']+r) for r in range(cfg['ties'])]
                    res['slots'][f'{name}|N{n}|L{L}|{k}'] = {kk: float(np.median([r[kk] for r in reps])) for kk in reps[0]}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    f2 = lambda s: f"n{s['n']} win {s.get('win_pct') or 0:.1f} mean {s.get('mean_bp') or 0:.0f}"
    for key in ('order', 'score', 'side', 'same_bar'):
        for k, v in res[key].items():
            print(key, k, ' | '.join(f'{p}: {f2(x)}' for p, x in v.items()))
    print('order_ge3', ' | '.join(f'{p}: {f2(x)}' for p, x in res['order_ge3'].items()))
    for c in ('confirm_60m', 'confirm_5m'):
        for k, v in res[c].items():
            print(c, k, ' | '.join(f'{p}: {f2(x)}' for p, x in v.items()))
    for k, v in res['slots'].items():
        print(f'{k:28s}', f"x{v['final_multiple']:.2f} dd {v['max_drawdown_pct']:.0f} liq {v['liquidations']:.0f} win {v['win_pct']:.1f} taken {v['taken']:.0f} skip {v['skipped']:.0f}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

"""Langlang three-slot sizing and time to 10x / 100x for the high-payoff version (owner 2026-10-27).

Owner: "我没说固定6个月时间段，这个时间当然间隔越短越好，没说要一直爆仓，只是迫不得已 ... 仓位管理可以学学浪浪的：三份资金、
固定金额 ... 亏了从外面补进，赚了提走 ... 赚到两倍后每份提高 ... 不用复利". Read-only on existing NautilusTrader fills
(worse path, 10bp slippage on non-target exits, two bad source bars dropped, MAE from Codex 5m bars as in
monday_limit_spike_opt.legs).
Sizing schemes (starting capital 1000 USDT):
  langlang  capital in 3 equal shares; one share b sits in the futures account; each trade uses margin b at leverage
            L, one position at a time; a loss is topped up from the reserve, a gain above b goes back to the reserve;
            a position whose MAE reaches 100/L - 1 percent is liquidated and loses b. When total capital doubles from
            the last reset, b = total / 3. Ruin = total below 10 percent of the start; the run restarts with a
            fresh 1000 (counted as a blow-up of the whole stake).
  allin     the lottery rule of monday_limit_lottery (all of the account at L, one position at a time).
Metric (owner: shorter is better): from each monthly start 2023-01 .. 2026-08 run forward to the end of the data;
days until total capital (or account) first reaches 10x and 100x of the starting 1000 counting stakes put in, the
whole-stake blow-ups and slot liquidations before that, and whether it is reached at all. Medians over TIES tie
orders. In-sample.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, RUN, clean
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-langlang-20261027-v1'
A = ROOT/'experiments/active'
DAY = 86_400_000_000_000


def path_run(t, scheme, lev, seed, goals=(10, 100)):
    """Forward run from the first row; returns days to each goal (None if never), blow-ups and liquidations."""
    rng = np.random.default_rng(seed)
    o = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t = t.iloc[o].reset_index(drop=True)
    liq = 100./lev-1.
    start_ns = int(t.entry_ns.min()) if len(t) else 0
    stakes, blow, nliq, busy = 1, 0, 0, -1
    reserve, b, last_reset, acct = 2000/3, 1000/3, 1000., 1000.
    hit = {g: None for g in goals}
    for e, x, net, mae in zip(t.entry_ns.to_numpy(), t.exit_ns.to_numpy(), t.net.to_numpy()/1e4, t.mae_pct.to_numpy()):
        if e < busy:
            continue
        busy = x if x > e else e+1
        if scheme == 'allin':
            m = acct
            acct += -m if mae >= liq else max(-m, m*lev*net)
            nliq += mae >= liq
            total = acct
            if acct < 100.:
                blow += 1; stakes += 1; acct = 1000.
        else:
            pnl = -b if mae >= liq else max(-b, b*lev*net)
            nliq += mae >= liq
            reserve += pnl  # top up a loss from the reserve or send a gain back: the slot stays at b
            total = reserve+b
            if total < 100.:
                blow += 1; stakes += 1; reserve, b, last_reset = 2000/3, 1000/3, 1000.
                total = 1000.
            elif total >= 2*last_reset:
                b, last_reset = total/3, total
                reserve = total-b
        for g in goals:
            if hit[g] is None and total >= g*1000.*stakes:
                hit[g] = dict(days=(x-start_ns)/DAY, blowups=blow, liquidations=nliq)
    return hit


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_langlang.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    tn = pd.read_csv(ROOT/cfg['trades'], usecols=['event', 'variant', 'score'])
    ev = tn[(tn.variant == 'v3_be17')]
    events = set(ev.event)-BAD_EVENTS; syms = sorted({e.split(':')[0] for e in events})
    src = {RUN.name: ['v3_be17'], 'exp-monday-limit-v3-levels-20261025-v1': ['opp1618_P30']}
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(so.legs, [(s, src, events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64')
    sets = {'v3': t[t.variant == 'v3_be17'], 'v3_ge06_opp1618': t[(t.variant == 'opp1618_P30') & (t.score >= .6)]}
    starts = pd.date_range(cfg['first_start'], cfg['last_start'], freq='MS', tz='UTC')
    res = dict(sets={k: int(len(v)) for k, v in sets.items()}, starts=len(starts), grid={})
    for name, x in sets.items():
        for scheme in cfg['schemes']:
            for L in cfg['leverage']:
                rows = []
                for k, s0 in enumerate(starts):
                    y = x[x.entry_t >= s0].reset_index(drop=True)
                    for r in range(cfg['ties']):
                        h = path_run(y, scheme, L, cfg['seed']+1000*k+r)
                        rows.append(dict(start=str(s0.date()), **{f'd{g}': (h[g]['days'] if h[g] else np.nan) for g in (10, 100)},
                                         **{f'b{g}': (h[g]['blowups'] if h[g] else np.nan) for g in (10, 100)}))
                d = pd.DataFrame(rows)
                out = {}
                for g in (10, 100):
                    reached = d[f'd{g}'].notna()
                    out[f'x{g}'] = dict(reached_pct=float(100*reached.mean()), days_median=float(d[f'd{g}'][reached].median()) if reached.any() else None,
                                        within_90d_pct=float(100*(d[f'd{g}'] <= 90).mean()), within_180d_pct=float(100*(d[f'd{g}'] <= 180).mean()),
                                        within_365d_pct=float(100*(d[f'd{g}'] <= 365).mean()),
                                        blowups_before_median=float(d[f'b{g}'][reached].median()) if reached.any() else None)
                out['by_start'] = {s: dict(d10=float(g.d10.median()) if g.d10.notna().any() else None, d100=float(g.d100.median()) if g.d100.notna().any() else None)
                                   for s, g in d.groupby('start')}
                res['grid'][f'{name}|{scheme}|L{L}'] = out
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(dict(sets=res['sets'], starts=res['starts'])))
    for k, v in res['grid'].items():
        a, c = v['x10'], v['x100']
        print(f"{k:32s} 10x: reached {a['reached_pct']:5.1f}% median {a['days_median'] or 0:6.0f}d <=90d {a['within_90d_pct']:5.1f} <=180d {a['within_180d_pct']:5.1f} "
              f"blow {a['blowups_before_median'] or 0:.0f} | 100x: reached {c['reached_pct']:5.1f}% median {c['days_median'] or 0:6.0f}d <=180d {c['within_180d_pct']:5.1f} <=365d {c['within_365d_pct']:5.1f} blow {c['blowups_before_median'] or 0:.0f}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

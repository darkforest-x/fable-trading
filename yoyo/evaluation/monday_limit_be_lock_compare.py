"""Profit lock at the reclaim decision: breakeven +50 / 100 / 200bp against 0 / 7 / 17bp (owner 2026-10-27).

Owner: "继续做收益最高的一版本，继续之前没弄完的" (the return-first version). Read-only on NautilusTrader fills:
exp-monday-limit-be-scalein-20261026-v1 (offsets 0 / 7 / 17bp) and exp-monday-limit-be-lock-20261027-v1 (50 / 100 /
200bp; a reclaim close short of the locked price exits at that close). Same events, frozen scores and stops; only the
offset differs. Per trade the worse intrabar path, 10bp slippage on non-target exits, the two bad source bars dropped,
MAE from Codex 5m bars (monday_limit_spike_opt.legs).
Reported per family (v2, v3) and offset: n, mean net, win rate (net > 1bp), exit-reason mix per period. For v3 the
sizing of monday_limit_return_opt on each offset: linear (score fraction, L 1-3) and slots (5 / 10 / 20 slots, 2-5x),
final multiple, drawdown and liquidations per period (2020-2024, 2025, 2026, last five months); medians over TIES
random orders of same-time fills. In-sample: the offsets are compared on the same history that chose v3.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, LAB, RUN, clean
from yoyo.evaluation.monday_limit_return_opt import linear
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-be-lock-compare-20261027-v1'
LOCK = ROOT/'experiments/active/exp-monday-limit-be-lock-20261027-v1'
A = ROOT/'experiments/active'


def events_of(symbol):
    out = set()
    for run in (RUN, LOCK):
        f = run/'ohlc'/'symbols'/symbol/'trades.csv.gz'
        if f.exists():
            out |= set(pd.read_csv(f, usecols=['trade_id']).trade_id.str.split(':').str[:3].str.join(':'))
    return out


def run(config):
    for p in ('yoyo/evaluation/monday_limit_be_lock_compare.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    if not (LOCK/'run_done.txt').exists():
        raise RuntimeError('Profit-lock run not finished')
    cfg = json.loads(config.read_text())
    syms = json.loads((LOCK/'config.json').read_text())['symbols']
    src = {RUN.name: cfg['base_variants'], LOCK.name: cfg['lock_variants']}
    with ProcessPoolExecutor(4) as ex:
        events = set().union(*ex.map(events_of, syms, chunksize=8))-BAD_EVENTS
        t = pd.concat([d for d in ex.map(so.legs, [(s, src, events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64')
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': lambda x: x.entry_t < B[0], 'y2025': lambda x: (x.entry_t >= B[0]) & (x.entry_t < B[1]),
            'y2026': lambda x: x.entry_t >= B[1], 'last5m': lambda x: x.entry_t >= window}
    res = dict(rows={v: int(len(g)) for v, g in t.groupby('variant')}, per_trade={}, sizing={})
    for v, g in t.groupby('variant'):
        res['per_trade'][v] = {per: dict(n=int(f(g).sum()), mean_bp=float(g[f(g)].net.mean()), win_pct=float(100*(g[f(g)].net > 1).mean()),
                                         sum_r=float(g[f(g)].net.sum()/1e4),
                                         reasons={LAB.get(int(k), str(k)): float(100*c) for k, c in g[f(g)].reason.value_counts(normalize=True).items()})
                               for per, f in pers.items()}
    for v in cfg['sized']:
        x0 = t[t.variant == v]
        for fam, n, L in [('linear', None, L) for L in cfg['linear_leverage']]+[('slots', n, L) for n in cfg['slots'] for L in cfg['slot_leverage']]:
            key = f"{v}|{fam}|{'' if n is None else f'N{n}|'}L{L}"
            res['sizing'][key] = {}
            for per, f in pers.items():
                x = x0[f(x0)].reset_index(drop=True)
                k = np.minimum(cfg['fraction']*(x.score.to_numpy()-cut)/mexcess, cfg['cap'])
                reps = [linear(x, k, L, cfg['seed']+r) if fam == 'linear' else so.simulate(x, L, n, True, True, seed=cfg['seed']+r)
                        for r in range(cfg['ties'])]
                res['sizing'][key][per] = {kk: float(np.median([r[kk] for r in reps])) for kk in reps[0]}
    S = res['sizing']
    ok = []
    for k, v in S.items():
        if all(v[p]['max_drawdown_pct'] <= cfg['max_dd'] for p in pers):
            ok.append((float(np.prod([max(v[p]['final_multiple'], 1e-9) for p in pers])**(1/len(pers))), k))
    res['dd_ok_all_periods'] = [dict(setting=k, gmean=g, **S[k]) for g, k in sorted(ok, reverse=True)[:cfg['top']]]
    res = clean(res)
    EXP.mkdir(exist_ok=True)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res['rows']))
    for v, r in res['per_trade'].items():
        print(f'{v:9s}', *(f"{per}: n{x['n']} mean {x['mean_bp']:.0f} win {x['win_pct']:.0f} sum {x['sum_r']:.1f}R" for per, x in r.items()))
    fmt = lambda v: ' | '.join(f"x{v[p]['final_multiple']:.2f} dd{v[p]['max_drawdown_pct']:.0f} liq{v[p]['liquidations']:.0f}" for p in pers)
    for k in S:
        if '|linear|L1' in k or 'N20|L3' in k or 'N10|L2' in k:
            print(f'{k:24s}', fmt(S[k]))
    print('== dd <= 40 in all periods')
    for r in res['dd_ok_all_periods']:
        print(f"{r['setting']:24s} g{r['gmean']:.2f}", fmt(r))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

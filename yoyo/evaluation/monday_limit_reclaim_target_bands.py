"""Score-dependent take-profit for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Owner: "止盈是否也要根据评分来呢 默认就默认 评分高的分批止盈？". Trades: alt signals,
60m confirmation, every target and the equal-weight partials of
monday_limit_reclaim_allexits (framework fills, worse of the two intrabar paths,
10bp slippage on non-target exits, no backstop); frozen entry score, top-40
percent only. Score bands are the training-score percentiles 60-80 / 80-90 /
90-100 used by the sizing and backstop studies. Variants:
  near        near Monday edge for every band (current rule);
  owner       near for the two lower bands, 50/50 near + far Monday edge for the
              top band (the owner's default idea, fixed before looking);
  chosen      per band the target or partial with the best 2025 mean net per
              trade, frozen for 2026.
Sizing as in exp-monday-limit-reclaim-sizing-20261025-v1 (equal 10 percent and
linear in score), gross <= 1x, medians over TIES random orders of same-time
fills. Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-target-bands-20261025-v1'
A = ROOT/'experiments/active'
SEL = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
TIES = 20
NAMES = list(ax.SHORT.values())+list(ax.PARTIALS)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_target_bands.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = sorted(d.name for d in ax.ALL.iterdir() if (d/'done.json').exists() and (ax.V2/d.name/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        rows = pd.concat([g for g, _ in ex.map(ax.load, syms, chunksize=8)], ignore_index=True)
    r = rows[rows.m == 60]
    legs = {leg: g.set_index('event') for leg, g in r.groupby('leg')}
    score, cut = ax.scores()
    meta = json.loads((A/'exp-monday-limit-reclaim-sizing-20261025-v1/summary.json').read_text())['results']['shape_meta']
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    var = {}
    for n in NAMES:
        d = ax.combine(legs, ax.PARTIALS.get(n, (n,))).join(score.rename('score'), how='inner')
        d = d[d.score >= cut]
        d['t'] = pd.to_datetime(d.entry, utc=True)
        d['band'] = np.digitize(d.score, [meta['p80'], meta['p90']])
        var[n] = d
    sel = lambda d: (d.t >= SEL[0]) & (d.t < SEL[1])
    mexcess = float((var['near'].score[sel(var['near'])]-cut).mean())
    periods = {'selection_2025': sel, 'test_2026': lambda d: d.t >= SEL[1], 'test_last5m': lambda d: d.t >= window}
    table = {int(b): {n: {per: dict(n=int(f(var[n][var[n].band == b]).sum()), mean_bp=float(var[n][(var[n].band == b) & f(var[n])].net.mean()))
                          for per, f in periods.items()} for n in NAMES} for b in range(3)}
    chosen = {b: max(NAMES, key=lambda n: table[b][n]['selection_2025']['mean_bp']) for b in range(3)}
    plans = {'near': {0: 'near', 1: 'near', 2: 'near'}, 'owner': {0: 'near', 1: 'near', 2: 'near+far'}, 'chosen': chosen}
    rng = np.random.default_rng(20261025)
    res = dict(threshold=cut, band_edges=[cut, meta['p80'], meta['p90']], linear_mean_excess=mexcess, band_table=table,
               chosen=chosen, plans={})
    for name, plan in plans.items():
        idx = var['near'].index
        for d in var.values():
            idx = idx.intersection(d.index)
        x = pd.concat([var[plan[b]].loc[idx][var[plan[b]].loc[idx].band == b] for b in range(3)])
        x = x.reset_index().sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
        x['k_equal'] = .10
        x['k_linear'] = np.minimum(.05*(x.score-cut)/mexcess, .5)
        out = {}
        for per, f in periods.items():
            y = x[f(x)].reset_index(drop=True)
            out[per] = dict(n=len(y), win_pct=float(100*(y.net > 0).mean()), mean_bp=float(y.net.mean()),
                            linear_weighted_bp=float((y.net*y.k_linear).sum()/y.k_linear.sum()))
            for sizing in ('equal', 'linear'):
                rep = []
                for _ in range(TIES):
                    order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
                    z = y.iloc[order].reset_index(drop=True)
                    rep.append(opt.replay_w(z, z[f'k_{sizing}'].to_numpy(), 1.)[0])
                fm = np.array([q['final_multiple'] for q in rep]); dd = np.array([q['max_drawdown_pct'] for q in rep])
                out[per][sizing] = dict(final_median=float(np.median(fm)), final_p10=float(np.quantile(fm, .1)),
                                        final_p90=float(np.quantile(fm, .9)), dd_median=float(np.median(dd)))
        res['plans'][name] = dict(plan=plan, results=out)
    EXP.mkdir(exist_ok=True)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(dict(chosen=chosen), ensure_ascii=False))
    for b in range(3):
        print('band', b, ' '.join(f"{n}:{table[b][n]['selection_2025']['mean_bp']:.1f}/{table[b][n]['test_2026']['mean_bp']:.1f}" for n in NAMES))
    for name, v in res['plans'].items():
        print(name, v['plan'], *(f"{per}: n{o['n']} win{o['win_pct']:.1f} mean{o['mean_bp']:.1f} eq {o['equal']['final_median']:.2f}/{o['equal']['dd_median']:.0f} "
                                 f"lin {o['linear']['final_median']:.2f}/{o['linear']['dd_median']:.0f}" for per, o in v['results'].items()))


if __name__ == '__main__':
    run()

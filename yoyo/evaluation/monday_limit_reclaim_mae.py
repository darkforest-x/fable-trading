"""Floating loss before the exit (max adverse excursion) in the Monday1618 limit-reclaim v2 trades (owner2026-10-25).

Owner: "那些盈利的单子有浮亏过吗？有多少浮亏的？". Trades: alt signals, 60m
confirmation, near Monday edge target; bars from the Codex O-H-L-C ledger
(entry_i, exit_i) and Codex's own 5m inputs (load_job); outcome = worse of the
O-H-L-C ledger and the monotone O-L-H-C v2 replay, 10bp slippage on non-target
exits; frozen entry score. Adverse excursion = largest move against the trade
from the fill: the fill bar's extreme counts (it always comes after a limit
fill, whichever way the bar runs); "strict" stops before the exit bar,
"loose" includes it (its extreme may come after a target fill). Reported in
percent of entry and in R (R = target distance / 1.618 = 0.382 Monday range),
for winners (worst net > 0), breakeven exits and failed reclaims, plus the
share of winners whose floating loss reached each backstop distance of the
grid in exp-monday-limit-backstop-*. Read-only; no trade is re-executed.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE
from yoyo.evaluation import monday_limit_reclaim_allexits as ax

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-mae-20261025-v1'
COLS = ['trade_id', 'population', 'reclaim_minutes', 'exit_mode', 'closed', 'side', 'entry', 'target_price', 'entry_i',
        'exit_i', 'reason', 'net_bp', 'entry_time']
R_GRID, P_GRID = [1, 2, 3, 5, 8], [5, 10, 20, 30]


def excursions(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge') & t.closed].copy()
    l = pd.read_csv(ax.V2/symbol/'trades.csv.gz', usecols=['trade_id', 'net_notional_bp', 'reason'])
    t = t.merge(l.rename(columns={'net_notional_bp': 'net_l', 'reason': 'reason_l'}), on='trade_id')
    if t.empty:
        return t
    f, _ = load_job(job)
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    strict, loose = [], []
    for a, b, s in zip(t.entry_i.astype(int), t.exit_i.astype(int), t.side):
        seg = slice(a, max(a+1, b))
        adv = lo if s == 1 else h
        pick = np.min if s == 1 else np.max
        strict.append(pick(adv[seg])); loose.append(pick(adv[a:b+1]))
    t['adv_strict'], t['adv_loose'] = strict, loose
    t['symbol'] = symbol
    return t


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_mae.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    syms = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat(list(ex.map(excursions, syms, chunksize=8)), ignore_index=True)
    slip = lambda n, r: n-np.where(r == 4, 0., 10.)
    t['net'] = np.minimum(slip(t.net_bp, t.reason), slip(t.net_l, t.reason_l))
    t['R_pct'] = 100*(t.target_price-t.entry).abs()/t.entry/1.618
    for k in ('strict', 'loose'):
        t[f'mae_{k}_pct'] = np.maximum(0., -t.side*(t[f'adv_{k}']-t.entry)/t.entry*100)
        t[f'mae_{k}_R'] = t[f'mae_{k}_pct']/t.R_pct
    t['event'] = t.trade_id.str.split(':').str[:3].str.join(':')
    score, cut = ax.scores()
    t['score'] = t.event.map(score)
    t['t'] = pd.to_datetime(t.entry_time, utc=True)
    failed = (t.reason == 11) | (t.reason_l == 11)
    t['group'] = np.select([t.net > 0, failed, (t.reason == 5) | (t.reason_l == 5)], ['win', 'failed_reclaim', 'breakeven'], 'other_loss')
    res = {}
    for pop, m in (('from2025_top40', (t.t >= SPLIT) & (t.score >= cut)), ('from2025_all', t.t >= SPLIT), ('all', t.t.notna())):
        x = t[m]
        out = dict(n=len(x), R_pct_median=float(x.R_pct.median()))
        for g, y in x.groupby('group'):
            d = dict(n=len(y))
            for k in ('strict', 'loose'):
                d[k] = dict(pct_q={str(q): float(y[f'mae_{k}_pct'].quantile(q)) for q in (.25, .5, .75, .9, .95, .99)},
                            R_q={str(q): float(y[f'mae_{k}_R'].quantile(q)) for q in (.25, .5, .75, .9, .95, .99)},
                            share_reached_R={str(r): float((y[f'mae_{k}_R'] >= r).mean()) for r in R_GRID},
                            share_reached_pct={str(q): float((y[f'mae_{k}_pct'] >= q).mean()) for q in P_GRID},
                            share_any_float_loss=float((y[f'mae_{k}_pct'] > 0).mean()))
            out[g] = d
        res[pop] = out
    EXP.mkdir(exist_ok=True)
    charts(t[(t.t >= SPLIT) & (t.score >= cut)])
    dump(EXP/'summary.json', dict(config=json.loads((EXP/'config.json').read_text()), results=res,
         source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])


def charts(x):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    grid = np.linspace(0, 10, 201)
    for ax_, unit, xs, lab in ((axes[0], 'R', grid, '最大浮亏（R，1R = 1.618 到 2.0 扩展位）'), (axes[1], 'pct', grid*4, '最大浮亏（% 价格）')):
        for g, col, name in (('win', BLUE, '盈利单'), ('failed_reclaim', ORANGE, '没收回止损单')):
            v = x.loc[x.group == g, f'mae_strict_{unit}'].to_numpy()
            ax_.plot(xs, [100*(v >= z).mean() for z in xs], color=col, lw=2, label=f'{name}（{len(v)}笔）')
        ax_.set_xlabel(lab); ax_.set_ylabel('浮亏达到该幅度的比例（%）'); ax_.grid(color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].legend(frameon=False)
    fig.suptitle('2025年起分数前40% · 出场前最大浮亏（成交那根K线的极值计入，出场那根不计）', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_mae_curves.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

"""Fast cut, slow breakeven: hybrids of the Codex Monday1618 limit-reclaim rules (owner2026-10-24).

Owner: 60 minutes is too long; more ideas. Rules a<b (5/15/60m confirmation)
are identical until the a-minute confirmation close (both only hold a resting
target). Hybrid a->b: if the a-minute candle fails to close back inside, exit
there (rule a's recorded outcome); otherwise do nothing at a and follow rule b
(its recorded outcome, breakeven only after the b-minute check). Outcomes are
therefore exact framework fills; nothing is re-executed. Random controls use
the same construction per control id (same submission across rules).
Alts, near Monday edge target, snapshot output/monday-partial-snapshot-20261024-2224,
split 2025-01-01. Subgroups use exp-monday-limit-reclaim-patterns3 entry-time
features (volatility quintile, fast approach, volume spike, opposite first).
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SNAP, SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-hybrid-20261024-v1'
FEAT = ROOT/'experiments/active/exp-monday-limit-reclaim-patterns3-20261024-v1/events_60m.csv.gz'
COLS = ['trade_id', 'asset_group', 'population', 'reclaim_minutes', 'exit_mode', 'net_bp', 'reclaim_status', 'closed', 'entry_time']
RULES = [('5', 5, None), ('15', 15, None), ('60', 60, None), ('5→15', 5, 15), ('5→60', 5, 60), ('15→60', 15, 60)]


def load(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    return t[(t.asset_group == 'ALTCOINS') & (t.exit_mode == 'near_monday_edge') & t.reclaim_minutes.isin([5, 15, 60])]


def wide(d):
    """One row per (event, control id) with net and status for each confirmation rule."""
    parts = d.trade_id.str.split(':')
    d = d.assign(event=parts.str[:3].str.join(':'), cid=parts.str[-1])
    w = d.set_index(['event', 'cid', 'reclaim_minutes'])[['net_bp', 'reclaim_status', 'closed']].unstack('reclaim_minutes')
    w.columns = [f'{a}_{b}' for a, b in w.columns]
    first = d.drop_duplicates(['event', 'cid']).set_index(['event', 'cid'])
    w['population'] = first.population; w['t'] = pd.to_datetime(first.entry_time, utc=True)
    w = w.reset_index()
    for name, a, b in RULES:
        if b is None:
            w[f'r_{name}'] = w[f'net_bp_{a}'].where(w[f'closed_{a}'] == True)
        else:
            cut = w[f'reclaim_status_{a}'] == 'failed'
            ok = (w[f'closed_{a}'] == True) & (cut | (w[f'closed_{b}'] == True))
            w[f'r_{name}'] = np.where(cut, w[f'net_bp_{a}'], w[f'net_bp_{b}'])
            w.loc[~ok, f'r_{name}'] = np.nan
            w[f'cut_{name}'] = cut
    return w


def stats(sig, ctl, mask):
    out = {}
    for half, h in (('discovery', sig.t < SPLIT), ('validation', sig.t >= SPLIT)):
        g = sig[mask & h]; out[half] = {}
        for name, a, b in RULES:
            v = g[f'r_{name}'].dropna()
            c = ctl.reindex(g.event)[f'r_{name}']
            full = ctl.reindex(g.event)[f'n_{name}'].to_numpy() == 3
            ok = full & g[f'r_{name}'].notna().to_numpy()
            out[half][name] = dict(n=len(v), win_pct=float(100*(v > 0).mean()), mean_bp=float(v.mean()), median_bp=float(v.median()),
                                   excess_bp=float((g[f'r_{name}'].to_numpy()[ok]-c.to_numpy()[ok]).mean()),
                                   cut_pct=float(100*g[f'cut_{name}'].mean()) if b else None)
    return out


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    labels = {'5': '5分钟确认', '15': '15分钟确认', '60': '60分钟确认', '5→15': '5分钟砍单\n15分钟保本',
              '5→60': '5分钟砍单\n60分钟保本', '15→60': '15分钟砍单\n60分钟保本'}
    groups = list(res)
    fig, axes = plt.subplots(len(groups), 2, figsize=(16, 3.6*len(groups)))
    for r, gname in enumerate(groups):
        for c, (metric, title) in enumerate((('win_pct', '胜率 %'), ('mean_bp', '每笔扣费后平均 bp'))):
            ax = axes[r, c]; x = np.arange(len(RULES)); w = .38
            for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
                v = [res[gname][half][n][metric] for n, _, _ in RULES]
                ax.bar(x+off, v, w*.92, color=col, label=lab)
                for xx, vv in zip(x+off, v):
                    ax.text(xx, vv, f'{vv:.0f}', ha='center', va='bottom' if vv >= 0 else 'top', color=INK, fontsize=8)
            ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, [labels[n] for n, _, _ in RULES], fontsize=8.5)
            ax.grid(axis='y', color=GRID, lw=.6); ax.set_title(f'{gname} · {title}', color=INK, loc='left', fontsize=11)
            for sp in ('top', 'right'):
                ax.spines[sp].set_visible(False)
    h, l = axes[0, 0].get_legend_handles_labels(); fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
    fig.suptitle('快速砍单 + 慢速保本 · 山寨币 1.618 挂单 · 止盈近侧周一边界', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_hybrids.png', dpi=110); plt.close(fig)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_hybrid.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(4) as ex:
        w = wide(pd.concat(list(ex.map(load, syms, chunksize=8)), ignore_index=True))
    sig = w[w.population == 'signal'].set_index('event', drop=False)
    rc = w[w.population == 'random']
    agg = {f'r_{n}': (f'r_{n}', 'mean') for n, _, _ in RULES} | {f'n_{n}': (f'r_{n}', 'count') for n, _, _ in RULES}
    ctl = rc.groupby('event').agg(**agg)
    f = pd.read_csv(FEAT)
    f['event'] = f.symbol+':'+f.week+':'+np.where(f.side_n == 1, '+1', '-1')
    f = f.set_index('event').reindex(sig.event)
    masks = {'全部': np.ones(len(sig), bool), '触及前波动最高20%': (f.vol_q == '最高').to_numpy(),
             '急速插针（30分钟内）只做多': ((f.approach_min < 30) & (f.side_n == 1)).to_numpy(),
             '触及前1小时成交额≥8倍': (f.vol_ratio >= 8).to_numpy()}
    res = {k: stats(sig, ctl, m) for k, m in masks.items()}
    EXP.mkdir(exist_ok=True)
    charts(res)
    dump(EXP/'summary.json', dict(config=cfg, events=int(len(sig)), results=res,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(res['全部'], ensure_ascii=False)[:2000])


if __name__ == '__main__':
    run()

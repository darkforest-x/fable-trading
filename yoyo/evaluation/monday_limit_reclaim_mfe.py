"""Floating profit before the stop in the Codex Monday1618 limit-reclaim trades (owner2026-10-25).

Owner: "有多少浮盈打止损的呢". For every alt signal trade with 60m confirmation
and the near Monday edge target (Codex O-H-L-C ledger, all 626 5m alt symbols),
the best unrealized move is read from Codex's own 5m bars (load_job):
  strict: bars strictly between the fill bar and the exit bar (no intrabar
    ordering needed);
  loose: also the exit bar (its extreme may come after the exit).
Excursion is reported in percent of the entry price and as a fraction of the
distance to the target. Trades that exit at breakeven (stop moved to entry) or
on a failed reclaim are counted by how far they had run first. Read-only;
nothing is re-executed. Same-bar exits have no bars in between.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-mfe-20261025-v1'
COLS = ['trade_id', 'asset_group', 'population', 'reclaim_minutes', 'exit_mode', 'side', 'entry', 'target_price',
        'entry_i', 'exit_i', 'reason', 'closed', 'net_bp', 'entry_time']
LABEL = {4: '止盈', 5: '收回后保本出场', 11: '没收回止损'}


def excursions(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return pd.DataFrame()
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    t = t[(t.asset_group == 'ALTCOINS') & (t.population == 'signal') & (t.reclaim_minutes == 60)
          & (t.exit_mode == 'near_monday_edge') & t.closed].copy()
    if t.empty:
        return t
    f, _ = load_job(job)
    h, l = f.high.to_numpy(), f.low.to_numpy()
    strict, loose = [], []
    for r in t.itertuples():
        a, b = int(r.entry_i), int(r.exit_i)
        best = h if r.side == 1 else l
        pick = np.max if r.side == 1 else np.min
        strict.append(pick(best[a+1:b]) if b > a+1 else np.nan)
        loose.append(pick(best[a+1:b+1]) if b > a else np.nan)
    t['symbol'] = symbol
    for name, ext in (('strict', strict), ('loose', loose)):
        ext = np.array(ext, float)
        t[f'mfe_{name}_pct'] = np.maximum(0., t.side*(ext-t.entry)/t.entry*100)
        t[f'mfe_{name}_frac'] = np.maximum(0., (ext-t.entry)/(t.target_price-t.entry))
    t['tp_pct'] = t.side*(t.target_price-t.entry)/t.entry*100
    return t


def summarize(t, col):
    out = {}
    for r, g in t.groupby('reason'):
        v = g[col].fillna(0)
        out[LABEL.get(int(r), str(r))] = dict(n=len(g), **{f'>={int(x*100)}%': int((v >= x).sum()) for x in (.25, .5, .75, .9)},
                                             median=float(v.median()))
    return out


def charts(t):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.8))
    bins = np.linspace(0, 1, 21)
    for ax, (r, title) in zip(axes, ((5, '收回后保本出场的单子'), (11, '没收回止损的单子'))):
        g = t[t.reason == r]
        v = g.mfe_strict_frac.fillna(0).clip(upper=1)
        ax.hist(v, bins=bins, color=BLUE if r == 5 else ORANGE, edgecolor=SURF, linewidth=1.5)
        for x in (.5, .9):
            k = int((v >= x).sum())
            ax.axvline(x, color='#c3c2b7', lw=1, ls='--')
            ax.text(x, ax.get_ylim()[1]*.92, f' ≥{int(x*100)}%：{k}笔（{100*k/len(g):.1f}%）', color=INK2, fontsize=9)
        ax.set_xlabel('出场前最大浮盈 / 到止盈的距离'); ax.set_ylabel('笔数')
        ax.set_title(f'{title}（{len(g)} 笔）', color=INK, loc='left')
        ax.set_xticks([0, .25, .5, .75, 1], ['0', '25%', '50%', '75%', '100%'])
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    fig.suptitle('浮盈有多少就回头了 · 山寨币 1.618 挂单 · 60分钟确认 · 止盈近侧周一边界（只看成交和出场之间的K线）', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_mfe_before_stop.png', dpi=130); plt.close(fig)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_mfe.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = sorted(x.name for x in SRC.iterdir() if (x/'identity.json').exists())
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([x for x in ex.map(excursions, syms, chunksize=4) if len(x)], ignore_index=True)
    t.drop(columns=['asset_group', 'population', 'reclaim_minutes', 'exit_mode', 'closed']).to_csv(EXP/'trades_mfe.csv.gz', index=False)
    nontp = t[t.reason != 4]
    res = dict(trades=len(t), symbols=int(t.symbol.nunique()),
               by_reason_strict=summarize(t, 'mfe_strict_frac'), by_reason_loose=summarize(t, 'mfe_loose_frac'),
               be_mfe_pct_quantiles=t[t.reason == 5].mfe_strict_pct.fillna(0).quantile([.5, .75, .9, .95]).to_dict(),
               nontp_half_way=int((nontp.mfe_strict_frac >= .5).sum()), nontp_n=len(nontp),
               by_side={str(s): summarize(g, 'mfe_strict_frac') for s, g in t.groupby('side')})
    EXP.mkdir(exist_ok=True)
    charts(t)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()

"""Fine score bands: key-level reach and take-profit ladders for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Owner: "还是不够细化啊 怎么定义高分？只算0.375？那0.5 0.6 0.9呢？". Same inputs and
definitions as monday_limit_reclaim_ladders (framework fills, 60m confirmation,
worse of the two intrabar paths, 10bp slippage on non-target exits, no
backstop, frozen entry score, top-40 percent), with fixed score bands
0.226 / 0.30 / 0.375 / 0.45 / 0.50 / 0.60 / 0.70 / 0.80 / 1. Per band: trade
counts, reach rate of each key level and continuation given the previous
level, median hours to reach, and for every ladder the mean net per trade in
2025, 2026 and from 2025, plus the paired difference to the near-edge target on
the same trades with its standard error (from 2025). Small bands are noisy;
the paired standard error says which differences are larger than noise.
Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation.monday_limit_reclaim_ladders import LADDERS, LEVELS, LZH, DZH, ladder

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-score-detail-20261025-v1'
EDGES = [.30, .375, .45, .50, .60, .70, .80]
SEL = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))


def labels(cut):
    lo = [cut]+EDGES
    hi = EDGES+[1.]
    return [f'{a:.3f}'.rstrip('0').rstrip('.')+'-'+(f'{b:.3f}'.rstrip('0').rstrip('.') if b < 1 else '1') for a, b in zip(lo, hi)]


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_score_detail.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = sorted(d.name for d in ax.ALL.iterdir() if (d/'done.json').exists() and (ax.V2/d.name/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        rows = pd.concat([g for g, _ in ex.map(ax.load, syms, chunksize=8)], ignore_index=True)
    score, cut = ax.scores()
    r = rows[rows.m == 60].copy()
    r['score'] = r.event.map(score)
    r = r[r.score >= cut]
    r['band'] = np.digitize(r.score, EDGES)
    r['t'] = pd.to_datetime(r.entry, utc=True)
    worse = r.net_l < r.net_o
    r['hit'] = np.where(worse, r.reason_l, r.reason_o) == 4
    r['hold_h'] = (np.where(worse, r.exit_l, r.exit_o)-r.entry)/3.6e12
    legs = {leg: g.set_index('event') for leg, g in r.groupby('leg')}
    lad = {k: ladder(legs, w) for k, w in LADDERS.items()}
    idx = lad['near'].index
    for d in lad.values():
        idx = idx.intersection(d.index)
    for n in LEVELS:
        idx = idx.intersection(legs[n].index)
    base = lad['near'].loc[idx]
    H = pd.DataFrame({n: legs[n].loc[idx, 'hit'] for n in LEVELS})
    T = pd.DataFrame({n: legs[n].loc[idx, 'hold_h'] for n in LEVELS})
    N = pd.DataFrame({k: lad[k].loc[idx, 'net'] for k in LADDERS})
    names = labels(cut)
    periods = {'y2025': (base.t >= SEL[0]) & (base.t < SEL[1]), 'y2026': base.t >= SEL[1], 'from_2025': base.t >= SEL[0]}
    bands = {}
    for b, name in enumerate(names):
        inb = base.band == b
        m = inb & periods['from_2025']
        h = H[m]
        out = dict(n={per: int((inb & msk).sum()) for per, msk in periods.items()}, score_range=name,
                   long_share_pct=float(100*(base.side[m] == 1).mean()) if m.any() else None,
                   reach_pct={n: float(100*h[n].mean()) for n in LEVELS},
                   continue_pct={LEVELS[i]: (float(100*h[LEVELS[i]][h[LEVELS[i-1]]].mean()) if h[LEVELS[i-1]].any() else None)
                                 for i in range(1, len(LEVELS))},
                   hours_median={n: (float(T[m][n][h[n]].median()) if h[n].any() else None) for n in LEVELS}, ladders={})
        for k in LADDERS:
            v = {per: float(N[k][inb & msk].mean()) for per, msk in periods.items()}
            diff = (N[k]-N['near'])[m]
            v['win_pct_from_2025'] = float(100*(N[k][m] > 0).mean())
            v['diff_vs_near_from_2025'] = float(diff.mean())
            v['diff_se'] = float(diff.std(ddof=1)/np.sqrt(len(diff))) if len(diff) > 1 else None
            out['ladders'][k] = v
        best = max(LADDERS, key=lambda k: out['ladders'][k]['from_2025'])
        out['best_from_2025'] = best
        out['best_beats_near_by_2se'] = bool(best != 'near' and out['ladders'][best]['diff_vs_near_from_2025'] > 2*out['ladders'][best]['diff_se'])
        bands[name] = out
    EXP.mkdir(exist_ok=True)
    charts(bands, names)
    dump(EXP/'summary.json', dict(config=cfg, threshold=cut, bands=bands, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    for name, o in bands.items():
        print(f"{name:10s} n25 {o['n']['y2025']:5d} n26 {o['n']['y2026']:5d} long {o['long_share_pct']:.0f}% reach " +
              '/'.join(f"{o['reach_pct'][n]:.0f}" for n in LEVELS) + ' cont ' + '/'.join(f"{o['continue_pct'][n]:.0f}" if o['continue_pct'][n] is not None else '-' for n in LEVELS[1:]) +
              ' h ' + '/'.join(f"{o['hours_median'][n]:.1f}" if o['hours_median'][n] is not None else '-' for n in LEVELS))
        print('   ', ' '.join(f"{k}:{o['ladders'][k]['y2025']:.0f}/{o['ladders'][k]['y2026']:.0f}/{o['ladders'][k]['from_2025']:.0f}"
                              f"(d{o['ladders'][k]['diff_vs_near_from_2025']:+.0f}±{o['ladders'][k]['diff_se'] or 0:.0f})" for k in LADDERS),
              '| best', o['best_from_2025'], o['best_beats_near_by_2se'])


def charts(bands, names):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    ramp = plt.get_cmap('Blues')(np.linspace(.35, 1, len(names)))
    fig, axes = plt.subplots(1, 2, figsize=(17, 5.5))
    x = np.arange(len(LEVELS))
    for i, name in enumerate(names):
        o = bands[name]
        axes[0].plot(x, [o['reach_pct'][n] for n in LEVELS], color=ramp[i], lw=2, marker='o', label=f"分数{name}（{o['n']['from_2025']}笔）")
        axes[1].plot(x[1:], [o['continue_pct'][n] if o['continue_pct'][n] is not None else np.nan for n in LEVELS[1:]], color=ramp[i], lw=2, marker='o')
    axes[0].set_xticks(x, [LZH[n] for n in LEVELS]); axes[1].set_xticks(x[1:], [f'到{LZH[n]}' for n in LEVELS[1:]])
    axes[0].set_title('到达各点位的比例（%）', color=INK, loc='left'); axes[1].set_title('到了上一个点位后，继续到达这个点位的比例（%）', color=INK, loc='left')
    axes[0].legend(frameon=False, fontsize=8)
    for ax_ in axes:
        ax_.grid(color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    fig.suptitle('2025年起 · 细分分数段 · 较差K线顺序 · 无兜底止损', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_reach_by_fine_band.png', dpi=130); plt.close(fig)
    keys = list(LADDERS)
    M = np.array([[bands[n]['ladders'][k]['from_2025'] for k in keys] for n in names])
    D = np.array([[bands[n]['ladders'][k]['diff_vs_near_from_2025'] for k in keys] for n in names])
    S = np.array([[bands[n]['ladders'][k]['diff_se'] or np.inf for k in keys] for n in names])
    fig, ax_ = plt.subplots(figsize=(17, 5.8))
    lim = np.nanmax(np.abs(D))
    ax_.imshow(D, cmap='RdBu', vmin=-lim, vmax=lim, aspect='auto')
    for i in range(len(names)):
        for j in range(len(keys)):
            sig = abs(D[i, j]) > 2*S[i, j] and keys[j] != 'near'
            ax_.text(j, i, f"{M[i, j]:.0f}" + ('*' if sig else ''), ha='center', va='center', fontsize=9,
                     color=INK if abs(D[i, j]) < .6*lim else 'white', fontweight='bold' if sig else 'normal')
    ax_.set_xticks(range(len(keys)), [DZH[k] for k in keys], rotation=30, ha='right', fontsize=9)
    ax_.set_yticks(range(len(names)), [f"分数{n}（{bands[n]['n']['from_2025']}笔）" for n in names], fontsize=9)
    ax_.set_title('2025年起每笔平均净收益（bp）· 颜色=比"全部近侧边界"多赚(蓝)或少赚(红) · *=差距超过2倍标准误', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'2_ladder_grid_by_fine_band.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

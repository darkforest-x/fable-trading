"""Score-weighted position sizing at an equal drawdown for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Owner: "假如分越高的 我给他的仓位越多呢 在总回撤不变的情况下获取最高的收益 应该如何做".
Trades are the framework fills of the current rule (60m confirmation, near
Monday edge target, worst of the two intrabar paths, 10bp slippage on
non-target exits), loaded through monday_limit_reclaim_allexits; the entry
score is the frozen score_model.json (fitted before 2025). Sizing never
changes the fills, so this is arithmetic on fills, like every earlier equity
replay. Shapes w(score), each normalized to mean 1 over the selection-period
trades at or above the top-40 threshold:
  equal       1 at or above the threshold (current rule);
  tier        1 / 2 / 3 for training-score percentiles 60-80 / 80-90 / 90-100;
  linear      score - threshold, at or above the threshold;
  kelly_top40 mean/variance of net return per training-score decile measured
              in the selection period, clipped at 0, top-40 deciles only;
  kelly_all   the same over every decile (low-score deciles enter only if
              their selection-period mean is positive).
Per-trade fraction k = 0.10 * c * w, capped at 0.5 of equity; gross open
notional <= 1x equity (no leverage), as the current rule. Selection period
2025 (out of sample for the score): for every shape the scale c on a fixed grid
with the highest final multiple whose realized max drawdown does not exceed
that of the current rule (equal, c = 1) is frozen. Test period: 2026 and its
last-five-months window, frozen c, compared with the current rule. The full
c grid is kept as a return/drawdown frontier for both periods. Drawdown is on
realized equity at exits (no mark-to-market), ties at a timestamp keep
row order.
Because many alts fill at the same bar time and the 1x gross cap then decides
which of them get capital, the whole procedure (selection and test) is also
repeated for TIES random orders of fills sharing a timestamp; medians and
10-90 percent ranges are reported and the share of orders in which each shape
beats the current rule in the test.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SPLIT, INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-sizing-20261025-v1'
SEL = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
GRID_C = [.25, .5, .75, 1., 1.25, 1.5, 1.75, 2., 2.5, 3., 4., 5.]
TIES = 20
SHAPES = ['equal', 'tier', 'linear', 'kelly_top40', 'kelly_all']
ZH = dict(equal='现行：等额每笔10%', tier='分档 1/2/3 倍', linear='按超出门槛的分数线性加仓',
          kelly_top40='按分数段凯利比例（只做前40%）', kelly_all='按分数段凯利比例（全部分数段）')
COLOR = dict(equal=INK2, tier='#2a78d6', linear='#eb6834', kelly_top40='#1baf7a', kelly_all='#eda100')


def near_trades():
    syms = sorted(d.name for d in ax.ALL.iterdir() if (d/'done.json').exists() and (ax.V2/d.name/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        rows = pd.concat([g for g, _ in ex.map(ax.load, syms, chunksize=8)], ignore_index=True)
    r = rows[(rows.m == 60) & (rows.leg == 'near')]
    d = ax.combine({'near': r.set_index('event')}, ('near',))
    score, cut = ax.scores()
    d = d.join(score.rename('score'), how='inner')
    d['t'] = pd.to_datetime(d.entry, utc=True)
    return d.reset_index().sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True), cut


def shapes(d, cut, sel):
    """Raw weights per shape; percentile and decile cuts from training-period (pre-2025) scores."""
    train = d.score[d.t < SPLIT]
    p80, p90 = np.quantile(train, [.8, .9])
    dec_cuts = np.quantile(train, np.linspace(.1, .9, 9))
    dec = np.searchsorted(dec_cuts, d.score, side='right')
    top = (d.score >= cut).to_numpy()
    r = d.net.to_numpy()/1e4
    kelly = np.zeros(10)
    for q in range(10):
        x = r[sel & (dec == q)]
        if len(x) > 30:
            kelly[q] = max(x.mean()/x.var(), 0.)
    w = dict(equal=top*1., tier=np.where(d.score >= p90, 3., np.where(d.score >= p80, 2., 1.))*top,
             linear=np.clip(d.score.to_numpy()-cut, 0, None)*top, kelly_top40=kelly[dec]*top, kelly_all=kelly[dec])
    norm = {k: v/v[sel & top].mean() for k, v in w.items()}
    meta = dict(p80=float(p80), p90=float(p90), decile_cuts=dec_cuts.tolist(), kelly_by_decile=kelly.tolist(),
                decile_selection_mean_bp={int(q): float(1e4*r[sel & (dec == q)].mean()) for q in range(10)},
                decile_selection_n={int(q): int((sel & (dec == q)).sum()) for q in range(10)})
    return norm, meta


def replay(d, mask, k):
    x = d[mask].reset_index(drop=True)
    out, path = opt.replay_w(x, np.minimum(k[mask], .5), 1.)
    return out, path, x


def evaluate(d, w, periods):
    """Frontier on both periods, scale chosen on 2025 under the current rule's drawdown, frozen test."""
    res = dict(frontier={}, chosen={}, test={})
    paths = {}
    for name in SHAPES:
        for per in ('selection_2025', 'test_2026'):
            res['frontier'][f'{name}|{per}'] = [dict(c=c, **replay(d, periods[per] & (w[name] > 0), .10*c*w[name])[0]) for c in GRID_C]
    limit = next(r for r in res['frontier']['equal|selection_2025'] if r['c'] == 1.)['max_drawdown_pct']
    for name in SHAPES:
        ok = [r for r in res['frontier'][f'{name}|selection_2025'] if r['max_drawdown_pct'] <= limit+1e-9]
        best = max(ok, key=lambda r: r['final_multiple']) if ok else min(res['frontier'][f'{name}|selection_2025'], key=lambda r: r['c'])
        res['chosen'][name] = best
        k = .10*best['c']*w[name]
        for per in ('test_2026', 'test_last5m'):
            out, path, x = replay(d, periods[per] & (w[name] > 0), k)
            kk = np.minimum(k[periods[per] & (w[name] > 0)], .5)
            res['test'][f'{name}|{per}'] = out | dict(trades=len(x), mean_fraction_pct=float(100*kk.mean()), max_fraction_pct=float(100*kk.max()),
                                                    mean_bp=float(x.net.mean()), weighted_mean_bp=float((x.net*kk).sum()/kk.sum()))
            paths[f'{name}|{per}'] = path
    res['selection_drawdown_limit_pct'] = limit
    return res, paths


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_sizing.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    d, cut = near_trades()
    sel = ((d.t >= SEL[0]) & (d.t < SEL[1])).to_numpy()
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    periods = dict(selection_2025=sel, test_2026=(d.t >= SEL[1]).to_numpy(), test_last5m=(d.t >= window).to_numpy())
    w, meta = shapes(d, cut, sel)
    res, paths = evaluate(d, w, periods)
    res.update(n={k: int(v.sum()) for k, v in periods.items()}, threshold=cut, shape_meta=meta)
    rng = np.random.default_rng(20261025)
    runs = []
    for _ in range(TIES):
        order = np.lexsort((rng.random(len(d)), d.entry.to_numpy()))
        r, _ = evaluate(d.iloc[order].reset_index(drop=True), {k: v[order] for k, v in w.items()}, {k: v[order] for k, v in periods.items()})
        runs.append(r)
    rob = {}
    for name in SHAPES:
        row = {'chosen_c': [r['chosen'][name]['c'] for r in runs]}
        for per in ('test_2026', 'test_last5m'):
            f = np.array([r['test'][f'{name}|{per}']['final_multiple'] for r in runs])
            dd = np.array([r['test'][f'{name}|{per}']['max_drawdown_pct'] for r in runs])
            base = np.array([r['test'][f'equal|{per}']['final_multiple'] for r in runs])
            row[per] = dict(final_median=float(np.median(f)), final_p10=float(np.quantile(f, .1)), final_p90=float(np.quantile(f, .9)),
                            dd_median=float(np.median(dd)), dd_p90=float(np.quantile(dd, .9)), beats_current_share=float((f > base).mean()))
        rob[name] = row
    res['tie_order_robustness'] = dict(orders=TIES, by_shape=rob)
    EXP.mkdir(exist_ok=True)
    charts(res, paths)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(dict(n=res['n'], limit=res['selection_drawdown_limit_pct'], chosen=res['chosen'], test=res['test'],
                          robustness=rob, meta=meta), ensure_ascii=False, indent=1))


def charts(res, paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.6))
    for ax_, per, title in zip(axes, ('selection_2025', 'test_2026'), ('2025年（选参数）', '2026年（检验，参数冻结）')):
        for name in SHAPES:
            if name == 'kelly_all' and res['frontier']['kelly_all|selection_2025'] == res['frontier']['kelly_top40|selection_2025']:
                continue
            f = [r for r in res['frontier'][f'{name}|{per}'] if r['c'] <= 2.]
            ax_.plot([r['max_drawdown_pct'] for r in f], [r['final_multiple'] for r in f], color=COLOR[name], lw=2, marker='o', ms=4, label=ZH[name])
            ch = res['chosen'][name]['c']
            pt = next(r for r in f if r['c'] == ch)
            ax_.plot(pt['max_drawdown_pct'], pt['final_multiple'], marker='o', ms=11, mfc='none', mec=COLOR[name], mew=2)
        ax_.axvline(res['selection_drawdown_limit_pct'], color=MUTED, lw=1, ls='--')
        ax_.set_yscale('log'); ax_.set_xlabel('最大回撤（%，已实现权益）'); ax_.set_ylabel('期末倍数（对数）')
        ax_.set_title(title, color=INK, loc='left'); ax_.grid(color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].legend(frameon=False, loc='upper left', fontsize=9)
    fig.suptitle('按分数加仓 · 同样回撤下的收益 · 每条线是同一方案仓位系数0.25→2，空心圈=按2025年回撤上限选定的仓位，虚线=现行方案2025年回撤',
                 color=INK, x=.01, ha='left', fontsize=10)
    fig.tight_layout(); fig.savefig(EXP/'1_frontier.png', dpi=130); plt.close(fig)
    fig, ax_ = plt.subplots(figsize=(11, 5))
    for name in SHAPES:
        if name == 'kelly_all' and res['frontier']['kelly_all|selection_2025'] == res['frontier']['kelly_top40|selection_2025']:
            continue
        pp = np.array(paths.get(f'{name}|test_2026') or [])
        if len(pp):
            ax_.plot(pd.to_datetime(pp[:, 0]), pp[:, 1], color=COLOR[name], lw=2, label=f"{ZH[name]}（仓位系数 {res['chosen'][name]['c']}）")
    ax_.axhline(1000, color='#c3c2b7', lw=1); ax_.legend(frameon=False, loc='upper left', fontsize=9)
    ax_.set_ylabel('已实现权益（USDT）')
    for sp in ('top', 'right'):
        ax_.spines[sp].set_visible(False)
    ax_.set_title('2026年检验 · 1000U · 仓位系数按2025年回撤上限冻结 · 较差K线顺序 · 总仓位≤1倍', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'2_test_equity.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

"""Key-level reach statistics and laddered take-profits by score band for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Owner: "我是说关键点位 周一高低点 区间内0.5 0.618 远侧1.618 这几个点 你详细统计分析并回测看看".
Levels for a long (mirror for a short), from the lower 1618: near Monday edge
(Monday low), range 0.5, range 0.618, far Monday edge (Monday high), opposite
1618. Inputs are framework fills only (monday_limit_reclaim_allexits: 60m
confirmation, one independent position per level target, worse of the two
intrabar paths, 10bp slippage on non-target exits, no backstop); frozen entry
score, top-40 percent, bands = training-score percentiles 60-80 / 80-90 /
90-100. A level counts as reached when its position exits at the target under
the worse path. A ladder splits the position across levels with fixed
weights; each part keeps the same fill, confirmation and breakeven stop and
rests its own target, so a ladder is the weighted mean of its level positions
per path, then the worse path, freeing capital at its last part. Per band the
ladder with the best 2025 mean net per trade is frozen and tested on 2026 and
the last five months; replays as in the sizing study (equal 10 percent and
linear in score, gross <= 1x, medians over TIES random orders of same-time
fills). Read-only arithmetic on framework fills.
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
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-ladders-20261025-v1'
A = ROOT/'experiments/active'
SEL = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
TIES = 20
LEVELS = ['near', 'r05', 'r0618', 'far', 'opp1618']
LZH = dict(near='近侧周一边界', r05='区间0.5', r0618='区间0.618', far='远侧周一边界', opp1618='对侧1.618')
LADDERS = {
    'near': {'near': 1.}, 'r05': {'r05': 1.}, 'r0618': {'r0618': 1.}, 'far': {'far': 1.}, 'opp1618': {'opp1618': 1.},
    'lad2': {'near': .5, 'r05': .5}, 'lad3': {'near': 1/3, 'r05': 1/3, 'r0618': 1/3},
    'lad4': {'near': .25, 'r05': .25, 'r0618': .25, 'far': .25}, 'lad5': {k: .2 for k in LEVELS},
    'front4': {'near': .4, 'r05': .3, 'r0618': .2, 'far': .1},
    'front5': {'near': .3, 'r05': .25, 'r0618': .2, 'far': .15, 'opp1618': .1},
    'back5': {'near': .1, 'r05': .15, 'r0618': .2, 'far': .25, 'opp1618': .3},
}
DZH = dict(near='全部近侧边界（现行）', r05='全部区间0.5', r0618='全部区间0.618', far='全部远侧边界', opp1618='全部对侧1.618',
           lad2='近侧/0.5 各半', lad3='近侧/0.5/0.618 各1/3', lad4='近侧到远侧4档各1/4', lad5='5档各1/5',
           front4='4档 40/30/20/10', front5='5档 30/25/20/15/10', back5='5档 10/15/20/25/30')
BZH = {0: '分数0.226-0.30', 1: '分数0.30-0.375', 2: '分数0.375以上'}


def ladder(legs, weights):
    names = list(weights)
    idx = legs[names[0]].index
    for n in names[1:]:
        idx = idx.intersection(legs[n].index)
    g = {n: legs[n].loc[idx] for n in names}
    out = g[names[0]][['symbol', 'side', 'entry', 'score', 'band', 't']].copy()
    no = sum(w*g[n].net_o for n, w in weights.items()); nl = sum(w*g[n].net_l for n, w in weights.items())
    eo = np.maximum.reduce([g[n].exit_o.to_numpy() for n in names]); el = np.maximum.reduce([g[n].exit_l.to_numpy() for n in names])
    worse = (nl < no).to_numpy()
    out['net'] = np.where(worse, nl, no); out['exit'] = np.where(worse, el, eo)
    return out


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_ladders.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = sorted(d.name for d in ax.ALL.iterdir() if (d/'done.json').exists() and (ax.V2/d.name/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        rows = pd.concat([g for g, _ in ex.map(ax.load, syms, chunksize=8)], ignore_index=True)
    score, cut = ax.scores()
    meta = json.loads((A/'exp-monday-limit-reclaim-sizing-20261025-v1/summary.json').read_text())['results']['shape_meta']
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    r = rows[rows.m == 60].copy()
    r['score'] = r.event.map(score)
    r = r[r.score >= cut]
    r['band'] = np.digitize(r.score, [meta['p80'], meta['p90']])
    r['t'] = pd.to_datetime(r.entry, utc=True)
    worse = r.net_l < r.net_o
    r['hit'] = np.where(worse, r.reason_l, r.reason_o) == 4
    r['hold_h'] = (np.where(worse, r.exit_l, r.exit_o)-r.entry)/3.6e12
    legs = {leg: g.set_index('event') for leg, g in r.groupby('leg')}
    periods = {'selection_2025': lambda d: (d.t >= SEL[0]) & (d.t < SEL[1]), 'test_2026': lambda d: d.t >= SEL[1],
               'test_last5m': lambda d: d.t >= window, 'from_2025': lambda d: d.t >= SEL[0]}
    # 1. Reach statistics per band, period and side.
    reach = {}
    common = legs['near'].index
    for n in LEVELS[1:]:
        common = common.intersection(legs[n].index)
    H = pd.DataFrame({n: legs[n].loc[common, 'hit'] for n in LEVELS})
    T = pd.DataFrame({n: legs[n].loc[common, 'hold_h'] for n in LEVELS})
    base = legs['near'].loc[common]
    for per, f in periods.items():
        for b in (0, 1, 2, 'all'):
            for side in (1, -1, 'both'):
                m = f(base) & ((base.band == b) if b != 'all' else True) & ((base.side == side) if side != 'both' else True)
                h = H[m]
                cond = {LEVELS[i]: float(h[LEVELS[i]][h[LEVELS[i-1]]].mean()) if h[LEVELS[i-1]].any() else None for i in range(1, len(LEVELS))}
                reach[f'{per}|{b}|{side}'] = dict(n=int(m.sum()), hit_pct={n: float(100*h[n].mean()) for n in LEVELS},
                                                  given_previous_pct={k: (100*v if v is not None else None) for k, v in cond.items()},
                                                  hours_median_when_hit={n: float(T[m][n][h[n]].median()) if h[n].any() else None for n in LEVELS})
    # 2. Ladders per band.
    lad = {k: ladder(legs, w) for k, w in LADDERS.items()}
    table = {}
    for b in (0, 1, 2):
        table[b] = {k: {per: dict(n=int((f(d) & (d.band == b)).sum()), mean_bp=float(d[f(d) & (d.band == b)].net.mean()),
                                  win_pct=float(100*(d[f(d) & (d.band == b)].net > 0).mean()))
                        for per, f in periods.items()} for k, d in lad.items()}
    chosen = {b: max(LADDERS, key=lambda k: table[b][k]['selection_2025']['mean_bp']) for b in (0, 1, 2)}
    # 3. Replays: one ladder for every band, and the per-band choice.
    mexcess = float((lad['near'].score[periods['selection_2025'](lad['near'])]-cut).mean())
    rng = np.random.default_rng(20261025)
    plans = {k: {0: k, 1: k, 2: k} for k in LADDERS} | {'band_chosen': chosen}
    idx = lad['near'].index
    for d in lad.values():
        idx = idx.intersection(d.index)
    replays = {}
    for name, plan in plans.items():
        x = pd.concat([lad[plan[b]].loc[idx][lad[plan[b]].loc[idx].band == b] for b in (0, 1, 2)])
        x = x.reset_index().sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
        x['k_equal'] = .10; x['k_linear'] = np.minimum(.05*(x.score-cut)/mexcess, .5)
        out = {}
        for per in ('selection_2025', 'test_2026', 'test_last5m'):
            y = x[periods[per](x)].reset_index(drop=True)
            out[per] = dict(n=len(y), mean_bp=float(y.net.mean()), win_pct=float(100*(y.net > 0).mean()))
            for sizing in ('equal', 'linear'):
                rep = []
                for _ in range(TIES):
                    order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
                    z = y.iloc[order].reset_index(drop=True)
                    rep.append(opt.replay_w(z, z[f'k_{sizing}'].to_numpy(), 1.)[0])
                fm = np.array([q['final_multiple'] for q in rep]); dd = np.array([q['max_drawdown_pct'] for q in rep])
                out[per][sizing] = dict(final_median=float(np.median(fm)), final_p10=float(np.quantile(fm, .1)),
                                        final_p90=float(np.quantile(fm, .9)), dd_median=float(np.median(dd)))
        replays[name] = dict(plan=plan, results=out)
    res = dict(threshold=cut, band_edges=[cut, meta['p80'], meta['p90']], reach=reach, ladder_table=table, chosen=chosen,
               replays=replays, linear_mean_excess=mexcess)
    EXP.mkdir(exist_ok=True)
    charts(res)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(dict(chosen=chosen), ensure_ascii=False))
    for k, v in reach.items():
        if k.startswith('from_2025') and k.endswith('both'):
            print(k, v['n'], {n: round(x, 1) for n, x in v['hit_pct'].items()}, {n: (round(x, 1) if x is not None else None) for n, x in v['given_previous_pct'].items()},
                  {n: (round(x, 1) if x is not None else None) for n, x in v['hours_median_when_hit'].items()})
    for b in (0, 1, 2):
        print('band', b, ' '.join(f"{k}:{table[b][k]['selection_2025']['mean_bp']:.1f}/{table[b][k]['test_2026']['mean_bp']:.1f}" for k in LADDERS))
    for name, v in replays.items():
        print(f"{name:12s}", *(f"{per}: mean{o['mean_bp']:.1f} eq {o['equal']['final_median']:.2f}/{o['equal']['dd_median']:.0f} lin {o['linear']['final_median']:.2f}/{o['linear']['dd_median']:.0f}"
                               for per, o in v['results'].items()))


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    cols = {0: '#eb6834', 1: '#1baf7a', 2: '#2a78d6'}
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    x = np.arange(len(LEVELS))
    for ax_, side, title in zip(axes, ('both', 1, -1), ('多空合计', '做多', '做空')):
        for b in (0, 1, 2):
            v = res['reach'][f'from_2025|{b}|{side}']
            ax_.plot(x, [v['hit_pct'][n] for n in LEVELS], color=cols[b], lw=2, marker='o', label=f"{BZH[b]}（{v['n']}笔）")
            for i, n in enumerate(LEVELS):
                if b == 2:
                    ax_.annotate(f"{v['hit_pct'][n]:.0f}%", (i, v['hit_pct'][n]), textcoords='offset points', xytext=(0, 6), ha='center', fontsize=8, color=INK2)
        ax_.set_xticks(x, [LZH[n] for n in LEVELS], fontsize=9); ax_.set_ylabel('到达比例（%）')
        ax_.set_title(f'2025年起 · {title}', color=INK, loc='left'); ax_.grid(color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].legend(frameon=False)
    fig.suptitle('各关键点位的到达比例（成交后、在保本或没收回出场之前碰到；较差K线顺序）', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_level_reach.png', dpi=130); plt.close(fig)
    names = list(LADDERS)
    fig, axes = plt.subplots(1, 3, figsize=(18, 6.5), sharey=True)
    y = np.arange(len(names)); h = .38
    for ax_, b in zip(axes, (0, 1, 2)):
        for off, per, col, lab in ((-h/2, 'selection_2025', '#898781', '2025年'), (h/2, 'test_2026', '#2a78d6', '2026年')):
            ax_.barh(y+off, [res['ladder_table'][b][k][per]['mean_bp'] for k in names], h*.92, color=col, label=lab)
        ax_.axvline(0, color='#c3c2b7', lw=1); ax_.set_title(BZH[b], color=INK, loc='left'); ax_.grid(axis='x', color=GRID, lw=.6)
        ax_.set_xlabel('每笔平均净收益（bp）')
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].set_yticks(y, [DZH[k] for k in names]); axes[0].invert_yaxis(); axes[0].legend(frameon=False, loc='lower right')
    fig.suptitle('按分数段的分批止盈 · 较差K线顺序 · 非止盈出场加10bp滑点 · 无兜底止损', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'2_ladders_by_band.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

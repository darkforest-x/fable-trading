"""All five targets, partial exits across them, and the 5m confirmation for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Owner: "你这套策略只有周一低点止盈这一种？其他的呢，比如低点，区间0.5，0.618 高点，
反向1.618呢，分批在这些点位止盈都试过了吗", plus the 5m paper track. Inputs are
framework fills only: the Codex O-H-L-C ledger (exp-monday-limit-reclaim-20261024-v1)
and the monotone O-L-H-C replays (exp-monday-limit-reclaim-olhc-allexits-20261025-v1
for 5m x 5 targets and 60m x 4 targets, exp-monday-limit-reclaim-olhc-20261025-v2
for 60m near edge). Targets for a long (mirror for a short), distance from the
lower 1618 in R = 0.382 Monday range: near edge = Monday low 1.618R, range 0.5
2.93R, range 0.618 3.24R, far edge = Monday high 4.24R, opposite 1618 5.85R.
Per trade the worse path is kept; 10bp slippage on every non-target exit leg.
A partial exit is the equal-weight mean of its legs per path (same fill,
confirmation and breakeven stop, each leg with its own resting target), then
the worse path; it frees capital at its last leg. The entry score is the frozen
score_model.json of exp-monday-limit-reclaim-recent-20261025-v1 (top-40 threshold
from fills before 2025), applied unchanged to every target and confirmation
because the fill event is identical. Periods: before 2025 (where the score was
fitted), from 2025, and the last five months window of the recent study.
1000 USDT replays: 10 percent per trade, gross open notional <= 1x equity.
Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-allexits-20261025-v1'
A = ROOT/'experiments/active'
ALL = A/'exp-monday-limit-reclaim-olhc-allexits-20261025-v1/symbols'
V2 = A/'exp-monday-limit-reclaim-olhc-20261025-v2/symbols'
RECENT = A/'exp-monday-limit-reclaim-recent-20261025-v1'
MODES = ['near_monday_edge', 'range_0.5', 'range_0.618', 'opposite_monday_edge', 'opposite_1618']
SHORT = dict(zip(MODES, ['near', 'r05', 'r0618', 'far', 'opp1618']))
ZH = dict(near='近侧周一边界', r05='区间0.5', r0618='区间0.618', far='远侧周一边界', opp1618='对侧1.618')
PARTIALS = {'near+r05': ('near', 'r05'), 'near+far': ('near', 'far'), 'near+opp1618': ('near', 'opp1618'),
            'r05+far': ('r05', 'far'), 'near+r05+far': ('near', 'r05', 'far'), 'all5': tuple(SHORT.values())}
PZH = {'near+r05': '分批：近侧边界+区间0.5', 'near+far': '分批：近侧+远侧边界', 'near+opp1618': '分批：近侧边界+对侧1.618',
       'r05+far': '分批：区间0.5+远侧边界', 'near+r05+far': '分批：近侧+0.5+远侧各1/3', 'all5': '分批：五个点位各1/5'}
KEEP = ['trade_id', 'side', 'reason', 'entry_time', 'exit_time', 'net_notional_bp']


def load(symbol):
    c = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'population', 'reclaim_minutes', 'closed', 'side', 'reason',
                                                          'entry_time', 'exit_time', 'net_bp'])
    c = c[(c.population == 'signal') & c.reclaim_minutes.isin([5, 60]) & c.closed].rename(columns={'net_bp': 'net_notional_bp'})[KEEP]
    l = pd.concat([pd.read_csv(d/symbol/'trades.csv.gz', usecols=KEEP) for d in (ALL, V2)], ignore_index=True)
    l = l[~l.trade_id.duplicated()]
    m = c.merge(l[['trade_id', 'reason', 'exit_time', 'net_notional_bp']], on='trade_id', suffixes=('_o', '_l'))
    parts = m.trade_id.str.split(':')
    m['event'] = parts.str[:3].str.join(':')
    m['m'] = parts.str[3].str[1:].astype(int)
    m['leg'] = parts.str[4].map(SHORT)
    for s in ('o', 'l'):
        m[f'net_{s}'] = m[f'net_notional_bp_{s}']-np.where(m[f'reason_{s}'] == 4, 0., opt.SLIP)
        m[f'exit_{s}'] = pd.to_datetime(m[f'exit_time_{s}'], utc=True).astype('int64')
    m['entry'] = pd.to_datetime(m.entry_time, utc=True).astype('int64')
    m['symbol'] = symbol
    return m[['event', 'symbol', 'm', 'leg', 'side', 'entry', 'net_o', 'net_l', 'exit_o', 'exit_l', 'reason_o', 'reason_l']], (len(c), len(m))


def scores():
    """Frozen entry score per fill event, features as in monday_limit_reclaim_recent."""
    spec = json.loads((RECENT/'score_model.json').read_text())
    syms = sorted(d.name for d in V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        extra = pd.concat(list(ex.map(opt.ledger_extra, syms, chunksize=8)), ignore_index=True)
    extra['event'] = extra.trade_id.str.split(':').str[:3].str.join(':')
    ev = pd.read_csv(A/'exp-monday-limit-reclaim-patterns3-20261024-v1/events_60m.csv.gz')
    ev['event'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')
    s = ev.set_index('event')[['side_n', 'vol_ratio', 'approach_min', 'opp_first', 'monday_close_side', 'liquidity', 'toward24', 'cluster24']]
    s = s.join(extra.set_index('event')[['bucket', 'range_pct']], how='inner')
    s['side'] = s.side_n.astype(float)
    s['log_vol_ratio'] = np.log(s.vol_ratio.clip(lower=.05)); s['log_approach'] = np.log1p(s.approach_min.clip(lower=0))
    s['opp_first'] = (s.opp_first == '是').astype(int); s['log_liquidity'] = np.log10(s.liquidity.clip(lower=1e4))
    s = s.dropna(subset=spec['features'])
    z = (s[spec['features']].to_numpy(float)-np.array(spec['mean']))/np.array(spec['scale'])
    s['score'] = 1/(1+np.exp(-((z*np.array(spec['coef'])).sum(1)+spec['intercept'])))
    return s.score, spec['top40_threshold']


def combine(legs, names):
    """Equal-weight partial over the named legs, per path, then the worse path."""
    g = [legs[n] for n in names]
    idx = g[0].index
    for x in g[1:]:
        idx = idx.intersection(x.index)
    g = [x.loc[idx] for x in g]
    out = g[0][['symbol', 'side', 'entry']].copy()
    no, nl = (sum(x[f'net_{s}'] for x in g)/len(g) for s in ('o', 'l'))
    eo, el = (np.maximum.reduce([x[f'exit_{s}'].to_numpy() for x in g]) for s in ('o', 'l'))
    worse = (nl < no).to_numpy()
    out['net'] = np.where(worse, nl, no)
    out['exit'] = np.where(worse, el, eo)
    out['target_any'] = np.where(worse, sum((x.reason_l == 4) for x in g), sum((x.reason_o == 4) for x in g)) > 0
    return out


def stats(x):
    hold = (x.exit-x.entry)/3.6e12
    return dict(n=len(x), win_pct=float(100*(x.net > 0).mean()), mean_bp=float(x.net.mean()), median_bp=float(x.net.median()),
                target_hit_pct=float(100*x.target_any.mean()), hold_h_median=float(hold.median()),
                long_mean_bp=float(x.net[x.side == 1].mean()), short_mean_bp=float(x.net[x.side == -1].mean()))


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_allexits.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = sorted(d.name for d in ALL.iterdir() if (d/'done.json').exists() and (V2/d.name/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        got = list(ex.map(load, syms, chunksize=8))
    rows = pd.concat([g for g, _ in got], ignore_index=True)
    coverage = dict(symbols=len(syms), codex_rows=int(sum(n for _, (n, _) in got)), matched_rows=int(sum(k for _, (_, k) in got)))
    score, cut = scores()
    rows['score'] = rows.event.map(score)
    window = [pd.Timestamp(t) for t in json.loads((RECENT/'summary.json').read_text())['results']['window']]
    res = dict(coverage=coverage, score_threshold=cut, window=[str(t) for t in window], variants={}, replays={})
    paths = {}
    for m in (60, 5):
        r = rows[rows.m == m]
        legs = {leg: g.set_index('event') for leg, g in r.groupby('leg')}
        variants = {leg: combine(legs, (leg,)) for leg in SHORT.values()} | {k: combine(legs, v) for k, v in PARTIALS.items()}
        for name, d in variants.items():
            d = d.join(score.rename('score'), how='left')
            t = pd.to_datetime(d.entry, utc=True)
            top = d.score >= cut
            periods = {'before_2025': t < SPLIT, 'from_2025': t >= SPLIT, 'last5m': (t >= window[0])}
            res['variants'][f'm{m}|{name}'] = {f'{per}|{f}': stats(d[mask & sel]) for per, mask in periods.items()
                                               for f, sel in (('all', np.ones(len(d), bool)), ('top40', top.to_numpy()))}
            res['variants'][f'm{m}|{name}']['unscored'] = int(d.score.isna().sum())
            for per, mask in (('from_2025', periods['from_2025']), ('last5m', periods['last5m'])):
                for f, sel in (('all', np.ones(len(d), bool)), ('top40', top.to_numpy())):
                    x = d[mask & sel].sort_values('entry').reset_index(drop=True)
                    out, path = opt.replay_w(x, np.full(len(x), .10), 1.)
                    res['replays'][f'm{m}|{name}|{per}|{f}'] = out
                    if f == 'top40' and per == 'last5m':
                        paths[f'm{m}|{name}'] = path
    EXP.mkdir(exist_ok=True)
    charts(res, paths)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(dict(coverage=coverage, replays=res['replays']), ensure_ascii=False, indent=1))


def charts(res, paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    names = list(SHORT.values())+list(PARTIALS)
    labs = [ZH.get(n, PZH.get(n)) for n in names]
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.2), sharey=True)
    y = np.arange(len(names)); h = .38
    for ax, key, title in zip(axes, ('from_2025|top40', 'last5m|top40', 'from_2025|all'),
                              ('2025年起 · 只做分数前40%', '近5个月 · 只做分数前40%', '2025年起 · 不打分全部做')):
        for off, m, col in ((-h/2, 60, BLUE), (h/2, 5, ORANGE)):
            v = [res['variants'][f'm{m}|{n}'][key]['mean_bp'] for n in names]
            ax.barh(y+off, v, h*.92, color=col, label=f'{m}分钟确认')
        ax.axvline(0, color='#c3c2b7', lw=1); ax.axhline(4.5, color=GRID, lw=1, ls='--')
        ax.set_title(title, color=INK, loc='left'); ax.grid(axis='x', color=GRID, lw=.6); ax.set_xlabel('每笔平均净收益（bp）')
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    axes[0].set_yticks(y, labs); axes[0].invert_yaxis(); axes[0].legend(frameon=False, loc='lower right')
    fig.suptitle('止盈位与分批止盈 · 每笔取两种K线顺序中较差的结果 · 止损和市价出场加10bp滑点', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_targets_mean.png', dpi=130); plt.close(fig)
    best = sorted(names, key=lambda n: -res['replays'][f'm60|{n}|last5m|top40']['final_multiple'])[:3]
    show = [('m60|near', '60分钟 · 近侧边界（现行）', BLUE, '-'), ('m5|near', '5分钟 · 近侧边界', ORANGE, '-')]
    for n, col in zip([n for n in best if n != 'near'][:2], ('#1baf7a', MUTED)):
        show.append((f'm60|{n}', f'60分钟 · {ZH.get(n, PZH.get(n))}', col, '--'))
    fig, ax = plt.subplots(figsize=(11, 5))
    for key, lab, col, ls in show:
        pp = np.array(paths[key]) if paths.get(key) else None
        if pp is None or not len(pp):
            continue
        ax.plot(pd.to_datetime(pp[:, 0]), pp[:, 1], color=col, lw=2, ls=ls, label=lab)
    ax.axhline(1000, color='#c3c2b7', lw=1); ax.legend(frameon=False, loc='upper left')
    ax.set_ylabel('已实现权益（USDT）')
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title('近5个月 · 1000U · 只做分数前40% · 每笔10%、总仓位≤1倍 · 较差K线顺序', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'2_last5m_equity.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

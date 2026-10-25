"""Laddered take-profits at the key levels x backstop x fine score band, whole history (owner2026-10-25).

Owner: "分批止盈也要测啊 而且分批止盈 不只是分2次止盈啊 多次也可以啊分别在关键点位". Legs are
framework fills, one independent position per (event, level, backstop): near-edge
legs from exp-monday-limit-v3-grid-20261025-v1 (near_<stop>), range 0.5 / 0.618 /
far Monday edge / opposite 1618 legs from exp-monday-limit-v3-levels-20261025-v1,
events with frozen score >= 0.5 since 2020. Every leg of a ladder shares the fill,
the 60m confirmation, the breakeven move and the backstop and rests its own
target, so a ladder is the weighted sum of its legs per intrabar path (10bp
slippage on each non-target leg exit), then the worse path; capital is freed at
its last leg. Parity: the levels run's opp1618_<stop> must equal the grid run's
v3t_<stop> for events >= 0.6 (same orders). BABYUSDT:2026-06-01:-1 is dropped
(bad source bar). Score bands 0.5 / 0.6 / 0.7 / 0.8; periods 2020-2024 (score
training period), 2025, 2026, last five months. Per band x backstop x ladder:
mean net and the paired difference to the near target with its standard error.
Per backstop, per-band ladders are chosen on 2020-2024 and, separately, on 2025.
Plans are replayed from 1000 USDT with linear sizing in score (cap 50 or 15
percent, gross <= 1x), medians over TIES random orders of same-time fills.
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
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation.monday_limit_reclaim_ladders import LADDERS, DZH

EXP = ROOT/'experiments/active/exp-monday-limit-v3-ladder-compare-20261025-v1'
A = ROOT/'experiments/active'
GRIDRUN, LEVELS = A/'exp-monday-limit-v3-grid-20261025-v1', A/'exp-monday-limit-v3-levels-20261025-v1'
STOPS = ['far', 'P10', 'P12', 'P15', 'P18', 'P20', 'P25', 'P30']
SZH = dict(far='不设', P10='-10%', P12='-12%', P15='-15%', P18='-18%', P20='-20%', P25='-25%', P30='-30%')
EDGES = [.6, .7, .8]
BNAMES = ['0.5-0.6', '0.6-0.7', '0.7-0.8', '0.8-1']
BAD_EVENTS = {'BABYUSDT:2026-06-01:-1'}
B = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
TIES = 10
COLS = ['trade_id', 'net_notional_bp', 'reason', 'entry_time', 'exit_time', 'score', 'variant']


def load(symbol):
    """Legs for events >= 0.5 from both runs and both paths, plus the v3t parity rows."""
    out, par = [], 0
    for path in ('ohlc', 'olhc'):
        g = pd.read_csv(GRIDRUN/path/'symbols'/symbol/'trades.csv.gz', usecols=COLS)
        v = pd.read_csv(LEVELS/path/'symbols'/symbol/'trades.csv.gz', usecols=COLS)
        g = g[g.score >= .5]
        t3 = g[g.variant.str.startswith('v3t_') & (g.score >= .6)]
        if len(t3):
            x = t3.assign(base=t3.trade_id.str.rsplit(':v_', n=1).str[0], st=t3.variant.str[4:])
            y = v[v.variant.str.startswith('opp1618_')]
            y = y.assign(base=y.trade_id.str.rsplit(':v_', n=1).str[0], st=y.variant.str[8:])
            mm = x.merge(y[['base', 'st', 'net_notional_bp']], on=['base', 'st'], how='left', suffixes=('', '_v'))
            par += int(((mm.net_notional_bp-mm.net_notional_bp_v).abs() > 1e-6).sum()+mm.net_notional_bp_v.isna().sum())
        legs = pd.concat([g[g.variant.str.startswith('near_')], v], ignore_index=True)
        legs['path'] = path
        out.append(legs)
    t = pd.concat(out, ignore_index=True)
    t['event'] = t.trade_id.str.split(':').str[:3].str.join(':')
    t[['leg', 'stop']] = t.variant.str.rsplit('_', n=1, expand=True)
    return t.assign(symbol=symbol), par


def compose(legs):
    """Ladder outcomes per (event, stop, ladder): weighted legs per path, then the worse path."""
    legs = legs.copy()
    legs['net_s'] = legs.net_notional_bp-np.where(legs.reason == 4, 0., opt.SLIP)
    legs['exit_ns'] = pd.to_datetime(legs.exit_time, utc=True).astype('int64')
    legs['entry_ns'] = pd.to_datetime(legs.entry_time, utc=True).astype('int64')
    wide = legs.pivot_table(index=['event', 'stop'], columns=['leg', 'path'], values=['net_s', 'exit_ns'], aggfunc='first')
    meta = legs.drop_duplicates(['event', 'stop']).set_index(['event', 'stop'])[['score', 'entry_ns']]
    rows = []
    for name, w in LADDERS.items():
        no = sum(wt*wide[('net_s', lg, 'ohlc')] for lg, wt in w.items()); nl = sum(wt*wide[('net_s', lg, 'olhc')] for lg, wt in w.items())
        eo = np.vstack([wide[('exit_ns', lg, 'ohlc')].to_numpy(float) for lg in w]).max(axis=0)
        el = np.vstack([wide[('exit_ns', lg, 'olhc')].to_numpy(float) for lg in w]).max(axis=0)
        worse = (nl < no).to_numpy()
        d = pd.DataFrame(dict(net=np.where(worse, nl, no), exit=np.where(worse, el, eo)), index=wide.index).dropna()
        rows.append(d.assign(ladder=name))
    out = pd.concat(rows).join(meta).reset_index()
    out['entry'] = out.entry_ns
    out['t'] = pd.to_datetime(out.entry, utc=True)
    out['band'] = np.digitize(out.score, EDGES)
    return out


def periods(window):
    return {'y2020_2024': lambda t: t < B[0], 'y2025': lambda t: (t >= B[0]) & (t < B[1]), 'y2026': lambda t: t >= B[1],
            'last5m': lambda t: t >= window, 'from_2025': lambda t: t >= B[0]}


def replay_plan(args):
    name, x, cut, mexcess, window, seed = args
    rng = np.random.default_rng(seed)
    out = {}
    for per, within in periods(window).items():
        if per == 'from_2025':
            continue
        y = x[within(x.t)].sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
        lin = (.05*(y.score-cut)/mexcess).to_numpy()
        r = dict(n=len(y), mean_bp=float(y.net.mean()), win_pct=float(100*(y.net > 0).mean()))
        for sizing, cap in (('linear', .5), ('linear_cap15', .15)):
            k = np.minimum(lin, cap)
            rep = []
            for _ in range(TIES):
                order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
                z = y.iloc[order].reset_index(drop=True)
                rep.append(opt.replay_w(z, k[order], 1.)[0])
            fm = np.array([q['final_multiple'] for q in rep]); dd = np.array([q['max_drawdown_pct'] for q in rep])
            r[sizing] = dict(final_median=float(np.median(fm)), dd_median=float(np.median(dd)))
        out[per] = r
    return name, out


def run():
    p = 'yoyo/evaluation/monday_limit_v3_ladder_compare.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = json.loads((LEVELS/'config.json').read_text())['symbols']
    for run_ in (GRIDRUN, LEVELS):
        miss = [(k, s) for k in ('ohlc', 'olhc') for s in syms if not (run_/k/'symbols'/s/'done.json').exists()]
        if miss:
            raise RuntimeError(f'Incomplete {run_.name}: {len(miss)}')
    with ProcessPoolExecutor(4) as ex:
        got = list(ex.map(load, syms, chunksize=8))
    legs = pd.concat([t for t, _ in got], ignore_index=True)
    parity = int(sum(p_ for _, p_ in got))
    legs = legs[~legs.event.isin(BAD_EVENTS)]
    lad = compose(legs)
    cut = json.loads((LEVELS/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    pers = periods(window)
    table = {}
    near = lad[lad.ladder == 'near'].set_index(['event', 'stop']).net
    for (b, s, name), g in lad.groupby(['band', 'stop', 'ladder']):
        d = g.set_index(['event', 'stop']).net-near.reindex(g.set_index(['event', 'stop']).index)
        row = {per: dict(n=int(f(g.t).sum()), mean_bp=float(g.net[f(g.t)].mean()) if f(g.t).any() else None) for per, f in pers.items()}
        for per in ('y2020_2024', 'from_2025'):
            dd = d[pers[per](g.t).to_numpy()].dropna()
            row[per] |= dict(diff_vs_near=float(dd.mean()) if len(dd) else None, diff_se=float(dd.std(ddof=1)/np.sqrt(len(dd))) if len(dd) > 1 else None)
        table[f'{BNAMES[b]}|{s}|{name}'] = row
    chosen = {}
    for s in STOPS:
        for basis in ('y2020_2024', 'y2025'):
            chosen[f'{s}|{basis}'] = {BNAMES[b]: max(LADDERS, key=lambda k: (table.get(f'{BNAMES[b]}|{s}|{k}', {}).get(basis, {}).get('mean_bp') or -1e9))
                                      for b in range(len(BNAMES))}
    plans = {}
    for s in STOPS:
        for k in LADDERS:
            plans[f'{s}|all_{k}'] = {bn: k for bn in BNAMES}
        for basis in ('y2020_2024', 'y2025'):
            plans[f'{s}|chosen_on_{basis}'] = chosen[f'{s}|{basis}']
    keyed = lad.set_index(['stop', 'ladder'])
    jobs = []
    for i, (pname, plan) in enumerate(plans.items()):
        s = pname.split('|')[0]
        parts = [keyed.loc[(s, plan[bn])].reset_index(drop=True).pipe(lambda x, b=b: x[x.band == b]) for b, bn in enumerate(BNAMES)]
        x = pd.concat(parts, ignore_index=True)[['event', 'score', 'entry', 'exit', 'net', 't']]
        jobs.append((pname, x, cut, mexcess, window, 20261025+i))
    with ProcessPoolExecutor(4) as ex:
        replays = dict(ex.map(replay_plan, jobs, chunksize=4))
    res = dict(checks=dict(levels_vs_grid_opp1618_mismatches=parity, dropped_events=sorted(BAD_EVENTS)), threshold=cut, linear_mean_excess=mexcess,
               band_table=table, chosen=chosen, plans={k: dict(plan=v, results=replays[k]) for k, v in plans.items()})
    EXP.mkdir(exist_ok=True)
    charts(res)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))
    best = sorted(res['plans'].items(), key=lambda kv: -kv[1]['results']['y2026']['linear']['final_median'])[:12]
    for k, v in best+[(k, res['plans'][k]) for k in res['plans'] if k.endswith('all_near')]:
        print(f'{k:28s}', *(f"{per}: {o['linear']['final_median']:.2f}/{o['linear']['dd_median']:.0f} cap15 {o['linear_cap15']['final_median']:.2f}/{o['linear_cap15']['dd_median']:.0f} ({o['mean_bp']:.0f}bp)"
                              for per, o in v['results'].items()))
    for k, v in chosen.items():
        print('chosen', k, v)


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    keys = list(LADDERS)
    fig, axes = plt.subplots(1, len(BNAMES), figsize=(22, 6.5), sharey=True)
    for ax_, bn in zip(axes, BNAMES):
        M = np.array([[res['band_table'].get(f'{bn}|{s}|{k}', {}).get('from_2025', {}).get('mean_bp') or np.nan for s in STOPS] for k in keys])
        D = np.array([[res['band_table'].get(f'{bn}|{s}|{k}', {}).get('from_2025', {}).get('diff_vs_near') or 0. for s in STOPS] for k in keys])
        S = np.array([[res['band_table'].get(f'{bn}|{s}|{k}', {}).get('from_2025', {}).get('diff_se') or np.inf for s in STOPS] for k in keys])
        lim = np.nanmax(np.abs(D)) or 1
        ax_.imshow(D, cmap='RdBu', vmin=-lim, vmax=lim, aspect='auto')
        for i in range(len(keys)):
            for j in range(len(STOPS)):
                sig = abs(D[i, j]) > 2*S[i, j] and keys[i] != 'near'
                ax_.text(j, i, f'{M[i, j]:.0f}'+('*' if sig else ''), ha='center', va='center', fontsize=8, color=INK if abs(D[i, j]) < .6*lim else 'white')
        n = res['band_table'].get(f'{bn}|far|near', {}).get('from_2025', {}).get('n')
        ax_.set_title(f'分数 {bn}（2025年起 {n} 笔）', color=INK, loc='left'); ax_.set_xticks(range(len(STOPS)), [SZH[s] for s in STOPS], fontsize=8)
    axes[0].set_yticks(range(len(keys)), [DZH[k] for k in keys], fontsize=9)
    fig.suptitle('2025年起每笔平均净收益（bp）· 颜色=比同一止损下"全部近侧边界"多赚(蓝)/少赚(红) · *=超过2倍标准误 · 横轴=兜底止损', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_ladder_stop_band.png', dpi=120); plt.close(fig)


if __name__ == '__main__':
    run()

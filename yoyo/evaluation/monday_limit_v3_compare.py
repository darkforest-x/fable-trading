"""v3 candidate versus the current v2 rule, with attribution (owner2026-10-25).

Inputs: exp-monday-limit-v3-20261025-v1 (one native run per intrabar path with
variants current / near_P20 / target_only / v3; see monday_limit_v3). Every
per-symbol output is checked; 'current' must equal the existing ledgers trade
for trade. The event BABYUSDT:2026-06-01:-1 is dropped from every variant: its
source bar (2026-06-05 09:30 UTC) has high 1.30 from 0.012 while OKX's bar high
is 0.01871, which breaks any resting stop comparison. Per trade the worse
path; 10bp slippage on every non-target exit. Entry thresholds 0.226 (top-40)
and 0.5. Sizing as in the sizing study (equal 10 percent, and linear
0.05 * (score - 0.226) / m, m = mean excess score of 2025 top-40 fills, cap 0.5),
gross <= 1x, medians over TIES random orders of same-time fills. Periods 2025,
2026 and the last five months. Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-v3-compare-20261025-v1'
RUN = ROOT/'experiments/active/exp-monday-limit-v3-20261025-v1'
SEL = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
TIES = 20
BAD_EVENTS = {'BABYUSDT:2026-06-01:-1'}
VZH = dict(current='现行 v2（近侧止盈，无兜底）', near_P20='只加 20% 兜底止损', target_only='只改止盈（0.6 以上挂对侧1.618）', v3='v3：改止盈 + 20% 兜底止损')


def load(args):
    path, symbol = args
    d = RUN/path/'symbols'/symbol
    done = json.loads((d/'done.json').read_text())
    t = pd.read_csv(d/'trades.csv.gz')
    return t.assign(symbol=symbol), (None if done.get('positions', 0) == len(t) else f"rows {len(t)} != positions {done.get('positions')}")


def parity(symbol):
    o = pd.read_csv(RUN/'ohlc'/'symbols'/symbol/'trades.csv.gz'); l = pd.read_csv(RUN/'olhc'/'symbols'/symbol/'trades.csv.gz')
    out = []
    for x, ref, col in ((o, pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'net_bp']), 'net_bp'),
                        (l, pd.read_csv(ax.V2/symbol/'trades.csv.gz', usecols=['trade_id', 'net_notional_bp']), 'net_notional_bp')):
        x = x[x.variant == 'current']
        m = x.assign(base=x.trade_id.str.rsplit(':v_', n=1).str[0]).merge(ref.rename(columns={'trade_id': 'base', col: 'ref'}), on='base', how='left')
        out.append(int(((m.net_notional_bp-m.ref).abs() > 1e-6).sum()+m.ref.isna().sum()))
    return out


def run():
    p = 'yoyo/evaluation/monday_limit_v3_compare.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = json.loads((RUN/'config.json').read_text())['symbols']
    missing = {k: [s for s in syms if not (RUN/k/'symbols'/s/'done.json').exists()] for k in ('ohlc', 'olhc')}
    if any(missing.values()):
        raise RuntimeError(f'Incomplete runs: { {k: len(v) for k, v in missing.items()} }')
    with ProcessPoolExecutor(4) as ex:
        got = {k: list(ex.map(load, [(k, s) for s in syms], chunksize=8)) for k in ('ohlc', 'olhc')}
        par = np.array(list(ex.map(parity, syms, chunksize=8)))
    issues = {k: [i for _, i in v if i] for k, v in got.items()}
    o = pd.concat([t for t, _ in got['ohlc']], ignore_index=True); l = pd.concat([t for t, _ in got['olhc']], ignore_index=True)
    m = o.merge(l[['trade_id', 'net_notional_bp', 'reason', 'entry_time', 'exit_time']], on='trade_id', suffixes=('_o', '_l'))
    no = m.net_notional_bp_o-np.where(m.reason_o == 4, 0., opt.SLIP); nl = m.net_notional_bp_l-np.where(m.reason_l == 4, 0., opt.SLIP)
    worse = (nl < no).to_numpy()
    m['net'] = np.where(worse, nl, no)
    m['reason_w'] = np.where(worse, m.reason_l, m.reason_o)
    m['entry'] = pd.to_datetime(np.where(worse, m.entry_time_l, m.entry_time_o), utc=True).astype('int64')
    m['exit'] = pd.to_datetime(np.where(worse, m.exit_time_l, m.exit_time_o), utc=True).astype('int64')
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':')
    dropped = int(m.event.isin(BAD_EVENTS).sum())
    m = m[~m.event.isin(BAD_EVENTS)]
    m['t'] = pd.to_datetime(m.entry, utc=True)
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = float((m.score[(m.variant == 'current') & (m.t >= SEL[0]) & (m.t < SEL[1])]-cut).mean())
    m['k_equal'] = .10
    m['k_linear'] = np.minimum(.05*(m.score-cut)/mexcess, .5)
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    periods = {'y2025': lambda t: (t >= SEL[0]) & (t < SEL[1]), 'y2026': lambda t: t >= SEL[1], 'last5m': lambda t: t >= window}
    rng = np.random.default_rng(20261025)
    res = dict(checks=dict(missing={k: len(v) for k, v in missing.items()}, row_issues=issues,
                           current_parity_mismatches=dict(ohlc=int(par[:, 0].sum()), olhc=int(par[:, 1].sum())), dropped_rows=dropped),
               threshold=cut, linear_mean_excess=mexcess, results={})
    paths = {}
    for name, thr, g in [(n, thr, gg[gg.score >= lo]) for n, gg in m.groupby('variant') for thr, lo in ((.226, cut), (.5, .5))]:
        g = g.sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
        out = {}
        for per, within in periods.items():
            y = g[within(g.t)].reset_index(drop=True)
            out[per] = dict(n=len(y), win_pct=float(100*(y.net > 0).mean()), mean_bp=float(y.net.mean()), p1_bp=float(y.net.quantile(.01)),
                            min_bp=float(y.net.min()), backstop_exit_pct=float(100*(y.reason_w == 1).mean()),
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
                if per == 'y2026' and sizing == 'linear':
                    paths[f'{name}|{thr}'] = opt.replay_w(y, y.k_linear.to_numpy(), 1.)[1]
        res['results'][f'{name}|{thr}'] = out
    EXP.mkdir(exist_ok=True)
    charts(res, paths)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))
    for k, out in res['results'].items():
        print(f"{k:18s}", *(f"{per}: n{o['n']} win{o['win_pct']:.1f} mean{o['mean_bp']:.1f} min{o['min_bp']:.0f} eq {o['equal']['final_median']:.2f}/{o['equal']['dd_median']:.0f} "
                            f"lin {o['linear']['final_median']:.2f}/{o['linear']['dd_median']:.0f}" for per, o in out.items()))


def charts(res, paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    cols = dict(current=INK2, near_P20='#eda100', target_only='#1baf7a', v3='#2a78d6')
    fig, ax_ = plt.subplots(figsize=(12, 5.2))
    for name in VZH:
        for thr, ls in ((.226, '-'), (.5, '--')):
            pp = np.array(paths.get(f'{name}|{thr}') or [])
            if len(pp):
                ax_.plot(pd.to_datetime(pp[:, 0]), pp[:, 1], color=cols[name], lw=2, ls=ls, label=f"{VZH[name]} · 门槛 {thr}")
    ax_.axhline(1000, color='#c3c2b7', lw=1); ax_.legend(frameon=False, fontsize=8, loc='upper left', ncol=2)
    ax_.set_ylabel('已实现权益（USDT）')
    for sp in ('top', 'right'):
        ax_.spines[sp].set_visible(False)
    ax_.set_title('2026年 · 1000U · 按分数线性加仓 · 总仓位≤1倍 · 较差K线顺序（实线门槛0.226，虚线门槛0.5）', color=INK, loc='left')
    ax_.grid(color=GRID, lw=.6)
    fig.tight_layout(); fig.savefig(EXP/'1_v3_equity_2026.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

"""Best-return search over backstop percent x target rule for the Monday1618 limit-reclaim rule, whole history (owner2026-10-25).

Owner: "我们就是要测试出最好的收益方案来" and "你看的怎么只有25年的一年的数据呢". Inputs:
exp-monday-limit-v3-grid-20261025-v1 (one native run per intrabar path; variants
near_<stop> = near Monday edge target everywhere, v3t_<stop> = opposite 1618
from score 0.6; stops far / -10 / -12 / -15 / -18 / -20 / -25 / -30 percent;
all weeks since 2020, frozen score >= top-40 threshold). Checks: per-symbol
outputs readable with rows equal to positions; near_far equals the existing
ledgers trade for trade. The event BABYUSDT:2026-06-01:-1 is dropped (bad
source bar, see monday_limit_v3_compare). Per trade the worse path; 10bp
slippage on every non-target exit. Thresholds 0.226 and 0.5. Periods:
2020-2024 (the score's training period, so scores there are in-sample),
2025, 2026 and the last five months. Sizing: equal 10 percent; linear
0.05 * (score - 0.226) / m (m = mean excess score of 2025 top-40 fills) capped
at 50, 20, 15 or 10 percent; gross <= 1x; medians over TIES random orders of
same-time fills. Read-only arithmetic on framework fills.
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

EXP = ROOT/'experiments/active/exp-monday-limit-v3-grid-compare-20261025-v1'
RUN = ROOT/'experiments/active/exp-monday-limit-v3-grid-20261025-v1'
TIES = 10
BAD_EVENTS = {'BABYUSDT:2026-06-01:-1'}
STOPS = ['far', 'P10', 'P12', 'P15', 'P18', 'P20', 'P25', 'P30']
SZH = dict(far='不设', P10='-10%', P12='-12%', P15='-15%', P18='-18%', P20='-20%', P25='-25%', P30='-30%')
SIZINGS = {'equal': None, 'linear': .5, 'linear_cap20': .2, 'linear_cap15': .15, 'linear_cap10': .10}
B = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))


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
        x = x[x.variant == 'near_far']
        m = x.assign(base=x.trade_id.str.rsplit(':v_', n=1).str[0]).merge(ref.rename(columns={'trade_id': 'base', col: 'ref'}), on='base', how='left')
        bad = m[((m.net_notional_bp-m.ref).abs() > 1e-6) | m.ref.isna()]
        out.append(sorted(bad.base.str.split(':').str[:3].str.join(':').unique()))
    return out


def periods(window):
    return {'y2020_2024': lambda t: t < B[0], 'y2025': lambda t: (t >= B[0]) & (t < B[1]), 'y2026': lambda t: t >= B[1], 'last5m': lambda t: t >= window}


def evaluate(args):
    """All thresholds, periods and sizings for one variant."""
    name, g, cut, mexcess, window, seed = args
    rng = np.random.default_rng(seed)
    out = {}
    for thr, lo in ((.226, cut), (.5, .5)):
        gg = g[g.score >= lo].sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
        for per, within in periods(window).items():
            y = gg[within(gg.t)].reset_index(drop=True)
            lin = .05*(y.score-cut)/mexcess
            r = dict(n=len(y), win_pct=float(100*(y.net > 0).mean()), mean_bp=float(y.net.mean()), p1_bp=float(y.net.quantile(.01)),
                     min_bp=float(y.net.min()), backstop_exit_pct=float(100*(y.reason_w == 1).mean()),
                     linear_weighted_bp=float((y.net*lin).sum()/lin.sum()))
            for sizing, cap in SIZINGS.items():
                k = np.full(len(y), .10) if cap is None else np.minimum(lin.to_numpy(), cap)
                rep = []
                for _ in range(TIES):
                    order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
                    z = y.iloc[order].reset_index(drop=True)
                    rep.append(opt.replay_w(z, k[order], 1.)[0])
                fm = np.array([q['final_multiple'] for q in rep]); dd = np.array([q['max_drawdown_pct'] for q in rep])
                r[sizing] = dict(final_median=float(np.median(fm)), final_p10=float(np.quantile(fm, .1)), final_p90=float(np.quantile(fm, .9)),
                                 dd_median=float(np.median(dd)))
            out[f'{thr}|{per}'] = r
    return name, out


def run():
    p = 'yoyo/evaluation/monday_limit_v3_grid_compare.py'
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
        par = list(ex.map(parity, syms, chunksize=8))
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
    mexcess = float((m.score[(m.variant == 'near_far') & (m.t >= B[0]) & (m.t < B[1])]-cut).mean())
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    cols = ['event', 'variant', 'score', 'entry', 'exit', 'net', 'reason_w', 't']
    jobs = [(name, g[cols].copy(), cut, mexcess, window, 20261025+i) for i, (name, g) in enumerate(m.groupby('variant'))]
    with ProcessPoolExecutor(4) as ex:
        results = dict(ex.map(evaluate, jobs))
    res = dict(checks=dict(missing={k: len(v) for k, v in missing.items()}, row_issues=issues,
                           near_far_parity_mismatch_events=dict(ohlc=sorted({e for a, _ in par for e in a}), olhc=sorted({e for _, b in par for e in b})),
                           dropped_rows=dropped),
               threshold=cut, linear_mean_excess=mexcess, results=results)
    EXP.mkdir(exist_ok=True)
    charts(res)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))
    for thr in (.226, .5):
        for sizing in ('linear', 'linear_cap15', 'equal'):
            print(f'-- threshold {thr} · {sizing} (final multiple / max DD) · per-trade mean bp')
            for tg in ('near', 'v3t'):
                for s in STOPS:
                    r = results[f'{tg}_{s}']
                    print(f'{tg}_{s:4s}', ' | '.join(f"{per}: {r[f'{thr}|{per}'][sizing]['final_median']:.2f}/{r[f'{thr}|{per}'][sizing]['dd_median']:.0f} "
                                                  f"({r[f'{thr}|{per}']['mean_bp']:.1f}bp)" for per in ('y2020_2024', 'y2025', 'y2026', 'last5m')))


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    pers = [('y2020_2024', '2020–2024（训练期）'), ('y2025', '2025年'), ('y2026', '2026年'), ('last5m', '近5个月')]
    fig, axes = plt.subplots(1, 4, figsize=(20, 5), sharey=False)
    x = np.arange(len(STOPS))
    for ax_, (per, title) in zip(axes, pers):
        for tg, col, lab in (('near', '#898781', '全部近侧边界止盈'), ('v3t', '#2a78d6', '0.6以上挂对侧1.618')):
            for sizing, ls in (('linear', '-'), ('linear_cap15', '--')):
                ax_.plot(x, [res['results'][f'{tg}_{s}'][f'0.226|{per}'][sizing]['final_median'] for s in STOPS], color=col, ls=ls, marker='o', lw=2,
                         label=f"{lab} · {'线性加仓' if sizing == 'linear' else '线性加仓，单笔≤15%'}")
        ax_.set_xticks(x, [SZH[s] for s in STOPS], fontsize=9); ax_.set_title(title, color=INK, loc='left'); ax_.grid(color=GRID, lw=.6)
        ax_.axhline(1, color='#c3c2b7', lw=1); ax_.set_xlabel('兜底止损')
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].set_ylabel('1000U 期末倍数（中位数）'); axes[0].legend(frameon=False, fontsize=8, loc='upper left')
    fig.suptitle('兜底止损 × 止盈方式 · 分数前40% · 较差K线顺序 · 非止盈出场加10bp滑点 · 总仓位≤1倍', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_grid_multiples.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

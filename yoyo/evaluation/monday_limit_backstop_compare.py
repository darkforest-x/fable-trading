"""Which backstop distance earns the most for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Inputs: exp-monday-limit-backstop-{ohlc,olhc}-20261025-v1 (framework fills of the
backstop grid, weeks from 2024-12-30). Every per-symbol output is checked
(readable, rows equal to recorded positions). Parity: config "far" must equal
the existing ledgers (Codex O-H-L-C, monotone O-L-H-C v2) trade for trade.
Per trade the worse path is kept; 10bp slippage on every non-target exit
(backstop exits included). Frozen entry score; top-40 percent threshold.
Sizing as in exp-monday-limit-reclaim-sizing-20261025-v1: equal 10 percent of
top-40 trades, and linear 0.05 * (score - threshold) / m with m the mean excess
score of 2025 top-40 fills, capped at 0.5; gross <= 1x. The backstop is chosen
on 2025 by the linear-sizing final multiple (also among backstops only, since
the owner requires one) and judged on 2026 and the last five months; a
score-band variant picks one backstop per fixed score band (0.226 / 0.30 /
0.375 / 0.45 / 0.50 / 0.60 / 0.70 / 0.80) by 2025 mean net per trade. Each replay is repeated over TIES random orders of same-time
fills (medians reported). Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SPLIT, INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-backstop-compare-20261025-v1'
A = ROOT/'experiments/active'
RUNS = {'o': A/'exp-monday-limit-backstop-ohlc-20261025-v1', 'l': A/'exp-monday-limit-backstop-olhc-20261025-v1'}
SEL = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
TIES = 20
ZH = dict(far='不设（现行）', R1='1R', R2='2R', R3='3R', R5='5R', R8='8R', P5='5%', P10='10%', P20='20%', P30='30%', by_score='按分数分段')


def load(args):
    root, symbol = args
    d = root/'symbols'/symbol
    done = json.loads((d/'done.json').read_text())
    t = pd.read_csv(d/'trades.csv.gz')
    issue = None if done.get('positions', 0) == len(t) else f"rows {len(t)} != positions {done.get('positions')}"
    return t.assign(symbol=symbol), issue


def parity(symbol):
    """'far' rows versus the existing ledgers, both paths."""
    o = pd.read_csv(RUNS['o']/'symbols'/symbol/'trades.csv.gz'); l = pd.read_csv(RUNS['l']/'symbols'/symbol/'trades.csv.gz')
    o, l = o[o.config == 'far'], l[l.config == 'far']
    c = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'population', 'reclaim_minutes', 'exit_mode', 'net_bp', 'reason'])
    c = c[(c.population == 'signal') & (c.reclaim_minutes == 60) & (c.exit_mode == 'near_monday_edge')]
    v = pd.read_csv(ax.V2/symbol/'trades.csv.gz', usecols=['trade_id', 'net_notional_bp', 'reason'])
    out = []
    for x, ref, col in ((o, c, 'net_bp'), (l, v, 'net_notional_bp')):
        m = x.assign(base=x.trade_id.str.rsplit(':bs_', n=1).str[0]).merge(ref, left_on='base', right_on='trade_id', how='left', suffixes=('', '_ref'))
        out.append(int(((m.net_notional_bp-m[col]).abs() > 1e-6).sum()+m[col].isna().sum()+(m.reason != m.reason_ref).sum()))
    return out


def run():
    p = 'yoyo/evaluation/monday_limit_backstop_compare.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = json.loads((RUNS['o']/'config.json').read_text())['symbols']
    missing = {k: [s for s in syms if not (r/'symbols'/s/'done.json').exists()] for k, r in RUNS.items()}
    if any(missing.values()):
        raise RuntimeError(f'Incomplete runs: { {k: len(v) for k, v in missing.items()} }')
    with ProcessPoolExecutor(4) as ex:
        got = {k: list(ex.map(load, [(r, s) for s in syms], chunksize=8)) for k, r in RUNS.items()}
        par = np.array(list(ex.map(parity, syms, chunksize=8)))
    issues = {k: [i for _, i in v if i] for k, v in got.items()}
    o = pd.concat([t for t, _ in got['o']], ignore_index=True); l = pd.concat([t for t, _ in got['l']], ignore_index=True)
    m = o.merge(l[['trade_id', 'net_notional_bp', 'reason', 'entry_time', 'exit_time']], on='trade_id', suffixes=('_o', '_l'))
    no = m.net_notional_bp_o-np.where(m.reason_o == 4, 0., opt.SLIP); nl = m.net_notional_bp_l-np.where(m.reason_l == 4, 0., opt.SLIP)
    worse = (nl < no).to_numpy()
    m['net'] = np.where(worse, nl, no)
    m['reason_w'] = np.where(worse, m.reason_l, m.reason_o)
    m['entry'] = pd.to_datetime(np.where(worse, m.entry_time_l, m.entry_time_o), utc=True).astype('int64')
    m['exit'] = pd.to_datetime(np.where(worse, m.exit_time_l, m.exit_time_o), utc=True).astype('int64')
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':')
    score, cut = ax.scores()
    m['score'] = m.event.map(score)
    m = m.dropna(subset=['score'])
    m['t'] = pd.to_datetime(m.entry, utc=True)
    window = pd.Timestamp(json.loads((ax.RECENT/'summary.json').read_text())['results']['window'][0])
    sel = (m.t >= SEL[0]) & (m.t < SEL[1])
    top = m.score >= cut
    mexcess = float((m.score[sel & top & (m.config == 'far')]-cut).mean())
    m['k_equal'] = np.where(top, .10, 0.)
    m['k_linear'] = np.where(top, np.minimum(.05*(m.score-cut)/mexcess, .5), 0.)
    far_win = m[m.config == 'far'].set_index('event').net > 0
    periods = {'selection_2025': lambda t: (t >= SEL[0]) & (t < SEL[1]), 'test_2026': lambda t: t >= SEL[1],
               'test_last5m': lambda t: t >= window}
    rng = np.random.default_rng(20261025)
    res = dict(checks=dict(missing={k: len(v) for k, v in missing.items()}, row_issues=issues,
                           far_parity_mismatches=dict(ohlc=int(par[:, 0].sum()), olhc=int(par[:, 1].sum()))),
               linear_mean_excess=mexcess, threshold=cut, configs={})
    paths = {}

    def evaluate(g, name):
        """Per-period stats of top-40 trades and equal/linear replays over random tie orders."""
        g = g.sort_values(['entry', 'event'], kind='mergesort').reset_index(drop=True)
        r = {}
        for per, within in periods.items():
            x = g[within(g.t)]
            xt = x[x.score >= cut]
            lost = (xt.reason_w == 1) & xt.event.map(far_win).fillna(False).astype(bool)
            r[per] = dict(n=len(xt), win_pct=float(100*(xt.net > 0).mean()), mean_bp=float(xt.net.mean()),
                          p1_bp=float(xt.net.quantile(.01)), min_bp=float(xt.net.min()),
                          backstop_exit_pct=float(100*(xt.reason_w == 1).mean()), far_winners_stopped=int(lost.sum()),
                          all_mean_bp=float(x.net.mean()))
            for sizing in ('equal', 'linear'):
                y = xt.reset_index(drop=True)
                base, path = opt.replay_w(y, y[f'k_{sizing}'].to_numpy(), 1.)
                rep = []
                for _ in range(TIES):
                    order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
                    z = y.iloc[order].reset_index(drop=True)
                    rep.append(opt.replay_w(z, z[f'k_{sizing}'].to_numpy(), 1.)[0])
                f = np.array([q['final_multiple'] for q in rep]); dd = np.array([q['max_drawdown_pct'] for q in rep])
                r[per][sizing] = dict(stable=base, final_median=float(np.median(f)), final_p10=float(np.quantile(f, .1)),
                                      final_p90=float(np.quantile(f, .9)), dd_median=float(np.median(dd)))
                if per == 'test_2026':
                    paths[f'{name}|{sizing}'] = path
        return r

    for name, g in m.groupby('config'):
        res['configs'][name] = evaluate(g, name)
    best = max(res['configs'], key=lambda k: res['configs'][k]['selection_2025']['linear']['final_median'])
    res['chosen_on_2025_linear'] = best
    # Owner requires a backstop: the recommendation is chosen among backstop configs only ('far' = none).
    res['chosen_backstop_on_2025_linear'] = max((k for k in res['configs'] if k != 'far'),
                                                key=lambda k: res['configs'][k]['selection_2025']['linear']['final_median'])
    # Score-dependent backstop: per fixed score band (owner asked for finer bands, as in
    # monday_limit_reclaim_score_detail), the config with the best 2025 mean net per top-40 trade among
    # backstops (owner requires one), frozen for 2026; paired difference to no backstop with its standard error.
    edges = [cut, .30, .375, .45, .50, .60, .70, .80]
    tops = m[m.score >= cut].copy()
    tops['band'] = np.digitize(tops.score, edges[1:])
    far_net = tops[tops.config == 'far'].set_index('event').net
    bands = {}
    for b, y in tops.groupby('band'):
        table = {c: {per: float(g[within(g.t)].net.mean()) for per, within in periods.items()} for c, g in y.groupby('config')}
        for c, g in y[y.t >= SEL[0]].groupby('config'):
            d = g.net.to_numpy()-far_net.reindex(g.event).to_numpy()
            table[c] |= dict(diff_vs_none_from_2025=float(np.nanmean(d)), diff_se=float(np.nanstd(d, ddof=1)/np.sqrt(np.isfinite(d).sum())))
        bands[int(b)] = dict(low=float(edges[int(b)]), n_2025=int(periods['selection_2025'](y[y.config == 'far'].t).sum()),
                             n_2026=int(periods['test_2026'](y[y.config == 'far'].t).sum()),
                             chosen=max((c for c in table if c != 'far'), key=lambda c: table[c]['selection_2025']),
                             best_including_none=max(table, key=lambda c: table[c]['selection_2025']), mean_bp_by_config=table)
    pick = tops[tops.config == tops.band.map({b: v['chosen'] for b, v in bands.items()})]
    res['score_bands'] = bands
    res['configs']['by_score'] = evaluate(pick, 'by_score')
    EXP.mkdir(exist_ok=True)
    charts(res, paths)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(dict(checks=res['checks'], chosen=best, chosen_backstop=res['chosen_backstop_on_2025_linear'], bands={b: (v['low'], v['chosen'], v['n_2025']) for b, v in res['score_bands'].items()}), ensure_ascii=False))
    for name, r in res['configs'].items():
        print(name, *(f"{per}: n{r[per]['n']} win{r[per]['win_pct']:.1f} mean{r[per]['mean_bp']:.1f} p1 {r[per]['p1_bp']:.0f} min {r[per]['min_bp']:.0f} "
                      f"bs{r[per]['backstop_exit_pct']:.1f}% lostwin {r[per]['far_winners_stopped']} eq {r[per]['equal']['final_median']:.2f}/{r[per]['equal']['dd_median']:.0f} "
                      f"lin {r[per]['linear']['final_median']:.2f}/{r[per]['linear']['dd_median']:.0f}" for per in periods))


def charts(res, paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    names = [n for n in ZH if n in res['configs']]
    x = np.arange(len(names)); w = .38
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax_, per, title in zip(axes, ('selection_2025', 'test_2026', 'test_last5m'), ('2025年（选止损）', '2026年（检验）', '近5个月')):
        for off, sizing, col, lab in ((-w/2, 'equal', '#898781', '等额每笔10%'), (w/2, 'linear', '#2a78d6', '按分数线性加仓')):
            ax_.bar(x+off, [res['configs'][n][per][sizing]['final_median'] for n in names], w*.92, color=col, label=lab)
        ax_.axhline(1, color='#c3c2b7', lw=1); ax_.set_xticks(x, [ZH[n] for n in names], fontsize=9)
        ax_.set_title(title, color=INK, loc='left'); ax_.grid(axis='y', color=GRID, lw=.6); ax_.set_ylabel('1000U 期末倍数（中位数）')
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].legend(frameon=False, loc='upper left')
    fig.suptitle('兜底止损距离 · 只做分数前40% · 每笔取两种K线顺序中较差的结果 · 止损出场加10bp滑点 · 总仓位≤1倍', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_backstop_multiples.png', dpi=130); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.6))
    for ax_, key, title in ((axes[0], 'mean_bp', '每笔平均净收益（bp）'), (axes[1], 'p1_bp', '最差1%单子的亏损（bp）')):
        for off, per, col, lab in ((-w/2, 'selection_2025', '#898781', '2025年'), (w/2, 'test_2026', '#2a78d6', '2026年')):
            ax_.bar(x+off, [res['configs'][n][per][key] for n in names], w*.92, color=col, label=lab)
        ax_.axhline(0, color='#c3c2b7', lw=1); ax_.set_xticks(x, [ZH[n] for n in names], fontsize=9)
        ax_.set_title(title, color=INK, loc='left'); ax_.grid(axis='y', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].legend(frameon=False)
    fig.tight_layout(); fig.savefig(EXP/'2_backstop_per_trade.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

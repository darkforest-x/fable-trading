"""Fee-covering breakeven and scale-in legs: comparison on framework fills (owner2026-10-26).

Inputs: exp-monday-limit-be-scalein-20261026-v1 (native runs, both intrabar paths). Checks: every per-symbol output
readable with rows equal to positions; offset-0 rows equal the earlier ledgers (v2_be0 = grid near_far, v3_be0 =
levels r0618_P30 / opp1618_P30). The two bad source bars are dropped. 10bp slippage on every non-target exit
(breakeven, failed reclaim, reclaim-inside-offset exits and backstops).
Breakeven offsets: per family and offset, the worse path per trade, exit-reason mix, mean net, and 1000 USDT replays
with linear sizing in score (v2: threshold 0.226; v3: threshold 0.5; cap 0.5, gross <= 1x).
Scale-in (v3, score >= 0.6, offset 0): parent plus an equal-notional child at 5 or 10 percent; the worse intrabar
path is chosen per event on the combined result and both legs use it. Reported: child fill rate, child net, the
combined net per parent notional, and replays where the child takes the parent's fraction at its own fill time.
Periods 2020-2024 (score training period), 2025, 2026, last five months; medians over TIES random orders of
same-time fills. Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-be-scalein-compare-20261026-v1'
A = ROOT/'experiments/active'
RUN = A/'exp-monday-limit-be-scalein-20261026-v1'
BAD_EVENTS = {'BABYUSDT:2026-06-01:-1', 'DASHUSDT:2020-04-13:+1'}
B = (pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC'))
TIES = 10
LAB = {4: 'target', 5: 'breakeven', 11: 'failed_reclaim', 12: 'reclaimed_inside_offset', 1: 'backstop'}


def load(symbol):
    out, issues, par = {}, [], [[], []]
    for k, path in enumerate(('ohlc', 'olhc')):
        d = RUN/path/'symbols'/symbol
        done = json.loads((d/'done.json').read_text())
        t = pd.read_csv(d/'trades.csv.gz')
        if done.get('positions', 0) != len(t):
            issues.append(f'{path}: rows {len(t)} != positions {done.get("positions")}')
        t['base'] = t.trade_id.str.rsplit(':v_', n=1).str[0]
        if len(t):
            g = pd.read_csv(A/'exp-monday-limit-v3-grid-20261025-v1'/path/'symbols'/symbol/'trades.csv.gz', usecols=['trade_id', 'variant', 'net_notional_bp'])
            g = g[g.variant == 'near_far'].assign(base=lambda x: x.trade_id.str.rsplit(':v_', n=1).str[0])
            m = t[t.variant == 'v2_be0'].merge(g[['base', 'net_notional_bp']], on='base', how='left', suffixes=('', '_r'))
            lv = pd.read_csv(A/'exp-monday-limit-v3-levels-20261025-v1'/path/'symbols'/symbol/'trades.csv.gz', usecols=['trade_id', 'variant', 'score', 'net_notional_bp'])
            lv = lv[((lv.score >= .6) & (lv.variant == 'opp1618_P30')) | ((lv.score < .6) & (lv.variant == 'r0618_P30'))]
            lv = lv.assign(base=lambda x: x.trade_id.str.rsplit(':v_', n=1).str[0])
            n = t[t.variant == 'v3_be0'].merge(lv[['base', 'net_notional_bp']], on='base', how='left', suffixes=('', '_r'))
            bad = lambda z: set(z.base[((z.net_notional_bp-z.net_notional_bp_r).abs() > 1e-6) | z.net_notional_bp_r.isna()])
            par[k] = sorted(bad(m) | bad(n))
        out[path] = t
    return symbol, out, issues, par


def worse(o, l):
    m = o.merge(l[['trade_id', 'net_notional_bp', 'reason', 'entry_time', 'exit_time']], on='trade_id', how='outer', suffixes=('_o', '_l'))
    for s in ('o', 'l'):
        m[f'net_{s}'] = m[f'net_notional_bp_{s}']-np.where(m[f'reason_{s}'] == 4, 0., opt.SLIP)
    return m


def clean(x):
    """NaN means no rows in a period; store it as null."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return None if isinstance(x, float) and not np.isfinite(x) else x


def periods(window):
    return {'y2020_2024': lambda t: t < B[0], 'y2025': lambda t: (t >= B[0]) & (t < B[1]), 'y2026': lambda t: t >= B[1], 'last5m': lambda t: t >= window}


def replay_job(args):
    name, x, cut, mexcess, window, seed = args
    rng = np.random.default_rng(seed)
    out = {}
    for per, within in periods(window).items():
        y = x[within(x.t)].sort_values(['entry', 'tid'], kind='mergesort').reset_index(drop=True)
        k = np.minimum(.05*(y.score.to_numpy()-cut)/mexcess, .5)
        rep = []
        for _ in range(TIES):
            order = np.lexsort((rng.random(len(y)), y.entry.to_numpy()))
            z = y.iloc[order].reset_index(drop=True)
            rep.append(opt.replay_w(z, k[order], 1.)[0])
        out[per] = dict(n=len(y), final_median=float(np.median([r['final_multiple'] for r in rep])),
                        dd_median=float(np.median([r['max_drawdown_pct'] for r in rep])))
    return name, out


def run():
    p = 'yoyo/evaluation/monday_limit_be_scalein_compare.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = json.loads((RUN/'config.json').read_text())['symbols']
    missing = [(k, s) for k in ('ohlc', 'olhc') for s in syms if not (RUN/k/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    with ProcessPoolExecutor(4) as ex:
        got = list(ex.map(load, syms, chunksize=8))
    o = pd.concat([g['ohlc'] for _, g, _, _ in got], ignore_index=True); l = pd.concat([g['olhc'] for _, g, _, _ in got], ignore_index=True)
    issues = [i for _, _, iss, _ in got for i in iss]
    parity = dict(ohlc=sorted({e for *_, pr in got for e in pr[0]}), olhc=sorted({e for *_, pr in got for e in pr[1]}))
    m = worse(o, l)
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':')
    m = m[~m.event.isin(BAD_EVENTS)].copy()
    m['entry_ns_o'] = pd.to_datetime(m.entry_time_o, utc=True).astype('int64'); m['exit_ns_o'] = pd.to_datetime(m.exit_time_o, utc=True).astype('int64')
    m['entry_ns_l'] = pd.to_datetime(m.entry_time_l, utc=True).astype('int64'); m['exit_ns_l'] = pd.to_datetime(m.exit_time_l, utc=True).astype('int64')
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = periods(window)
    res = dict(checks=dict(missing=len(missing), row_issues=issues, parity_mismatch_events=parity), offsets={}, scale_in={}, replays={})
    jobs = []
    # Breakeven offsets: per-trade worse path.
    single = m[~m.variant.str.contains('add')].copy()
    w = (single.net_l < single.net_o).to_numpy()
    single['net'] = np.where(w, single.net_l, single.net_o); single['reason_w'] = np.where(w, single.reason_l, single.reason_o)
    single['entry'] = np.where(w, single.entry_ns_l, single.entry_ns_o); single['exit'] = np.where(w, single.exit_ns_l, single.exit_ns_o)
    single['t'] = pd.to_datetime(single.entry, utc=True); single['tid'] = single.trade_id
    for v, g in single.groupby('variant'):
        r = {}
        for per, f in pers.items():
            y = g[f(g.t)]
            r[per] = dict(n=len(y), mean_bp=float(y.net.mean()), win_pct=float(100*(y.net > 0).mean()),
                          reasons={LAB.get(int(k), str(k)): float(100*c) for k, c in y.reason_w.value_counts(normalize=True).items()})
        res['offsets'][v] = r
        jobs.append((v, g[['tid', 'score', 'entry', 'exit', 'net', 't']].copy(), cut, mexcess, window, 20261026+len(jobs)))
    # Scale-in: combined parent + child per event, worse path on the combined result.
    par_ = m[m.variant == 'v3_be0'].assign(base=lambda x: x.trade_id.str.rsplit(':v_', n=1).str[0]).set_index('base')
    for dd in json.loads((RUN/'config.json').read_text())['scale_in_pct']:
        ch = m[m.variant == f'v3_add{dd}'].assign(base=lambda x: x.trade_id.str.rsplit(':v_', n=1).str[0]).set_index('base')
        hp = par_[par_.score >= .6]
        c = ch.reindex(hp.index)
        co = hp.net_o+c.net_o.fillna(0.); cl = hp.net_l+c.net_l.fillna(0.)
        wl = (cl < co).to_numpy()
        pnet = np.where(wl, hp.net_l, hp.net_o); cnet = np.where(wl, c.net_l, c.net_o)
        filled = ~np.isnan(cnet)
        t = pd.to_datetime(np.where(wl, hp.entry_ns_l, hp.entry_ns_o), utc=True)
        sres = {}
        for per, f in pers.items():
            sel = np.asarray(f(t))
            sres[per] = dict(events=int(sel.sum()), child_fill_pct=float(100*filled[sel].mean()),
                             parent_mean_bp=float(np.nanmean(pnet[sel])), child_mean_bp_when_filled=float(np.nanmean(cnet[sel & filled])) if (sel & filled).any() else None,
                             combined_mean_bp_per_parent_notional=float(np.nanmean(pnet[sel]+np.nan_to_num(cnet[sel]))),
                             parent_mean_bp_when_child_filled=float(np.nanmean(pnet[sel & filled])) if (sel & filled).any() else None)
        res['scale_in'][f'add{dd}'] = sres
        # Plan for replay: v3_be0 for score < 0.6 plus parents and filled children (worse combined path) for >= 0.6.
        lo = single[(single.variant == 'v3_be0') & (single.score < .6)][['tid', 'score', 'entry', 'exit', 'net', 't']]
        hp2 = pd.DataFrame(dict(tid=hp.trade_id.to_numpy(), score=hp.score.to_numpy(), entry=np.where(wl, hp.entry_ns_l, hp.entry_ns_o),
                                exit=np.where(wl, hp.exit_ns_l, hp.exit_ns_o), net=pnet))
        ch2 = pd.DataFrame(dict(tid=c.trade_id.to_numpy(), score=hp.score.to_numpy(), entry=np.where(wl, c.entry_ns_l, c.entry_ns_o),
                                exit=np.where(wl, c.exit_ns_l, c.exit_ns_o), net=cnet))[filled]
        plan = pd.concat([lo, hp2, ch2], ignore_index=True)
        plan['t'] = pd.to_datetime(plan.entry, utc=True)
        jobs.append((f'v3_be0_plus_add{dd}', plan, cut, mexcess, window, 20261026+len(jobs)))
    with ProcessPoolExecutor(4) as ex:
        res['replays'] = dict(ex.map(replay_job, jobs))
    EXP.mkdir(exist_ok=True)
    charts(res)
    res = clean(res)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))
    for v, r in res['offsets'].items():
        print(f'{v:8s}', *(f"{per}: n{x['n']} mean {x['mean_bp']:.1f} win {x['win_pct']:.1f} " + ' '.join(f"{k[:4]}{vv:.0f}" for k, vv in x['reasons'].items()) for per, x in r.items()))
    for k, r in res['scale_in'].items():
        print(k, json.dumps({p_: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in x.items()} for p_, x in r.items()}, ensure_ascii=False))
    for k, r in res['replays'].items():
        print(f'{k:22s}', *(f"{per}: {x['final_median']:.2f}/{x['dd_median']:.0f}" for per, x in r.items()))


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    pers = [('y2020_2024', '2020–2024'), ('y2025', '2025'), ('y2026', '2026'), ('last5m', '近5个月')]
    rows = [('v2_be0', 'v2 保本=开仓价'), ('v2_be7', 'v2 保本+0.07%'), ('v2_be17', 'v2 保本+0.17%'),
            ('v3_be0', 'v3 保本=开仓价'), ('v3_be7', 'v3 保本+0.07%'), ('v3_be17', 'v3 保本+0.17%'),
            ('v3_be0_plus_add5', 'v3 + 浮亏-5%加仓'), ('v3_be0_plus_add10', 'v3 + 浮亏-10%加仓')]
    rows = [r for r in rows if r[0] in res['replays']]
    fig, axes = plt.subplots(1, len(pers), figsize=(20, 5.5), sharey=True)
    y = np.arange(len(rows))
    cols = ['#898781']*3+['#2a78d6']*3+['#1baf7a']*2
    for ax_, (per, title) in zip(axes, pers):
        vals = [res['replays'][k][per]['final_median'] for k, _ in rows]
        ax_.barh(y, vals, .7, color=cols[:len(rows)])
        for yy, (k, _) in zip(y, rows):
            r = res['replays'][k][per]
            ax_.text(r['final_median'], yy, f" {r['final_median']:.2f}x / {r['dd_median']:.0f}%", va='center', fontsize=8, color=INK2)
        ax_.axvline(1, color='#c3c2b7', lw=1); ax_.set_title(title, color=INK, loc='left'); ax_.grid(axis='x', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].set_yticks(y, [lab for _, lab in rows]); axes[0].invert_yaxis()
    fig.suptitle('保本价覆盖手续费 · 浮亏加仓 · 1000U 按分数线性加仓（期末倍数 / 最大回撤）· 较差K线顺序 · 非止盈出场加10bp滑点', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_be_scalein.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

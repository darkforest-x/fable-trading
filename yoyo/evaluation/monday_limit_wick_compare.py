"""5m wick stop versus the 60m reclaim rule for the Monday1618 limit (owner2026-10-25).

Inputs: exp-monday-limit-wick-stop-{ohlc,olhc}-20261025-v1 (fill-bar wick stop,
near Monday edge target, both intrabar paths, monotone segments) and the 60m
reclaim near-edge legs of exp-monday-limit-reclaim-exits-{ohlc,olhc}-20261025-v1.
Every per-symbol output is checked first (readable, rows equal to recorded
positions) because two schedulers briefly overlapped on 2026-10-25. Per trade
the worse path is kept; extra slippage of 10/20/30bp is charged on non-target
exits. R = entry-to-wick distance. Reports per-notional and per-R results by
stop-distance bucket and period, and 1000 USDT replays: equal notional (10
percent, gross <= 1x) and equal risk (1 percent of equity per trade, notional
capped at 3x equity per trade and 5x in total). Read-only arithmetic on
framework fills.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-wick-compare-20261025-v1'
A = ROOT/'experiments/active'
BUCKETS = ([0, 15, 30, 60, 120, 250, 1e9], ['<0.15%', '0.15-0.3%', '0.3-0.6%', '0.6-1.2%', '1.2-2.5%', '>2.5%'])


def load(root):
    rows, bad = [], []
    for d in sorted((root/'symbols').iterdir()):
        done = d/'done.json'
        if not done.exists():
            bad.append(dict(symbol=d.name, issue='missing done')); continue
        try:
            t = pd.read_csv(d/'trades.csv.gz')
        except Exception as err:
            bad.append(dict(symbol=d.name, issue=repr(err))); continue
        meta = json.loads(done.read_text())
        if meta.get('positions', 0) != len(t):
            bad.append(dict(symbol=d.name, issue=f"rows {len(t)} != positions {meta.get('positions')}"))
        rows.append(t.assign(symbol=d.name))
    return pd.concat(rows, ignore_index=True), bad


def replay(t, k, gross):
    net = t.net.to_numpy()/1e4
    same = t.exit.to_numpy() == t.entry.to_numpy()
    ev = np.concatenate([np.c_[t.exit.to_numpy(), np.where(same, 2., 0.), np.arange(len(t))],
                         np.c_[t.entry.to_numpy(), np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    eq, peak, dd, opened, notional, path = 1000., 1000., 0., 0., np.zeros(len(t)), []
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            notional[j] = max(0., min(k[j]*eq, gross*eq-opened)); opened += notional[j]
        else:
            eq += notional[j]*net[j]; opened -= notional[j]
            peak = max(peak, eq); dd = max(dd, 1-eq/peak); path.append((ts, eq))
            if eq <= 0:
                return dict(final_multiple=0., max_drawdown_pct=100.), path
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd), path


def worst(o, l, slip):
    m = o.merge(l[['trade_id', 'net_notional_bp', 'reason', 'entry_time', 'exit_time']], on='trade_id', suffixes=('_o', '_l'))
    no = m.net_notional_bp_o-np.where(m.reason_o == 4, 0., slip); nl = m.net_notional_bp_l-np.where(m.reason_l == 4, 0., slip)
    use_l = nl < no
    m['entry_px'] = m['entry']
    m['net'] = np.where(use_l, nl, no)
    m['entry'] = pd.to_datetime(np.where(use_l, m.entry_time_l, m.entry_time_o), utc=True).astype('int64')
    m['exit'] = pd.to_datetime(np.where(use_l, m.exit_time_l, m.exit_time_o), utc=True).astype('int64')
    m['t'] = pd.to_datetime(m.entry_time_o, utc=True)
    return m


def stats(x, col='net'):
    return dict(n=len(x), win_pct=float(100*(x[col] > 0).mean()), mean=float(x[col].mean()), median=float(x[col].median()))


def run():
    p = 'yoyo/evaluation/monday_limit_wick_compare.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    wo, bad_o = load(A/'exp-monday-limit-wick-stop-ohlc-20261025-v1'); wl, bad_l = load(A/'exp-monday-limit-wick-stop-olhc-20261025-v1')
    ro, _ = load(A/'exp-monday-limit-reclaim-exits-ohlc-20261025-v1'); rl, _ = load(A/'exp-monday-limit-reclaim-exits-olhc-20261025-v1')
    ro = ro[ro.trade_id.str.endswith(':tp100')]; rl = rl[rl.trade_id.str.endswith(':tp100')]
    checks = dict(wick_ohlc_issues=bad_o, wick_olhc_issues=bad_l)
    res = dict(checks=checks, wick={}, reclaim={}, replays={})
    paths = {}
    for slip in (10, 20, 30):
        w = worst(wo, wl, slip)
        w['R_bp'] = w.side*(w.entry_px-w.wick_stop)/w.entry_px*1e4
        w = w[w.R_bp > 0].copy(); w['R'] = w.net/w.R_bp
        w['b'] = pd.cut(w.R_bp, bins=BUCKETS[0], labels=BUCKETS[1])
        half = np.where(w.t < SPLIT, 'discovery', 'validation')
        res['wick'][f'slip{slip}'] = dict(all=stats(w) | dict(mean_R=float(w.R.mean()), median_R=float(w.R.median())),
            by_half={h: stats(g) | dict(mean_R=float(g.R.mean())) for h, g in w.groupby(half)},
            by_stop={str(b): stats(g) | dict(mean_R=float(g.R.mean()), median_R=float(g.R.median()))
                     for b, g in w.groupby('b', observed=True)})
        r = worst(ro, rl, slip)
        res['reclaim'][f'slip{slip}'] = dict(all=stats(r), by_half={h: stats(g) for h, g in r.groupby(np.where(r.t < SPLIT, 'discovery', 'validation'))})
        ww = w.reset_index(drop=True); rr = r.reset_index(drop=True)
        for name, data, k, gross in (('wick_notional10', ww, np.full(len(ww), .10), 1.),
                                     ('wick_risk1pct', ww, np.minimum(100/ww.R_bp.to_numpy(), 3.), 5.),
                                     ('wick_risk1pct_stop0.15-0.6', ww, np.where(ww.R_bp.between(15, 60), np.minimum(100/ww.R_bp.to_numpy(), 3.), 0.), 5.),
                                     ('reclaim_notional10', rr, np.full(len(rr), .10), 1.)):
            out, path = replay(data, k, gross)
            res['replays'][f'{name}|slip{slip}'] = out
            if slip == 10:
                paths[name] = path
            if slip == 20 and name.startswith('wick_risk1pct'):
                paths[name+'|slip20'] = path
    EXP.mkdir(exist_ok=True)
    charts(res, paths)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1)[:8000])


def charts(res, paths):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.6))
    labs = BUCKETS[1]; x = np.arange(len(labs)); w = .26
    for off, slip, col in ((-w, 10, BLUE), (0, 20, ORANGE), (w, 30, MUTED)):
        b = res['wick'][f'slip{slip}']['by_stop']
        axes[0].bar(x+off, [b[l]['mean_R'] if l in b else np.nan for l in labs], w*.92, color=col, label=f'止损滑点 {slip}bp')
        axes[1].bar(x+off, [b[l]['mean'] if l in b else np.nan for l in labs], w*.92, color=col)
    for ax, t in zip(axes, ('每笔平均（R，按风险等额）', '每笔平均（bp，按名义等额）')):
        ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, labs); ax.set_xlabel('止损距离（入场价到影线极值）')
        ax.set_title(t, color=INK, loc='left'); ax.grid(axis='y', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    axes[0].set_yscale('symlog', linthresh=1); axes[0].legend(frameon=False, loc='upper right')
    fig.suptitle('5分钟影线止损 · 止盈近侧周一边界 · 每笔取两种K线顺序中较差的结果', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_wick_by_stop.png', dpi=130); plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 5))
    names = {'reclaim_notional10': ('60分钟确认，每笔10%，总仓位≤1倍', MUTED), 'wick_notional10': ('影线止损，每笔10%，总仓位≤1倍', BLUE),
             'wick_risk1pct': ('影线止损，每笔风险1%（单笔≤3倍、总≤5倍）', ORANGE),
             'wick_risk1pct_stop0.15-0.6': ('同上，只做止损0.15%-0.6%', '#1baf7a')}
    for k, (lab, col) in names.items():
        for key, ls, suffix in ((k, '-', '（滑点10bp）'), (k+'|slip20', '--', '（滑点20bp）')):
            if key not in paths:
                continue
            pp = np.array(paths[key][::max(1, len(paths[key])//3000)])
            ax.plot(pd.to_datetime(pp[:, 0]), np.maximum(pp[:, 1], 1e-2), color=col, lw=2, ls=ls, label=lab+suffix)
    ax.set_yscale('log'); ax.axhline(1000, color='#c3c2b7', lw=1); ax.legend(frameon=False, loc='upper left', fontsize=9)
    ax.set_ylabel('已实现权益（USDT，对数）')
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title('1000U · 较差K线顺序 · 2020–2026（虚线=止损滑点20bp；按风险等额的曲线只在滑点10bp时上天，20bp直接归零）', color=INK, loc='left', fontsize=10)
    fig.tight_layout(); fig.savefig(EXP/'2_wick_equity.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()

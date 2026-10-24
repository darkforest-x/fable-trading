"""Review of the Codex Monday1618 limit-reclaim snapshot (owner2026-10-24).

Owner asked for a detailed analysis with charts of the partial snapshot
output/monday-partial-snapshot-20261024-2224 (Notion task #70: rest a limit at
the1618, after the next 1/3/5/15/60-minute candle closes back inside move the
stop to entry, else exit at that close; five targets), and whether 1000 USDT
can compound to 100x-10000x. Reads only the framework ledgers listed in the
snapshot manifest (NautilusTrader1.231.0 trades.csv.gz, real maker/taker
fees); no trade is re-executed. Reported:
  grid of mean net bp per trade, median, win rate, top-1percent profit share,
  mean without the top1percent, and complete matched groups (signal with all
  three random controls closed) signal minus control;
  outcome mix, entry year and side for the best mean cell (alts, 60m, near
  Monday edge, chosen after seeing the grid);
  an equity replay of those framework trades from 1000 USDT with a fixed
  fraction k of realized equity as notional per trade (overlaps allowed),
  optional extra taker slippage and a per-trade notional cap. This is
  arithmetic on recorded fills, not a new backtest; it ignores unrealized
  losses, funding, queue misses and liquidity.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-review-20261024-v1'
SRC = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1/symbols'
SNAP = ROOT/'output/monday-partial-snapshot-20261024-2224/manifest.json'
COLS = ['trade_id', 'symbol', 'asset_group', 'week', 'side', 'population', 'reclaim_minutes', 'exit_mode',
        'net_bp', 'reason', 'closed', 'entry_time', 'exit_time']
TARGETS = ['near_monday_edge', 'range_0.5', 'range_0.618', 'opposite_monday_edge', 'opposite_1618']
TARGET_ZH = dict(near_monday_edge='近侧周一边界', **{'range_0.5': '区间0.5', 'range_0.618': '区间0.618'},
                 opposite_monday_edge='远侧周一边界', opposite_1618='对侧1.618')
INK, INK2, MUTED, GRID, SURF = '#0b0b0b', '#52514e', '#898781', '#e1e0d9', '#fcfcfb'
BLUE, ORANGE, AQUA, YELLOW = '#2a78d6', '#eb6834', '#1baf7a', '#eda100'


def load(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    return t[t.reclaim_minutes.isin([5, 15, 60])]


def cell(x):
    """Per-trade stats of one (period, target) cell; x holds signal and controls."""
    grp = x.trade_id.str.rsplit(':', n=1).str[0]
    sig = x[(x.population == 'signal') & x.closed]
    ctl = x[x.population == 'random'].groupby(grp[x.population == 'random']).agg(
        n=('closed', 'sum'), mean=('net_bp', 'mean'))
    joined = ctl.reindex(grp[sig.index])
    full = (joined.n == 3).to_numpy()
    v = np.sort(sig.net_bp.to_numpy())[::-1]; top = max(1, len(v)//100)
    return dict(n=len(v), mean_bp=float(v.mean()), median_bp=float(np.median(v)), win_pct=float(100*(v > 0).mean()),
                top1pct_share=float(100*v[:top].sum()/v.sum()), mean_without_top1pct_bp=float(v[top:].mean()),
                complete_n=int(full.sum()), complete_signal_bp=float(sig.net_bp.to_numpy()[full].mean()),
                complete_control_bp=float(joined['mean'].to_numpy()[full].mean()),
                incomplete_signal_bp=float(sig.net_bp.to_numpy()[~full].mean()))


def replay(t, k, cap=None, slip_bp=0., start=None):
    """Realized-equity path from 1000 USDT; notional=min(k*equity, cap) at entry."""
    if start is not None:
        t = t[t.entry >= start]
    net = t.net_bp.to_numpy()/1e4 - np.where(t.reason.to_numpy() == 4, 0., slip_bp/1e4)
    ev = np.concatenate([np.c_[t.exit.to_numpy(), np.zeros(len(t)), np.arange(len(t))],
                         np.c_[t.entry.to_numpy(), np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]  # exits before entries at the same time
    eq, peak, dd, expo, notional, opened = 1000., 1000., 0., 0., np.zeros(len(t)), 0.
    path = []
    for ts, is_entry, j in ev:
        j = int(j)
        if is_entry:
            notional[j] = min(k*eq, cap) if cap else k*eq
            opened += notional[j]; expo = max(expo, opened/eq)
        else:
            eq += notional[j]*net[j]; opened -= notional[j]
            peak = max(peak, eq); dd = max(dd, 1-eq/peak); path.append((ts, eq))
            if eq <= 0:
                return dict(final_multiple=0., max_drawdown_pct=100., max_gross_exposure=expo, ruined=True), path
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, max_gross_exposure=expo, ruined=False), path


def charts(grid, best, conc, years, paths, outcomes):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF, 'axes.grid': False})
    div = LinearSegmentedColormap.from_list('d', ['#d03b3b', '#f0efec', BLUE])
    # 1. Grid heatmaps: all-signal mean and complete-group excess over random.
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.2))
    for ax, key, title in ((axes[0], 'mean_bp', '全部信号：每笔扣费后平均收益（bp）'),
                           (axes[1], 'excess', '完整匹配组：信号 − 随机对照（bp）')):
        m = np.array([[grid[(p, tg)]['mean_bp'] if key == 'mean_bp'
                       else grid[(p, tg)]['complete_signal_bp']-grid[(p, tg)]['complete_control_bp']
                       for tg in TARGETS] for p in (5, 15, 60)])
        lim = np.abs(m).max()
        ax.imshow(m, cmap=div, norm=TwoSlopeNorm(0, -lim, lim), aspect='auto')
        for (i, j), val in np.ndenumerate(m):
            ax.text(j, i, f'{val:+.1f}', ha='center', va='center', color=INK, fontsize=11)
        ax.set_xticks(range(5), [TARGET_ZH[t] for t in TARGETS]); ax.set_yticks(range(3), ['5分钟确认', '15分钟确认', '60分钟确认'])
        ax.set_title(title, color=INK, fontsize=12, loc='left')
        for s in ax.spines.values():
            s.set_visible(False)
    fig.suptitle('山寨币 534 个 · 1.618 挂单收回确认（快照 536/633 品种，扣真实手续费）', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_grid.png', dpi=130); plt.close(fig)
    # 2. Profit concentration of the best cell.
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.plot(conc[:, 0], conc[:, 1], color=BLUE, lw=2)
    ax.axhline(100, color='#c3c2b7', lw=1); ax.axvline(1, color=GRID, lw=1)
    i1 = np.searchsorted(conc[:, 0], 1.); ipk = int(np.argmax(conc[:, 1]))
    ax.annotate(f'前 1% 的单子 = 总利润的 {conc[i1, 1]:.0f}%', (1, conc[i1, 1]), (6, conc[i1, 1]-25), color=INK2,
                arrowprops=dict(arrowstyle='-', color=MUTED))
    ax.annotate(f'前 {conc[ipk, 0]:.0f}% 的单子 = {conc[ipk, 1]:.0f}%，其余全部合计亏钱', (conc[ipk, 0], conc[ipk, 1]),
                (conc[ipk, 0]+8, conc[ipk, 1]+10), color=INK2, arrowprops=dict(arrowstyle='-', color=MUTED))
    ax.set_xlim(0, 100); ax.set_xlabel('按单笔收益从高到低排序，前 x% 的单子'); ax.set_ylabel('累计占总利润 %')
    ax.grid(axis='y', color=GRID, lw=.6); ax.set_title(f"利润集中度 · {best}", color=INK, loc='left')
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    fig.tight_layout(); fig.savefig(EXP/'2_concentration.png', dpi=130); plt.close(fig)
    # 3. Outcome mix: share of trades and mean result, two panels (no dual axis).
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    names = list(outcomes); share = [outcomes[n]['share_pct'] for n in names]; mean = [outcomes[n]['mean_bp'] for n in names]
    cols = [AQUA, MUTED, ORANGE]
    axes[0].barh(names, share, color=cols, height=.55)
    axes[1].barh(names, mean, color=cols, height=.55); axes[1].axvline(0, color='#c3c2b7', lw=1)
    for ax, vals, fmt, t in ((axes[0], share, '{:.0f}%', '占全部单子'), (axes[1], mean, '{:+.0f}bp', '每笔平均扣费后')):
        for y, v in enumerate(vals):
            ax.text(v, y, ' '+fmt.format(v), va='center', ha='left' if v >= 0 else 'right', color=INK, fontsize=10)
        ax.set_title(t, color=INK, loc='left', fontsize=11); ax.invert_yaxis()
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
    axes[1].set_xlim(min(mean)*1.6, max(mean)*1.35)
    fig.suptitle(f'三种结局 · {best}', color=INK, x=.01, ha='left'); fig.tight_layout()
    fig.savefig(EXP/'3_outcomes.png', dpi=130); plt.close(fig)
    # 4. By entry year.
    fig, ax = plt.subplots(figsize=(10, 4.4))
    yrs = sorted(years); w = .26; x = np.arange(len(yrs))
    for off, key, col, lab in ((-w, 'all_bp', BLUE, '全部信号'), (0, 'complete_signal_bp', AQUA, '完整匹配组·信号'),
                               (w, 'complete_control_bp', ORANGE, '完整匹配组·随机对照')):
        ax.bar(x+off, [years[y][key] for y in yrs], w*.9, color=col, label=lab)
    ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, [f"{y}\n{years[y]['n']}笔" for y in yrs])
    ax.set_ylabel('每笔扣费后平均（bp）'); ax.legend(frameon=False, ncols=3, loc='upper left')
    ax.grid(axis='y', color=GRID, lw=.6); ax.set_title(f'按入场年份 · {best}', color=INK, loc='left')
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    fig.tight_layout(); fig.savefig(EXP/'4_years.png', dpi=130); plt.close(fig)
    # 5. Equity replays (log scale).
    fig, ax = plt.subplots(figsize=(11, 5))
    for (lab, col, ls), path in zip((('每笔名义 = 权益 10%', BLUE, '-'), ('10% + 平仓滑点 10bp', ORANGE, '-'),
                                     ('10% + 单笔上限 2 万U', AQUA, '-'), ('每笔名义 = 权益 2%', YELLOW, '-')), paths):
        p = np.array(path)
        ax.plot(pd.to_datetime(p[:, 0]), p[:, 1], color=col, lw=2, ls=ls, label=lab)
    ax.set_yscale('log'); ax.axhline(1000, color='#c3c2b7', lw=1)
    for mult in (100, 1000, 10000):
        ax.axhline(1000*mult, color=GRID, lw=.8, ls='--'); ax.text(ax.get_xlim()[0], 1000*mult, f' {mult}倍', color=MUTED, va='bottom')
    ax.set_ylabel('已实现权益（USDT，对数）'); ax.legend(frameon=False, loc='upper left')
    ax.set_title(f'1000U 按历史成交复利（理想成交、无资金费、无排队）· {best}', color=INK, loc='left')
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    fig.tight_layout(); fig.savefig(EXP/'5_equity.png', dpi=130); plt.close(fig)


def run():
    for p in ('yoyo/evaluation/monday_limit_reclaim_review.py',):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(4) as ex:
        d = pd.concat(list(ex.map(load, syms, chunksize=8)), ignore_index=True)
    alt = d[d.asset_group == 'ALTCOINS']
    grid = {(p, tg): cell(x) for (p, tg), x in alt.groupby(['reclaim_minutes', 'exit_mode'])}
    x = alt[(alt.reclaim_minutes == 60) & (alt.exit_mode == 'near_monday_edge')]
    best = '山寨币 · 60分钟确认 · 止盈近侧周一边界'
    sig = x[(x.population == 'signal') & x.closed].copy()
    v = np.sort(sig.net_bp.to_numpy())[::-1]; cum = np.cumsum(v)/v.sum()*100
    conc = np.c_[np.arange(1, len(v)+1)/len(v)*100, cum]
    conc = conc[np.unique(np.r_[np.linspace(0, len(v)-1, 400).astype(int), np.arange(min(len(v), 2000))])]
    labels = {4: '止盈成交', 5: '收回后保本出场', 11: '没收回、确认收盘止损'}
    outcomes = {labels[r]: dict(share_pct=float(100*(sig.reason == r).mean()), mean_bp=float(sig.net_bp[sig.reason == r].mean()))
                for r in (4, 5, 11)}
    sig['year'] = pd.to_datetime(sig.entry_time, utc=True).dt.year
    grp = x.trade_id.str.rsplit(':', n=1).str[0]
    ctl = x[x.population == 'random'].groupby(grp[x.population == 'random']).agg(n=('closed', 'sum'), m=('net_bp', 'mean'))
    j = ctl.reindex(grp[sig.index]); sig['full'] = (j.n == 3).to_numpy(); sig['cm'] = j.m.to_numpy()
    years = {int(y): dict(n=len(g), all_bp=float(g.net_bp.mean()), complete_signal_bp=float(g.net_bp[g.full].mean()),
                          complete_control_bp=float(g.cm[g.full].mean())) for y, g in sig.groupby('year')}
    sides = {('long' if s == 1 else 'short'): float(g.net_bp.mean()) for s, g in sig.groupby('side')}
    tr = sig.assign(entry=pd.to_datetime(sig.entry_time, utc=True).astype('int64'),
                    exit=pd.to_datetime(sig.exit_time, utc=True).astype('int64'))
    start24 = pd.Timestamp('2024-01-01', tz='UTC').value
    runs, paths = {}, []
    for name, kw in (('k10', dict(k=.10)), ('k10_slip10', dict(k=.10, slip_bp=10)), ('k10_cap20000', dict(k=.10, cap=2e4)),
                     ('k02', dict(k=.02)), ('k05', dict(k=.05)), ('k25', dict(k=.25)), ('k50', dict(k=.50)),
                     ('k10_slip20', dict(k=.10, slip_bp=20)), ('k10_from2024', dict(k=.10, start=start24)),
                     ('k10_slip10_cap20000', dict(k=.10, slip_bp=10, cap=2e4))):
        runs[name], path = replay(tr, **kw)
        if name in ('k10', 'k10_slip10', 'k10_cap20000', 'k02'):
            paths.append(path[::max(1, len(path)//3000)])
    EXP.mkdir(exist_ok=True)
    charts(grid, best, conc, years, paths, outcomes)
    dump(EXP/'summary.json', dict(config=cfg, snapshot_symbols=len(syms),
        grid={f'{p}m|{tg}': r for (p, tg), r in grid.items()}, best_cell=best, outcomes=outcomes, years=years, sides=sides,
        equity_replays=runs, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(dict(runs=runs, outcomes=outcomes, sides=sides), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()

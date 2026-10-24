"""1000 USDT equity replay of the filtered Codex Monday1618 limit-reclaim trades (owner2026-10-25).

Owner: "用这些条件过滤后，做一次 1000U 的复利模拟。去做吧". Rule: alts, 60m
confirmation, near Monday edge target, snapshot output/monday-partial-snapshot-20261024-2224.
Filters from the pattern rounds (entry-time information only):
  vol_top: Codex prior-14-bar TR/close bucket at or above the pre-2025 80th
    percentile (a fixed absolute threshold, not a full-history quantile);
  vol_spike: last-hour quote volume >= 8x the prior 7-day median;
  fast_long: longs reaching the1618 within 30 minutes of crossing the Monday low
    (fast shorts excluded: their same-bar targets are inflated by the O-H-L-C path);
  opp_first: the opposite1618 already filled this week;
  any_strong: union of the four; vol_top_opp: vol_top and opp_first.
Replay of recorded fills in event time from 1000 USDT: notional = min(k x
realized equity, gross cap x equity - open notional, per-trade cap); extra
slippage on non-target exits. Arithmetic on framework fills, not a new
backtest; no funding, queue misses, unrealized-loss margin or liquidity model.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SNAP, SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-filtered-replay-20261025-v1'
FEAT = ROOT/'experiments/active/exp-monday-limit-reclaim-patterns3-20261024-v1/events_60m.csv.gz'
COLS = ['symbol', 'asset_group', 'week', 'side', 'population', 'reclaim_minutes', 'exit_mode', 'net_bp', 'reason',
        'closed', 'entry_time', 'exit_time', 'bucket']
NAMES = {'all': '全部（不过滤）', 'vol_top': '触及前波动最高', 'vol_spike': '触及前放量≥8倍', 'fast_long': '急速插针只做多',
         'opp_first': '另一侧1.618已先触及', 'any_strong': '任一强条件', 'vol_top_opp': '波动最高+另一侧先触及'}


def load(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    return t[(t.asset_group == 'ALTCOINS') & (t.population == 'signal') & (t.reclaim_minutes == 60)
             & (t.exit_mode == 'near_monday_edge') & t.closed]


def replay(t, k, gross, slip_bp=0., cap=None):
    net = t.net_bp.to_numpy()/1e4-np.where(t.reason.to_numpy() == 4, 0., slip_bp/1e4)
    ev = np.concatenate([np.c_[t.exit.to_numpy(), np.zeros(len(t)), np.arange(len(t))],
                         np.c_[t.entry.to_numpy(), np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]  # exits before entries at the same time
    eq, peak, dd, opened, notional, taken = 1000., 1000., 0., 0., np.zeros(len(t)), 0
    path = []
    for ts, is_entry, j in ev:
        j = int(j)
        if is_entry:
            n = max(0., min(k*eq, gross*eq-opened, cap if cap else np.inf))
            notional[j] = n; opened += n; taken += n > 0
        else:
            eq += notional[j]*net[j]; opened -= notional[j]
            peak = max(peak, eq); dd = max(dd, 1-eq/peak); path.append((ts, eq))
            if eq <= 0:
                return dict(final_multiple=0., max_drawdown_pct=100., trades_taken=taken, ruined=True), path
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, trades_taken=taken, ruined=False), path


def yearly(path):
    p = pd.Series([e for _, e in path], index=pd.to_datetime([t for t, _ in path], utc=True))
    ends = p.groupby(p.index.year).last(); start = pd.concat([pd.Series([1000.]), ends.iloc[:-1]]).to_numpy()
    return {int(y): float(e/s) for y, e, s in zip(ends.index, ends.to_numpy(), start)}


def charts(paths, table):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    cols = {'all': MUTED, 'vol_top': BLUE, 'vol_spike': ORANGE, 'fast_long': '#1baf7a', 'any_strong': '#6250d6'}
    fig, ax = plt.subplots(figsize=(12, 5.6))
    for f, path in paths.items():
        if f not in cols:
            continue
        p = np.array(path[::max(1, len(path)//3000)])
        ax.plot(pd.to_datetime(p[:, 0]), p[:, 1], color=cols[f], lw=2, label=NAMES[f])
    ax.set_yscale('log'); ax.axhline(1000, color='#c3c2b7', lw=1)
    right = ax.get_xlim()[1]
    for mult in (10, 100, 1000):
        ax.axhline(1000*mult, color=GRID, lw=.8, ls='--'); ax.text(right, 1000*mult, f'{mult}倍 ', color=MUTED, va='bottom', ha='right')
    ax.set_ylabel('已实现权益（USDT，对数）'); ax.legend(frameon=False, loc='upper left', ncols=2)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title('1000U 复利 · 每笔 10% 权益 · 总仓位 ≤ 3 倍 · 平仓滑点 10bp · 60分钟确认 · 止盈近侧周一边界', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'1_equity_filters.png', dpi=130); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.8))
    fs = list(NAMES); ks = sorted({r['k'] for r in table})
    for ax, slip in zip(axes, (10, 30)):
        m = np.array([[next(r['final_multiple'] for r in table if r['filter'] == f and r['k'] == k and r['gross'] == 3 and r['slip_bp'] == slip)
                       for k in ks] for f in fs])
        ax.imshow(np.log10(np.clip(m, 1e-3, None)), cmap='RdBu', vmin=-2, vmax=2, aspect='auto')
        for (i, j), v in np.ndenumerate(m):
            ax.text(j, i, f'{v:.2f}倍' if v < 10 else f'{v:.0f}倍', ha='center', va='center', color=INK, fontsize=9)
        ax.set_xticks(range(len(ks)), [f'每笔{int(k*100)}%' for k in ks]); ax.set_yticks(range(len(fs)), [NAMES[f] for f in fs])
        ax.set_title(f'总仓位≤3倍 · 平仓滑点 {slip}bp · 2020–2026 最终倍数', color=INK, loc='left')
        for sp in ax.spines.values():
            sp.set_visible(False)
    fig.tight_layout(); fig.savefig(EXP/'2_multiple_grid.png', dpi=130); plt.close(fig)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_filtered_replay.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat(list(ex.map(load, syms, chunksize=8)), ignore_index=True)
    f = pd.read_csv(FEAT).rename(columns={'side_n': 'side'})[['symbol', 'week', 'side', 'approach_min', 'vol_ratio', 'opp_first']]
    t = t.merge(f, on=['symbol', 'week', 'side'], how='left')
    t['entry'] = pd.to_datetime(t.entry_time, utc=True).astype('int64'); t['exit'] = pd.to_datetime(t.exit_time, utc=True).astype('int64')
    disc = pd.to_datetime(t.entry_time, utc=True) < SPLIT
    thr = float(np.quantile(t.bucket[disc], .8))
    masks = {'all': t.net_bp.notna(), 'vol_top': t.bucket >= thr, 'vol_spike': t.vol_ratio >= 8,
             'fast_long': (t.approach_min < 30) & (t.side == 1), 'opp_first': t.opp_first == '是'}
    masks['any_strong'] = masks['vol_top'] | masks['vol_spike'] | masks['fast_long'] | masks['opp_first']
    masks['vol_top_opp'] = masks['vol_top'] & masks['opp_first']
    table, paths, years, sets = [], {}, {}, {}
    for name, m in masks.items():
        x = t[m.fillna(False).to_numpy()].reset_index(drop=True)
        sets[name] = dict(n=len(x), win_pct=float(100*(x.net_bp > 0).mean()), mean_bp=float(x.net_bp.mean()),
                          median_bp=float(x.net_bp.median()), first=str(pd.to_datetime(x.entry_time, utc=True).min()))
        for k in (.05, .10, .20, .30):
            for gross in (1, 3):
                for slip in (0, 10, 30):
                    r, path = replay(x, k, gross, slip)
                    table.append(dict(filter=name, k=k, gross=gross, slip_bp=slip, **r))
                    if k == .10 and gross == 3 and slip == 10:
                        paths[name] = path; years[name] = yearly(path)
        r, _ = replay(x, .10, 3, 10, cap=2e4)
        table.append(dict(filter=name, k=.10, gross=3, slip_bp=10, cap=2e4, **r))
    EXP.mkdir(exist_ok=True)
    charts(paths, table)
    dump(EXP/'summary.json', dict(config=cfg, vol_bucket_threshold=thr, vol_tr_close_pct=float(100*2**(thr/4)), sets=sets,
        replays=table, yearly_k10_gross3_slip10=years,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(dict(sets=sets, years=years), ensure_ascii=False)[:3000])


if __name__ == '__main__':
    run()

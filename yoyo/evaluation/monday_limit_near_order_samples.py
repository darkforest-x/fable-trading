"""Near-target, score >= 0.6, EMA order score > 0 subset: statistics and sampled charts (owner 2026-10-26).

Owner asked what "分数 ≥0.6 + 排列分 > 0 + 止盈挂近侧 89.5% / 77.5%" is, whether it was backtested, how many
trades, and for sampled charts with explanations. The rows are existing NautilusTrader fills, not a new backtest:
v2_be17 of exp-monday-limit-be-scalein-20261026-v1 (limit at the Monday 1618, 60m confirmation, breakeven at entry
+0.17 percent, near Monday edge target, no backstop), worse intrabar path per trade with 10bp slippage on non-target
exits as saved in exp-monday-limit-time-news-20261026-v1, filtered to score >= 0.6 and l2_order_score > 0 (causal
feature table exp-monday-limit-score-features-v2-20261025-v1). The filter was found on the same history.
Statistics: counts, outcome mix, net quantiles, holding time, side and year; added after the first run showed a
median holding time of zero: trades that fill and exit inside the same 5m bar (flash wicks) versus the rest. Charts: eight trades drawn with a fixed
seed (three targets, two breakevens, two failed reclaims, plus the worst trade), 15m candles from the Codex 5m
inputs (load_job) with EMA 8/13/21/34/55 as in yoyo.data.indicators (ewm span, adjust=False), the 1618 entry, the
Monday edges, the touch and the 60m confirmation candle; the order score is recomputed from those EMAs at the last
15m bar closed before the touch and checked against the feature table. Read-only.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_be_scalein_compare import RUN, B
from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, MUTED, SURF
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-near-order-samples-20261026-v1'
SPANS = (8, 13, 21, 34, 55)
EMA_COLORS = ('#c6dbef', '#9ecae1', '#6baed6', '#3182bd', '#08519c')
CAUSE = {4: '止盈', 5: '保本出场', 11: '没收回止损', 12: '收回不足保本价'}


def subset(cfg):
    t = pd.read_csv(ROOT/cfg['trades'], parse_dates=['t', 'exit_t'])
    t = t[t.variant == cfg['variant']]
    f = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    t = t.merge(f, on='event', how='left')
    return t[(t.score >= cfg['min_score']) & (t.order > 0)].reset_index(drop=True)


def stats(x):
    hold = (x.exit_t-x.t).dt.total_seconds()/3600
    return dict(n=int(len(x)), win_pct=float(100*x.win.mean()), flat_pct=float(100*x.flat.mean()),
                loss_pct=float(100*(1-x.win-x.flat).mean()), mean_bp=float(x.net.mean()), median_bp=float(x.net.median()),
                causes={CAUSE.get(int(k), str(k)): int(v) for k, v in x.reason.value_counts().items()},
                win_mean_bp=float(x.net[x.win == 1].mean()) if x.win.any() else None,
                loss_mean_bp=float(x.net[(x.win == 0) & (x.flat == 0)].mean()) if ((x.win == 0) & (x.flat == 0)).any() else None,
                worst_bp=float(x.net.min()), hold_median_h=float(hold.median()), hold_p90_h=float(hold.quantile(.9)))


def detail(trade_id):
    """Entry, target and the worse path's exit for one trade."""
    symbol = trade_id.split(':')[0]
    rows = {}
    for path in ('ohlc', 'olhc'):
        x = pd.read_csv(RUN/path/'symbols'/symbol/'trades.csv.gz')
        rows[path] = x[x.trade_id == trade_id].iloc[0]
    net = {k: r.net_notional_bp-(0. if r.reason == 4 else opt.SLIP) for k, r in rows.items()}
    r = rows['olhc'] if net['olhc'] < net['ohlc'] else rows['ohlc']
    return symbol, r


def draw(ax, row, r, symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    m15 = f.resample('15min').agg(dict(open='first', high='max', low='min', close='last')).dropna()
    for s in SPANS:
        m15[f'ema{s}'] = m15.close.ewm(span=s, adjust=False).mean()
    side, entry, target = int(row.side), float(r.entry), float(r.target_price)
    W = abs(target-entry)/.618
    hi, lo = (target+W, target) if side == 1 else (target, target-W)
    touch = pd.Timestamp(r.entry_time)-pd.Timedelta(minutes=5)  # fill bar open
    exit_t = pd.Timestamp(r.exit_time)
    end = min(max(exit_t, touch+pd.Timedelta(hours=3))+pd.Timedelta(hours=3), touch+pd.Timedelta(days=3))
    w = m15[(m15.index >= touch-pd.Timedelta(hours=12)) & (m15.index <= end)]
    x = np.arange(len(w)); up = (w.close >= w.open).to_numpy()
    for col, msk in (('#2e9e6b', up), ('#d9534f', ~up)):
        ax.vlines(x[msk], w.low.to_numpy()[msk], w.high.to_numpy()[msk], color=col, lw=.7)
        ax.bar(x[msk], np.maximum(np.abs(w.close-w.open).to_numpy()[msk], w.close.to_numpy()[msk]*1e-5),
               bottom=np.minimum(w.open, w.close).to_numpy()[msk], color=col, width=.7)
    for s, c in zip(SPANS, EMA_COLORS):
        ax.plot(x, w[f'ema{s}'].to_numpy(), color=c, lw=1.1, label=f'EMA{s}')
    xi = lambda ts: float(np.clip(w.index.searchsorted(ts, side='right')-1, 0, len(w)-1))
    prev = m15.index[m15.index.searchsorted(touch-pd.Timedelta(minutes=15), side='right')-1]
    e = m15.loc[prev, [f'ema{s}' for s in SPANS]].to_numpy()
    order = int(sum((e[i] >= e[i+1]) if side == 1 else (e[i] <= e[i+1]) for i in range(4)))
    c0 = touch.floor('60min')+pd.Timedelta(minutes=60)
    ax.axvspan(xi(c0)-.5, xi(c0+pd.Timedelta(minutes=60))+.5, color='#8888ff', alpha=.10)
    ax.axvline(xi(prev)+.5, color=MUTED, lw=.8, ls=':')
    for y, col, ls, lab in ((entry, '#e69500', '--', '1.618 挂单'), (target, '#1D9E75', '-', '止盈：近侧周一边界'),
                            (hi if side == 1 else lo, '#9b9b9b', ':', '远侧周一边界')):
        ax.axhline(y, color=col, ls=ls, lw=1.1)
        ax.text(len(w)-.5, y, f' {lab}', color=col, fontsize=8, va='center', ha='left', clip_on=False)
    ax.plot(xi(touch), entry, marker='^' if side == 1 else 'v', color='black', ms=10, zorder=5)
    if exit_t <= w.index[-1]+pd.Timedelta(minutes=15):
        col = '#1D9E75' if row.win else ('#9b9b9b' if row.flat else '#d9534f')
        ax.plot(xi(exit_t-pd.Timedelta(minutes=1)), float(r.exit_price), marker='X', color=col, ms=11, zorder=5)
    bj = touch.tz_convert('Asia/Shanghai')
    hold = (exit_t-touch).total_seconds()/3600
    ax.set_title(f"{CAUSE.get(int(row.reason), str(row.reason))} · {symbol.removesuffix('USDT')} {'做多' if side == 1 else '做空'} · "
                 f"{bj:%Y-%m-%d %H:%M}（北京）\n分数 {row.score:.2f} · 排列分 {int(row.order)}（复算 {order}）· "
                 f"净 {row.net/100:+.2f}% · 持仓 {hold:.1f} 小时", fontsize=10, color=INK, loc='left')
    ticks = np.linspace(0, len(w)-1, 5).astype(int)
    ax.set_xticks(ticks, [w.index[i].tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M') for i in ticks], fontsize=8)
    ax.tick_params(axis='y', labelsize=8); ax.grid(alpha=.15)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    return order


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_near_order_samples.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.facecolor': SURF,
                         'figure.facecolor': SURF})
    t = subset(cfg)
    early = t.t < B[0]
    t['year'] = t.t.dt.year
    res = dict(all=stats(t), early=stats(t[early]), late=stats(t[~early]),
               side={('做多' if s == 1 else '做空'): stats(t[t.side == s]) for s in (1, -1)},
               year={str(y): stats(g) for y, g in t.groupby('year')},
               order={str(int(o)): stats(g) for o, g in t.groupby('order')},
               symbols=int(t.event.str.split(':').str[0].nunique()), weeks=int(t.week.nunique()))
    same = t.exit_t == t.t
    res['same_bar'] = dict(all=dict(yes=stats(t[same]), no=stats(t[~same])), early=dict(yes=stats(t[same & early]), no=stats(t[~same & early])),
                           late=dict(yes=stats(t[same & ~early]), no=stats(t[~same & ~early])))
    rng = np.random.default_rng(cfg['seed'])
    picks = []
    for reason, k in ((4, 3), (5, 2), (11, 2)):
        pool = t[t.reason == reason]
        picks += [r for r in pool.iloc[np.sort(rng.choice(len(pool), k, replace=False))].itertuples()]
    picks.append(next(t.loc[[t.net.idxmin()]].itertuples()))
    fig, axes = plt.subplots(4, 2, figsize=(20, 22))
    samples = []
    for ax, row in zip(axes.flat, picks):
        symbol, r = detail(row.trade_id)
        order = draw(ax, row, r, symbol)
        samples.append(dict(trade_id=row.trade_id, reason=int(row.reason), net_bp=float(row.net), score=float(row.score),
                            order_feature=int(row.order), order_recomputed=order, entry=float(r.entry), target=float(r.target_price),
                            exit_price=float(r.exit_price), entry_time=str(r.entry_time), exit_time=str(r.exit_time)))
    axes.flat[0].legend(loc='upper left', fontsize=8, ncol=5, frameon=False)
    fig.suptitle('分数≥0.6 + 均线排列分>0 + 止盈挂近侧：抽样（15分钟K线；三角=成交，X=出场，蓝底=确认用的60分钟K线，点线=计算排列分的最后一根K线）',
                 color=INK, x=.01, ha='left', fontsize=13)
    fig.tight_layout(rect=(0, 0, .95, .975)); fig.savefig(exp/'samples.png', dpi=80); plt.close(fig)
    res['samples'] = samples
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps({k: res[k] for k in ('all', 'early', 'late', 'symbols', 'weeks', 'same_bar')}, ensure_ascii=False))
    for s in samples:
        print(s['trade_id'], s['reason'], round(s['net_bp']), s['order_feature'], s['order_recomputed'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)

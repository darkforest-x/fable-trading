"""Trade frequency over time for the spike-HW v1 backtest (owner 2026-10-27).

Owner: "spike的回测数据中，画时间序列图，看看开单频率如何". Read-only on trades.csv of
exp-monday-limit-spike-hw-20261026-v1 (219 worse-path framework trades, before slot skipping). Dates are Beijing
calendar days of the fill. Outputs 4_frequency.png and frequency.json next to the trades: monthly counts split
into winners and the rest, a daily stem plot of fills per day (cluster days labelled), cumulative trades, and
counts per year, per active week and per day, gaps between trading days, share on the busiest days, and how many
fills a 10-slot account could take on the busiest days. Added: the expected forward pace since 2025 on the full
Binance universe and on symbols with a current OKX USDT swap (okx_swaps.json saved by
exp-monday-limit-okx-wick-check-20261026-v1), i.e. how long 50 paper trades should take.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, INK2, MUTED, GRID, SURF

EXP = ROOT/'experiments/active/exp-monday-limit-spike-hw-20261026-v1'
WIN, REST = '#2a78d6', '#e34948'


def run():
    p = 'yoyo/evaluation/monday_limit_spike_hw_frequency.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    t = pd.read_csv(EXP/'trades.csv', parse_dates=['entry_t'])
    t['win'] = t.net > 1
    bj = t.entry_t.dt.tz_convert('Asia/Shanghai')
    t['day'] = bj.dt.tz_localize(None).dt.normalize()
    t['month'] = bj.dt.tz_localize(None).dt.to_period('M')
    t['week'] = t.event.str.split(':').str[1]
    days = t.groupby('day').agg(n=('win', 'size'), wins=('win', 'sum')).sort_index()
    gaps = days.index.to_series().diff().dt.days.dropna()
    since = t[t.entry_t >= pd.Timestamp('2023-01-01', tz='UTC')]
    all_weeks = pd.date_range('2023-01-02', t.entry_t.max().tz_localize(None), freq='W-MON')
    top = days.n.nlargest(8)
    res = dict(trades=int(len(t)), trading_days=int(len(days)), first=str(t.day.min().date()), last=str(t.day.max().date()),
               per_year={str(y): int(n) for y, n in t.groupby(bj.dt.year).size().items()},
               since_2023=dict(trades=int(len(since)), weeks=len(all_weeks), weeks_with_trades=int(since.week.nunique()),
                               share_weeks_with_trades=float(100*since.week.nunique()/len(all_weeks)),
                               trades_per_calendar_week=float(len(since)/len(all_weeks)),
                               trades_per_active_week=float(len(since)/since.week.nunique())),
               per_trading_day=dict(median=float(days.n.median()), mean=float(days.n.mean()), max=int(days.n.max()),
                                    one_trade_days_pct=float(100*(days.n == 1).mean())),
               gap_days_between_trading_days=dict(median=float(gaps.median()), p75=float(gaps.quantile(.75)), p90=float(gaps.quantile(.9)), max=int(gaps.max())),
               busiest_days={str(d.date()): dict(n=int(n), wins=int(days.wins[d]), taken_by_10_slots=int(min(n, 10))) for d, n in top.items()},
               share_on_top5_days=float(100*top.head(5).sum()/len(t)),
               months_with_trades=int(t.month.nunique()))
    listed = set(json.loads((ROOT/'experiments/active/exp-monday-limit-okx-wick-check-20261026-v1/inputs/okx_swaps.json').read_text()))
    recent = t[t.entry_t >= pd.Timestamp('2025-01-01', tz='UTC')]
    weeks = (recent.entry_t.max()-pd.Timestamp('2025-01-01', tz='UTC')).days/7
    on_okx = recent.symbol.map(lambda s_: f"{s_.removesuffix('USDT')}-USDT-SWAP" in listed)
    res['forward_pace_since_2025'] = dict(weeks=float(weeks), trades=int(len(recent)), per_week=float(len(recent)/weeks),
                                          okx_listed_trades=int(on_okx.sum()), okx_per_week=float(on_okx.sum()/weeks),
                                          weeks_to_50_binance=float(50/(len(recent)/weeks)), weeks_to_50_okx=float(50/max(on_okx.sum()/weeks, 1e-9)))
    dump(EXP/'frequency.json', res)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharex=True, gridspec_kw=dict(height_ratios=[1.1, 1.2, .8]))
    def style(ax, title):
        ax.set_title(title, color=INK, loc='left', fontsize=11); ax.grid(axis='y', color=GRID, lw=.6); ax.set_axisbelow(True)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    m = t.groupby('month').agg(wins=('win', 'sum'), n=('win', 'size'))
    months = pd.period_range(t.month.min(), t.month.max(), freq='M')
    m = m.reindex(months, fill_value=0)
    x = months.to_timestamp()+pd.Timedelta(days=14)
    axes[0].bar(x, m.wins, width=24, color=WIN, label='赢单')
    axes[0].bar(x, m.n-m.wins, width=24, bottom=m.wins, color=REST, label='非赢单')
    axes[0].legend(frameon=False, loc='upper left')
    style(axes[0], f'每月开单数（{len(t)} 笔，按北京时间成交日；回测成交，未扣除满仓跳过）')
    axes[1].vlines(days.index, 0, days.n, color=INK2, lw=1.2)
    axes[1].scatter(days.index, days.n, s=12, color=INK2, zorder=3)
    for d, n in top.head(5).items():
        axes[1].annotate(f'{d:%Y-%m-%d}：{n} 笔', (d, n), xytext=(6, 2), textcoords='offset points', fontsize=9, color=INK)
    axes[1].axhline(10, color=MUTED, ls='--', lw=1)
    axes[1].text(days.index.min(), 10.4, '10 份仓位上限', color=MUTED, fontsize=9)
    style(axes[1], f'每天开单数（{len(days)} 个有单的日子；前 5 天占全部单子的 {res["share_on_top5_days"]:.0f}%）')
    c = np.arange(1, len(t)+1)
    axes[2].step(t.sort_values('entry_t').entry_t.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None), c, where='post', color=WIN, lw=1.8)
    style(axes[2], '累计开单数')
    axes[2].xaxis.set_major_locator(mdates.YearLocator()); axes[2].xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    fig.tight_layout(); fig.savefig(EXP/'4_frequency.png', dpi=130); plt.close(fig)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()

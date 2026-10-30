"""Render three saved repeated-touch examples at 5m and 15m resolution.

Owner 2026-10-30 requested three annotated K-line images. All episode IDs,
Monday levels, outcomes and the noon cutoff come from the frozen 859-event
study. Public cached 5m OHLC must aggregate exactly to that study's frozen
15m data. Five-minute marks refine event time, never redefine the 859 cohort.
No signals, fills, profits or live services are recalculated or modified.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd

from yoyo.evaluation.btc_1618_return_count import CACHE, END, EXP

OUT = EXP/'charts'
TZ = 'Asia/Shanghai'
FONT = '/System/Library/Fonts/PingFang.ttc'
BG, PANEL, INK, MUTED = '#0c111b', '#101925', '#eef2f7', '#a4b2c5'
GOLD, BLUE, GREEN, RED = '#f5bc5c', '#65b8f3', '#50d9ae', '#f38a92'
CASES = [
    dict(slug='01_upper_returned', title='上沿触达后，回到周一区间',
         start='2026-10-28 16:30:00+08:00',
         left='2026-10-28 10:00:00+08:00', right='2026-10-28 23:00:00+08:00'),
    dict(slug='02_lower_returned', title='下沿触达后，回到周一区间',
         start='2026-10-30 04:45:00+08:00',
         left='2026-10-30 02:00:00+08:00', right='2026-10-30 07:00:00+08:00'),
    dict(slug='03_lower_not_returned', title='下沿再次触达，当周没有回到区间',
         start='2026-10-26 08:45:00+08:00',
         left='2026-10-26 05:00:00+08:00', right='2026-10-26 12:00:00+08:00'),
]


def load():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT/'inputs_5m.csv.gz'
    if path.exists():
        raw = pd.read_csv(path)
    else:
        with sqlite3.connect(CACHE.as_uri()+'?mode=ro', uri=True) as db:
            rows = [json.loads(x[0]) for x in db.execute(
                'SELECT raw FROM candles WHERE key=? AND t<? ORDER BY t',
                ('https://www.okx.com|BTC-USDT-SWAP|5m', int(END.timestamp()*1000)))]
        raw = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close',
                                         'contracts', 'base', 'quote', 'confirm'])
        assert raw['confirm'].astype(str).eq('1').all()
        raw.to_csv(path, index=False)
    raw.index = pd.to_datetime(raw.ts.astype('int64'), unit='ms', utc=True)
    five = raw[['open', 'high', 'low', 'close']].astype(float)
    assert five.index.is_unique and (np.diff(raw.ts.astype('int64')) == 300_000).all()
    source = pd.read_csv(EXP/'inputs/recent_15m.csv.gz')
    source.index = pd.to_datetime(source.ts.astype('int64'), unit='ms', utc=True)
    fifteen = source[['open', 'high', 'low', 'close']].astype(float)
    aggregate = five.resample('15min').agg(dict(open='first', high='max', low='min', close='last'))
    aggregate = aggregate[five.close.resample('15min').count().eq(3)]
    common = aggregate.index.intersection(fifteen.index)
    assert np.allclose(aggregate.loc[common], fifteen.loc[common], rtol=0, atol=1e-8)
    return five, fifteen, pd.read_csv(EXP/'events.csv'), len(common)


def location(frame, stamp):
    return int(frame.index.searchsorted(stamp, side='right')-1)


def label(ax, x, y, message, color, dx, dy):
    ax.annotate(message, (x, y), xytext=(dx, dy), textcoords='offset points',
                fontsize=13.5, fontweight='bold', color=color, ha='left' if dx >= 0 else 'right',
                va='bottom' if dy >= 0 else 'top', zorder=12,
                bbox=dict(boxstyle='round,pad=.45', facecolor=BG, edgecolor=color, alpha=.97, lw=.8),
                arrowprops=dict(arrowstyle='->', color=color, lw=1.3, shrinkA=7, shrinkB=8))


def render(case, number, five, fifteen, events):
    row = events.loc[events.start.eq(case['start'])].iloc[0]
    left, right = pd.Timestamp(case['left']), pd.Timestamp(case['right'])
    t15 = pd.Timestamp(row.start)
    fine = five[(five.index >= t15) & (five.index < t15+pd.Timedelta(minutes=15))]
    t5 = fine[(fine.low <= row.level) & (fine.high >= row.level)].index[0]
    good = row.same_week == 'returned'
    week = pd.Timestamp(row.week).tz_localize('America/New_York')
    expiry = (pd.Timestamp(row.week)+pd.Timedelta(days=7)).tz_localize('America/New_York')
    mon = fifteen[(fifteen.index >= week) & (fifteen.index < week+pd.Timedelta(days=1))]
    assert len(mon) == 96
    high, low = float(mon.high.max()), float(mon.low.min())
    assert np.isclose(row.edge, high if row.side == 'upper' else low)
    post = five[(five.index > t5) & (five.index < min(expiry, END))]
    reached = post[post.low <= row.edge] if row.side == 'upper' else post[post.high >= row.edge]
    ret5 = reached.index[0] if len(reached) else None
    assert (ret5 is not None) == good
    if good:
        ret15 = pd.Timestamp(row.return_bar)
        assert ret5.floor('15min') == ret15
    else:
        ret15 = None
        assert right == expiry
    frames = [x[(x.index >= left) & (x.index < right)] for x in [five, fifteen]]
    for data, minutes in zip(frames, [5, 15]):
        assert len(data) == int((right-left).total_seconds()/60/minutes)
    lo = min(float(frames[0].low.min()), float(row.edge), float(row.level))
    hi = max(float(frames[0].high.max()), float(row.edge), float(row.level))
    span = hi-lo
    ymin, ymax = lo-.09*span, hi+.20*span
    fig, axes = plt.subplots(1, 2, figsize=(22, 10), sharey=True, facecolor=BG)
    fig.subplots_adjust(left=.055, right=.98, bottom=.22, top=.77, wspace=.09)
    weekday = row.week
    badge = '当周已返回' if good else '当周未返回'
    fig.text(.045, .94, f'{number:02d}  /  BTC · {case["title"]}', fontsize=25,
             fontweight='bold', color=INK)
    fig.text(.045, .89, f'OKX BTC-USDT 永续  |  纽约周一 {weekday}  |  周一区间 {low:,.1f} — {high:,.1f} USDT',
             fontsize=14.5, color=MUTED)
    fig.text(.975, .89, badge, ha='right', fontsize=16, color=GREEN if good else RED, fontweight='bold')
    detail = f'本次触达（5m）{t5.tz_convert(TZ):%m/%d %H:%M} → 返回 {ret5.tz_convert(TZ):%H:%M}' if good else \
             f'本次触达（5m）{t5.tz_convert(TZ):%m/%d %H:%M} → 截止 {expiry.tz_convert(TZ):%m/%d %H:%M} 未返回'
    fig.text(.045, .845, detail, fontsize=16, color=INK)

    for ax, data, minutes, hit, ret in zip(axes, frames, [5, 15], [t5, t15], [ret5, ret15]):
        ax.set_facecolor(PANEL)
        ax.set_title(f'{minutes} 分钟 K 线', loc='left', color=INK, fontsize=17, pad=16, fontweight='bold')
        ax.axhspan(low, high, color=BLUE, alpha=.10, zorder=0)
        for px, name, color, style in [(row.level, f'{"上" if row.side == "upper" else "下"}沿 1.618', GOLD, '--'),
                                        (row.edge, f'周一{"最高" if row.side == "upper" else "最低"}价 · 返回边界', BLUE, '-')]:
            ax.axhline(px, color=color, lw=1.3, ls=style, alpha=.95, zorder=3)
            ax.text(.015, px, f' {name}  {px:,.1f} ', transform=ax.get_yaxis_transform(),
                    fontsize=12.5, color=color, va='bottom', zorder=10,
                    bbox=dict(facecolor=PANEL, edgecolor='none', alpha=.94, pad=3))
        for j, (o, h, l, c) in enumerate(data.to_numpy()):
            color = '#76bfa9' if c >= o else '#c78087'
            ax.vlines(j, l, h, color=color, lw=.9, zorder=5)
            ax.add_patch(Rectangle((j-.32, min(o, c)), .64, max(abs(c-o), span*.0006),
                                    facecolor=color, edgecolor=color, lw=.3, zorder=6))
        j = location(data, hit)
        last = location(data, pd.Timestamp(row.episode_last_bar)+pd.Timedelta(minutes=15)-pd.Timedelta(minutes=minutes))
        ax.axvspan(j-.5, last+.5, color=GOLD, alpha=.09, zorder=1)
        ax.scatter(j, row.level, s=150, facecolors='none', edgecolors=GOLD, lw=2.2, zorder=11)
        label(ax, j, row.level, f'① 触达 {hit.tz_convert(TZ):%H:%M}', GOLD,
              -18 if good and row.side == 'lower' else 12,
              (-28 if good else -55) if row.side == 'lower' else 65)
        if good:
            rj = location(data, ret)
            ax.scatter(rj, row.edge, marker='D', s=90, color=GREEN, edgecolor=BG, lw=1, zorder=11)
            label(ax, rj, row.edge, f'② 返回 {ret.tz_convert(TZ):%H:%M}', GREEN,
                  20 if row.side == 'lower' else -15, 65 if row.side == 'lower' else -70)
        else:
            ax.axvline(len(data)-.5, color=INK, ls=':', lw=1.4)
            ax.text(.98, .98, '本周结束 12:00\n始终未到蓝色边界', transform=ax.transAxes,
                    ha='right', va='top', color=RED, fontsize=13.5, fontweight='bold',
                    bbox=dict(facecolor=BG, edgecolor=RED, boxstyle='round,pad=.5', lw=.8))
            others = events[(events.week == row.week) & (events.side == row.side)]
            for other in others.itertuples():
                stamp = pd.Timestamp(other.start)
                if left <= stamp < t15:
                    if minutes == 5:
                        window = five[(five.index >= stamp) & (five.index < stamp+pd.Timedelta(minutes=15))]
                        stamp = window[(window.low <= other.level) & (window.high >= other.level)].index[0]
                    oj = location(data, stamp)
                    ax.scatter(oj, other.level, s=95, facecolors='none', edgecolors=MUTED, lw=1.6, zorder=10)
                    label(ax, oj, other.level, '前一次触达', MUTED, -15, 62)
        ax.set_ylim(ymin, ymax)
        ax.set_xlim(-1, len(data)+1)
        tick_hours = 2 if right-left > pd.Timedelta(hours=9) else 1
        tick_times = pd.date_range(left, right, freq=f'{tick_hours}h', inclusive='left')
        ticks = [location(data, stamp) for stamp in tick_times]
        ax.set_xticks(ticks, tick_times.strftime('%H:%M'), fontsize=12.5)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:,.0f}'))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        ax.tick_params(colors=MUTED, axis='both', labelsize=12.5, length=0, pad=9)
        ax.grid(axis='y', color='#65778f', alpha=.14, lw=.7)
        for spine in ax.spines.values():
            spine.set_color('#334052')
        ax.set_xlabel(f'{left:%Y/%m/%d} · 北京时间', fontsize=13, color=MUTED, labelpad=12)
    fig.text(.045, .125, '金色虚线：1.618    蓝色实线：返回边界    蓝色阴影：周一区间    圆圈：触达    菱形：返回    浅金色带：同一15m触达段',
             color=INK, fontsize=13.5)
    fig.text(.045, .083, '同一案例、相同时间窗与价格轴；15m 用于原859次计数，5m 仅细化触价/返回时刻。时间均为K线起始时间。',
             color=MUTED, fontsize=12.5)
    fig.text(.045, .045, '返回只计纽约下周一前；示例为人工挑选，含已返回与未返回案例，不代表全部样本的比例。',
             color=MUTED, fontsize=12.5)
    output = OUT/(case['slug']+'.png')
    fig.savefig(output, dpi=160, facecolor=BG)
    plt.close(fig)
    return dict(**case, image=str(output.relative_to(EXP)), week=row.week, side=row.side,
                level=float(row.level), monday_high=high, monday_low=low,
                touch_5m=str(t5), return_5m=str(ret5) if ret5 is not None else None,
                return_15m=str(ret15) if ret15 is not None else None, outcome=row.same_week,
                bars_5m=len(frames[0]), bars_15m=len(frames[1]))


def run():
    # The font bundled on this machine renders Chinese labels in PNG output.
    font_path = Path(FONT)
    if not font_path.exists():
        font_path = Path('/Library/Fonts/Arial Unicode.ttf')
    prop = FontProperties(fname=str(font_path))
    plt.rcParams.update({'font.family': prop.get_name(), 'axes.unicode_minus': False})
    five, fifteen, events, common = load()
    cases = [render(case, i+1, five, fifteen, events) for i, case in enumerate(CASES)]
    receipt = dict(cases=cases, aggregated_5m_matches_saved_15m_bars=common,
                   selection='Illustrative upper return, lower return, lower nonreturn; not random or representative.',
                   source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                   source_15m='inputs/recent_15m.csv.gz', source_5m='charts/inputs_5m.csv.gz')
    (OUT/'manifest.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    run()

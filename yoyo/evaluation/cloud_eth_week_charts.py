"""Plot the most recent 20 distinct NY signal weeks from frozen ETH V1.9 fills.

Each sheet uses hourly OHLC for the entry detail and resamples the same bars
for the full trade path. The developing cloud stripe uses the previous closed
12H EMA pair and each completed H1 close; the fixed stop is the one in the
ledger. All fill prices and exits are read from the original simulation.
"""
from pathlib import Path
from zipfile import ZipFile

import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, StrMethodFormatter
import numpy as np
import pandas as pd

from yoyo.monitor.monday_fib import week_window


ROOT = Path(__file__).resolve().parents[2]
EXPS = ROOT / 'experiments/active'
STUDY = EXPS / 'exp-cloud-eth-hourly-early-execution-20261115-v1'
SOURCE = EXPS / 'exp-cloud-top20-peak10r-20261108-v1/inputs/ETH'
OUT = ROOT / 'output/pdf/eth-v19-20-weeks'
H, KEY = 3_600_000, 12 * 3_600_000
NAVY, AMBER, INK, MUTED = '#245ca2', '#b7600d', '#132638', '#53697a'
UP, DOWN, RAIL, STOP = '#168577', '#cb5a62', '#7e60a4', '#ae566c'
ARMS = {'early': ('1H 提前', AMBER), 'confirmed': ('12H 确认', NAVY)}
REASONS = dict(cloud_flip='云带反向', fixed_stop='固定止损', scheduled_fail='12H 未确认', data_end='期末结算')


def bjt(ms, fmt='%m/%d %H:%M'):
    return pd.to_datetime(ms, unit='ms', utc=True).tz_convert('Asia/Shanghai').strftime(fmt)


def exit_time(row):
    return int(row.exit_hour_ms) + {'open': 0, 'intrabar': H // 2, 'close': H}[row.exit_phase]


def candles(ax, bars, hours):
    # Center each OHLC glyph in its actual aggregation interval.
    x = bars.index.to_numpy() / H + hours / 2
    w = hours * .32
    colors = np.where(bars.close >= bars.open, UP, DOWN)
    ax.vlines(x, bars.low, bars.high, colors=colors, lw=.9, zorder=2)
    bodies = np.stack([np.c_[x-w, bars.open], np.c_[x+w, bars.open],
                       np.c_[x+w, bars.close], np.c_[x-w, bars.close]], axis=1)
    ax.add_collection(PolyCollection(bodies, facecolors=colors, edgecolors=colors, linewidths=.65, zorder=3))


def panel(ax, hourly, bounds, period, group, calendar, cloud=None):
    first, last = bounds
    bars = hourly.loc[first:last-1]
    if period != 1:
        bars = bars.groupby(bars.index // (period*H) * (period*H)).agg(
            dict(open='first', high='max', low='min', close='last'))
    ax.set_facecolor('white')
    candles(ax, bars, period)
    for k in group.key_ms.unique():
        x1, x2 = max(k, first), min(k+KEY, last)
        if x2 > x1:
            ax.axvspan(x1/H, x2/H, facecolor='#e8edf4', alpha=.85, zorder=0)
            if x2 == k + KEY:
                ax.axvline(x2/H, color='#aebac8', lw=.8, ls=':', zorder=1)
    prices = [bars.low.min(), bars.high.max()]
    for raw_stop, rows in group.groupby('stop'):
        side = rows.side.iloc[0]
        rail = calendar['lower'] if side == 1 else calendar['upper']
        assert np.isclose(rail * (1 - .015 * side), raw_stop)
        for level, start, end, color, dash in (
            (rail, calendar['end'], calendar['expires'], RAIL, (0,(6,3))),
            (raw_stop, rows.entry_ms.min(), max(exit_time(r) for r in rows.itertuples()), STOP, (0,(2,2)))):
            left, right = max(first, start), min(last, end)
            if right > left:
                ax.hlines(level, left/H, right/H, color=color, linewidth=1, ls=dash, zorder=3)
                prices.append(level)
    for r in group.sort_values('cohort').itertuples():
        confirmed = r.cohort == 'confirmed'
        color = ARMS[r.cohort][1]
        if first <= r.entry_ms <= last:
            ax.scatter(r.entry_ms/H, r.entry_px, s=81 if confirmed else 50, marker='D' if confirmed else 'o',
                       facecolors='white', edgecolors=color, linewidths=2.1, zorder=6)
        at = exit_time(r)
        if first <= at <= last:
            ax.scatter(at/H, r.exit_px, s=126 if confirmed else 51, marker='x',
                       color=color, linewidths=2.4 if confirmed else 1.8, zorder=7)
    lo, hi = min(prices), max(prices)
    pad = max(hi-lo, hi*.006)*.09
    ax.set_xlim(first/H-.2*period, last/H+.4*period)
    ax.set_ylim(lo-pad, hi+pad)
    ax.spines[:].set_visible(False)
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ticks = np.linspace(first/H, last/H, 5).round().astype(int)
    ax.set_xticks(ticks, [bjt(t*H, '%m/%d\n%H:%M' if last-first < 6*24*H else '%m/%d') for t in ticks])
    ax.tick_params(length=0, pad=5, colors=MUTED, labelsize=9)
    ax.grid(axis='y', color='#dfe5ea', linewidth=.6)
    ax.set_axisbelow(True)
    if cloud is not None:
        # One small colored segment belongs to the hour that has just closed.
        states = cloud.loc[first:last-1]
        ax.hlines(np.full(len(states), .012), states.index/H, (states.index+H)/H,
                  transform=ax.get_xaxis_transform(), colors=np.where(states > 0, UP, DOWN),
                  lw=3, zorder=7)


def txt(fig, x, y, text, size, color=INK, **kwargs):
    return fig.text(x, y, text, size=size, color=color, **kwargs)


def draw(week, group, hourly, cloud, num):
    keys = sorted(group.key_ms.unique())
    start = max(hourly.index.min(), keys[0]-9*H)
    cutoff = hourly.index.max()+H
    detail_end = min(cutoff, keys[-1]+KEY+6*H)
    last_exit = max(exit_time(r) for r in group.itertuples())
    duration = (max(detail_end, last_exit) - start)/H
    period = 1 if duration <= 116 else (4 if duration <= 380 else 12)
    step = H*period
    full = (max(hourly.index.min(), start//step*step),
            min(cutoff, int(np.ceil((max(detail_end, last_exit+4*step))/step))*step))
    cal = week_window(int(keys[0]))
    assert cal['week'] == week
    monday = hourly.loc[cal['start']:cal['end']-1]
    assert len(monday) == 24
    lo, hi = monday.low.min(), monday.high.max()
    cal.update(lower=lo-.618*(hi-lo), upper=hi+.618*(hi-lo))
    both = len(group) == 2
    category = ('同根同向确认' if group.matched.all() else '同周不同信号') if both else (
        '仅触发 1H 提前单' if group.cohort.iloc[0] == 'early' else '仅出现 12H 确认单')
    direction = ' / '.join('做多' if s == 1 else '做空' for s in dict.fromkeys(group.side))
    fig = plt.figure(figsize=(15,8.7), facecolor='white')
    txt(fig,.055,.934,'ETH / USDT',25, weight='bold', fontfamily='DejaVu Sans')
    txt(fig,.266,.938,'SPIKE 云带 v1.9',17)
    txt(fig,.945,.947,f'{num:02d} / 20',12,MUTED,ha='right',fontfamily='DejaVu Sans')
    txt(fig,.055,.875,f'纽约交易周  {week}     ·     {direction}     ·     {category}',15)
    sides = sorted(set(group.side), reverse=True)
    rail_text = '   /   '.join(f'{"下轨" if s == 1 else "上轨"} 1.618   {cal["lower" if s == 1 else "upper"]:,.2f}' for s in sides)
    txt(fig,.945,.878,rail_text,13,RAIL,ha='right')
    left, right = fig.add_axes([.065,.376,.405,.396]), fig.add_axes([.553,.376,.405,.396])
    panel(left,hourly,(start,detail_end),1,group,cal,cloud)
    panel(right,hourly,full,period,group,cal)
    txt(fig,.055,.804,'入场细节   ·   1H K 线',14,weight='bold')
    txt(fig,.539,.804,f'完整持仓路径   ·   {period}H K 线',14,weight='bold')
    fig.add_artist(Line2D([.055,.947],[.843,.843], transform=fig.transFigure, color='#d4dfe7', lw=.9))
    txt(fig,.055,.284,'○  1H 提前入场',11,AMBER)
    txt(fig,.20,.284,'◇  12H 确认入场',11,NAVY)
    txt(fig,.36,.284,'×  同色平仓',11,MUTED)
    txt(fig,.51,.284,'长虚线  1.618',10.5,RAIL)
    txt(fig,.66,.284,'短虚线  固定止损',10.5,STOP)
    txt(fig,.82,.284,'灰底  信号 12H',10.5,MUTED)
    columns=[.055,.212,.435,.674,.945]
    for x,title in zip(columns,['规则','入场时间 / 价格','退出时间 / 价格','退出原因','扣费净 R']):
        txt(fig,x,.224,title,10,MUTED,ha='right' if x==columns[-1] else 'left')
    fig.add_artist(Line2D([.055,.947],[.213,.213], transform=fig.transFigure, color='#d4dfe7', lw=.9))
    for y,(arm,(name,color)) in zip([.167,.117],ARMS.items()):
        txt(fig,columns[0],y,name,12,color,weight='bold')
        rows=group[group.cohort == arm]
        if rows.empty:
            txt(fig,columns[1],y,'当周未触发',11,MUTED)
            continue
        r=next(rows.itertuples())
        phase = '时内' if r.exit_phase=='intrabar' else ''
        efmt = '%m/%d %H' if phase else '%m/%d %H:%M'
        txt(fig,columns[1],y,f'{bjt(r.entry_ms)}   /   {r.entry_px:,.2f}',11)
        txt(fig,columns[2],y,f'{bjt(exit_time(r),efmt)}{phase}   /   {r.exit_px:,.2f}',11)
        txt(fig,columns[3],y,REASONS[r.exit_reason],11)
        txt(fig,columns[-1],y,f'{r.net_r:+.2f} R',13,UP if r.net_r>0 else DOWN,ha='right',weight='bold')
    txt(fig,.055,.052,'北京时间（UTC+8）    ·    左图底部色带为每小时收盘时的 12H 云带方向，绿多红空。',10,MUTED)
    txt(fig,.945,.052,'OKX 永续    ·    盘中退出定位至小时    ·    成交与盈亏取自原回测',9.5,MUTED,ha='right')
    return fig


def main():
    font='/System/Library/Fonts/Hiragino Sans GB.ttc'
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({'font.family': font_manager.FontProperties(fname=font).get_name(),
                         'axes.unicode_minus':False, 'pdf.fonttype':42})
    hourly, twelve = (pd.read_csv(SOURCE/f'bars{n}h.csv.gz',index_col='time',float_precision='round_trip')
                      for n in (1,12))
    prev = pd.DataFrame({n:twelve.close.ewm(span=n,adjust=False).mean().shift() for n in (20,50)})
    aligned = prev.reindex(hourly.index//KEY*KEY)
    fast, slow = (aligned[n].to_numpy() + (hourly.close.to_numpy() - aligned[n].to_numpy())*(2/(n+1)) for n in (20,50))
    cloud = pd.Series(fast-slow, index=hourly.index)
    trades = pd.read_csv(STUDY/'trades.csv',float_precision='round_trip')
    weeks = sorted(trades.week.unique(), reverse=True)[:20]
    assert len(weeks)==20
    OUT.mkdir(parents=True, exist_ok=True)
    files=[]
    with PdfPages(OUT/'ETH-v19-20-weeks.pdf',metadata={'Title':'ETH 云带 v1.9 | 最近 20 个信号周'}) as pdf:
        for n, week in enumerate(weeks,1):
            group = trades[trades.week == week]
            for r in group.itertuples():
                assert hourly.at[r.entry_ms,'open'] == r.entry_px
                bar=hourly.loc[r.exit_hour_ms]
                assert bar.low-1e-7 <= r.exit_px <= bar.high+1e-7
                if r.exit_phase in ('open','close'):
                    assert np.isclose(bar[r.exit_phase],r.exit_px)
            fig = draw(week,group,hourly,cloud,n)
            path = OUT/f'{n:02d}_ETH_{week}.png'
            fig.savefig(path, dpi=140)
            pdf.savefig(fig)
            plt.close(fig)
            files.append(path)
    with ZipFile(OUT/'ETH-v19-20-PNG.zip','w') as bundle:
        for path in files:
            bundle.write(path,path.name)


if __name__ == '__main__':
    main()

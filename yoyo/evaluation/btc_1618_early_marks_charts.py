"""Render all 43 frozen short-excursion BTC15m signals requested by the owner.

Source: exp-btc-1618-early-marks-20261031-v1, without fetching or rescanning.
Each panel reads 30 prior candles, the signal candle, and 29 later candles.
Only the selected week's completed Monday1.618 is drawn, from its availability
through next Monday. Glyph timestamps and prices come from the frozen census.
Future candles and full excursion duration are retrospective review context.
There are 43 signals in 42 excursions; both signals in the shared excursion
retain individual charts. No orders, PnL, targets or stops are inferred.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_1618_early_marks import EXP, ROOT, STEP, COLS
from yoyo.evaluation.btc_annual_signal_charts import FONT

OUT = ROOT / 'artifacts/btc-1618-short-excursions-43-20261031'
BG, FG, MUTED = '#080b10', '#e5e9f0', '#8b95a5'
GOLD = '#e6bd6a'
COLORS = {'upper': '#ff626d', 'lower': '#43e39c'}


def stamp(value, fmt='%m-%d %H:%M'):
    return pd.Timestamp(value).tz_convert('Asia/Shanghai').strftime(fmt)


def prepare():
    """Validate every chart against frozen candles, levels and signal rows."""
    matches = pd.read_csv(EXP/'short_excursion_matches.csv', float_precision='round_trip')
    bars = pd.read_csv(EXP/'bars.csv.gz', index_col=0, parse_dates=True,
                       float_precision='round_trip')
    marks = pd.read_csv(EXP/'all_outside_marks.csv', float_precision='round_trip')
    marks = marks[marks.basis.eq('wick') & marks.visible_v242].copy()
    matches = matches.sort_values(['signal_open', 'side']).reset_index(drop=True)
    assert len(matches) == 43 and matches.episode_id.nunique() == 42
    assert matches.side.value_counts().to_dict() == {'upper': 23, 'lower': 20}
    result = []
    for n, row in enumerate(matches.to_dict('records'), 1):
        opened = pd.Timestamp(row['signal_open']).tz_convert('UTC')
        i = bars.index.get_loc(opened)
        frame = bars.iloc[i-30:i+30]
        assert len(frame) == 60 and (np.diff(frame.index.asi8) == STEP.value).all()
        assert np.allclose(bars.iloc[i][COLS], [row[c] for c in COLS], rtol=0, atol=1e-8)
        week = pd.Timestamp(row['week'])
        ready = (week+pd.Timedelta(days=1)).tz_localize('America/New_York')
        expiry = (week+pd.Timedelta(days=7)).tz_localize('America/New_York')
        monday = bars[(bars.index >= week.tz_localize('America/New_York')) & (bars.index < ready)]
        assert len(monday) == 96
        hi, lo = monday.high.max(), monday.low.min()
        actual = lo+1.618*(hi-lo) if row['side'] == 'upper' else hi-1.618*(hi-lo)
        assert abs(actual-row['level']) < 1e-8
        first, last = pd.Timestamp(row['start']), pd.Timestamp(row['last'])
        excursion = bars.loc[first:last]
        assert len(excursion) == row['episode_bars'] <= 5
        beyond = excursion.high.gt(actual) if row['side'] == 'upper' else excursion.low.lt(actual)
        assert beyond.all() and row['ordinal'] == bars.index.get_loc(opened)-bars.index.get_loc(first)+1
        for edge in [first-STEP, last+STEP]:
            if edge >= ready:
                b = bars.loc[edge]
                assert b.high <= actual if row['side'] == 'upper' else b.low >= actual
        nearby = marks[marks.week.eq(row['week']) & marks.side.eq(row['side'])].copy()
        times = pd.to_datetime(nearby.signal_open, utc=True)
        nearby = nearby[(times >= frame.index[0]) & (times <= frame.index[-1])]
        assert nearby.signal_open.eq(row['signal_open']).sum() == 1
        filename = f'{n:02d}_{stamp(opened, "%Y%m%d_%H%M")}_{"cross" if row["side"] == "upper" else "check"}.png'
        row.update(number=n, filename=filename, ready=str(ready), expiry=str(expiry),
                   candles=[dict(time=str(t), **{c: float(v[c]) for c in COLS}) for t, v in frame.iterrows()],
                   nearby_marks=nearby[['signal_open', 'side', 'high', 'low']].to_dict('records'))
        result.append(row)
    return result


def setup():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font_manager.fontManager.addfont(FONT)
    plt.rcParams.update({'font.family': font_manager.FontProperties(fname=FONT).get_name(),
                         'axes.unicode_minus': False, 'figure.facecolor': BG,
                         'axes.facecolor': BG, 'savefig.facecolor': BG,
                         'text.color': FG, 'font.size': 14})


def draw(r):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import MaxNLocator, StrMethodFormatter
    candles, side = r['candles'], r['side']
    color = COLORS[side]
    start = pd.Timestamp(candles[0]['time'])
    x = lambda t: (pd.Timestamp(t)-start)/STEP
    low = min(min(c['low'] for c in candles), r['level'])
    high = max(max(c['high'] for c in candles), r['level'])
    span = high-low
    fig = plt.figure(figsize=(16, 9), dpi=150)
    fig.text(.045, .952, f'{r["number"]:02d} / 43   BTCUSDT.P · 15分钟', fontsize=23, weight='bold', va='top')
    fig.text(.955, .95, stamp(r['signal_open'], '%Y-%m-%d %H:%M')+'  北京时间', fontsize=17, ha='right', va='top')
    direction = '上穿 · 红叉 ×' if side == 'upper' else '下穿 · 绿勾 ✓'
    fig.text(.045, .884, direction, color=color, fontsize=18)
    fig.text(.955, .884, f'信号在越线第 {r["ordinal"]} 根  /  整段越线 {r["episode_bars"]} 根',
             fontsize=16, color=FG, ha='right')
    ax = fig.add_axes([.07, .19, .80, .64])
    ax.set(xlim=(-1, 60), ylim=(low-span*.13, high+span*.13))
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ax.tick_params(axis='both', colors=MUTED, length=0, labelsize=12, pad=10)
    ticks = [0, 10, 20, 30, 40, 50, 59]
    ax.set_xticks(ticks, [stamp(candles[j]['time'], '%m-%d\n%H:%M') for j in ticks])
    for name, spine in ax.spines.items():
        spine.set_visible(name in ('bottom', 'right'))
        spine.set_color('#29313d')
    ax.grid(axis='y', color='#29313d', alpha=.55, lw=.65, linestyle=(0, (2, 5)))
    ax.axvspan(x(r['start'])-.48, x(r['last'])+.48, color=GOLD, alpha=.13, lw=0, zorder=0)
    # The level never appears over Monday candles before it was available.
    a, b = max(-.5, x(r['ready'])-.5), min(59.5, x(r['expiry'])-.5)
    ax.hlines(r['level'], a, b, color=GOLD, lw=1.65, zorder=2)
    ax.annotate(('上' if side == 'upper' else '下')+f' 1.618   {r["level"]:,.1f}',
                xy=(1, r['level']), xycoords=('axes fraction', 'data'),
                xytext=(0, 9), textcoords='offset points', ha='right', va='bottom',
                color=GOLD, fontsize=15, bbox=dict(facecolor=BG, edgecolor='none', pad=3))
    for j, c in enumerate(candles):
        candle_color = '#eef1f5' if j == 30 else '#8792a2'
        bottom, top = sorted([c['open'], c['close']])
        ax.vlines(j, c['low'], c['high'], color=candle_color, lw=1.3 if j == 30 else 1.0, zorder=3)
        ax.add_patch(Rectangle((j-.32, bottom), .64, max(top-bottom, span*.0003),
                               facecolor=candle_color, edgecolor=candle_color, lw=.7, zorder=3))
    for mark in r['nearby_marks']:
        primary = pd.Timestamp(mark['signal_open']) == pd.Timestamp(r['signal_open'])
        mx = x(mark['signal_open'])
        price = mark['high'] if side == 'upper' else mark['low']
        offset = 20 if side == 'upper' else -20
        ax.annotate('×' if side == 'upper' else '✓', xy=(mx, price),
                    xytext=(0, offset), textcoords='offset points', color=color,
                    fontsize=32 if primary else 20, alpha=1 if primary else .4,
                    ha='center', va='center', weight='bold', zorder=6)
    ax.axvline(30, color=color, alpha=.38, lw=.9, linestyle=(0, (3, 5)), zorder=1)
    fig.text(.07, .068, '金线：1.618   ·   淡金色带：本段越线 K 线   ·   亮色勾叉：本张信号',
             fontsize=13, color=MUTED)
    fig.text(.955, .068, 'OKX · V2.42 · 60根原始K线', fontsize=12, ha='right', color=MUTED)
    fig.text(.07, .029, '历史复盘；横轴为K线开盘时间，信号在该K收盘确认。各图价格轴独立。', fontsize=10, color='#697587')
    path = OUT/'png'/r['filename']
    fig.savefig(path)
    plt.close(fig)
    return path


def pack(rows):
    from PIL import Image, ImageDraw, ImageFont
    sheets = []
    title_font = ImageFont.truetype(FONT, 40)
    for page, a in enumerate(range(0, len(rows), 8), 1):
        batch = rows[a:a+8]
        height = ((len(batch)+1)//2)*675+100
        sheet = Image.new('RGB', (2400, height), BG)
        d = ImageDraw.Draw(sheet)
        d.text((35, 23), f'BTC 15分钟 · 短暂越线43例  |  {a+1:02d}–{a+len(batch):02d}  |  {page}/6', font=title_font, fill=FG)
        for j, row in enumerate(batch):
            with Image.open(OUT/'png'/row['filename']) as im:
                assert im.size == (2400, 1350)
                thumb = im.convert('RGB').resize((1200, 675), Image.Resampling.LANCZOS)
                sheet.paste(thumb, ((j%2)*1200, 100+(j//2)*675))
        path = OUT/f'overview_{page:02d}.jpg'
        sheet.save(path, quality=95, subsampling=0)
        sheets.append(str(path.relative_to(OUT)))
    fields = ['number', 'filename', 'signal_open', 'side', 'level', 'ordinal', 'episode_bars', 'episode_id']
    pd.DataFrame(rows)[fields].to_csv(OUT/'index.csv', index=False)
    archive = OUT/'BTC_15m_43_short_excursions.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
        for r in rows:
            z.write(OUT/'png'/r['filename'], arcname='png/'+r['filename'])
        for filename in sheets+['index.csv']:
            z.write(OUT/filename, arcname=filename)
    with ZipFile(archive) as z:
        assert z.testzip() is None
        assert sum(name.startswith('png/') for name in z.namelist()) == 43
    return sheets, archive


def run():
    (OUT/'png').mkdir(parents=True, exist_ok=True)
    rows = prepare()
    setup()
    for row in rows:
        draw(row)
    sheets, archive = pack(rows)
    receipt = dict(charts=43, excursions=42, candles_per_chart=60,
                   dimensions=[2400, 1350], sheets=sheets, archive=str(archive),
                   source=str(EXP), source_commit=subprocess.check_output(
                       ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   rows=rows, validation=dict(all_43_signal_OHLC_match=True,
                        all_43_levels_match_completed_monday=True,
                        all_43_excursion_boundaries_match=True, zip_integrity=True),
                   reproduction='.venv/bin/python -m yoyo.evaluation.btc_1618_early_marks_charts')
    (OUT/'delivery.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()

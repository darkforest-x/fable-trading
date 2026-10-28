"""Render a bounded OKX second-candle preview, without trading or replay.

Source: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history
Owner 2026-10-28 requested 1s/5s/10s charts of the pictured WEN move. Reuse
the existing fetcher's public request/throttle; its CLI only accepts minutes.
Read confirmed exchange 1s OHLCV, require continuous coverage, then aggregate
complete epoch-aligned 5s/10s groups. No interpolation or inferred fills.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from src.data.fetch_okx import API, _request


def fetch(symbol, start, end, out):
    raw_path = out / 'okx-1s-raw.json'
    contract = {'symbol': symbol, 'start': start.isoformat(), 'end': end.isoformat()}
    if raw_path.exists():
        raw = json.loads(raw_path.read_text())
        assert raw['contract'] == contract, 'Cache belongs to another interval'
    else:
        rows, pages = [], []
        cursor = int(end.timestamp() * 1000)
        while cursor > int(start.timestamp() * 1000):
            url = API + '?' + urlencode(dict(instId=symbol, bar='1s', after=cursor, limit=100))
            response = _request(url)
            assert response.get('code') == '0', response
            batch = response.get('data', [])
            assert batch, 'Source returned no candles'
            oldest = min(int(row[0]) for row in batch)
            assert oldest < cursor, 'Pagination did not advance'
            rows.extend(batch)
            pages.append({'url': url, 'response': response})
            cursor = oldest
        raw = {'contract': contract, 'pages': pages, 'rows': rows}
        raw_path.write_text(json.dumps(raw, indent=2))
    columns = ['ts', 'open', 'high', 'low', 'close', 'volume', 'base_volume', 'quote_volume', 'confirm']
    frame = pd.DataFrame(raw['rows'], columns=columns).astype(float)
    assert not frame.ts.duplicated().any(), 'Overlapping API pages'
    frame.index = pd.to_datetime(frame.pop('ts'), unit='ms', utc=True)
    frame = frame.sort_index().loc[lambda d: (d.index >= start) & (d.index < end)]
    expected = pd.date_range(start, end, freq='1s', inclusive='left').tz_convert('UTC')
    assert frame.index.equals(expected), 'Missing seconds; no synthetic gap fill allowed'
    assert (frame.confirm == 1).all(), 'Unconfirmed source candle'
    assert np.isfinite(frame.to_numpy()).all()
    assert (frame[['open', 'high', 'low', 'close']] > 0).all().all()
    assert (frame.high >= frame[['open', 'low', 'close']].max(axis=1)).all()
    assert (frame.low <= frame[['open', 'high', 'close']].min(axis=1)).all()
    assert (frame[['volume', 'base_volume', 'quote_volume']] >= 0).all().all()
    return frame


def aggregate(frame, seconds):
    rule = f'{seconds}s'
    grouped = frame.resample(rule, origin='epoch', closed='left', label='left')
    assert (grouped.size() == seconds).all(), 'Partial aggregation bucket'
    result = grouped.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
                          'volume': 'sum', 'base_volume': 'sum', 'quote_volume': 'sum'})
    assert result.high.max() == frame.high.max() and result.low.min() == frame.low.min()
    assert np.isclose(result.volume.sum(), frame.volume.sum())
    return result


def render(frames, symbol, start, end, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection, PolyCollection
    from matplotlib.font_manager import FontProperties
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    font = FontProperties(fname='/System/Library/Fonts/Hiragino Sans GB.ttc')
    bg, panel, ink, muted = '#0e1116', '#11161e', '#e7edf5', '#8797ab'
    up, down = '#21b58f', '#ec526e'
    plt.rcParams.update({'font.family': font.get_name(), 'axes.unicode_minus': False})
    fig = plt.figure(figsize=(18.4, 14.8), dpi=140, facecolor=bg)
    fig.text(.035, .966, 'SPIKE  /  秒级 K 线', color=ink, fontsize=21, weight='bold')
    fig.text(.965, .968, symbol + '  ·  OKX 永续', color=ink, fontsize=15, ha='right')
    fig.text(.035, .934, f'{start:%Y-%m-%d %H:%M:%S} — {end:%H:%M:%S}  北京时间  ·  同一时间窗',
             color=muted, fontsize=12)
    fig.text(.965, .934, '历史静态预览 · 真实行情', color=muted, fontsize=12, ha='right')
    grid = fig.add_gridspec(3, 1, left=.045, right=.93, bottom=.07, top=.895, hspace=.34)
    origin = start.timestamp()
    duration = (end-start).total_seconds()
    all_low = min(frame.low.min() for frame in frames.values())
    all_high = max(frame.high.max() for frame in frames.values())
    padding = max((all_high-all_low)*.12, .001)
    for i, (seconds, frame) in enumerate(frames.items()):
        pair = grid[i].subgridspec(2, 1, height_ratios=[4, .7], hspace=.1)
        ax = fig.add_subplot(pair[0])
        vol = fig.add_subplot(pair[1], sharex=ax)
        x = frame.index.astype('int64').to_numpy()/1e9-origin+seconds/2
        colors = np.where(frame.close >= frame.open, up, down)
        width = seconds*.7
        wick = [[(a, lo), (a, hi)] for a, lo, hi in zip(x, frame.low, frame.high)]
        ax.add_collection(LineCollection(wick, colors=colors, linewidths=.8))
        polygons = [[(a-width/2, o), (a-width/2, c), (a+width/2, c), (a+width/2, o)]
                    for a, o, c in zip(x, frame.open, frame.close)]
        ax.add_collection(PolyCollection(polygons, facecolors=colors, edgecolors=colors, linewidths=.7))
        for a, o, c, color in zip(x, frame.open, frame.close, colors):
            if o == c:
                ax.plot([a-width/2, a+width/2], [o, o], color=color, linewidth=.75)
        vol.bar(x, frame.volume, width=width, color=colors, alpha=.5)
        ax.set_ylim(all_low-padding, all_high+padding)
        ax.set_xlim(0, duration)
        ax.set_title(f'{seconds} 秒   /   {len(frame)} 根 K 线', loc='left',
                     color=ink, fontsize=14, pad=13, weight='bold')
        ax.text(1, 1.07, '价格 USDT', transform=ax.transAxes, ha='right', color=muted, fontsize=10)
        for axes in (ax, vol):
            axes.set_facecolor(panel)
            axes.tick_params(colors=muted, labelsize=10, length=0, pad=8)
            axes.yaxis.tick_right()
            for spine in axes.spines.values():
                spine.set_visible(False)
            axes.grid(axis='y', color='#26303e', alpha=.5, linewidth=.5)
            axes.axvline(60, color='#63748a', linestyle=(0, (3, 5)), linewidth=.7, alpha=.7)
        ax.yaxis.set_major_locator(MaxNLocator(5))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:.3f}'))
        ax.tick_params(labelbottom=False)
        vol.set_ylim(0, max(1, frame.volume.max())*1.2)
        vol.set_yticks([])
        vol.text(.008, .8, '成交量（张）', transform=vol.transAxes, color=muted, fontsize=9)
        vol.set_xticks(np.arange(0, duration+1, 30))
        vol.xaxis.set_major_formatter(FuncFormatter(
            lambda value, _: (start+pd.Timedelta(seconds=value)).strftime('%H:%M:%S')))
    fig.text(.045, .025, '来源：OKX 已确认 1 秒 OHLCV；5 秒 / 10 秒按整秒边界聚合。三个面板使用相同时间与价格范围。',
             color=muted, fontsize=11)
    target = out/'wen-1s-5s-10s.png'
    fig.savefig(target, facecolor=bg)
    plt.close(fig)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='WEN-USDT-SWAP')
    parser.add_argument('--start', default='2026-10-28T19:59:00+08:00')
    parser.add_argument('--end', default='2026-10-28T20:03:00+08:00')
    parser.add_argument('--out', type=Path, default=Path('output/seconds-kline-preview-20261028'))
    args = parser.parse_args()
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    assert start.tzinfo is not None and end.tzinfo is not None
    assert 0 < (end-start).total_seconds() <= 1800
    assert start.timestamp() % 10 == end.timestamp() % 10 == 0
    args.out.mkdir(parents=True, exist_ok=True)
    source = fetch(args.symbol, start, end, args.out)
    frames = {seconds: aggregate(source, seconds) for seconds in [1, 5, 10]}
    for seconds, frame in frames.items():
        frame.to_csv(args.out/f'candles-{seconds}s.csv', index_label='utc')
    image_path = render(frames, args.symbol, start, end, args.out)
    receipt = {'symbol': args.symbol, 'start': start.isoformat(), 'end_exclusive': end.isoformat(),
               'rows': {str(s): len(d) for s, d in frames.items()}, 'source': API,
               'continuous_confirmed_seconds': True, 'interpolated': False,
               'zero_volume_seconds': int((source.volume == 0).sum()),
               'image': str(image_path.resolve())}
    (args.out/'receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

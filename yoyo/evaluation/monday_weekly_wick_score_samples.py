"""Render an outcome-blind 2026 example for each weekly wick timeframe and side.

Sampling uses the selected estimator's executable test events, sorted by event
ID, before querying any scores or outcomes. Draws use a fixed seed. Candle
times are UTC-aligned and read from the same frozen H1 tape as the census.
"""
from pathlib import Path
import random

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from yoyo.evaluation import month_firstweek_wick_sample_charts as chart
from yoyo.evaluation.monday_weekly_wick_census3r import EXP, load_frozen_tape


def sample_ids(scored):
    eligible = scored.loc[scored.selected & scored.status.eq('entered'), ['event_id', 'tf', 'side']]
    if not eligible.event_id.is_unique:
        raise ValueError('Expected one final selected model per event')
    rng = random.Random(20261116)
    return [rng.choice(sorted(eligible.loc[eligible.tf.eq(tf) & eligible.side.eq(side), 'event_id']))
            for tf in (1, 4, 12) for side in (-1, 1)]


def panel(draw, x, y, event, prediction, label):
    tf, side = int(event.tf), int(event.side)
    raw = load_frozen_tape(event.symbol)
    ts = raw[:, 0].astype('int64')
    start = int(event.bar_ms)
    step = tf * chart.H
    offsets = list(range(-14, 10))
    bars = [chart.aggregate(raw, ts, start + i * step, tf) for i in offsets]
    selected = bars[offsets.index(0)]
    np.testing.assert_allclose(selected, [event.bar_open, event.bar_high, event.bar_low, event.bar_close],
                               rtol=1e-10, atol=0)
    draw.rounded_rectangle((x,y,x+774,y+315), radius=12, fill=chart.PANEL)
    title = f"{tf}h  ·  {event.symbol}   {'上轨做空' if side == -1 else '下轨做多'}"
    chart.label(draw, (x+20,y+12), title, 25, chart.INK, True)
    chart.label(draw, (x+754,y+18), chart.when(event.close_ms, '%Y-%m-%d %H:%M'), 17,
                chart.MUTED, anchor='ra')
    low, right = x+19, x+666
    top, bottom = y+62, y+211
    scale_values = [p for b in bars if b is not None for p in b]
    yprice, lower, upper = chart.ymap([*scale_values, event.rail], top, bottom)
    dx = (right-low)/len(bars)

    def point(i):
        return low+(i-offsets[0]+.5)*dx

    draw.rectangle((point(0)-.48*dx, top, point(0)+.48*dx, bottom), fill='#383227')
    for price in np.linspace(lower, upper, 5)[1:-1]:
        draw.line((low,yprice(price),right,yprice(price)),fill=chart.GRID)
        chart.label(draw,(right+6,yprice(price)),chart.price(price),14,chart.QUIET,anchor='lm')
    active_start = max(low, point((event.anchor_ready_ms-start)/step)-dx*.5)
    active_end = min(right, point((event.expires_ms-start)/step)-dx*.5)
    draw.line((active_start,yprice(event.rail),active_end,yprice(event.rail)),fill=chart.GOLD,width=2)
    for i,b in zip(offsets,bars):
        if b is not None:
            chart.candle(draw,point(i),yprice,*b,half=.27*dx)
    chart.label(draw,(point(0),bottom+5),'信号K',15,chart.GOLD,True,'ma')
    result = {'tp':'先到 3R', 'sl':'先止损', 'censored':'观察截止仍未决'}[label.outcome]
    band = '高分组' if prediction.band == 'top10' else '普通组'
    chart.label(draw,(x+20,y+241),f'原始评分 {100*prediction.raw_score:.1f} / 100   ·   {band}',
                19,chart.INK)
    chart.label(draw,(x+754,y+241),f'实际结果   {result}',19,
                chart.TARGET if label.outcome=='tp' else chart.MUTED,True,'ra')
    detail = (f'入场 {chart.price(event.entry)}     止损 {chart.price(event.stop)}'
              f'     3R {chart.price(label.target_price)}')
    chart.label(draw,(x+20,y+279),detail,17,chart.MUTED)


def main():
    scores = pd.read_csv(EXP/'test_scores.csv.gz')
    ids = sample_ids(scores)
    scores = scores.loc[scores.selected].set_index('event_id')
    events = pd.read_csv(EXP/'first_breaches.csv.gz').set_index('event_id')
    labels = pd.read_csv(EXP/'outcomes_3r.csv.gz').set_index('event_id')
    image = Image.new('RGB', (1604,1125), chart.BG)
    draw = ImageDraw.Draw(image)
    chart.label(draw,(20,16),'周一 1.618 首次纯影线收回',36,chart.INK,True)
    chart.label(draw,(20,69),'2026 测试样本，每个周期的多空各随机抽一笔。金线为当周 1.618，金色背景为信号 K。',22,chart.MUTED)
    for i,key in enumerate(ids):
        panel(draw,20+(i%2)*790,120+(i//2)*327,events.loc[key],scores.loc[key],labels.loc[key])
    chart.label(draw,(20,1093),'币安 USDT 永续合约成交 K 线  ·  周一按纽约时间划分  ·  每张图右上为北京时间的信号收盘时刻',17,chart.QUIET)
    output = Path(EXP)/'sample_scores.png'
    image.save(output, optimize=True)
    print(output, *ids, sep='\n')


if __name__ == '__main__':
    main()

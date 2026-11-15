"""Render six direction/timeframe-stratified examples plus the owner's USELESS example.

Sample selection uses only entered event IDs, chosen timeframe and direction.
Market data is the frozen Binance H1 tape from the priority census. Reaggregate
complete UTC candles for display; the settled 1R order result is read from the
existing ledger after sampling.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import random
import sys
from zipfile import ZIP_STORED, ZipFile

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from yoyo.evaluation import month_firstweek_12h_wick_census as base
from yoyo.evaluation.month_firstweek_wick_priority import EXP, frozen_tape

H = base.HOUR_MS
OUT = base.ROOT / 'output/monthweek-wick-priority-samples-20261116'
REFERENCE = 'USELESSUSDT/2025-11/lower'
BG, PANEL, GRID = '#0e141b', '#151e27', '#2d3b47'
INK, MUTED, QUIET = '#f1f5f6', '#b2c2cb', '#90a2af'
UP, DOWN, GOLD = '#42c69e', '#f27782', '#f4c66d'
BLUE, STOP, TARGET = '#80bfff', '#ff949b', '#72dabb'


@lru_cache(maxsize=24)
def font(size, bold=False):
    weight = 'Medium' if bold else 'Light'
    return ImageFont.truetype(f'/System/Library/Fonts/STHeiti {weight}.ttc', size)


def label(d, xy, value, size=20, color=INK, bold=False, anchor='la'):
    d.text(xy, str(value), font=font(size,bold), fill=color, anchor=anchor)


def price(value):
    return np.format_float_positional(float(f'{value:.7g}'), trim='-')


def when(ms, fmt='%m-%d %H:%M'):
    return pd.Timestamp(int(ms), unit='ms', tz='UTC').tz_convert('Asia/Shanghai').strftime(fmt)


def ymap(values, top, bottom, padding=.12):
    lo, hi = min(values), max(values)
    pad = max((hi-lo)*padding, abs(hi)*1e-7)
    lo, hi = lo-pad, hi+pad
    return lambda v: bottom-(v-lo)*(bottom-top)/(hi-lo), lo, hi


def dashed(d, x0, y, x1, color, dash=8, gap=5):
    for x in range(round(x0), round(x1), dash+gap):
        d.line((x,y,min(x+dash,x1),y), fill=color, width=2)


def candle(d, x, y, o, h, l, c, half):
    color = UP if c >= o else DOWN
    d.line((x,y(h),x,y(l)), fill=color, width=3)
    a,b = sorted([y(o),y(c)])
    d.rectangle((x-half,a,x+half,max(a+2,b)), fill=color)


def aggregate(raw, ts, start, hours):
    j,k = np.searchsorted(ts, [start,start+hours*H])
    if k-j != hours:
        return None
    return raw[j,1], raw[j:k,2].max(), raw[j:k,3].min(), raw[k-1,4]


def pick(rows):
    rng = random.Random(20261116)
    ids = []
    for tf in (1,4,12):
        for side in (-1,1):
            eligible = rows[(rows.tf==tf)&(rows.side==side)&(rows.index!=REFERENCE)]
            ids.append(rng.choice(sorted(eligible.index)))
    return [*ids, REFERENCE]


def context_panel(d, raw, ts, row, w):
    left, right, top, bottom = 75,1038,727,863
    a,i = np.searchsorted(ts, [w['ready_ms'], row.first_breach_ms])
    extremes = raw[a:i+1, 3 if row.side==1 else 2]
    assert len(extremes) and np.all(np.diff(ts[a:i+1])==H)
    assert np.all(row.side*(extremes[:-1]-row.rail)>=-row.rail*1e-12)
    desc = '最低价' if row.side==1 else '最高价'
    label(d,(75,638),'首次穿线之前',25,INK,True)
    if i>a:
        previous = min(extremes[:-1]) if row.side==1 else max(extremes[:-1])
        assert np.isclose(previous,row.prior_extreme,rtol=1e-12,atol=0)
        detail = f'从首周确认到首次外穿前，共 {i-a} 根 1h K；此前{desc} {price(previous)}'
    else:
        detail = '首周确认后的第一根 1h K 即外穿'
    label(d,(75,682),detail,19,MUTED)
    y,lo,hi = ymap([*extremes,row.rail],top,bottom)
    xs = np.linspace(left,right,len(extremes)) if len(extremes)>1 else [right]
    pts = [(x,y(v)) for x,v in zip(xs,extremes)]
    for v in np.linspace(lo,hi,4)[1:-1]:
        d.line((left,y(v),right,y(v)),fill=GRID,width=1)
    dashed(d,left,y(row.rail),right,GOLD,9,5)
    if len(pts)>1:
        d.line(pts,fill='#9cbeda',width=3)
    x,last=pts[-1]
    d.ellipse((x-5,last-5,x+5,last+5),fill=GOLD)
    label(d,(left,879),when(w['ready_ms'])+'  首周确认',17,MUTED)
    label(d,(right,879),when(row.first_breach_ms)+'  首次外穿所在小时',17,MUTED,anchor='ra')
    label(d,(75,921),f'蓝线为每根 1h 的{desc}，金线为本月固定 1.618。',17,QUIET)


def draw_event(row, outcome, raw, w):
    ts = raw[:,0].astype('int64')
    tf, bar = int(row.tf), int(row.bar_ms)
    side = int(row.side)
    direction = '下轨做多' if side==1 else '上轨做空'
    rail = row.rail
    image = Image.new('RGB',(1680,1012),BG)
    d = ImageDraw.Draw(image)
    label(d,(52,26),'币安 USDT 永续   /   月首周 1.618',22,MUTED)
    label(d,(52,65),row.symbol,49,INK,True)
    sx = 52+d.textlength(row.symbol,font=font(49,True))+30
    label(d,(sx,84),direction,29,TARGET if side==1 else STOP,True)
    label(d,(1627,26),'信号收盘  ·  北京时间',20,MUTED,anchor='ra')
    label(d,(1627,62),when(row.close_ms,'%Y-%m-%d  %H:%M'),32,INK,True,'ra')
    for bounds in [(52,142,1143,594),(1161,142,1628,594),(52,614,1143,968),(1161,614,1628,968)]:
        d.rounded_rectangle(bounds,radius=13,fill=PANEL)
    label(d,(75,160),f'{tf}h 确认  ·  信号前后',27,INK,True)
    for x,cl,txt in [(485,GOLD,'1.618'),(621,BLUE,'入场'),(743,STOP,'止损'),(869,TARGET,'1R 目标')]:
        d.line((x,183,x+22,183),fill=cl,width=3)
        label(d,(x+29,171),txt,18,MUTED)

    left,right,top,bottom=75,1038,225,503
    offsets=np.arange(-21,13)
    bars = [(int(i),aggregate(raw,ts,bar+int(i)*tf*H,tf)) for i in offsets]
    found=dict(bars)[0]
    assert found is not None and np.allclose(found,[row.bar_open,row.bar_high,row.bar_low,row.bar_close],rtol=1e-10,atol=0)
    values=[p for _,v in bars if v is not None for p in v]
    target=outcome.target_price
    y,lo,hi=ymap([*values,rail,row.stop,row.entry,target],top,bottom)
    step=(right-left)/len(offsets)
    def x(offset): return left+(offset-offsets[0]+.5)*step
    d.rectangle((x(0)-.48*step,top,x(0)+.48*step,bottom),fill='#383227')
    for v in np.linspace(lo,hi,6)[1:-1]:
        d.line((left,y(v),right,y(v)),fill=GRID)
        label(d,(right+8,y(v)),price(v),15,QUIET,anchor='lm')
    # Candle timestamps denote opens. Place the range line only within the NY anchor's active interval.
    rx0=max(left,x((w['ready_ms']-bar)/(tf*H))-.5*step)
    rx1=min(right,x((w['expires_ms']-bar)/(tf*H))-.5*step)
    if rx0<rx1:
        d.line((rx0,y(rail),rx1,y(rail)),fill=GOLD,width=3)
    for val,color in [(row.stop,STOP),(row.entry,BLUE),(target,TARGET)]:
        dashed(d,x(1)-step*.40,y(val),right,color)
    for i,v in bars:
        if v is not None: candle(d,x(i),y,*v,half=step*.28)
    label(d,(x(0),212),'信号 K',16,GOLD,True,'mm')
    en=float(x(1));py=y(row.entry)
    d.ellipse((en-5,py-5,en+5,py+5),fill=BLUE,outline=BG,width=1)
    exit_offset=int((outcome.exit_ms-bar)//(tf*H)) if pd.notna(outcome.exit_ms) else None
    exit_visible=exit_offset is not None and exit_offset in offsets
    if exit_visible:
        ex,ey=x(exit_offset),y(outcome.exit_price)
        color=TARGET if outcome.outcome=='tp' else STOP
        for dx in [-1,1]:d.line((ex-5,ey-dx*5,ex+5,ey+dx*5),fill=color,width=3)
    for i in [-20,-13,-6,1,8,12]:
        label(d,(x(i),518),when(bar+i*tf*H,'%m-%d'),16,MUTED,anchor='ma')
        label(d,(x(i),540),when(bar+i*tf*H,'%H:%M'),14,QUIET,anchor='ma')
    label(d,(75,562),'金色底为信号 K，蓝点为下一根开盘入场。'+('叉号为 1R 方案退出。' if exit_visible else '1R 方案在图窗之后退出。'),17,QUIET)

    label(d,(1183,160),'同一次外穿',27,INK,True)
    label(d,(1183,201),'三个周期对齐同一条 1.618',18,MUTED)
    compare=[]
    for h in (1,4,12):
        start=int(row.first_breach_ms)//(h*H)*h*H
        v=aggregate(raw,ts,start,h)
        assert v is not None
        compare.append((h,v))
    cy,_,_=ymap([v for _,bar_values in compare for v in bar_values]+[rail],255,443,.16)
    d.line((1184,cy(rail),1604,cy(rail)),fill=GOLD,width=2)
    for k,(h,v) in enumerate(compare):
        xx=1250+k*140
        candle(d,xx,cy,*v,half=18)
        label(d,(xx,463),f'{h}h',24,INK,True,'ma')
        qualifies=side*(v[0]-rail)>rail*1e-12 and side*(v[3]-rail)>rail*1e-12 and side*((v[2] if side==1 else v[1])-rail)<-rail*1e-12
        assert qualifies==(getattr(row,'state_'+str(h)+'h')=='wick')
        if h==tf:
            status,col='采用',GOLD
        elif qualifies:
            status,col='也成立',MUTED
        else:
            status,col='实体触线或越线',QUIET
        label(d,(xx,502),status,16,col,h==tf,'ma')
    label(d,(1183,558),'较大周期按各自收盘回看。',17,QUIET)
    context_panel(d,raw,ts,row,w)

    label(d,(1183,638),'信号价格',25,INK,True)
    rows=[('1.618',price(rail),GOLD),
          ('信号开 / 收',price(row.bar_open)+'  /  '+price(row.bar_close),INK),
          ('针尖 / 止损',price(row.tip)+'  /  '+price(row.stop),STOP),
          ('入场 / 1R',price(row.entry)+'  /  '+price(target),BLUE)]
    for j,(key,val,col) in enumerate(rows):
        yy=693+j*40
        label(d,(1183,yy),key,18,MUTED)
        label(d,(1604,yy-1),val,19,col,False,'ra')
    d.line((1183,850,1604,850),fill=GRID,width=1)
    result={'tp':'先到 1R','sl':'先止损','censored':'尚未触发'}[outcome.outcome]
    label(d,(1183,865),result,25,TARGET if outcome.outcome=='tp' else STOP,True)
    if pd.notna(outcome.exit_ms):
        unit='分钟' if outcome.resolution=='1m' else '小时'
        label(d,(1183,912),when(outcome.exit_ms)+f' 所在{unit}触发',17,MUTED)
    label(d,(52,980),'来源：币安合约成交 K 线   ·   首周按纽约时间   ·   图中时间为北京时间',17,QUIET)
    return image


def main():
    signals=pd.read_csv(EXP/'first_breaches.csv.gz').query('status=="entered"').set_index('event_id')
    ids=pick(signals)
    ranks=pd.read_csv(EXP/'outcomes.csv.gz').query('cohort=="signal" and target=="1R"').set_index('event_id')
    contracts=pd.read_csv(base.EXP/'contracts.csv').set_index('symbol')
    ends=pd.read_csv(base.REFRESH/'roster.csv').set_index('symbol').end_ms
    cfg=json.loads((base.EXP/'config.json').read_text())
    windows={w['month']:w for w in base.calendar(cfg)}
    cutoff=int(pd.Timestamp(cfg['data_cutoff_exclusive']).timestamp()*1000)
    sys.path.insert(0,str(base.PARENT))
    from census12 import read_raw
    OUT.mkdir(parents=True,exist_ok=True)
    paths=[]
    for n,key in enumerate(ids,1):
        r=signals.loc[key]
        raw=frozen_tape(r.symbol,contracts.loc[r.symbol],ends.at[r.symbol],cutoff,read_raw)
        im=draw_event(r,ranks.loc[key],raw,windows[r.month])
        path=OUT/f'{n:02d}_{int(r.tf)}h_{key.replace("/","_")}.png'
        im.save(path)
        paths.append(path)
        print(key,path.name,flush=True)
    with ZipFile(OUT/'wick-samples-7.zip','w',compression=ZIP_STORED) as z:
        for path in paths:z.write(path,path.name)


if __name__=='__main__':
    main()

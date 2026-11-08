"""Render every definite >10R chart reference from frozen native 12H OHLC.
Blue candles locate the key. Dates for crossing/peak refer to the candle open.
Outputs stay tied to the ledger, with an original-screenshot example in the ZIP.
"""
from io import BytesIO
import math
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from yoyo.evaluation.cloud_peak10_inputs import EXP, OHLC
from yoyo.monitor.cloud_signals import STEP, ema_points

FONT = '/System/Library/Fonts/STHeiti Light.ttc'
INK = '#203047'
GRAY = '#5e6b7c'
BLUE = '#1f61bf'
GREEN = '#087956'
RED = '#ad4252'


def font(size):
    return ImageFont.truetype(FONT, size)


def stamp(text):
    return int(pd.Timestamp(text).timestamp() * 1000)


def label(t):
    return pd.to_datetime(t,unit='ms',utc=True).tz_convert('Asia/Shanghai').strftime('%y-%m-%d')


def render(row, bars):
    key, confirmed = stamp(row.key_open_bjt), stamp(row.confirmed_bjt)
    first, peak = stamp(row.first_gt10_bjt), stamp(row.peak_time_bjt)
    end = stamp(row.path_end_bar_bjt) if pd.notna(row.path_end_bar_bjt) else int(bars.index[-1])
    data = bars.loc[key-16*STEP:end+2*STEP]
    start, last = data.index[0], data.index[-1]
    cols = ['high','low','ema20','ema50']
    ref, stop = row.reference_entry, row.reference_stop
    risk = abs(ref-stop)
    peak_price = ref + row.side*risk*row.peak_lower_r
    assert first in data.index and peak in data.index and first >= confirmed
    assert data.loc[peak,'low'] - risk*1e-7 <= peak_price <= data.loc[peak,'high']+risk*1e-7
    bottom, top = np.log(min(data[cols].min().min(),stop)), np.log(max(data[cols].max().max(),stop))
    margin = (top-bottom)*.08
    bottom, top = bottom-margin, top+margin
    image = Image.new('RGB', (1150,665), '#ffffff')
    d = ImageDraw.Draw(image)
    l,r,u,b = 64,1012,149,544
    def x(t): return round(l + (r-l)*(t-start)/(last-start))
    def y(v): return round(b - (b-u)*(math.log(v)-bottom)/(top-bottom))
    d.text((48,27), f'{row.symbol}   /   12H   /   '+('多头' if row.side==1 else '空头'),font=font(34),fill=INK)
    d.text((1100,25),f'峰值 {row.peak_lower_r:,.2f}R',font=font(33),fill=GREEN,anchor='ra')
    d.text((50,83),'关键 K 收盘确认   '+row.confirmed_bjt[:16].replace('T',' ')+'（北京时间）',font=font(19),fill=GRAY)
    d.text((1100,84),'OKX USDT 永续   ·   SPIKE 云带 V1.7',font=font(17),fill=GRAY,anchor='ra')
    for n in range(6):
        value=math.exp(bottom + (top-bottom)*n/5)
        yy=y(value)
        d.line((l,yy,r,yy),fill='#e3e7eb')
        d.text((r+13,yy),f'{value:,.7g}',font=font(15),fill=GRAY,anchor='lm')
    d.text((r+13,u-20),'USDT / 对数轴',font=font(15),fill=GRAY)
    for t in np.linspace(start,last,5):
        d.text((x(t),b+15),label(t),font=font(15),fill=GRAY,anchor='ma')
    rr = list(data.itertuples())
    for left,right in zip(rr,rr[1:]):
        if right.Index-left.Index != STEP: continue
        d.polygon([(x(left.Index),y(left.ema20)),(x(right.Index),y(right.ema20)),
                   (x(right.Index),y(right.ema50)),(x(left.Index),y(left.ema50))],
                  fill='#d5ece2' if left.ema20>left.ema50 else '#f0dce0')
    wick = max(1,min(6,int((r-l)*STEP/(last-start)*.32)))
    for candle in rr:
        xx=x(candle.Index)
        color=BLUE if candle.Index==key else ('#586a7e' if candle.close>=candle.open else '#263e53')
        d.line((xx,y(candle.high),xx,y(candle.low)),fill=color,width=1)
        y1,y2=sorted((y(candle.open),y(candle.close)))
        d.rectangle((xx-wick,y1,xx+wick,max(y2,y1+1)),fill=color)
    for value,color in [(ref,BLUE),(stop,RED)]:
        for xx in range(x(confirmed),x(end)+1,10):
            d.line((xx,y(value),min(xx+5,x(end)),y(value)),fill=color,width=2)
    xx=x(first)
    d.line((xx,u,xx,b),fill='#ad7614',width=1)
    d.polygon([(xx-6,u-5),(xx+6,u-5),(xx,u+3)],fill='#ad7614')
    xx,yy=x(peak),y(peak_price)
    d.ellipse((xx-5,yy-5,xx+5,yy+5),fill='white',outline=GREEN,width=3)
    d.text((50,595),f'蓝 K  关键K     蓝虚线  参考价 {ref:.7g}     红虚线  止损 {stop:.7g}',font=font(17),fill=INK)
    d.text((50,632),f'金线  首超 10R 所在 K  {label(first)}       绿圈  峰值所在 K  {label(peak)}',font=font(16),fill=GRAY)
    reason={'cloud_flip':'盘中云带反向','stop':'触及参考止损','open':'仍在观察'}
    d.text((1100,632),reason[row.exit_reason],font=font(16),fill=GRAY,anchor='ra')
    stream = BytesIO()
    image.save(stream,format='PNG')
    return stream.getvalue()


def main():
    signals = pd.read_csv(EXP/'signals.csv',float_precision='round_trip')
    hits = signals[signals.status=='hit']
    target = EXP/'逐笔12H图.zip'
    temp = target.with_suffix('.tmp')
    with zipfile.ZipFile(temp,'w') as bundle:
        for symbol, group in hits.groupby('symbol'):
            bars = pd.read_csv(EXP/'inputs'/symbol/'bars12h.csv.gz',index_col='time',float_precision='round_trip')
            ema = ema_points(bars[OHLC].reset_index().to_dict(orient='records'))
            bars[['ema20','ema50']] = pd.DataFrame(ema).set_index('time')[['ema20','ema50']]
            for row in group.itertuples():
                name = f'{symbol}/{row.confirmed_bjt[:13].replace("T","_")}_{row.peak_lower_r:.2f}R.png'
                bundle.writestr(name,render(row,bars))
        assert len(bundle.namelist())==len(hits)==len(set(bundle.namelist()))
    temp.replace(target)


if __name__=='__main__':
    main()

"""Review complete Binance H1 paths and highlight the actual 12h wick contacts."""
import argparse

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from census12 import read_raw
from chart12 import GRAY, GREEN, INK, MUTED, fmt, font
from scan import CUTOFF, ROOT, bounds

DEST = ROOT / 'output/month-first-week-pattern-review'
HOUR = pd.Timedelta(hours=1)
BJ = 'Asia/Shanghai'


def local(t):
    return t.tz_convert(BJ).strftime('%Y-%m-%d %H:%M')


def review(symbol, month, side):
    anchor, ready, expiry = bounds(month)
    raw = read_raw(symbol)
    raw = raw[raw[:, 0] + 3600000 <= CUTOFF]
    a = pd.DataFrame(raw[:, 1:], columns=['o','h','l','c'],
                     index=pd.to_datetime(raw[:, 0], unit='ms', utc=True))
    active = a.loc[anchor:expiry-HOUR]
    assert active.index.equals(pd.date_range(anchor, expiry, inclusive='left', freq='h'))
    high, low = active.loc[:ready-HOUR, 'h'].max(), active.loc[:ready-HOUR, 'l'].min()
    width = high-low
    rail = low-.618*width if side == '下轨' else high+.618*width
    window = active.loc[ready:]
    hits = window[(window.l <= rail) & (window.h >= rail)]
    contact = hits.groupby(hits.index.floor('12h')).head(1).index
    if not len(contact):
        raise ValueError('No intersection with this rail.')
    last = contact[-1]
    after = a.loc[last+HOUR:last+45*24*HOUR]
    reached = after.h >= rail*1.2 if side == '下轨' else after.l <= rail*.8
    goal = after.index[reached][0]
    assert after.loc[:goal].index.equals(pd.date_range(last+HOUR, goal, freq='h'))
    start = anchor.floor('12h') - 24*HOUR
    stop = min(max(expiry.ceil('12h'), goal.ceil('12h') + 4*24*HOUR), (a.index[-1]+HOUR).floor('12h'))
    chart_hours = a.loc[start:stop-HOUR]
    assert chart_hours.index.equals(pd.date_range(start, stop, inclusive='left', freq='h'))
    bars = chart_hours.resample('12h').agg({'o':'first','h':'max','l':'min','c':'last'})
    needles = bars.loc[contact.floor('12h')]
    inward = needles[['o','c']].min(axis=1)>rail if side == '下轨' else needles[['o','c']].max(axis=1)<rail
    assert inward.all()
    path = DEST / f'{symbol}_{month}_{"lower" if side == "下轨" else "upper"}.png'
    draw(path, symbol, month, side, bars, needles, high, low, rail, ready, expiry)
    tips = needles.l if side=='下轨' else needles.h
    return dict(symbol=symbol, month=month, side=side, rail=rail,
                touch_hours=[local(t) for t in contact], tips=tips.tolist(), closes=needles.c.tolist(),
                first_20_after_final=local(goal), hours_after_final=int((goal-last)/HOUR),
                chart=str(path))


def draw(path, symbol, month, side, bars, needles, high, low, rail, ready, expiry):
    image = Image.new('RGB', (1480,700), 'white')
    d = ImageDraw.Draw(image)
    left,right,top,bottom = 36,1360,100,611
    start,stop = bars.index[0],bars.index[-1]+20*HOUR
    floor,ceil = bars.l.min(),bars.h.max()
    pad = (ceil-floor)*.09
    floor,ceil = max(0,floor-pad),ceil+pad
    def x(t): return left+(right-left)*((t-start)/(stop-start))
    def y(p): return top+(ceil-p)/(ceil-floor)*(bottom-top)
    def center(t): return x(t+6*HOUR)
    d.text((left,17),f'{symbol} 永续合约  ·  12小时  ·  Binance',font=font(29),fill=INK)
    d.text((left,62),f'{month}   月首周 1.618',font=font(19),fill=MUTED)
    d.text((right+12,28),'USDT',font=font(16),fill=MUTED)
    d.rectangle((x(ready),y(high),x(expiry),y(low)),fill='#f5f5f6')
    for p in np.linspace(floor,ceil,7)[1:-1]:
        for xx in range(left,right,8): d.line((xx,y(p),xx+2,y(p)),fill='#d8dadd')
        d.text((right+12,y(p)-9),fmt(p),font=font(16),fill=MUTED)
    ticks = pd.date_range(start,stop,freq=f'{max(2,round((stop-start).days/8))}D')
    for t in ticks:
        xx = center(t)
        for yy in range(top,bottom,8): d.line((xx,yy,xx,yy+2),fill='#dedfe1')
        d.text((xx-20,bottom+11),t.tz_convert(BJ).strftime('%m/%d'),font=font(16),fill=MUTED)
    for label,p in [('上 1.618',high+.618*(high-low)),('首周高',high),
                    ('首周低',low),('下 1.618',low-.618*(high-low))]:
        if floor<p<ceil:
            col = INK if '1.618' in label else '#84888c'
            d.line((x(ready),y(p),x(expiry),y(p)),fill=col,width=2)
            text = f'{label}  {fmt(p)}'
            d.text((x(expiry)-d.textlength(text,font=font(15))-6,y(p)-24),text,font=font(15),fill=col)
    half = max(2,round((x(start+12*HOUR)-x(start))*.32))
    for t,k in bars.iterrows():
        end = t+12*HOUR
        s,r,_ = bounds(end.tz_convert('America/New_York').strftime('%Y-%m'))
        col = GREEN if t<=s<end or (t>=s and end<=r) else GRAY
        xx = center(t)
        d.line((xx,y(k.h),xx,y(k.l)),fill=col,width=2)
        a,b = sorted([y(k.o),y(k.c)])
        d.rectangle((xx-half,a,xx+half,max(b,a+1)),fill=col)
    for t,k in needles.iterrows():
        tip = k.l if side=='下轨' else k.h
        y0,y1 = sorted([y(rail),y(tip)])
        xx = center(t)
        d.rectangle((xx-half-4,y0-5,xx+half+4,y1+5),outline='#c84740',width=2)
    d.text((left,655),f'红框为触及{side}的影线   ·   绿 K 为月首周   ·   横轴为北京时间',font=font(16),fill=MUTED)
    path.parent.mkdir(parents=True,exist_ok=True)
    image.save(path)


if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('symbol')
    p.add_argument('month')
    p.add_argument('side',choices=['上轨','下轨'])
    args = p.parse_args()
    print(review(args.symbol,args.month,args.side))

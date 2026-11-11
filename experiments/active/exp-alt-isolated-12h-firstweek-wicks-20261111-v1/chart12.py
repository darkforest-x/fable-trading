"""Chart audited 12-hour cases using actual Binance candles and their own NY levels."""
import argparse
import math

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from census12 import HOUR, OUT, read_raw
from scan import bounds

GRAY='#9297a1'
GREEN='#31c85d'
INK='#343a41'
MUTED='#666e78'


def stamp(ms):
    return pd.Timestamp(ms, unit='ms', tz='UTC').tz_convert('Asia/Shanghai')


def fmt(p):
    places=max(1,4-int(math.floor(math.log10(p))))
    return f'{p:.{places}f}'


def font(sz):
    return ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',sz)


def chart(row):
    raw=read_raw(row.symbol)
    anchor, ready, end=bounds(row.month)
    start=int(anchor.timestamp()*1000)//(12*HOUR)*12*HOUR
    stop=row.open_ms+12*HOUR+7*24*HOUR
    rows=raw[(raw[:,0]>=start)&(raw[:,0]<stop)]
    bars=[]
    for t in np.unique(rows[:,0].astype('int64')//(12*HOUR)):
        z=rows[rows[:,0].astype('int64')//(12*HOUR)==t]
        if len(z)==12:
            bars.append((t*12*HOUR,z[0,1],z[:,2].max(),z[:,3].min(),z[-1,4]))
    bars=np.array(bars)
    w,h=1440,760
    im=Image.new('RGB',(w,h),'white')
    d=ImageDraw.Draw(im)
    a,b,top,bot=44,w-122,105,h-86
    low=np.min(bars[:,3]);hi=np.max(bars[:,2])
    spread=hi-low
    lo=low-.07*spread; high=hi+.07*spread
    x=lambda t: a+(b-a)*((t-start)/(stop-start+12*HOUR))
    y=lambda p: top+(high-p)/(high-lo)*(bot-top)
    cn='下轨' if row.side=='lower' else '上轨'
    d.text((a,17),f'{row.symbol}  ·  12小时  ·  Binance USDT 永续',font=font(27),fill=INK)
    d.text((a,60),f'{row.bar_open_Beijing} 开盘（北京）    {cn}插针收回    影线占 {row.wick_pct:.1f}%    收盘后7天最大反向幅度 {row.mfe7:.1f}%',font=font(18),fill=MUTED)
    def dashed(x1,x2,yy,color):
        for xx in range(int(x1),int(x2),8):d.line((xx,yy,min(xx+2,x2),yy),fill=color)
    for p in np.linspace(lo,high,7):
        yy=y(p);dashed(a,b,yy,'#e1e4e7');d.text((b+12,yy-10),fmt(p),font=font(16),fill=MUTED)
    H,L=row.week_high,row.week_low
    rail_end=min(b,x(int(end.timestamp()*1000)))
    rail_start=max(a,x(int(ready.timestamp()*1000)))
    for label,p,color in [('首周高',H,'#9a9da2'),('首周低',L,'#9a9da2'),('上 1.618',H+.618*(H-L),INK),('下 1.618',L-.618*(H-L),INK)]:
        if lo<=p<=high:
            yy=y(p);d.line((rail_start,yy,rail_end,yy),fill=color,width=2)
            labelstr=f'{label}  {fmt(p)}'
            f=font(14); box=d.textbbox((0,0),labelstr,font=f)
            yylabel=yy-24;xx=max(a,rail_end-(box[2]-box[0])-6)
            d.rectangle((xx-3,yylabel,rail_end,yy-3),fill='white')
            d.text((xx,yylabel),labelstr,font=f,fill=color)
    half=max(2,int((x(start+12*HOUR)-x(start))*.32))
    for t,o,hp,lp,c in bars:
        xx=x(t); color=GREEN if anchor.timestamp()*1000<=t<ready.timestamp()*1000 else GRAY
        d.line((xx,y(lp),xx,y(hp)),fill=color,width=2)
        topb,bottom=sorted([y(o),y(c)])
        d.rectangle((xx-half,topb,xx+half,max(topb+2,bottom)),fill=color)
        if int(t)==row.open_ms:
            # Box the price segment where the wick actually crossed the rail.
            tip=lp if row.side=='lower' else hp
            y0,y1=sorted([y(tip),y(row.level)])
            d.rectangle((xx-half-5,y0-5,xx+half+5,y1+5),outline='#c74d48',width=2)
    xx=start
    while xx<stop:
        label=stamp(xx).strftime('%m-%d');d.text((x(xx)-18,bot+12),label,font=font(15),fill=MUTED)
        xx+=max(3,round((stop-start)/(24*HOUR)/9))*24*HOUR
    d.text((a,h-37),'月首周 1.618  ·  绿K为纽约月首周  ·  红框为针尖触轨位置',font=font(16),fill=MUTED)
    folder=OUT/'charts';folder.mkdir(exist_ok=True)
    path=folder/(row.symbol+'_'+row.bar_open_Beijing[:10]+'.png')
    im.save(path)
    return path


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('cases',nargs='+');args=p.parse_args()
    frame=pd.read_csv(OUT/'isolated_12h.csv')
    for case in args.cases:
        symbol,date=case.split('/')
        event=frame[(frame.symbol==symbol)&(frame.bar_open_Beijing.str.startswith(date))]
        if len(event)!=1:raise ValueError(case)
        print(chart(next(event.itertuples())))

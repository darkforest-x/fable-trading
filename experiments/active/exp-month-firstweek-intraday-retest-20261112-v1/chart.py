"""Plot the two confirmation decisions on the same timeline for the owner's ETC example."""
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from review import HOUR, OUT, ROOT, load_bars, summarize_bars
from chart12 import font, fmt


def main():
    symbol='ETCUSDT'
    t=1635321600000
    data=pd.read_csv(OUT/'events.csv.gz')
    cases=data[(data.touch_ms==t)&(data.symbol==symbol)].set_index('minutes')
    assert set(cases.index)=={5,15} and set(cases.state)=={'held','failed'}
    raw=load_bars(symbol,'2021-10')
    start=t
    end=t+7*HOUR
    after=t+HOUR
    bars=raw[(raw[:,0]>=start)&(raw[:,0]<end)]
    assert len(bars)==84
    im=Image.new('RGB',(1480,815),'white')
    d=ImageDraw.Draw(im)
    L,R=65,1344
    def x(ms):
        return L+(R-L)*(ms-start)/(end-start)
    def txt(pos,msg,size=18,color='#525b64',**kw):
        d.text(pos,msg,font=font(size),fill=color,**kw)
    txt((L,14),'ETCUSDT 永续合约  ·  同一次下轨插针后的两种确认',30,'#252b31')
    txt((L,62),'币安  ·  2021/10/27 北京时间  ·  17:00 的 1H 收盘首次给出做多观察方向',19)
    txt((L,104),'灰色区域为方向 K 线收盘以前    黑色水平线为小级别突破价    红框为第一次回踩    菱形为下一根开盘',16)
    for tf,top in [(5,182),(15,515)]:
        row=cases.loc[tf]
        z=bars if tf==5 else summarize_bars(bars,3)
        # Focus both panels on the post-signal price range so the two retests remain readable.
        post=bars[bars[:,0]>=after]
        low,high=post[:,3].min(),post[:,2].max()
        span=high-low
        low-=span*.10
        high+=span*.10
        bottom=top+223
        def y(price):
            return bottom-(price-low)/(high-low)*(bottom-top)
        d.rectangle((L,top,x(after),bottom),fill='#f3f4f5')
        for price in np.linspace(low,high,5):
            yy=y(price)
            for left in range(L,R,9):
                d.line((left,yy,left+2,yy),fill='#e1e3e6')
            txt((R+8,yy),fmt(price),15,anchor='lm')
        step=tf*60000
        half=(R-L)*step/(end-start)*.32
        for ms,o,h,l,c in z:
            if ms<after:
                continue
            xx=x(ms+step/2)
            d.line((xx,y(l),xx,y(h)),fill='#878d97',width=2)
            a,b=sorted((y(o),y(c)))
            d.rectangle((xx-half,a,xx+half,max(b,a+1)),fill='#9297a1')
        level=y(row.pivot_price)
        d.line((x(row.pivot_known_ms),level,x(row.read_ms),level),fill='#343a40',width=2)
        k=z[z[:,0]==row.read_ms-step][0]
        xx=x(k[0]+step/2)
        d.rectangle((xx-half-3,y(k[2])-4,xx+half+3,y(k[3])+4),outline='#bd4a47',width=2)
        xx,yy=x(row.read_ms+step/2),y(row.ref_open)
        d.line([(xx,yy-6),(xx+6,yy),(xx,yy+6),(xx-6,yy),(xx,yy-6)],fill='#375e77',width=2)
        word='守住' if row.state=='held' else '失守'
        clock=pd.Timestamp(row.read_ms,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%H:%M')
        txt((L,top-34),f'{tf} 分钟    突破价 {fmt(row.pivot_price)}    {clock} 确认{word}',20,'#252b31')
        for ms in range(start,end+1,HOUR):
            label=pd.Timestamp(ms,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%H:%M')
            txt((x(ms),bottom+8),label,15,anchor='ma')
    dest=ROOT/'output/month-firstweek-1h-wicks-20261112/ETCUSDT-20211027-5m-15m.png'
    dest.parent.mkdir(parents=True,exist_ok=True)
    im.save(dest)
    print(dest)


if __name__=='__main__':
    main()

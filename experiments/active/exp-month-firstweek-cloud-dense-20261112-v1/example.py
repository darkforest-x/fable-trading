"""Show the same ETC alert as the prior review, irrespective of its outcome."""
import sys
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from observe import DATA,HERE,M,F,resample,ew,avg

def draw():
    eid='ETCUSDT|2021-10-27'; m=pd.read_csv(DATA/'manifest.csv.gz').set_index('id').loc[eid]
    a=np.load(DATA/'tapes'/f'{eid}.npy'); b=resample(a)
    events=pd.concat([pd.read_csv(DATA/f'{x}_events.csv.gz') for x in ('base','grade')])
    ev=events[events.id.eq(eid)].set_index('arm')
    known=int(m.known_ms); start=known-120*M; end=known+24*60*M
    clock=lambda ms:pd.Timestamp(ms,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%H:%M')
    im=Image.new('RGB',(1600,1010),'white'); d=ImageDraw.Draw(im)
    def text(x,y,t,size=17,color='#444b55',anchor=None):
        d.text((x,y),t,font=ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',size),fill=color,anchor=anchor)
    L,R=67,1490; x=lambda stamp: L+(R-L)*(stamp-start)/(end-start)
    text(L,12,'ETCUSDT 永续  ·  1H 方向之后，云带与六线何时响应',31,'#263240')
    text(L,64,'币安   2021-10-27 至 10-28，均为北京时间   |   17:00 下轨插针收回，观察做多   |   灰色底为方向尚未确定',18)
    text(L,104,'云带：绿色 EMA20 在 EMA50 上方，红色在下方     六线：20、60、120 周期的 SMA / EMA     竖色标：对应信号首次收盘确认',16)
    for idx,(bars,tf,label,keys) in enumerate([(a,5,'5 分钟云带',['cloud5_flip']),(b,15,'15 分钟云带',['cloud15_flip']),(a,5,'5 分钟六均线',['break5','launch5'])]):
        step=tf*M; ts=bars[:,0]+step; selected=np.flatnonzero((ts>start)&(ts<=end))
        px=bars[selected]; top=185+idx*264; bottom=top+184
        c=bars[:,4]
        if idx<2: lines=[ew(c,2/21),ew(c,2/51)]
        else: lines=[avg(c,n) if typ=='s' else ew(c,2/(n+1)) for n in (20,60,120) for typ in ('s','e')]
        bounds=np.concatenate([px[:,2],px[:,3],*[v[selected] for v in lines]])
        lo,hi=bounds.min(),bounds.max(); pad=.07*(hi-lo); lo-=pad; hi+=pad
        y=lambda v:bottom-(v-lo)/(hi-lo)*(bottom-top)
        d.rectangle((L,top,x(known),bottom),fill='#f3f4f5')
        for v in np.linspace(lo,hi,4):
            yy=y(v); d.line((L,yy,R,yy),fill='#e4e6e8'); text(R+12,yy,f'{v:.2f}',14,anchor='lm')
        if lo<=m.rail<=hi:
            d.line((L,y(m.rail),R,y(m.rail)),fill='#797d83'); text(R-4,y(m.rail)-1,f'下 1.618  {m.rail:.3f}',13,anchor='rs')
        if idx<2:
            fast,slow=lines
            for j,k in zip(selected[:-1],selected[1:]):
                x0,x1=x(ts[j]),x(ts[k])
                av,bv=fast[k],slow[k]
                col='#d6e9e2' if av>bv else '#f2dcdf'
                d.polygon([(x0,y(fast[j])),(x1,y(av)),(x1,y(bv)),(x0,y(slow[j]))],fill=col)
        candle='#8b919a'; half=max(1,(R-L)*step/(end-start)*.30)
        for j in selected:
            x0=x(ts[j]-step//2); o,h,l,cl=bars[j,1:]; d.line((x0,y(h),x0,y(l)),fill=candle)
            yy=sorted((y(o),y(cl))); d.rectangle((x0-half,yy[0],x0+half,max(yy[1],yy[0]+1)),fill=candle)
        if idx==2:
            for v,col in zip(lines,['#709ea7','#366a76','#a99866','#846c32','#ae98bd','#8e6fa2']):
                d.line([(x(ts[j]),y(v[j])) for j in selected],fill=col,width=1)
        d.line((x(known),top,x(known),bottom),fill='#737b86',width=1)
        captions=[]
        for key,col,short in zip(keys,['#2f779e','#a66539'],(['云带翻多'] if idx<2 else ['突破六线','五根确认'])):
            s=ev.at[key,'read_ms']
            if pd.isna(s): captions.append(short+'：24h 未出现'); continue
            xx=x(s); d.line((xx,top,xx,bottom),fill=col,width=2)
            captions.append(short+'：'+clock(s))
        text(L,top-34,label+'     '+ '   /   '.join(captions),20,'#293746')
        for stamp in range(known-120*M,end+1,120*M):
            text(x(stamp),bottom+6,clock(stamp),13,anchor='ma')
    text(L,966,'每种信号都等 K 线收盘才标记。不同周期的均线独立计算；标记用来复盘，不表示实际成交。',15)
    dest=HERE.parents[2]/'output/month-firstweek-cloud-dense-20261112/ETC-5m-15m.png'
    dest.parent.mkdir(exist_ok=True,parents=True); im.save(dest)
    print(dest)
if __name__=='__main__': draw()

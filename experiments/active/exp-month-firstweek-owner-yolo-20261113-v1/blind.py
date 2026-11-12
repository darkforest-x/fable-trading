"""Model-independent, causal visual reference for one explicitly defined launch.
Enumerate every first close strictly outside all six lines in the expected direction
after four complete post-H1 candles, with previous close not outside its own six
lines. Show only prices through that close. Reviewers see no model, identity,
group, future return or labelled owner-provided small-timeframe core. Yellow area
is four bars immediately before breakout; their entire bundle is judged visually.
"""
from hashlib import sha256
from PIL import Image,ImageDraw,ImageFont
import numpy as np
import pandas as pd
from windows import DATA,OUT,MINUTE,cohort,frame,window_end,MA_COLUMNS,ew

COLORS=['#2186a1','#61b5c5','#986914','#cc9c45','#774297','#b680c8']

def font(n):
    return ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',n)

def card(bars,i,key,side):
    lo_i=i-16; z=bars.iloc[lo_i:i+1]
    ma=z[MA_COLUMNS].to_numpy();c=z.close.to_numpy();o=z.open.to_numpy()
    h=z.high.to_numpy();l=z.low.to_numpy()
    atr=bars.atr.iat[i-1]; minp=min(l.min(),ma.min());maxp=max(h.max(),ma.max())
    pad=.13*max(maxp-minp,atr)
    lower=minp-pad;upper=maxp+pad
    canvas=Image.new('RGB',(534,255),'white');d=ImageDraw.Draw(canvas)
    x=lambda j:19+j*29; y=lambda p:219-(p-lower)/(upper-lower)*172
    d.text((12,3),f'{key}     '+('向上' if side=='long' else '向下'),fill='#29323c',font=font(19))
    d.rectangle((x(12)-12,43,x(15)+12,221),fill='#fff2cf')
    for j in range(len(z)):
        a=x(j); d.line((a,y(h[j]),a,y(l[j])), fill='#96999a',width=1)
        by=sorted([y(o[j]),y(c[j])]);d.rectangle((a-5,by[0],a+5,max(by[1],by[0]+1)),fill='#a8aaad')
    for k,color in enumerate(COLORS):
        d.line([(x(j),y(ma[j,k])) for j in range(len(z))],fill=color,width=2)
    height=172*atr/(upper-lower)
    d.line((511,188-height,511,188),fill='#434b55',width=2)
    for yy in (188-height,188): d.line((506,yy,516,yy),fill='#434b55')
    d.text((483,196),'1ATR',font=font(12),fill='#434b55')
    d.text((12,228),'蓝 20     棕 60     紫 120       黄底为四根核心候选',font=font(14),fill='#444c55')
    return canvas

def main():
    review=OUT/'blind';review.mkdir(parents=True,exist_ok=True)
    refs=[]
    for r in cohort().itertuples():
        for tf in (5,15):
            b=frame(r,tf); step=tf*MINUTE; t=b.open_ms.to_numpy(dtype='int64')
            d=1 if r.side=='long' else -1
            c=b.close.to_numpy()
            ph=np.r_[c[0],c[:-1]]
            tr=np.maximum(b.high-b.low,np.maximum(abs(b.high-ph),abs(b.low-ph)))
            b['atr']=np.r_[np.full(13,np.nan),ew(np.r_[tr[:14].mean(),tr.iloc[14:]],1/14)]
            outside=d*c>(d*b[MA_COLUMNS].to_numpy()).max(axis=1)
            hits=np.flatnonzero(outside & ~np.r_[False,outside[:-1]] &
                 (t-4*step>=r.known_ms) & (t+step<=window_end(r)))
            for i in hits:
                key=sha256(f'owndense1618|{r.case_id}|{tf}|{t[i]}'.encode()).hexdigest()[:8]
                chart=card(b,i,key,r.side)
                refs.append((dict(key=key,case_id=r.case_id,pair_id=r.pair_id,role=r.role,tf=tf,
                         side=r.side,read_ms=t[i]+step,core_start_ms=t[i-4],core_end_ms=t[i-1]),chart))
    refs.sort(key=lambda item:item[0]['key'])
    for j in range(0,len(refs),9):
        sheet=Image.new('RGB',(1602,765),'#f7f7f7')
        for k,(_,im) in enumerate(refs[j:j+9]):sheet.paste(im,((k%3)*534,(k//3)*255))
        sheet.save(review/f'sheet{j//9+1:02}.png')
    pd.DataFrame([r for r,_ in refs]).to_csv(DATA/'blind_key.csv',index=False)
    print('blind candidate charts',len(refs),flush=True)
if __name__=='__main__':main()

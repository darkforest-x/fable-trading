"""Render all Sep16 touch events from the persisted, reviewed OHLCV context."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.font_manager import FontProperties
from yoyo.evaluation.monday_sep16_review import EXP, ROOT, DAY, DAY_END, MON, END, STEP, bj
FONT=FontProperties(fname='/System/Library/Fonts/STHeiti Light.ttc')
plt.rcParams['font.family']=FONT.get_name();plt.rcParams['axes.unicode_minus']=False


def render(e,f,view):
    t=pd.Timestamp(e['first_touch_utc'])
    if view=='detail':z=f.loc[t-pd.Timedelta(hours=1):t+pd.Timedelta(hours=3)];minutes=5
    else:z=f.loc[MON:END-STEP].resample('1h').agg(dict(open='first',high='max',low='min',close='last',volume='sum')).dropna();minutes=60
    fig,(ax,av)=plt.subplots(2,1,figsize=(14,7),sharex=True,gridspec_kw={'height_ratios':[4,1]},layout='constrained')
    fig.patch.set_facecolor('#f8fafb');ax.set_facecolor('#ffffff');av.set_facecolor('#ffffff')
    for i,(_,b) in enumerate(z.iterrows()):
        color='#169c81' if b.close>=b.open else '#d74662'
        ax.vlines(i,b.low,b.high,color=color,lw=.7)
        ax.add_patch(Rectangle((i-.32,min(b.open,b.close)),.64,max(abs(b.close-b.open),float(z.close.median())*.000002),color=color,lw=.4))
        av.bar(i,b.volume,color=color,width=.65,alpha=.72)
    levels=[('level',e['direction']+' 1.618','#d74662' if e['side']==-1 else '#169c81'),('monday_high','周一高','#3c7fc4'),('monday_low','周一低','#3c7fc4')]
    low=float(z.low.min());high=float(z.high.max());gap=max(high-low,high*.001)
    for field,label,color in levels:
        price=e[field]
        if view=='full' or low-gap*.2<price<high+gap*.2:
            ax.axhline(price,color=color,lw=.9,alpha=.9,label=f'{label} {price:.8g}')
            if view=='detail':ax.text(.008,price,f' {label} {price:.8g}',transform=ax.get_yaxis_transform(),va='bottom',fontsize=9,color=color)
            low=min(low,price);high=max(high,price)
    if view=='full':ax.legend(loc='upper left',fontsize=9,framealpha=.9)
    pi=z.index.get_indexer([t.floor(f'{minutes}min')])[0]
    if pi>=0:
        ax.scatter([pi],[e['level']],color='#c78d29',s=50,zorder=8,edgecolor='white')
        ax.annotate('当日首次触价 '+bj(t)[11:],(pi,e['level']),xytext=(12,35),textcoords='offset points',arrowprops={'arrowstyle':'->','color':'#9d6c1e'},fontsize=10,color='#795421')
        ax.axvline(pi,ls=':',lw=.8,color='#be8b35')
    if view=='full':
        start=int(z.index.searchsorted(DAY));stop=int(z.index.searchsorted(DAY_END))
        ax.axvspan(start-.5,stop-.5,color='#93b9db',alpha=.14);av.axvspan(start-.5,stop-.5,color='#93b9db',alpha=.14)
        after=int(z.index.searchsorted(DAY_END));ax.axvspan(after-.5,len(z)-.5,color='#b6c0c8',alpha=.09)
        touched=(f.low<=e['level'])&(f.high>=e['level'])&(f.index>=DAY)&(f.index<DAY_END)
        xs=sorted(set(z.index.get_indexer(f.index[touched].floor('1h'))));xs=[i for i in xs if i>=0]
        ax.scatter(xs,[e['level']]*len(xs),s=12,color='#bf8b2e',zorder=6)
    ax.text(.5,.53,e['symbol'],transform=ax.transAxes,fontsize=56,weight='bold',color='#24475e',alpha=.075,ha='center',va='center')
    ax.set_ylim(low-(high-low)*.1,high+(high-low)*.13);ax.set_xlim(-1,len(z))
    ticks=np.unique(np.linspace(0,len(z)-1,8).astype(int));av.set_xticks(ticks,[bj(z.index[i])[5:] for i in ticks],fontsize=8)
    ax.grid(alpha=.12);av.grid(axis='y',alpha=.1);ax.spines[['top','right']].set_visible(False);av.spines[['top','right']].set_visible(False)
    ax.tick_params(axis='y',labelsize=9);av.tick_params(axis='y',labelsize=8);av.set_ylabel('成交量',fontsize=9)
    title=f"{e['symbol']} · {e['direction']} 1.618 · {'5分钟触价局部' if view=='detail' else '1小时后续全程'}"
    ax.set_title(title,loc='left',fontsize=17,pad=17)
    fig.supxlabel('北京时间｜蓝底：9月16日；灰底：其后行情｜金点：当日触价｜线位按纽约9月14日周一冻结｜非成交回测',fontsize=9)
    file=EXP/'images'/f"{e['event_id']}-{view}.png";fig.savefig(file,dpi=110);plt.close(fig)


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:raise RuntimeError('Commit renderer first')
    events=pd.read_csv(EXP/'events.csv').to_dict('records');done=0
    for symbol,rows in pd.DataFrame(events).groupby('symbol'):
        f=pd.read_csv(EXP/'contexts'/f'{symbol}.csv.gz',parse_dates=['time_utc']).set_index('time_utc')
        for e in rows.to_dict('records'):
            for v in ['detail','full']:
                if (v=='full' and '--refresh-full' in sys.argv) or not (EXP/'images'/f"{e['event_id']}-{v}.png").exists():render(e,f,v)
            done+=1
        if done%20==0:print(f'Rendered {done}/{len(events)} events',flush=True)
    print(f'Finished: {len(events)*2} charts',flush=True)


if __name__=='__main__':main()

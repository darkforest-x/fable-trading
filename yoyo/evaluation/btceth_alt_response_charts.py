"""Source-bound5m reaction figures, not a report or strategy performance chart.

Zero is the touch-containing5m candle CLOSE; its touch lies in [-5,0] minutes,
not an observed tick timestamp. Upward means movement against the initial
spike for both sides. Context prices after zero are explicitly retrospective.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btceth_alt_response_source import ROOT,EXP,STEP
from yoyo.evaluation.btc_1618_early_marks_charts import setup,BG,FG,MUTED,GOLD
from yoyo.evaluation.eth_reclaim_trade_charts import candles,decorate,bounds

OUT=ROOT/'artifacts/btceth-5m-alt-response-20261101'
BLUE='#65b8ff';GREEN='#5dd5b1';RED='#fa7585'

def run():
    import matplotlib.pyplot as plt
    setup();OUT.mkdir(exist_ok=True)
    events=pd.read_csv(EXP/'events.csv');paths=pd.read_csv(EXP/'paths.csv')
    fig,axes=plt.subplots(2,2,figsize=(18,11),dpi=150)
    fig.subplots_adjust(left=.075,right=.97,bottom=.14,top=.85,hspace=.40,wspace=.18)
    fig.suptitle('BTC / ETH触及5分钟1.618时，山寨怎样反应？',fontsize=24,y=.965)
    fig.text(.075,.91,'9—10月固定20币池 · 山寨为其余18币等权 · 只观察价格路径，不是交易收益',fontsize=14,color=MUTED)
    for ax,(leader,side) in zip(axes.ravel(),[('BTC',1),('BTC',-1),('ETH',1),('ETH',-1)]):
        p=paths[paths.leader.eq(leader)&paths.side.eq(side)]
        complete=p.groupby('event_id')[['alt_mean','control','leader_return']].apply(lambda g:len(g)==25 and g.notna().all().all())
        ids=complete[complete].index;shown=p[p.event_id.isin(ids)]
        if shown.empty:
            shown=p;note='无完整匹配对照'
        else:note=f'完整配对{len(ids)} / 总触线{p.event_id.nunique()}'
        a=shown.groupby('offset')[['alt_mean','leader_return','control']].mean()
        for col,color,label,ls in [('alt_mean',BLUE,'山寨等权均值','-'),('leader_return',GOLD,leader,'-'),('control',MUTED,'相似背景非触线时刻','--')]:
            if a[col].notna().any():ax.plot(a.index,a[col],color=color,label=label,lw=2,ls=ls)
        ax.axvspan(-5,0,color=GOLD,alpha=.12);ax.axvline(0,color=FG,ls=':',lw=.8);ax.axhline(0,color=MUTED,lw=.6)
        ax.set_title(f'{leader} {"下刺" if side==1 else "上刺"} · {note}',fontsize=16,loc='left')
        ax.set_xlim(-60,60);ax.grid(alpha=.12);ax.set_ylabel('反向涨跌幅（%）',fontsize=12)
        ax.set_xlabel('距触价K收盘的分钟数',fontsize=12);ax.legend(fontsize=10,loc='best')
    fig.text(.075,.065,'基准=触价K前一根收盘；下刺时向上代表上涨，上刺时向上代表下跌。灰带是触价所在5分钟，无法判断该K内先后。',fontsize=12,color=MUTED)
    fig.text(.075,.032,'对照匹配同一周、触价前30分钟涨跌幅和波动；排除BTC/ETH冲击前后65分钟。样本相关、观察期短，均值不等于可交易优势。',fontsize=11,color=MUTED)
    fig.savefig(OUT/'反应总览.png',dpi=150);plt.close(fig)
    values={r['event_id']:r for r in json.loads((EXP/'path_values.json').read_text())};chosen=[]
    # Examples selected by chronology and event subtype, never subsequent PnL.
    for leader in ['BTC','ETH']:
        for side in [1,-1]:
            g=events[events.leader.eq(leader)&events.side.eq(side)&events.kind.eq('pure_wick_reclaim')]
            if len(g):chosen.append(g.sort_values('time').iloc[0])
    records=[]
    for r in chosen:
        leader=r.leader;side=int(r.side);t=pd.Timestamp(r.time);f=pd.read_csv(EXP/'frozen'/f'{leader}.csv.gz',index_col=0,parse_dates=True)
        start=t-pd.Timedelta(hours=1);end=t+pd.Timedelta(minutes=65);frame=f[(f.index>=start)&(f.index<end)]
        val=values[r.event_id];arr=np.array(val['values']);alts=[i for i,s in enumerate(val['symbols']) if s!=leader]
        fig=plt.figure(figsize=(18,12),dpi=150);fig.text(.065,.95,f'{leader} {"下插针" if side==1 else "上插针"} · {t.tz_convert("Asia/Shanghai"):%Y-%m-%d %H:%M} 北京时间',fontsize=24)
        fig.text(.065,.915,'该方向首个当根收回样本，按时间选取 · 上：主币5m K线；下：18个山寨的同步反应',fontsize=14,color=MUTED)
        ax=fig.add_axes([.065,.61,.87,.25]);candles(ax,frame,STEP,t);bounds(ax,frame,[r.level]);decorate(ax,start,end,True)
        ax.axhline(r.level,color=GOLD,lw=1.2);ax.text(.99,.95,f'5m 1.618 = {r.level:,.2f}',transform=ax.transAxes,ha='right',va='top',fontsize=14,color=GOLD)
        from yoyo.evaluation.eth_reclaim_trade_charts import dn
        ax.axvspan(dn(t),dn(t+STEP),color=GOLD,alpha=.15)
        line=fig.add_axes([.065,.32,.87,.20]);offset=np.arange(-12,13)*5
        for i in alts:line.plot(offset,arr[i],color=BLUE,alpha=.14,lw=.8)
        line.plot(offset,np.nanmedian(arr[alts],axis=0),color=BLUE,lw=2.5,label='山寨中位数')
        line.plot(offset,arr[val['symbols'].index(leader)],color=GOLD,lw=2,label=leader)
        line.axvspan(-5,0,color=GOLD,alpha=.10);line.axhline(0,color=MUTED,lw=.6);line.axvline(0,color=FG,ls=':',lw=.8)
        line.set_xlim(-60,60);line.set_ylabel('反向涨跌幅（%）');line.grid(alpha=.1);line.legend(loc='best',fontsize=11)
        heat=fig.add_axes([.065,.09,.87,.16]);selected=[-1,0,1,3,6,12];matrix=arr[alts][:,[j+12 for j in selected]].T
        vmax=max(.1,float(np.nanquantile(np.abs(matrix),.95)))
        im=heat.imshow(matrix,aspect='auto',cmap='RdBu',vmin=-vmax,vmax=vmax)
        heat.set_xticks(range(len(alts)),[val['symbols'][i] for i in alts],fontsize=10)
        heat.set_yticks(range(6),['前1根','触价K','后5m','后15m','后30m','后60m'],fontsize=10)
        for y in range(6):
            for x in range(len(alts)):
                if np.isfinite(matrix[y,x]):heat.text(x,y,f'{matrix[y,x]:+.1f}',ha='center',va='center',fontsize=8,color='black' if abs(matrix[y,x])<vmax*.55 else 'white')
        fig.text(.065,.035,'价格均以触价前一根收盘为基准；正值=反向恢复。0为触价K收盘，非精确触价秒数；之后均为事后观察。',fontsize=11,color=MUTED)
        path=OUT/f'{leader}_{"lower" if side==1 else "upper"}_first_reclaim.png';fig.savefig(path,dpi=150);plt.close(fig)
        records.append(dict(event_id=r.event_id,image=str(path)))
    (OUT/'examples.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    print('Rendered',1+len(records),'figures',flush=True)

if __name__=='__main__':
    assert subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode==0
    run()

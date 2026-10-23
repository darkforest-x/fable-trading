"""Render frozen sampled OHLC as standalone scientific PNG figures.

These are retrospective illustrations, not causal model inputs. Signals use
original timeframe; exact sub5m exit second is unavailable and shown as a band.
No chart sampling, resampling, parameter change or backtest is performed here.
"""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.font_manager import FontProperties
from datetime import datetime,timezone,timedelta

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/monday-cases-report-20261023'
FONT=FontProperties(fname='/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
plt.rcParams.update({'font.family':FONT.get_name(),'font.size':10,'axes.unicode_minus':False,'savefig.facecolor':'white'})
C={'up':'#079b80','down':'#dc4560','line':'#29354a','grid':'#e8ecf1','blue':'#2463cd','amber':'#b6770d','purple':'#8260a5'}


def ts(value):return datetime.fromisoformat(value).timestamp()*1000

def bjt(value):return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')


def draw(sample,mode):
    bars=sample[f'{mode}_bars'];n=len(bars);tf=sample['timeframe_minutes'];duration=tf*60000
    fig=plt.figure(figsize=(14,7),dpi=125);grid=fig.add_gridspec(5,1,hspace=.03);ax=fig.add_subplot(grid[:4]);vol=fig.add_subplot(grid[4],sharex=ax)
    fig.subplots_adjust(left=.075,right=.78,top=.84,bottom=.14)
    def xpos(value):
        t=ts(value)
        # Compress closed-market gaps on the chart, preserve true timestamp labels.
        for i,b in enumerate(bars):
            if b[0]<=t<b[0]+duration:return i+(t-b[0])/duration-.5
        return -1 if t<bars[0][0] else n
    values=[v for b in bars for v in [b[2],b[3]]]+[sample[k] for k in ['stop','target','entry','level','monday_high','monday_low']]
    low=min(values);high=max(values);pad=(high-low)*.12 or high*.01
    ax.set_ylim(low-pad,high+pad);ax.set_xlim(-1,n)
    p=xpos(sample['pin_time'])+.5;e=xpos(sample['entry_time'])+.5
    ax.axvspan(p-.5,p+.5,color='#e5b858',alpha=.16,zorder=0)
    for j in range(1,2 if sample['case']==1 else 4):ax.axvspan(p+j-.5,p+j+.5,color='#77a7e9',alpha=.12,zorder=0)
    for i,b in enumerate(bars):
        _,o,h,l,c,v=b;color=C['up'] if c>=o else C['down']
        ax.vlines(i,l,h,color=color,linewidth=.9,zorder=3)
        ax.add_patch(Rectangle((i-.3,min(o,c)),.6,max(abs(c-o),(high-low)*.001),facecolor=color,edgecolor=color,linewidth=.6,zorder=4))
        if v is not None:vol.bar(i,v,width=.65,color=color,alpha=.64)
    levels=[('周一高',sample['monday_high'],'#9ba7b5',':'),('周一低',sample['monday_low'],'#9ba7b5',':'),('1.618',sample['level'],C['purple'],'-'),('原插针止损',sample['stop'],C['down'],'--'),('周一边缘止盈',sample['target'],C['blue'],'--'),('实际入场',sample['entry'],C['line'],'-.')]
    for label,v,color,style in levels:ax.axhline(v,color=color,linestyle=style,linewidth=1,alpha=.8)
    for j,(label,v,color,style) in enumerate(levels):fig.text(.80,.79-j*.057,f'{label}\n{v:,.9g}',color=color,fontsize=10,va='top')
    ax.text(.5,.52,f"{sample['symbol']}  ·  {tf}m",transform=ax.transAxes,ha='center',va='center',fontsize=39,color='#253e58',alpha=.07,weight='bold',zorder=1)
    if 0<=e<n:
        ax.scatter([e],[sample['entry']],marker='^' if sample['side']==1 else 'v',s=65,color=C['line'],zorder=7)
        ax.axvline(e,color=C['line'],linestyle=':',lw=.8)
    exitx=xpos(sample['exit_interval_start'])+.5
    if 0<=exitx<n:
        ax.scatter([exitx],[sample['exit_price']],marker='X',s=65,color=C['amber'],zorder=8)
        ax.axvspan(exitx-.5,exitx-.5+5/tf,color=C['amber'],alpha=.18,zorder=2)
    for x,label in [(p,'插针'),(e,'入场')]+[(p+j,'吞没' if sample['case']==1 else f'确认{j}') for j in range(1,2 if sample['case']==1 else 4)]:
        if 0<=x<n:ax.text(x,high+pad*(.45 if label in ['插针','入场'] else .12),label,ha='center',fontsize=8,color=C['line'],rotation=45 if n>90 else 0)
    indices=sorted(set([0,n-1]+[round(i*(n-1)/5) for i in range(1,5)]));vol.set_xticks(indices);vol.set_xticklabels([datetime.fromtimestamp(bars[i][0]/1000,timezone(timedelta(hours=8))).strftime('%m/%d\n%H:%M') for i in indices],fontsize=9)
    plt.setp(ax.get_xticklabels(),visible=False)
    ax.grid(axis='y',color=C['grid'],lw=.7);ax.set_ylabel('价格',fontsize=10)
    for axis in [ax,vol]:
        for side in ['top','right']:axis.spines[side].set_visible(False)
        for side in ['left','bottom']:axis.spines[side].set_color('#d7dee7')
        axis.tick_params(colors='#526175',labelsize=9)
    if sample['symbol']=='XAUUSD':vol.set_yticks([]);vol.text(.01,.55,'现货历史无有效成交量；不绘制量柱',transform=vol.transAxes,color='#6b7789',fontsize=9)
    else:vol.set_ylabel('原币量',fontsize=9);vol.ticklabel_format(axis='y',style='sci',scilimits=(0,0));vol.yaxis.get_offset_text().set_fontsize(8)
    ret=sample['gross_r'] if sample['net_r'] is None else sample['net_r'];basis='毛' if sample['net_r'] is None else '净'
    fig.suptitle(f"情况{sample['case']} · {sample['symbol']} · {tf}m · 做{sample['direction']} · {sample['outcome']} {ret:+.3f}R（{basis}）",x=.075,ha='left',y=.965,fontsize=16,weight='bold')
    fig.text(.075,.91,f"{'入场局部' if mode=='detail' else '持仓全程'}｜{sample['sample_id']}｜北京时间 UTC+8｜{n} 根原周期K线；休市/缺口按已观测K线压缩",fontsize=10,color='#526175')
    fig.text(.075,.065,f"插针开盘 {bjt(sample['pin_time'])}   入场 {bjt(sample['entry_time'])}   退出区间 {bjt(sample['exit_interval_start'])} — {bjt(sample['exit_interval_end'])}",fontsize=9,color='#526175')
    fig.text(.075,.035,'▲/▼ 入场  × 实际退出  黄底=插针/退出5m区间  蓝底=确认；退出区间内具体秒数未知。历史复盘图含入场后的行情。',fontsize=9,color='#526175')
    path=OUT/'charts'/f"{sample['sample_id']}-{mode}.png";path.parent.mkdir(exist_ok=True);fig.savefig(path,dpi=125);plt.close(fig)
    return str(path.relative_to(OUT))


def main():
    samples=json.loads((OUT/'samples.json').read_text());manifest=[]
    for sample in samples:
        manifest.append({'sample_id':sample['sample_id'],'detail':draw(sample,'detail'),'full':draw(sample,'full')})
    (OUT/'charts-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(f'{len(manifest)*2} charts rendered')

if __name__=='__main__':main()

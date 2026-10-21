"""Render standalone diagnostic bars from the frozen two-hour MFE tables.

No new selection or calculation of trade outcomes. Blue denotes original
signals, gray denotes their matched backgrounds; shared axes preserve scale.
"""
import json
import subprocess
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from yoyo.evaluation.range_twohour_mfe import DEST, ROOT, read


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit chart builder before rendering')
    cfg=json.loads((DEST/'config.json').read_text())
    font=FontProperties(fname='/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    plt.rcParams.update({'font.family':font.get_name(),'axes.unicode_minus':False,'font.size':11,
        'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.edgecolor':'#D6DBE3'})
    h=read(DEST/'histogram.csv')
    fig,axes=plt.subplots(2,2,figsize=(15,9),sharey=True)
    for row,spec in enumerate(cfg['symbols']):
        coin=spec['symbol']
        for col,measure in enumerate(['mfe_r','full_2h_mfe_r_ignoring_stop']):
            ax=axes[row,col]
            for pop,offset,color,name in [('signal',-.18,'#2675D8','原规则信号'),('random',.18,'#AAB4C2','匹配随机')]:
                data=h[(h.symbol==coin)&(h.population==pop)&(h.measure==measure)]
                x=np.arange(len(data))
                ax.bar(x+offset,data.percent,.34,color=color,label=name)
                if pop=='signal':
                    for xx,v,n in zip(x+offset,data.percent,data.n):
                        ax.text(xx,v+.55,str(n),ha='center',fontsize=9,color='#245C99')
            ax.set_xticks(x,data['bin'],rotation=20)
            ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)
            ax.set_ylabel('占全部信号比例（%）')
            title='止损前或 2 小时内' if col==0 else '完整 2 小时 · 忽略止损'
            ax.set_title(f'{coin} · n={spec["n"]}  |  '+title,loc='left',pad=12,fontweight='bold')
            if row==0 and col==0:ax.legend(frameon=False)
    axes[0,0].set_ylim(0,max(h.percent.max()+7,40))
    fig.suptitle('V2.31 原规则｜2 小时最大浮盈 R 分布',fontsize=21,x=.055,ha='left',y=.985)
    fig.text(.055,.936,'2021-10-21 至 2026-10-21  ·  BTC 485 笔 / ETH 385 笔  ·  柱上数字为信号笔数',color='#596575')
    fig.text(.055,.025,'左列：保留原始影线止损；保守剔除止损触发的整根 5m K 线。右列：含止损后的走势，不代表可兑现利润。\n1R = 入场价到初始止损的距离；浮盈未扣费；不使用局部高低点出场。',fontsize=10,color='#596575')
    fig.tight_layout(rect=[.025,.075,.99,.91])
    fig.savefig(DEST/'mfe_histogram.png',dpi=180,facecolor='white');plt.close(fig)
    reach=read(DEST/'target_reach.csv')
    fig,axes=plt.subplots(1,2,figsize=(14,4.8),sharey=True)
    for ax,spec in zip(axes,cfg['symbols']):
        for pop,offset,color,name in [('signal',-.18,'#2675D8','原规则信号'),('random',.18,'#AAB4C2','匹配随机')]:
            data=reach[(reach.symbol==spec['symbol'])&(reach.population==pop)]
            x=np.arange(len(data));values=data.hit_rate*100
            ax.bar(x+offset,values,.34,color=color,label=name)
            for xx,v in zip(x+offset,values):ax.text(xx,v+.6,f'{v:.1f}%',ha='center',fontsize=10)
        ax.set_xticks(x,[f'{t}R' for t in cfg['targets']]);ax.set_ylabel('初始止损前、2h 内先触目标比例（%）')
        ax.set_title(f'{spec["symbol"]} · n={spec["n"]}',loc='left',fontweight='bold');ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)
    axes[0].legend(frameon=False);axes[0].set_ylim(0,max(reach.hit_rate)*100+9)
    fig.suptitle('目标越远，2 小时内能先兑现的笔数越少',fontsize=19,x=.06,ha='left')
    fig.text(.06,.015,'NautilusTrader 1.231.0 原生订单；5m O-H-L-C；各目标单独执行；毛目标触及率，不是扣费后的胜率。',fontsize=10,color='#596575')
    fig.tight_layout(rect=[.02,.06,.99,.93]);fig.savefig(DEST/'target_reach.png',dpi=180,facecolor='white');plt.close(fig)
    print('Charts exported to '+str(DEST),flush=True)


if __name__=='__main__':main()

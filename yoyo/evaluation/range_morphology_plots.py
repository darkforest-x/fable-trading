"""Static research figures for frozen candle morphology diagnostics.

Raw scatter: every annual trade, key-candle geometry versus equal-notional
net basis points, no clipping or fitted predictive model. Contrast figure:
all prespecified conditions, BTC and ETH separately, week-bootstrap95percent
win-rate-difference intervals. These are association figures, not a dashboard.
"""
import argparse
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np

from yoyo.evaluation.range_morphology import CONFIG,ROOT,read,join_outcomes

FONT=FontProperties(fname='/System/Library/Fonts/Supplemental/Arial Unicode.ttf')


def raw_figure(cfg,dest):
    fields=[('wick_share','触价影线 / 全K振幅'),('wick_atr','触价影线 / 此前ATR14'),('penetration','越线深度 / 触价影线')]
    fig,axes=plt.subplots(2,3,figsize=(15,8.5),squeeze=False)
    for row,coin in enumerate(['BTC','ETH']):
        d=join_outcomes(read(dest/f'{coin}_features.csv'),read(ROOT/cfg['source']/coin/'pivot/trades.csv'))
        for ax,(field,label) in zip(axes[row],fields):
            for win,color,marker in [(0,'#9098A3','x'),(1,'#176BBA','o')]:
                part=d[d.net_win==win]
                ax.scatter(part[field],part.out_net_notional_bp,s=26,alpha=.72,c=color,marker=marker,
                    label=('净盈利' if win else '净亏损')+f' n={len(part)}')
            ax.axhline(0,c='#323A43',lw=.8);ax.grid(axis='y',alpha=.13)
            ax.set_xlabel(label,fontproperties=FONT);ax.set_ylabel(f'{coin} 每笔净收益（bp）',fontproperties=FONT)
            ax.spines[['top','right']].set_visible(False)
        axes[row,0].legend(prop=FONT,frameon=False,fontsize=9)
    fig.suptitle('关键K线形态与逐笔净收益：每个点是一笔已发生的交易',fontproperties=FONT,fontsize=17,y=.98)
    fig.text(.5,.925,'15m全部关键位入场 · 原15m分型退出 · 扣0.2%费用 · 同名义金额口径，1bp=0.01%',ha='center',fontproperties=FONT,fontsize=10,color='#58626E')
    fig.tight_layout(rect=[0,.02,1,.9]);fig.savefig(dest/'raw_shape_returns.png',dpi=180,facecolor='white');plt.close(fig)


def contrast_figure(cfg,dest):
    summary=json.loads((dest/'summary.json').read_text());cuts=cfg['hypotheses'];n=len(cuts)
    fig,axes=plt.subplots(1,2,figsize=(16,11),sharey=True)
    for ax,coin,color in zip(axes,['BTC','ETH'],['#176BBA','#C18D21']):
        records={r['cut']:r for r in summary['associations'] if r['symbol']==coin}
        for i,cut in enumerate(cuts):
            r=records[cut['id']];center=r['delta_win']
            if center is None: continue
            low,high=r['bootstrap95'];y=n-1-i
            ax.plot([100*low,100*high],[y,y],c=color,alpha=.55,lw=2)
            ax.scatter([100*center],[y],s=42,c=color,marker='o' if r['inferable'] else 'x',zorder=3)
            ax.text(1.02,y,f"{r['yes']['wins']}/{r['yes']['n']} vs {r['no']['wins']}/{r['no']['n']}",
                    transform=ax.get_yaxis_transform(),va='center',ha='left',fontsize=8,color='#58626E')
        ax.set_xlim(-85,85);ax.set_xticks([-75,-50,-25,0,25,50,75]);ax.axvline(0,c='#323A43',lw=1)
        ax.grid(axis='x',alpha=.12);ax.spines[['top','right','left']].set_visible(False)
        ax.set_title(coin+'：满足条件 − 不满足条件',fontproperties=FONT,fontsize=14,pad=12)
        ax.set_xlabel('净胜率差（百分点）',fontproperties=FONT);ax.tick_params(axis='y',length=0)
    axes[0].set_yticks(np.arange(n));axes[0].set_yticklabels([c['label'] for c in cuts[::-1]],fontproperties=FONT,fontsize=10)
    fig.suptitle('形态是否改变胜率：同时看效应大小、样本量和不确定性',fontproperties=FONT,fontsize=17,y=.97)
    fig.text(.5,.925,'点为观察差异；横线为按周重采样95%区间（未做多重比较校正）。右侧为盈利笔数/总笔数；×表示检验样本不足。',ha='center',fontproperties=FONT,fontsize=10,color='#58626E')
    fig.tight_layout(rect=[0,.04,1,.90]);fig.savefig(dest/'shape_winrate_contrasts.png',dpi=180,facecolor='white');plt.close(fig)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--raw-only',action='store_true');args=parser.parse_args()
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    raw_figure(cfg,dest)
    if not args.raw_only: contrast_figure(cfg,dest)
    print(str(dest))


if __name__=='__main__': main()

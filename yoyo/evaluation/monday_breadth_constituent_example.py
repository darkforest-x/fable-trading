"""Plot per-coin outcomes for the owner's September 16 example, from saved labels."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[2]/'experiments/active/exp-monday-breadth-constituent-six-day-20261108-v1'


def main():
    d = pd.read_csv(OUT/'5m_13_case_members.csv')
    all_case = d[d.entry_bjt.str.startswith('2026-09-16 03:20')]
    assert len(all_case)==68
    g = all_case[all_case.eligible].dropna(subset=['net_pct','peak_pct']).sort_values('net_pct').reset_index(drop=True)
    if not len(g):
        raise ValueError('No observable constituents')
    font = Path('/System/Library/Fonts/Hiragino Sans GB.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus'] = False
    fig,ax=plt.subplots(figsize=(10.8,4.8))
    fig.subplots_adjust(left=.08,right=.98,bottom=.21,top=.81)
    ax.vlines(g.index,g.net_pct,g.peak_pct,color='#a6bbc2',lw=1.05,alpha=.8)
    ax.scatter(g.index,g.peak_pct,color='#0d8176',s=22,label='六天内最高浮盈（毛）',zorder=4)
    ax.scatter(g.index,g.net_pct,color='#314878',s=20,label='第六天到期收益（扣费）',zorder=4)
    ax.axhline(0,lw=1,c='#8c514a')
    ax.set_ylabel('相对入场价（%）')
    ax.set_xlabel(f'一列一个币，按到期收益从低到高排列（{len(g)} 个有完整行情）')
    picks=np.unique(np.round(np.linspace(0,len(g)-1,min(7,len(g)))).astype(int))
    ax.set_xticks(picks,labels=[g.loc[i,'symbol'].removesuffix('USDT') for i in picks],fontsize=8)
    ax.spines[['right','top']].set_visible(False)
    ax.grid(axis='y',alpha=.14)
    ax.legend(loc='upper left',frameon=False)
    fig.text(.08,.92,'9 月 16 日的 68 币信号：六天里涨到过哪里，最终留下多少',size=17,weight='bold')
    fig.text(.08,.853,'北京时间 2026-09-16 02:50 收盘触发，03:20 确认无新增后买入。每个币的最高点可能出现在不同时间。',size=10,color='#465664')
    fig.savefig(OUT/'5m_sept16_constituents.png',dpi=160,facecolor='white')
    plt.close(fig)


if __name__=='__main__':
    main()

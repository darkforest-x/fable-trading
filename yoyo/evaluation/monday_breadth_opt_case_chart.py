"""Plot the requested September BTC exits and realized versus potential constituent returns."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt,font_manager,dates
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation.monday_breadth_opt_payoffs import load_full_ohlc


def run():
    out=study.OUT
    adv=pd.read_csv(out/'sept16_btc_funding_aware_rules.csv',parse_dates=['entry_time','first_exit_time','exit_time'])
    r=adv[adv.rule.eq('btc_funded_partial_144_6_0_0_0_0.75')].iloc[0]
    quotes,_=load_full_ohlc('BTCUSDT',r.entry_time,r.exit_time,repair_missing=False)
    funded=pd.read_parquet(out/'sept16_original_funded.parquet')
    alt=funded[(funded.policy_id==5)&(~funded.asset.isin(['BTCUSDT','ETHUSDT','SOLUSDT']))].sort_values('funded_pct')
    assert len(alt)==68 and alt.funded_pct.notna().all()
    b=quotes.tz_convert('Asia/Shanghai')
    buy=r.entry_time.tz_convert('Asia/Shanghai');tp=r.first_exit_time.tz_convert('Asia/Shanghai');last=r.exit_time.tz_convert('Asia/Shanghai')
    remainder=quotes.loc[r.exit_time,'open']
    sale=(r.exit_vwap-.25*remainder)/.75
    font=Path('/System/Library/Fonts/Hiragino Sans GB.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,'font.size':10})
    fig,(top,basket,bottom)=plt.subplots(3,1,figsize=(9.8,9.9),layout='constrained',height_ratios=[1.2,.75,1])
    top.plot(b.index,b.close,color='#264b64',lw=1.05)
    for t,price,caption,offset in [(buy,r.entry_open,'入场  76,127.8',(6,12)),(tp,sale,'卖出 75%  ·  86,325',(-124,18)),
                                  (last,remainder,'卖出余下 25%  ·  86,280',(-110,-40))]:
        top.scatter(t,price,c='#a74324',s=30,zorder=3)
        top.annotate(caption,(t,price),xytext=offset,textcoords='offset points',fontsize=9,
                     arrowprops=dict(arrowstyle='-',color='#a74324'))
    top.set_title('9 月 16 日的 BTC 分批退出拿到 10,025 净点',loc='left',fontsize=15,pad=13,weight='bold')
    top.set_ylabel('BTC / USDT');top.margins(x=.035,y=.19);top.grid(axis='y',alpha=.17)
    top.xaxis.set_major_locator(dates.DayLocator(tz=buy.tz))
    top.xaxis.set_major_formatter(dates.DateFormatter('%m/%d',tz=buy.tz))
    top.set_xlabel('北京时间  ·  已扣开平仓手续费及实际资金费')
    curve=pd.read_csv(out/'basket_trail_sep16_curve.csv.gz',parse_dates=['close_time_utc']).set_index('close_time_utc')
    curve=curve.tz_convert('Asia/Shanghai').synchronized_equal_weight_basket_close_return_pct
    basket.plot(curve.index,curve.to_numpy(),color='#147662',linewidth=1.1)
    peak=curve.idxmax()
    basket.scatter([peak],[curve.max()],s=24,color='#a74324',zorder=3)
    basket.annotate(f'同一时刻最高  +{curve.max():.2f}%',(peak,curve.max()),
                    xytext=(-132,2),textcoords='offset points',fontsize=9,arrowprops=dict(arrowstyle='-',color='#a74324'))
    basket.set_title('68 个币同时等额买入后，整个篮子如何变化',loc='left',fontsize=14,pad=9,weight='bold')
    basket.set_ylabel('篮子毛浮盈（%）');basket.margins(x=.035,y=.17);basket.grid(axis='y',alpha=.17)
    basket.xaxis.set_major_locator(dates.DayLocator(tz=buy.tz))
    basket.xaxis.set_major_formatter(dates.DateFormatter('%m/%d',tz=buy.tz))
    basket.set_xlabel(f'同一根 5 分钟线收盘计价  ·  第六天实际退出扣手续费和资金费后  +{alt.funded_pct.mean():.2f}%')
    xx=np.arange(len(alt))
    bottom.vlines(xx,alt.funded_pct,alt.peak_pct,color='#ccd6d7',lw=1.2)
    bottom.scatter(xx,alt.peak_pct,s=15,color='#788c9a',label=f'六天内各币最高浮盈，平均 {alt.peak_pct.mean():.2f}%')
    bottom.scatter(xx,alt.funded_pct,s=16,color='#147662',label=f'持到第六天的净收益，平均 {alt.funded_pct.mean():.2f}%')
    bottom.axhline(0,color='#554c46',lw=.7);bottom.grid(axis='y',alpha=.16);bottom.set_xlim(-1,68)
    bottom.set_title('逐币看，哪些山寨保住了六天里的涨幅',loc='left',fontsize=14,pad=9,weight='bold')
    bottom.set_ylabel('相对开仓价（%）');bottom.set_xlabel('每一列是一个币，按六天净收益从小到大排列')
    picks=np.linspace(0,67,7).astype(int);bottom.set_xticks(picks,[alt.asset.iloc[i][:-4] for i in picks],fontsize=8)
    bottom.legend(loc='upper left',frameon=False,fontsize=9)
    fig.savefig(out/'9月16日_实际退出与山寨浮盈.png',dpi=165,facecolor='white')
    plt.close(fig)


if __name__=='__main__':run()

"""Render two historical Freqtrade V6 trade reviews from frozen OHLCV only."""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent; R=ROOT/'results'; D=ROOT/'data/futures'
CASES=[('full_both_240m_baseline',True,'v6_4h_winner.png'),('full_both_60m_baseline',False,'v6_1h_loss.png')]
for run,winner,out in CASES:
    t=pd.read_csv(R/f'{run}_trades.csv.gz',parse_dates=['open_date','close_date'])
    row=t.loc[t.profit_ratio.idxmax() if winner else t.profit_ratio.idxmin()]
    tf='4h' if '240m' in run else '1h'; bars=pd.read_feather(D/f'ETH_USDT_USDT-{tf}-futures.feather')
    bars['date']=pd.to_datetime(bars.date,utc=True); start=row.open_date-pd.Timedelta(hours=100*int(tf[:-1])); end=row.close_date+pd.Timedelta(hours=72*int(tf[:-1])); x=bars[(bars.date>=start)&(bars.date<=end)]
    fig,ax=plt.subplots(figsize=(13,5)); ax.plot(x.date,x.close,color='#334155',lw=.9,label='frozen close')
    ax.axvline(row.open_date,color='#16a34a',label='actual next-open'); ax.axvline(row.close_date,color='#dc2626',label='actual exit'); ax.axhline(row.initial_stop_loss_abs,color='#f59e0b',ls='--',label='initial stop'); ax.axhline(row.stop_loss_abs,color='#a855f7',ls=':',label='effective protection'); ax.axvspan(row.close_date,end,color='#dbeafe',alpha=.45,label='post-exit review')
    ax.set_title(f'Historical review only — {tf} {"winner" if winner else "loss"}: signal→next-open→actual exit'); ax.legend(ncol=3,fontsize=8); fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(R/out,dpi=160); plt.close(fig)

"""Review 2026-08-19 Beijing against frozen NY Monday1.618 levels.

Descriptive OHLC review and projection of completed VectorBT fills only; no
new execution, optimization, thresholds or macro-causal claims. Candles through
Aug20 show later confirmation/failure explicitly, never as Aug19-known input.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_5m_wick_engulf as case1
from yoyo.evaluation import monday_15m_volume_rejection as case2
from yoyo.evaluation import monday_1h_wick_hold as case3
from yoyo.evaluation import monday_4h_close_hold as case4
from yoyo.evaluation.monday_5m_wick_gold_clock import read_minutes
from yoyo.evaluation.monday_5m_wick_gold import clean_minutes,invalid_monday_weeks
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp

EXP=ROOT/'experiments/active/exp-monday-aug19-review-20261024-v1'
CACHE=ROOT/'experiments/active/exp-monday-limit-timed-vectorbt-20261024-v1'
DATE='2026-08-19'
MAJORS=['BTCUSDT','ETHUSDT','BNBUSDT','DOGEUSDT']


def bj(t):return str(pd.Timestamp(t).tz_convert('Asia/Shanghai'))


def price_review(symbol,coverage):
    identity=json.loads((CACHE/'symbols'/symbol/'identity.json').read_text())
    sources=[s for s in identity['inputs'] if s['path'].endswith('.csv') and '_1m_' not in s['path']]
    for s in sources:assert file_stamp(s['path'])==s
    f=case2.load([s['path'] for s in sources]).loc['2026-08-16':'2026-08-21']
    ny=f.index.tz_convert('America/New_York');mon=f[ny.strftime('%Y-%m-%d')=='2026-08-17']
    assert len(mon)==288 and mon['count'].eq(5).all()
    hi=float(mon.high.max());lo=float(mon.low.min());upper=hi+.618*(hi-lo);lower=lo-.618*(hi-lo)
    day=f[f.index.tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')==DATE]
    assert len(day)==288 and np.all(np.diff(day.index.asi8)==300_000_000_000)
    c=coverage[(coverage.symbol==symbol)&(coverage.week=='2026-08-17')&(coverage.side==-1)].iloc[0]
    assert np.isclose(c.level,upper) and np.isclose(c.monday_high,hi) and np.isclose(c.monday_low,lo)
    t=pd.Timestamp(c.entry_time).floor('5min');j=f.index.get_loc(t)
    pin5=f.loc[t];next5=f.iloc[j+1]
    traces=[];rules={}
    rules['case1_5m_next_engulf']=bool(case1.signal_mask(f,upper,-1).iloc[j+1])
    for label,mod,period in [('case2_15m',case2,'15min'),('case3_1h',case3,'1h'),('case4_4h',case4,'4h')]:
        h=mod.aggregate(f);start=t.floor(period);a=h.index.get_loc(start)
        masks=mod.pattern_mask(h,pd.Series(upper,index=h.index),-1)
        pin=bool(masks[0].iloc[a]);confirm=bool(masks[2 if mod is case2 else 1].iloc[a])
        rules[label]=dict(pin=pin,confirmation=confirm,qualified=pin and confirm,
            pin_open=bj(start),decision_time=bj(start+pd.Timedelta(period)*4))
        if mod is case2:
            peak=bool(masks[1].iloc[a]);rules[label].update(volume_peak=peak,qualified=pin and confirm and peak,
                pin_volume=float(h.volume.iloc[a]),prior20_max=float(masks[3].iloc[a]))
        if mod is case3:
            subsequent=h.iloc[a+1:a+4]
            body_confirm=bool(len(subsequent)==3 and subsequent.bars.eq(12).all() and subsequent.minutes.eq(60).all()
                and subsequent[['open','close']].max(axis=1).le(upper).all())
            rules['case3_body_only']=dict(pin=pin,confirmation=body_confirm,qualified=pin and body_confirm)
        for k in range(4):
            bar=h.iloc[a+k]
            traces.append(dict(symbol=symbol,period=period,offset=k,open_time_bj=bj(h.index[a+k]),
                close_time_bj=bj(h.index[a+k]+pd.Timedelta(period)),upper=upper,
                **{col:float(bar[col]) for col in case1.OHLC}))
    result=dict(symbol=symbol,monday_high=hi,monday_low=lo,upper=upper,lower=lower,
        first_upper_interval_bj=bj(c.entry_time),resolution_minutes=int(c.entry_resolution_minutes),
        day_open=float(day.open.iloc[0]),day_high=float(day.high.max()),day_low=float(day.low.min()),day_close=float(day.close.iloc[-1]),
        high_time_bj=bj(day.high.idxmax()),low_time_bj=bj(day.low.idxmin()),day_change_pct=float((day.close.iloc[-1]/day.open.iloc[0]-1)*100),
        upper_touch_bars=int(day.high.ge(upper).sum()),upper_outside_closes=int(day.close.gt(upper).sum()),
        lower_touch_bars=int(day.low.le(lower).sum()),first5m={col:float(pin5[col]) for col in case1.OHLC},
        next5m={col:float(next5[col]) for col in case1.OHLC},rules=rules,sources=sources)
    return f,result,traces


def plot(frames,prices):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as md
    from matplotlib.patches import Rectangle
    from matplotlib.font_manager import FontProperties
    from zoneinfo import ZoneInfo
    font=FontProperties(fname='/System/Library/Fonts/STHeiti Light.ttc')
    plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,1,figsize=(16,10),layout='constrained')
    for ax,symbol in zip(axes,['BTCUSDT','ETHUSDT']):
        p=prices[symbol];f=frames[symbol].loc['2026-08-18 16:00':'2026-08-19 19:55']
        h=f.resample('15min').agg(dict(open='first',high='max',low='min',close='last')).dropna()
        for t,b in h.iterrows():
            x=md.date2num(t.to_pydatetime());color='#119977' if b.close>=b.open else '#d94664'
            ax.vlines(x,b.low,b.high,color=color,lw=.7)
            ax.add_patch(Rectangle((x-.0037,min(b.open,b.close)),.0074,max(abs(b.close-b.open),p['day_open']*.00002),facecolor=color,edgecolor=color,lw=.4))
        for key,label,color in [('upper','上沿1.618','#d94664'),('monday_high','周一高','#407daf'),('monday_low','周一低','#407daf'),('lower','下沿1.618','#119977')]:
            ax.axhline(p[key],color=color,lw=.9,alpha=.8)
            ax.text(.008,p[key],f' {label}  {p[key]:,.2f}',transform=ax.get_yaxis_transform(),fontproperties=font,fontsize=9,color=color,va='bottom')
        first=pd.Timestamp(p['first_upper_interval_bj']);ax.scatter([first],[p['upper']],color='#d94664',s=36,zorder=5)
        ax.annotate('首次触价 '+first.strftime('%H:%M'),xy=(first,p['upper']),xytext=(-100,42),textcoords='offset points',
            fontproperties=font,fontsize=10,arrowprops=dict(arrowstyle='->',color='#444'))
        midnight=pd.Timestamp('2026-08-20',tz='Asia/Shanghai');ax.axvspan(midnight,pd.Timestamp('2026-08-20 04:00',tz='Asia/Shanghai'),color='#8091a5',alpha=.09)
        ax.set_title(f'{symbol} · 15分钟K线 · 8月19日涨幅 {p["day_change_pct"]:+.2f}%',fontproperties=font,fontsize=14,loc='left')
        ymin=min(p['lower'],float(h.low.min()));ymax=max(p['upper'],float(h.high.max()));span=ymax-ymin
        ax.set_ylim(ymin-.04*span,ymax+.08*span)
        ax.set_xlim(pd.Timestamp('2026-08-19',tz='Asia/Shanghai'),pd.Timestamp('2026-08-20 04:00',tz='Asia/Shanghai'))
        ax.xaxis.set_major_locator(md.HourLocator(byhour=[0,4,8,12,16,20],tz=ZoneInfo('Asia/Shanghai')))
        ax.xaxis.set_major_formatter(md.DateFormatter('%m-%d %H:%M',tz=ZoneInfo('Asia/Shanghai')))
        ax.grid(alpha=.12);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('8.19 与周一1.618：短暂回落之后，上沿被突破',fontproperties=font,fontsize=18)
    fig.supxlabel('北京时间；灰底为8月20日后续行情。Binance永续；周一锚点按纽约时间冻结。',fontproperties=font,fontsize=10)
    fig.savefig(EXP/'btc-eth-aug19.png',dpi=160);plt.close(fig)


def main():
    warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:raise RuntimeError('Commit builder first')
    EXP.mkdir(exist_ok=True)
    c=pd.read_csv(CACHE/'entry-coverage.csv.gz',low_memory=False)
    ts=pd.to_datetime(c.entry_time,utc=True).dt.tz_convert('Asia/Shanghai')
    day=c[ts.dt.strftime('%Y-%m-%d').eq(DATE)].copy();day['entry_time_beijing']=ts.loc[day.index].astype(str)
    day.to_csv(EXP/'first-touches.csv',index=False)
    parts=[]
    for symbol,g in day.groupby('symbol'):
        f=pd.read_csv(CACHE/'symbols'/symbol/'trades.csv.gz',float_precision='round_trip')
        parts.append(f[f.pair_id.isin(g.pair_id)])
    cached=pd.concat(parts,ignore_index=True);cached.to_csv(EXP/'cached-outcomes.csv.gz',index=False)
    grid=cached[cached.scenario.eq('grid')];metrics=[]
    for group,x in [('all',grid),('altcoins',grid[~grid.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])]),('upper_short',grid[grid.side.eq(-1)]),('lower_long',grid[grid.side.eq(1)])]:
        for horizon,z in x.groupby('horizon'):
            a=z[z.population.eq('signal')].set_index('pair_id');b=z[z.population.eq('random')]
            controls=b.groupby('pair_id').net_bp.agg(['mean','count']);a=a.join(controls[controls['count'].eq(3)],how='left')
            bounds=cached[cached.parent_id.isin(a.parent_id)&cached.horizon.eq(horizon)].groupby('scenario').net_bp.apply(lambda v:int(v.gt(0).sum()))
            metrics.append(dict(group=group,horizon=int(horizon),n=len(a),wins=int(a.net_bp.gt(0).sum()),win_rate=float(a.net_bp.gt(0).mean()),mean_net_bp=float(a.net_bp.mean()),
                paired=int(a['mean'].notna().sum()),paired_signal_bp=float(a.loc[a['mean'].notna(),'net_bp'].mean()),control_bp=float(a['mean'].mean()),
                lower_wins=int(bounds['lower']),upper_wins=int(bounds['upper'])))
    pd.DataFrame(metrics).to_csv(EXP/'cached-statistics.csv',index=False)
    majors=grid[grid.population.eq('signal')&grid.symbol.isin(MAJORS)]
    majors.to_csv(EXP/'major-cached-outcomes.csv',index=False)
    case2_path=case2.EXP/'symbols/ETHUSDT/trades.csv.gz'
    oldtrades=pd.read_csv(case2_path,float_precision='round_trip')
    match=oldtrades[oldtrades.population.eq('signal')&oldtrades.entry_time.eq('2026-08-19 13:45:00+00:00')]
    assert len(match)==1
    trace=oldtrades[oldtrades.pair_id.isin(match.pair_id)]
    assert len(trace)==4 and match.closed.all()
    trace.to_csv(EXP/'eth-case2-cached-trade-and-controls.csv',index=False)
    frames={};prices={};traces=[]
    for symbol in MAJORS:
        f,p,t=price_review(symbol,c);frames[symbol]=f;prices[symbol]=p;traces+=t
        f.to_csv(EXP/f'{symbol}-context.csv.gz',index_label='time_utc')
    pd.DataFrame(traces).to_csv(EXP/'confirmation-candles.csv',index=False)
    path=ROOT/'data/histdata/XAUUSD/HISTDATA_COM_ASCII_XAUUSD_M1_20268.zip'
    raw,_=read_minutes([str(path)]);_,q=clean_minutes(raw);blocked=sorted(invalid_monday_weeks(q))
    assert '2026-08-17' in blocked
    gold=dict(status='unavailable_conflicting_monday',blocked_weeks=blocked,source=file_stamp(path),conflicting_minutes=q['conflicting_minutes'])
    summary=dict(date_beijing=DATE,monday_ny='2026-08-17',monday_window_beijing=['2026-08-17 12:00','2026-08-18 12:00'],
        first_touch_events=len(day),symbols=int(day.symbol.nunique()),upper=int(day.side.eq(-1).sum()),lower=int(day.side.eq(1).sum()),
        upper_at_23h=int((day.side.eq(-1)&ts.loc[day.index].dt.hour.eq(23)).sum()),prices=prices,statistics=metrics,gold=gold,
        eth_case2=dict(source=file_stamp(case2_path),trade=match.iloc[0].to_dict(),control_mean_net_r=float(trace[trace.population.eq('random')].net_r.mean())),
        native_reruns=0,limitations=['Observed registered universe only; incomplete/delisted histories limit breadth.',
        'Price action is descriptive, not causal proof of Fibonacci or an independent strategy validation.',
        'Cached returns use grid timing reference with separate true-clock bounds; current OKX fees, no funding/slippage/queue/liquidation.',
        'Controls match the week, symbol, side and lagged volatility; they need not enter on Aug19.'],
        cache=file_stamp(CACHE/'summary.json'),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',summary);plot(frames,prices)
    print(json.dumps({k:summary[k] for k in ['first_touch_events','symbols','upper','lower','upper_at_23h','gold']}),flush=True)
    print(json.dumps({s:p['rules'] for s,p in prices.items()}),flush=True)


if __name__=='__main__':main()

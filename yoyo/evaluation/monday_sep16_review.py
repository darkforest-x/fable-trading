"""Review all registered Binance 5m symbols on 2026-09-16 Beijing.

Source: registered recent SPIKE OHLCV, never generated candles. Monday levels
use a completed NY calendar Monday. Features use the touch candle and previous
20 completed volumes; volatility controls use previous 14 completed TR/open.
Forward prices are descriptive labels, not fills, P&L, or a new trading engine.
"""
from __future__ import annotations
import concurrent.futures as cf
import json
import subprocess
from pathlib import Path
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_5m_wick_engulf as c1
from yoyo.evaluation import monday_15m_volume_rejection as c2
from yoyo.evaluation import monday_1h_wick_hold as c3
from yoyo.evaluation import monday_4h_close_hold as c4

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-monday-sep16-review-20261024-v1'
SOURCE=ROOT/'data/crypto/market/research-spike_v128_recent_20260923/series'
OLD=ROOT/'data/crypto/market/kline_binance_um5m_forward_20260922/series'
COLS=['open','high','low','close','volume']
EXCLUDE=set('USDCUSDT BUSDUSDT TUSDUSDT FDUSDUSDT USDPUSDT USDEUSDT DAIUSDT USD1USDT PAXGUSDT XAUTUSDT XAUUSDT XAGUSDT BTCDOMUSDT DEFIUSDT FOOTBALLUSDT BLUEBIRDUSDT'.split())
START=pd.Timestamp('2026-09-13',tz='UTC'); END=pd.Timestamp('2026-09-21 04:00',tz='UTC')
MON=pd.Timestamp('2026-09-14 04:00',tz='UTC'); READY=MON+pd.Timedelta(days=1)
DAY=pd.Timestamp('2026-09-15 16:00',tz='UTC'); DAY_END=DAY+pd.Timedelta(days=1)
STEP=pd.Timedelta(minutes=5); HORIZONS=[5,15,60,240,1440,4320]


def clean(v):
    if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)):return [clean(x) for x in v]
    if isinstance(v,np.generic):return clean(v.item())
    if isinstance(v,float) and not np.isfinite(v):return None
    if isinstance(v,pd.Timestamp):return str(v)
    return v


def save(path,v):path.write_text(json.dumps(clean(v),ensure_ascii=False,allow_nan=False,separators=(',',':')))
def bj(t):return str(t.tz_convert('Asia/Shanghai'))[:16]
def complete(f,a,b):return f.index.equals(pd.date_range(a,b-STEP,freq=STEP))


def read(path):
    f=pd.read_csv(path,usecols=['ts']+COLS,float_precision='round_trip')
    f=f[(f.ts>=START.value//1_000_000)&(f.ts<END.value//1_000_000)].copy()
    f.index=pd.to_datetime(f.pop('ts'),unit='ms',utc=True).as_unit('ns')
    return f.sort_index()


def shape(b,level,side):
    inside=side*(np.array([b.open,b.close])-level)
    if inside.min()>0:return '纯影线触价'
    if side*(b.close-level)>0:return '实体触线后收回'
    if b.close==level:return '收盘在线上'
    return '收盘留在外侧'


def forward(f,t,side):
    out={};price=float(f.loc[t,'close'])
    for h in HORIZONS:
        when=t+pd.Timedelta(minutes=h)
        z=f.loc[t+STEP:when]
        ok=len(z)==h//5 and when in f.index and complete(z,t+STEP,when+STEP)
        out[f'pct_{h}']=side*(float(f.loc[when,'close'])/price-1)*100 if ok else None
    return out


def process(path):
    symbol=path.name.removesuffix('.csv.gz');meta=dict(symbol=symbol,source=str(path.relative_to(ROOT)),size_bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns)
    if symbol in EXCLUDE:return dict(coverage={**meta,'status':'排除：稳定币／贵金属／指数篮子'})
    f=read(path)
    if f.index.has_duplicates:return dict(coverage={**meta,'status':'排除：重复时间戳'})
    a=f[COLS].to_numpy();bad=(~np.isfinite(a).all(axis=1))|(f[['open','high','low','close']].min(axis=1)<=0)|(f.volume<0)|(f.high<f[['open','close','low']].max(axis=1))|(f.low>f[['open','close','high']].min(axis=1))
    if bad.any():return dict(coverage={**meta,'status':'排除：OHLCV异常','invalid_bars':int(bad.sum())})
    mon=f.loc[MON:READY-STEP];day=f.loc[DAY:DAY_END-STEP]
    meta.update(monday_bars=len(mon),day_bars=len(day),day_zero_volume=int(day.volume.eq(0).sum()),day_flat_bars=int(day.high.eq(day.low).sum()),follow_complete=complete(f.loc[DAY_END:END-STEP],DAY_END,END))
    if not complete(mon,MON,READY):return dict(coverage={**meta,'status':'排除：周一锚点缺K'})
    if not complete(day,DAY,DAY_END):return dict(coverage={**meta,'status':'排除：目标日缺K'})
    hi=float(mon.high.max());lo=float(mon.low.min());span=hi-lo
    if span<=0 or mon.volume.sum()<=0 or day.volume.sum()<=0 or day.high.max()==day.low.min():return dict(coverage={**meta,'status':'排除：平价或无成交量'})
    old=OLD/f'{symbol}.csv';meta['old_overlap_bars']=0;meta['old_conflict_bars']=0
    if old.exists():
        older=read(old);common=f.index.intersection(older.index)
        meta['old_overlap_bars']=len(common)
        meta['old_conflict_bars']=int((f.loc[common,COLS].to_numpy()!=older.loc[common,COLS].to_numpy()).any(axis=1).sum())
        if meta['old_conflict_bars']:return dict(coverage={**meta,'status':'排除：与旧数据重叠冲突'})
    f['count']=5
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14,min_periods=14).mean().shift()/f.open
    buckets=np.floor(np.log2(atr.where(atr>0))*4).fillna(-999).astype(int)
    vmax=f.volume.shift().rolling(20,min_periods=20).max();vmean=f.volume.shift().rolling(20,min_periods=20).mean()
    events=[];bars=[];controls=[];traces=[];rules=[]
    meta.update(status='纳入',monday_high=hi,monday_low=lo,upper=hi+.618*span,lower=lo-.618*span,day_change_pct=(day.close.iloc[-1]/day.open.iloc[0]-1)*100)
    for side in (1,-1):
        level=lo-.618*span if side==1 else hi+.618*span;label='下沿' if side==1 else '上沿'
        touches=day[(day.low<=level)&(day.high>=level)]
        beyond=day.low<=level if side==1 else day.high>=level
        meta[f'{label}_touch_bars']=len(touches);meta[f'{label}_beyond_without_touch']=bool(beyond.any() and touches.empty)
        if touches.empty:continue
        period=f.loc[READY:END-STEP];reached=period.low<=level if side==1 else period.high>=level
        first=period.index[reached][0];t=touches.index[0];bar=f.loc[t];event_id=f'{symbol}-{side}'
        prefix=complete(f.loc[READY:first],READY,first+STEP)
        weekly_first_day=bool(prefix and DAY<=first<DAY_END)
        event=dict(event_id=event_id,symbol=symbol,side=side,direction=label,first_touch_bj=bj(t),first_touch_utc=str(t),weekly_first_bj=bj(first),weekly_first_in_day=weekly_first_day,weekly_first_known=prefix,first_day_is_week_first=bool(prefix and t==first),level=level,monday_high=hi,monday_low=lo,touch_bars=len(touches),shape=shape(bar,level,side),day_change_pct=meta['day_change_pct'],**forward(f,t,side))
        for ti,b in touches.iterrows():
            rng=float(b.high-b.low);body=float(abs(b.close-b.open));k=dict(event_id=event_id,symbol=symbol,direction=label,open_time_bj=bj(ti),open_time_utc=str(ti),level=level,**{c:float(b[c]) for c in COLS},shape=shape(b,level,side),body_pct_range=body/rng*100 if rng else None,lower_wick_pct_range=(min(b.open,b.close)-b.low)/rng*100 if rng else None,upper_wick_pct_range=(b.high-max(b.open,b.close))/rng*100 if rng else None,volume_vs_prev20_max=b.volume/vmax.loc[ti] if vmax.loc[ti]>0 else None,volume_vs_prev20_mean=b.volume/vmean.loc[ti] if vmean.loc[ti]>0 else None,close_distance_pct=side*(b.close/level-1)*100,prior_close_inside=bool(side*(f.close.shift().loc[ti]-level)>0),weekly_first=bool(prefix and ti==first))
            bars.append(k)
            if ti==t:event.update({k:v for k,v in k.items() if k not in event})
        after=f.loc[t+STEP:END-STEP];continuous=complete(after,t+STEP,END)
        event['forward_complete']=continuous
        if continuous:
            favorable=after.high if side==1 else after.low;adverse=after.low if side==1 else after.high
            event['best_follow_pct']=float((side*(favorable/bar.close-1)*100).max());event['worst_follow_pct']=float((side*(adverse/bar.close-1)*100).min())
            event['best_time_bj']=bj((side*(favorable/bar.close-1)).idxmax());event['worst_time_bj']=bj((side*(adverse/bar.close-1)).idxmin())
            event['end_pct']=float(side*(after.close.iloc[-1]/bar.close-1)*100);event['end_inside']=bool(side*(after.close.iloc[-1]-level)>0)
            target=lo if side==1 else hi;hit=(after.high>=target) if side==1 else (after.low<=target)
            event['later_near_boundary_reached']=bool(hit.any());event['near_first_bj']=bj(after.index[hit][0]) if hit.any() else None
            event['near_already_reached_by_touch_close']=bool(side*(bar.close-target)>=0)
            breach=side*(after.close-level)<0;event['later_outside_close']=bool(breach.any());event['outside_first_bj']=bj(after.index[breach][0]+STEP) if breach.any() else None
            event['outside_close_share']=float(breach.mean());event['forward_hours']=len(after)/12
            event['path_label']=('回到近侧周一边界' if hit.any() else '未回周一边界')+'；'+('期末在内侧' if event['end_inside'] else '期末在外侧')
        # Same-symbol, same-day, same lagged-volatility-bucket random timing baseline.
        pool=day.index[(buckets.loc[day.index]==buckets.loc[t])&(day.index!=t)]
        if buckets.loc[t]!=-999 and len(pool)>=3:
            seed=20261024+sum((i+1)*ord(ch) for i,ch in enumerate(event_id))
            choices=np.random.default_rng(seed).choice(len(pool),3,replace=False)
            for j in choices:controls.append(dict(event_id=event_id,symbol=symbol,side=side,time_bj=bj(pool[j]),bucket=int(buckets.loc[t]),**forward(f,pool[j],side)))
        # Reuse existing frozen signal preparation; no fill simulation.
        for minutes,mod in [(15,c2),(60,c3),(240,c4)]:
            h=mod.aggregate(f);pin_t=t.floor(f'{minutes}min');i=h.index.get_indexer([pin_t])[0]
            masks=mod.pattern_mask(h,pd.Series(level,index=h.index),side)
            pin=bool(masks[0].iloc[i]);confirm=bool(masks[2 if minutes==15 else 1].iloc[i])
            peak=bool(masks[1].iloc[i]) if minutes==15 else True
            rule_id={15:'情况2：15m放量＋3根实体不碰线',60:'情况3：1h＋3根影线不碰线',240:'情况4：4h＋3根收盘不破线'}[minutes]
            admitted=bool(prefix and first.floor(f'{minutes}min')==pin_t and pin_t>=READY)
            base=dict(event_id=event_id,symbol=symbol,direction=label,timeframe_minutes=minutes,pin_time_bj=bj(pin_t),pin=pin,confirmation=confirm,volume_pass=peak,weekly_first_bar=admitted,decision_bj=bj(pin_t+pd.Timedelta(minutes=minutes)*4))
            rules.append(dict(**base,rule=rule_id,qualified=admitted and pin and confirm and peak))
            following=h.iloc[i+1:i+4]
            if minutes==60:
                bodyok=bool(len(following)==3 and following.bars.eq(12).all() and following.minutes.eq(60).all() and ((following[['open','close']].min(axis=1)>=level).all() if side==1 else (following[['open','close']].max(axis=1)<=level).all()))
                rules.append({**base,'rule':'情况3变体：1h＋3根实体不越线','confirmation':bodyok,'qualified':admitted and pin and bodyok})
            if minutes==240:
                tail=h.iloc[i+1:i+6];hold=bool(len(tail)==5 and tail.bars.eq(48).all() and tail.minutes.eq(240).all() and (side*(tail.close-level)>=0).all())
                rules.append({**base,'rule':'情况4参数：4h＋5根收盘不破线','confirmation':hold,'qualified':admitted and pin and hold,'decision_bj':bj(pin_t+pd.Timedelta(hours=24))})
            for off in range(6 if minutes==240 else 4):
                if i+off>=len(h):break
                b=h.iloc[i+off];bt=h.index[i+off]
                traces.append(dict(event_id=event_id,symbol=symbol,direction=label,timeframe_minutes=minutes,offset=off,time_bj=bj(bt),close_time_bj=bj(bt+pd.Timedelta(minutes=minutes)),level=level,**{col:float(b[col]) for col in ['open','high','low','close']},body_inside=bool(side*(min(b.open,b.close) if side==1 else max(b.open,b.close))-side*level>=0),wick_inside=bool(side*(b.low if side==1 else b.high)-side*level>0),close_inside=bool(side*(b.close-level)>=0)))
        engulf=c1.signal_mask(f,level,side);confirm_times=touches.index+STEP;valid=[x for x in confirm_times if x in f.index and bool(engulf.loc[x])]
        rules.append(dict(event_id=event_id,symbol=symbol,direction=label,rule='情况1：5m插针＋紧接吞没',qualified=bool(valid),signal_count=len(valid),decision_times_bj='；'.join(bj(x+STEP) for x in valid)))
        events.append(event)
    if events:f[COLS].to_csv(EXP/'contexts'/f'{symbol}.csv.gz',index_label='time_utc')
    return dict(coverage=meta,events=events,bars=bars,controls=controls,traces=traces,rules=rules)


def metrics(events,controls):
    e=pd.DataFrame(events);c=pd.DataFrame(controls);out=[]
    for name,frame in [('全部方向',e),('下沿',e[e.side==1]),('上沿',e[e.side==-1])]+[(s,e[e['shape']==s]) for s in e['shape'].unique()]:
        for h in HORIZONS:
            col=f'pct_{h}';values=frame[col].dropna();cm=c.groupby('event_id')[col].agg(['mean','count']);paired=frame.set_index('event_id')[[col]].join(cm[cm['count']==3]).dropna()
            out.append(dict(group=name,horizon_minutes=h,n=len(values),positive=int((values>0).sum()),positive_rate=float((values>0).mean()),mean_pct=float(values.mean()),median_pct=float(values.median()),p25_pct=float(values.quantile(.25)),p75_pct=float(values.quantile(.75)),paired_n=len(paired),paired_mean_pct=float(paired[col].mean()),random_mean_pct=float(paired['mean'].mean()),paired_difference_pct=float((paired[col]-paired['mean']).mean())))
    return out


def main():
    warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:raise RuntimeError('Commit builder before generating')
    for sub in ['contexts','per-symbol','images']:(EXP/sub).mkdir(parents=True,exist_ok=True)
    paths=sorted(SOURCE.glob('*.csv.gz'));assert len(paths)==635,len(paths)
    outputs=[]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(process,p):p for p in paths if not (EXP/'per-symbol'/f'{p.name}.json').exists()}
        for n,future in enumerate(cf.as_completed(futures),1):
            p=futures[future];result=future.result();save(EXP/'per-symbol'/f'{p.name}.json',result)
            if n%50==0:print(f'Processed {n}/{len(futures)}',flush=True)
    for p in paths:outputs.append(json.loads((EXP/'per-symbol'/f'{p.name}.json').read_text()))
    tables={k:[x for r in outputs for x in r.get(k,[])] for k in ['events','bars','controls','traces','rules']};tables['coverage']=[r['coverage'] for r in outputs];tables['metrics']=metrics(tables['events'],tables['controls'])
    for k,rows in tables.items():pd.DataFrame(rows).to_csv(EXP/f'{k}.csv',index=False,encoding='utf-8-sig')
    e=pd.DataFrame(tables['events']);cv=pd.DataFrame(tables['coverage']);eligible=cv[cv.status=='纳入'];first=e[e.weekly_first_in_day]
    summary=dict(date='2026-09-16',timezone='Asia/Shanghai',monday_ny='2026-09-14',monday_start_bj=bj(MON),monday_end_bj=bj(READY),follow_end_bj=bj(END),registered_symbols=len(paths),eligible_symbols=len(eligible),excluded_by_reason=cv[cv.status!='纳入'].groupby('status').size().to_dict(),touched_symbols=int(e.symbol.nunique()),touch_events=len(e),touch_bars=len(tables['bars']),upper_symbols=int(e[e.side==-1].symbol.nunique()),lower_symbols=int(e[e.side==1].symbol.nunique()),both_symbols=int(e.groupby('symbol').size().eq(2).sum()),weekly_first_symbols=int(first.symbol.nunique()),weekly_first_events=len(first),shapes=e.groupby('shape').size().to_dict(),near_boundary_reached=int(e.later_near_boundary_reached.sum()),end_inside=int(e.end_inside.sum()),follow_complete_events=int(e.forward_complete.sum()),overlap_checked_symbols=int(cv.old_overlap_bars.fillna(0).gt(0).sum()),overlap_checked_bars=int(cv.old_overlap_bars.fillna(0).sum()),overlap_conflict_bars=int(cv.old_conflict_bars.fillna(0).sum()),outside_without_touch_symbols=int(eligible[['下沿_beyond_without_touch','上沿_beyond_without_touch']].any(axis=1).sum()),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),training_eligible=False,production_eligible=False)
    save(EXP/'summary.json',summary);save(EXP/'reviewed.json',dict(summary=summary,**tables));print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()

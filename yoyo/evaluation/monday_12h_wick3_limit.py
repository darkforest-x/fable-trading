"""Owner 2026-10-27 BTC 12H three-wick Monday 1.618 limit, run once in Nautilus.

Owner rules (chat + TradingView screenshot of 周一 1.618 V1.0, OKX BTCUSDT.P 12H):
- Levels: NY Monday high/low from 5m bars; lower = L-.618R (long), upper =
  H+.618R (short), rounded away from price to the 0.1 tick.
- Touch: a complete UTC 00/12 12H candle (Beijing 08/20) whose WICK reaches the
  level inside the week's window [Tue 00:00 NY, next Mon 00:00 NY) while its
  open and close both stay strictly on the near side. Body touches never count.
- After the third wick touch closes, rest a limit at the level until week end.
  The fourth reach of any kind fills it. One position per week side.
- Stop: extreme of the four touch candles (three wicks + fill candle) beyond by
  1 USD ("1个点"; the owner's drawn stop sits a few dollars under the lowest
  wick, not 1% away). It is first known at the fill candle close; the fill
  candle cannot reach it, so a 50% placeholder covers that interval only.
- Target: opposite 1.618 of the entry week, later of the next week once its
  Monday completes. Exit at the CLOSE of the first later 12H candle reaching it
  by wick only; a body beyond the level keeps the position open.
- Time: market exit 7 days after the fill bar's 5m close. Real OKX Lv1 fees:
  maker limit entry, taker stop/close/time exits. No funding or slippage.

Exit clocks are close-time decisions read from completed candles. They depend
on the fourth-touch bar, so the plan predicts it from the 5m lows/highs and
the run asserts every native fill matches; Nautilus alone executes the limit,
stop and market exits once. Controls share week, side and 14-bar 5m TR bucket,
submit at random 5m closes, and scale the signal geometry by close ratio.
"""
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_limit_reclaim_inputs import weekly_context
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

ROOT=Path(__file__).resolve().parents[2]
ID='exp-monday-12h-wick3-limit-20261027-v1'
EXP=ROOT/'experiments/active'/ID
CODE=['yoyo/evaluation/monday_12h_wick3_limit.py']
OHLC=['open','high','low','close']
HALF_DAY=pd.Timedelta(hours=12).value

def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n')

def candles12(f):
    """UTC 00/12 groups of 5m open-time bars; incomplete groups never qualify."""
    key=f.index.as_unit('ns').asi8//HALF_DAY
    _,start,count=np.unique(key,return_index=True,return_counts=True)
    end=start+count-1
    return dict(group=np.repeat(np.arange(len(start)),count),start=start,end=end,
        open=f.open.to_numpy()[start],close=f.close.to_numpy()[end],
        high=np.maximum.reduceat(f.high.to_numpy(),start),low=np.minimum.reduceat(f.low.to_numpy(),start),
        complete=count==144)

def touched(c,lo,hi,k,a,b,level,d):
    """Candle k reaches level within 5m window [a,b) by wick only.

    d=+1 approaches a level below price (reach by low, body above it); d=-1 a
    level above price. Only the part of a straddling candle inside the window
    can count; the body test uses the whole 12H candle the owner sees.
    """
    if not c['complete'][k]:return False
    s,e=max(c['start'][k],a),min(c['end'][k],b-1)
    if s>e:return False
    body=min(c['open'][k],c['close'][k]) if d>0 else max(c['open'][k],c['close'][k])
    reach=lo[s:e+1].min()<=level if d>0 else hi[s:e+1].max()>=level
    return bool(reach and d*(body-level)>0)

def first_reach(lo,hi,j,b,level,d):
    hit=np.flatnonzero(lo[j+1:b]<=level if d>0 else hi[j+1:b]>=level)
    return j+1+int(hit[0]) if len(hit) else None

def snap(x,tick,up):return float((np.ceil(x/tick-1e-7) if up else np.floor(x/tick+1e-7))*tick)

def level(w,side,tick,ratio=1.):
    """Entry level for side, scaled for controls; rounded away from price."""
    hi,lo=w['monday_high'],w['monday_low'];r=hi-lo
    return snap((lo-.618*r if side>0 else hi+.618*r)*ratio,tick,side<0)

def target(w,side,tick,ratio=1.):
    """Opposite 1.618; the price must reach at least the full level."""
    hi,lo=w['monday_high'],w['monday_low'];r=hi-lo
    return snap((hi+.618*r if side>0 else lo-.618*r)*ratio,tick,side>0)

def schedule(c,lo,hi,times,weeks,wi,side,fill,tick,ratio):
    """Close-time exits after the fill candle: wick-only target, else 7 days."""
    k4=int(c['group'][fill]);n=len(times)
    timed=int(times.searchsorted(times[fill]+pd.Timedelta(days=7)))
    tp=None
    for w in weeks[wi:wi+2]:
        if not w.get('valid'):continue
        y=target(w,side,tick,ratio);a,b=w['monitor_start_i'],w['monitor_end_i_exclusive']
        for k in range(max(k4+1,int(c['group'][a])),int(c['group'][b-1])+1):
            if c['end'][k]>timed:break
            if touched(c,lo,hi,k,a,b,y,-side):tp=int(c['end'][k]);break
        if tp is not None:break
    end=min(timed,tp if tp is not None else timed)
    return dict(target_i=tp if tp is not None else -1,timed_i=timed,end_i=min(end,n-1),censored=end>n-1)

def plan(f,cfg):
    tick=cfg['tick'];buf=cfg['stop_buffer_usd'];lo=f.low.to_numpy();hi=f.high.to_numpy();close=f.close.to_numpy()
    times=f.index;n=len(f);c=candles12(f);weeks=weekly_context(f,5)
    for w0,w1 in zip(weeks,weeks[1:]):
        if pd.Timestamp(w1['week'])-pd.Timestamp(w0['week'])!=pd.Timedelta(days=7):raise RuntimeError('Week sequence gap')
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    bucket=np.floor(np.log2((tr.rolling(14).mean()/f.close).where(lambda s:s>0))*4).fillna(-999).astype(int).to_numpy()
    rng=np.random.default_rng(cfg['seed']);orders=[];opps=[]
    for wi,w in enumerate(weeks):
        for side in (1,-1):
            event=f"BTC:{w['week']}:{side}";common=dict(event_id=event,week=w['week'],side=side)
            if not w.get('valid'):opps.append(dict(common,status='unobservable_week'));continue
            x=level(w,side,tick);a,b=w['monitor_start_i'],w['monitor_end_i_exclusive']
            ks=[k for k in range(int(c['group'][a]),int(c['group'][b-1])+1) if touched(c,lo,hi,k,a,b,x,side)][:cfg['touches_before_entry']]
            if len(ks)<cfg['touches_before_entry']:opps.append(dict(common,status=f'{len(ks)}_wick_touches',level=x));continue
            j=int(c['end'][ks[-1]])
            if j>=b-1:opps.append(dict(common,status='third_touch_at_week_end',level=x));continue
            cluster=min(c['low'][ks]) if side>0 else max(c['high'][ks])
            opps.append(dict(common,status='submitted',level=x,touches=[str(times[c['start'][k]]) for k in ks]))
            pool=np.arange(a,b-1);pool=pool[(bucket[pool]==bucket[j])&(bucket[pool]!=-999)&(pool!=j)]
            chosen=rng.choice(pool,cfg['controls_per_signal'],replace=False) if len(pool)>=cfg['controls_per_signal'] else []
            for k,cj in enumerate([j,*chosen]):
                cj=int(cj);ratio=close[cj]/close[j]
                price=x if k==0 else snap(x*ratio,tick,side<0)
                row=dict(common,trade_id=f'{event}:c{k}',population='signal' if k==0 else 'random',control_id=k,
                    entry_i=cj,entry=price,ratio=ratio,submitted=str(times[cj]+pd.Timedelta(minutes=5)),
                    volatility_bucket=int(bucket[cj]),target_price=np.nan)
                if side*(close[cj]-price)<tick*(1-1e-7):raise RuntimeError('Limit not resting')
                fill=first_reach(lo,hi,cj,b,price,side)
                stop0=snap(price*(1-side*.5),tick,side<0)
                if fill is not None and not c['complete'][c['group'][fill]]:
                    # Fill candle unfinished at data end: the stop is unknowable.
                    opps.append(dict(common,status='censored_fill_candle',control_id=k));continue
                if fill is None:
                    orders.append(dict(row,predicted_fill_i=-1,expire_i=b-1,end_i=min(b,n-1),stop=stop0,risk=side*(price-stop0),
                        stop_update_i=-1,stop_update=np.nan,true_stop=np.nan,target_i=-1,timed_i=-1,censored=False));continue
                k4=int(c['group'][fill])
                extreme=min(cluster*ratio,c['low'][k4]) if side>0 else max(cluster*ratio,c['high'][k4])
                stop=snap(extreme-side*buf,tick,side<0)
                ex=schedule(c,lo,hi,times,weeks,wi,side,fill,tick,ratio)
                orders.append(dict(row,predicted_fill_i=fill,expire_i=fill,stop=stop0,risk=side*(price-stop0),
                    stop_update_i=int(c['end'][k4]),stop_update=stop,true_stop=stop,
                    fill_candle=str(times[c['start'][k4]]),**ex))
    return pd.DataFrame(orders),pd.DataFrame(opps),weeks

def drawdown(r):
    eq=np.cumsum(r);return float((eq-np.maximum.accumulate(np.r_[0,eq])[1:]).min()) if len(r) else 0.

def stats(p):
    if not len(p):return dict(trades=0)
    r=p.net_r.to_numpy();wins=r[r>0];loss=r[r<=0]
    return dict(trades=len(p),win_rate=float((r>0).mean()),mean_r=float(r.mean()),median_r=float(np.median(r)),
        sum_r=float(r.sum()),mean_win_r=float(wins.mean()) if len(wins) else None,mean_loss_r=float(loss.mean()) if len(loss) else None,
        profit_factor=float(wins.sum()/-loss.sum()) if loss.sum()<0 else None,max_drawdown_r=drawdown(p.sort_values('exit_i').net_r.to_numpy()),
        mean_net_pct=float(p.net_pct.mean()),sum_net_pct=float(p.net_pct.sum()),mean_risk_pct=float(p.risk_pct.mean()),
        mean_fee_r=float(p.fee_r_true.mean()),mean_hold_days=float(p.hold_days.mean()),
        exits=p.exit_kind.value_counts().to_dict())

def analyze(orders,rows,audit,f,cfg,out):
    filled={o['trade_id']:o['fill_i'] for o in audit['opportunities'] if o['status']=='filled'}
    expected=orders.set_index('trade_id').predicted_fill_i
    got=pd.Series(filled,dtype=float).reindex(expected.index).fillna(-1).astype(int)
    if not got.equals(expected.astype(int)):raise RuntimeError('Native fills differ from the predicted fourth touch')
    m=rows.copy()
    if not set(m.reason)<= {0,9}:raise RuntimeError('Unexpected exit path (placeholder stop or other)')
    true_risk=m.side*(m.entry-m.true_stop);usd=m.net_r*m.risk
    m=m.assign(true_risk=true_risk,net_r=usd/true_risk,gross_r=m.gross_r*m.risk/true_risk,fee_r_true=m.fee_r*m.risk/true_risk,
        net_pct=usd/m.entry*100,risk_pct=true_risk/m.entry*100,hold_days=(m.exit_i-m.entry_i)*5/1440,
        exit_kind=np.select([m.reason.eq(9),m.exit_i.eq(m.target_i),m.exit_i.eq(m.timed_i)],['stop','target_wick_close','time_7d'],'data_end'))
    m.to_csv(out/'positions.csv',index=False)
    sig=m[m.population.eq('signal')];summary=dict(identity=dict(rows=len(f),first=str(f.index[0]),last=str(f.index[-1])),
        signal=stats(sig),signal_long=stats(sig[sig.side.eq(1)]),signal_short=stats(sig[sig.side.eq(-1)]),
        signal_by_year={y:stats(g) for y,g in sig.groupby(sig.week.str[:4])},random=stats(m[m.population.eq('random')]))
    # Per submitted order: an unfilled limit is a zero-return outcome of the rule.
    per=orders[['event_id','week','population','trade_id']].merge(m[['trade_id','net_r','net_pct']],on='trade_id',how='left').fillna({'net_r':0.,'net_pct':0.})
    pairs=[]
    for event,g in per.groupby('event_id'):
        s,r=g[g.population.eq('signal')],g[g.population.eq('random')]
        if len(s)==1 and len(r)==cfg['controls_per_signal']:
            pairs.append(dict(event_id=event,week=s.week.iloc[0],signal_r=float(s.net_r.iloc[0]),random_r=float(r.net_r.mean()),
                signal_pct=float(s.net_pct.iloc[0]),random_pct=float(r.net_pct.mean())))
    paired=pd.DataFrame(pairs);paired.to_csv(out/'matched_pairs.csv',index=False)
    if len(paired):
        x=paired.assign(d=paired.signal_r-paired.random_r);ix=np.random.default_rng(cfg['seed']).integers(0,len(x),size=(5000,len(x)))
        boot=x.d.to_numpy()[ix].mean(axis=1)
        summary['matched_per_submission']=dict(pairs=len(x),signal_mean_r=float(x.signal_r.mean()),random_mean_r=float(x.random_r.mean()),
            excess_mean_r=float(x.d.mean()),excess_bootstrap95=np.quantile(boot,[.025,.975]).tolist(),
            signal_mean_pct=float(x.signal_pct.mean()),random_mean_pct=float(x.random_pct.mean()))
    summary['submitted_signals']=int(orders.population.eq('signal').sum());summary['filled_signals']=len(sig)
    dump(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

def main():
    argparse.ArgumentParser().parse_args()
    cfg=json.loads((EXP/'config.json').read_text());out=EXP
    if (out/'summary.json').exists():raise RuntimeError('Reuse existing results; no replay')
    for path in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git','diff','--quiet','HEAD','--',path],cwd=ROOT).returncode or subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,capture_output=True).returncode:raise RuntimeError('Commit builder first')
    data=ROOT/cfg['input']
    f=pd.read_csv(data/'candles.csv.gz',index_col='open_time',parse_dates=True)
    f.index=pd.DatetimeIndex(f.index).as_unit('ns');f.attrs['group']='crypto_5m'
    orders,opps,weeks=plan(f,cfg)
    orders.to_csv(out/'orders.csv',index=False);opps.to_csv(out/'opportunities.csv',index=False);dump(out/'weeks.json',weeks)
    print(f"{len(f)} 5m bars; {orders.population.eq('signal').sum()} signal and {orders.population.eq('random').sum()} control orders",flush=True)
    frame=f[OHLC].copy();frame.index+=pd.Timedelta(minutes=5)
    policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,target_from_entry=True,
        breakeven_includes_cost=False,fill_maker_fee=cfg['maker_fee'],fill_taker_fee=cfg['taker_fee'])
    rows,fills,audit=run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),orders,policy,tick=cfg['tick'],
        market_tick=cfg['market_tick'],symbol='BTC',execution_minutes=5,entry_order_mode='limit',batch_bars=24000)
    rows.to_csv(out/'native_rows.csv',index=False);fills.to_csv(out/'fills.csv',index=False)
    dump(out/'audit.json',{k:v for k,v in audit.items() if k!='opportunities'})
    dump(out/'identity.json',dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        input=cfg['input'],receipt=json.loads((data/'receipt.json').read_text()),framework='NautilusTrader 1.231.0',
        code=[dict(path=p,size=(ROOT/p).stat().st_size,mtime_ns=(ROOT/p).stat().st_mtime_ns) for p in CODE]))
    analyze(orders,rows,audit,f,cfg,out)

if __name__=='__main__':main()

"""Fixed BTC/ETH/XAU no-backstop 12H baseline using native Nautilus orders.

NY Monday range is known at Tuesday NY midnight; first weekly-side 1.618
touch submits a resting limit on the preceding close. Controls match week,
direction and 14 completed 5m-bar relative TR bucket, scaling all geometry.
Only native fills define transactions. Weighted accounting and mark-to-market
below do not infer any fill or exit. 1R is 1% entry notional, not initial risk.
"""
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_limit_reclaim_inputs import weekly_context
from yoyo.evaluation.monday_limit_reclaim import segment_ends
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy
from yoyo.layers.l3_backtest.nautilus_touch_recovery import run_touch_recovery

ROOT=Path(__file__).resolve().parents[2]
ID='exp-monday-12h-partial-recovery-20261026-v1'
EXP=ROOT/'experiments/active'/ID
DATA=ROOT/'data/crypto/market'/ID
CODE=['yoyo/evaluation/monday_12h_recovery.py','yoyo/layers/l3_backtest/nautilus_touch_recovery.py']

def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n')

def prepare(f,cfg,symbol):
    tick=cfg['ticks'][symbol];ends=segment_ends(f,dict(symbol=symbol+'USDT',execution_minutes=5))
    weeks=weekly_context(f,5);h=f.high.to_numpy();l=f.low.to_numpy();close=f.close.to_numpy()
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    bucket=np.floor(np.log2((tr.rolling(14).mean()/f.close).where(lambda s:s>0))*4).fillna(-999).astype(int).to_numpy()
    rng=np.random.default_rng(cfg['seed']);orders=[];opps=[]
    for w in weeks:
        for side in [1,-1]:
            common=dict(event_id=f"{symbol}:{w['week']}:{side}",symbol=symbol,week=w['week'],side=side)
            if not w.get('valid'):opps.append(dict(common,status='unobservable_week'));continue
            hi,lo=w['monday_high'],w['monday_low'];width=hi-lo
            raw=lo-.618*width if side>0 else hi+.618*width
            entry=(np.floor(raw/tick+1e-7) if side>0 else np.ceil(raw/tick-1e-7))*tick
            near=lo if side>0 else hi
            targets=[lo+.618*width,hi,hi+.618*width] if side>0 else [hi-.618*width,lo,lo-.618*width]
            a,b=w['monitor_start_i'],w['monitor_end_i_exclusive']
            hit=np.flatnonzero(l[a:b]<=entry if side>0 else h[a:b]>=entry)
            if not len(hit):opps.append(dict(common,status='no_touch'));continue
            touch=a+int(hit[0]);j=touch-1
            if j<0 or side*(close[j]-entry)<tick*(1-1e-7) or ends[touch]<=j:
                opps.append(dict(common,status='already_marketable_or_missing_submission'));continue
            opps.append(dict(common,status='admitted',touch=str(f.index[touch])))
            pool=np.arange(a,max(a,b-1),dtype=int)
            pool=pool[(bucket[pool]==bucket[j])&(bucket[pool]!=-999)&(ends[pool]>pool)&(pool!=j)]
            chosen=rng.choice(pool,cfg['controls_per_signal'],replace=False) if len(pool)>=cfg['controls_per_signal'] else []
            attempts=[('signal',0,j,entry)]
            for k,cj in enumerate(chosen,1):
                ce=close[cj]*entry/close[j]
                ce=(np.floor(ce/tick+1e-7) if side>0 else np.ceil(ce/tick-1e-7))*tick
                attempts.append(('random',k,int(cj),ce))
            for pop,k,j,price in attempts:
                if side*(close[j]-price)<tick*(1-1e-7):continue
                end=int(ends[j+1]);expiry=min(b-1,end)
                if expiry<=j:continue
                position=common['event_id']+f':c{k}'
                for leg,(target,weight) in enumerate(zip(targets,cfg['weights']),1):
                    t=float(np.round(target*price/entry/tick)*tick)
                    orders.append(dict(common,trade_id=position+f':tp{leg}',position_key=position,population=pop,control_id=k,
                        entry_i=j,expire_i=expiry,end_i=end,entry=price,stop=np.nan,risk=price,target_price=t,
                        planned_target=t,leg=leg,weight=weight,near_edge=near*price/entry,reclaim_minutes=720,
                        volatility_bucket=int(bucket[j]),censor_reason='source_end' if end==len(f)-1 else 'missing_source_interval'))
    return pd.DataFrame(orders),pd.DataFrame(opps),weeks

def parents(rows):
    result=[]
    for key,g in rows.groupby('position_key'):
        if len(g)!=3 or g.entry_i.nunique()!=1 or set(g.leg)!={1,2,3}:raise RuntimeError('Incomplete native leg group')
        r=g.iloc[0];closed=g.reason.ne(0);tp=g.exit_kind.eq('target');be=g.exit_kind.isin(['fee_stop','fee_recovery'])
        result.append(dict(position_key=key,event_id=r.event_id,symbol=r.symbol,week=r.week,side=int(r.side),
            population=r.population,control_id=int(r.control_id),entry_time=r.entry_time,entry_i=int(r.entry_i),entry=float(r.entry),
            exit_time=g.exit_time.max(),last_i=int(g.exit_i.max()),closed=bool(closed.all()),open_weight=float(g.loc[~closed,'weight'].sum()),
            realized_r=float(g.loc[closed,'weighted_net_r'].sum()),open_mark_r=float(g.loc[~closed,'weighted_net_r'].sum()),
            total_mark_r=float(g.weighted_net_r.sum()),price_mae_r=float(g.mae_fixed_r.min()),price_mfe_r=float(g.mfe_fixed_r.max()),
            tp1=bool((tp&g.leg.eq(1)).any()),tp2=bool((tp&g.leg.eq(2)).any()),tp3=bool((tp&g.leg.eq(3)).any()),
            breakeven_only=bool(be.all()),partial_tp_then_be=bool(tp.any() and be.any() and closed.all()),
            returned_without_any_tp=bool(g.returned_to_range.any() and not tp.any()),
            returned_without_all_tp=bool(g.returned_to_range.any() and not tp.all()),
            failed_wait=bool(g.decision_status.eq('failed_wait').any()),fee_band=bool(g.decision_status.eq('fee_band_wait').any()),
            pending=bool((g.decision_status.eq('pending')&~closed).any()),holding_days=float((g.exit_i.max()-r.entry_i)*5/1440),
            ambiguous=bool(g.ambiguous.any())))
    return pd.DataFrame(result)

def equity(rows,f):
    """5m close valuation of fixed-notional native fills, including open marks.

    Each parent initially has 100R notional. Censored exits are liquidation
    marks, not realized proceeds. This is independent equal-notional exposure,
    with no account sizing, margin, funding or compounding assumption.
    """
    n=len(f);slope=np.zeros(n+1);offset=np.zeros(n+1)
    for r in rows.itertuples():
        a,b=int(r.entry_i),int(r.exit_i);m=r.weight*r.side*100/r.entry
        # Include a hypothetical taker close in all open marks consistently.
        m-=r.weight*.0005*100/r.entry
        c=-r.weight*r.side*100-r.weight*.0002*100
        slope[a]+=m;offset[a]+=c;slope[b]-=m;offset[b]-=c
        offset[b]+=r.weighted_net_r
    curve=np.cumsum(slope[:-1])*f.close.to_numpy()+np.cumsum(offset[:-1])
    np.testing.assert_allclose(curve[-1],rows.weighted_net_r.sum(),atol=1e-7)
    return curve

def metrics(p,rows,f):
    c=p[p.closed];openp=p[~p.closed];curve=equity(rows,f)
    peak=np.maximum.accumulate(np.maximum(curve,0));dd=curve-peak
    return dict(entered=len(p),closed=len(c),open_positions=len(openp),realized_r=float(p.realized_r.sum()),
        open_mark_r=float(p.open_mark_r.sum()),total_mark_r=float(p.total_mark_r.sum()),
        closed_parent_r=float(c.total_mark_r.sum()),open_underwater=int(openp.open_mark_r.lt(0).sum()),
        ever_price_underwater=int(p.price_mae_r.lt(-1e-7).sum()),mae_at_least_1r=int(p.price_mae_r.le(-1).sum()),
        mae_at_least_5r=int(p.price_mae_r.le(-5).sum()),mae_at_least_10r=int(p.price_mae_r.le(-10).sum()),
        worst_price_mae_r=float(p.price_mae_r.min()),median_price_mae_r=float(p.price_mae_r.median()),
        tp1=int(p.tp1.sum()),tp2=int(p.tp2.sum()),tp3=int(p.tp3.sum()),breakeven_only=int(p.breakeven_only.sum()),
        partial_tp_then_be=int(p.partial_tp_then_be.sum()),returned_without_any_tp=int(p.returned_without_any_tp.sum()),
        returned_without_all_tp=int(p.returned_without_all_tp.sum()),failed_wait=int(p.failed_wait.sum()),
        fee_band=int(p.fee_band.sum()),pending=int(p.pending.sum()),backstop=None,
        longest_holding_days=float(p.holding_days.max()),longest_open_days=float(openp.holding_days.max()) if len(openp) else 0,
        marked_max_drawdown_r=float(dd.min()),ambiguous_positions=int(p.ambiguous.sum()))

def analyze(rows,f,cfg,out):
    p=parents(rows);p.to_csv(out/'positions.csv',index=False)
    summary=dict(metrics=[],matched_controls=[],native_leg_count=len(rows))
    sig=p[p.population.eq('signal')]
    groups=[('all',sig),('common_since_2025-04-14',sig[sig.week>='2025-04-14'])]
    groups += [(year,sig[sig.week.str.startswith(year)]) for year in sorted(sig.week.str[:4].unique())]
    for period,g in groups:
        if len(g):
            legs=rows[rows.position_key.isin(g.position_key)]
            summary['metrics'].append(dict(period=period,**metrics(g,legs,f)))
    curve=equity(rows[rows.population.eq('signal')],f)
    pd.DataFrame(dict(marked_r=curve),index=f.index+pd.Timedelta(minutes=5)).resample('1D').last().to_csv(out/'daily_marked_r.csv')
    pairs=[]
    for event,g in p.groupby('event_id'):
        s=g[g.population.eq('signal')];r=g[g.population.eq('random')]
        if len(s)==1 and len(r)==cfg['controls_per_signal']:
            pairs.append(dict(event_id=event,week=s.iloc[0].week,signal_r=float(s.iloc[0].total_mark_r),random_r=float(r.total_mark_r.mean())))
    paired=pd.DataFrame(pairs);paired.to_csv(out/'matched_pairs.csv',index=False)
    if len(paired):
        for period,g in [('all',paired),('common_since_2025-04-14',paired[paired.week>='2025-04-14'])]:
            if not len(g):continue
            x=g.assign(delta=g.signal_r-g.random_r).groupby('week').delta.agg(['sum','count'])
            ix=np.random.default_rng(cfg['seed']).integers(0,len(x),size=(3000,len(x)))
            b=x['sum'].to_numpy()[ix].sum(axis=1)/x['count'].to_numpy()[ix].sum(axis=1)
            summary['matched_controls'].append(dict(period=period,pairs=len(g),signal_mean_r=float(g.signal_r.mean()),
                random_mean_r=float(g.random_r.mean()),excess_mean_r=float((g.signal_r-g.random_r).mean()),
                excess_week_bootstrap95=np.quantile(b,[.025,.975]).tolist()))
    dump(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

def main():
    arg=argparse.ArgumentParser();arg.add_argument('symbol',choices=['BTC','ETH','XAU']);a=arg.parse_args()
    cfg=json.loads((EXP/'config.json').read_text());out=EXP/a.symbol;out.mkdir(exist_ok=True)
    if (out/'summary.json').exists():raise RuntimeError('Reuse existing results; no replay')
    for path in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git','diff','--quiet','HEAD','--',path],cwd=ROOT).returncode or subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,capture_output=True).returncode:raise RuntimeError('Commit builder first')
    data=ROOT/'data/crypto/market/exp-xau-monday-recovery-20261026-v1' if a.symbol=='XAU' else DATA/a.symbol
    f=pd.read_csv(data/'candles.csv.gz',index_col='open_time',parse_dates=True)
    f.index=pd.DatetimeIndex(f.index).as_unit('ns');f.attrs['group']='crypto_5m'
    e,op,w=prepare(f,cfg,a.symbol);e.to_csv(out/'orders.csv.gz',index=False);op.to_csv(out/'opportunities.csv',index=False);dump(out/'weeks.json',w)
    print(f'{a.symbol}: {len(f)} source bars; {len(e)} native leg opportunities',flush=True)
    frame=f[OHLC].copy();frame.index+=pd.Timedelta(minutes=5)
    policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,target_from_entry=True,
        reclaim_next_bar=True,breakeven_includes_cost=False,intrabar_path='OHLC',monotone_segments=True,
        fill_maker_fee=cfg['maker_fee'],fill_taker_fee=cfg['taker_fee'])
    rows,fills,audit=run_touch_recovery(frame,e,policy,tick=cfg['ticks'][a.symbol],market_tick=.01,
        symbol=a.symbol,execution_minutes=5,batch_bars=24000)
    rows['weighted_net_r']=rows.net_r*100*rows.weight
    rows.to_csv(out/'legs.csv.gz',index=False);fills.to_csv(out/'fills.csv.gz',index=False);dump(out/'audit.json',audit)
    dump(out/'identity.json',dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        input=str(data.relative_to(ROOT)),receipt=json.loads((data/'receipt.json').read_text()),framework='NautilusTrader 1.231.0',
        code=[dict(path=p,size=(ROOT/p).stat().st_size,mtime_ns=(ROOT/p).stat().st_mtime_ns) for p in CODE]))
    analyze(rows,f,cfg,out)

OHLC=['open','high','low','close']
if __name__=='__main__':main()

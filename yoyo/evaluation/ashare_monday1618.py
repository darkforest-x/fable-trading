"""Frozen A-share Monday 1.618 study, native fills and a causal monthly pool.

Selection uses ONLY the previous 20 completed daily OHLC/volume observations:
mean (high-low)/open. The current listing catalog has survivor / historical ST
limitations, explicitly retained. Weekly levels use confirmed Monday daily
OHLC including auctions; execution uses raw Tencent hourly bars. Features and
control volatility buckets never read a fill bar's close. No ML or tuning.

Every filled signal is compared with random close entries in the same stock,
week and prior-day volatility bucket, with equal percentage TP/SL geometry,
T+1, costs and endpoint. Controls are conditional research, not funded accounts.
Cash distributions are added from corporate-action annotations with holding-
period dividend tax. Share-count actions censor prior outstanding trades;
censored outcomes are excluded from the evaluable result and counted openly.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import warnings
import zlib

import numpy as np
import pandas as pd

from yoyo.data.ashare_public import CACHE, daily, hourly
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT/'experiments/active/exp-ashare-monday1618-20261028-v1'
CODE = ['yoyo/evaluation/ashare_monday1618.py', 'yoyo/layers/l3_backtest/nautilus_ashare.py',
        'yoyo/data/ashare_public.py', str((EXP/'config.json').relative_to(ROOT))]


def read_daily(symbol):
    rows=daily(symbol)
    f=pd.DataFrame([r[:6] for r in rows], columns=['date','open','close','high','low','volume'])
    for k in f.columns[1:]: f[k]=pd.to_numeric(f[k],errors='raise')
    f['date']=pd.to_datetime(f.date)
    f=f.set_index('date').sort_index()
    if f.index.has_duplicates or not np.isfinite(f.to_numpy()).all() or (f[['open','close','high','low']]<=0).any().any():
        raise ValueError('Invalid daily candles')
    actions=[]
    for r in rows:
        if len(r)>6 and isinstance(r[6],dict) and r[6]:
            a=dict(r[6],date=r[0]); content=a.get('FHcontent','')
            a['cash_per_share']=float(a.get('fh_sh',0) or 0)/10
            a['share_change']=any(x in content for x in ('送','转','配','拆','合'))
            a['bonus_ratio']=sum(float(x) for x in re.findall(r'(?:送|转)([\d.]+)股',content))/10
            actions.append(a)
    f['volatility'] = ((f.high-f.low)/f.open).where(f.volume>0).rolling(20,min_periods=20).mean()
    return f,actions


def select(cfg):
    cat=json.loads((CACHE/'mainboard.json').read_text())
    names={r['symbol']:r['name'] for r in cat['rows']}
    exclusions=[]; data={}
    for s,name in names.items():
        if 'ST' in name.upper() or '退' in name:
            exclusions.append(dict(symbol=s,reason='current_ST_or_delisting_name'));continue
        try: data[s]=read_daily(s)
        except Exception as e:exclusions.append(dict(symbol=s,reason=f'daily_unavailable:{e}'))
    dates=[pd.Timestamp(cfg['start'])]+list(pd.date_range('2026-05-01',cfg['end'],freq='MS'))
    selected=[]
    for date in dates:
        eligible=[]
        for s,(f,_) in data.items():
            past=f[f.index<date]
            if len(past)<20 or not np.isfinite(past.volatility.iloc[-1]):continue
            if past.index[-1]<date-pd.Timedelta(days=10):continue
            eligible.append(dict(symbol=s,name=names[s],selection_date=str(date.date()),
                rank_asof=str(past.index[-1].date()),prior20_amplitude_pct=float(past.volatility.iloc[-1]*100)))
        ranked=sorted(eligible,key=lambda x:(-x['prior20_amplitude_pct'],x['symbol']))
        for rank,r in enumerate(ranked[:cfg['top_n']],1): selected.append(dict(r,rank=rank,eligible_universe=len(ranked)))
    return data,names,pd.DataFrame(selected),exclusions


def prepare_frame(symbol, d, actions, cfg):
    rows=hourly(symbol)
    f=pd.DataFrame([r[:6] for r in rows],columns=['time','open','close','high','low','volume'])
    f.index=pd.to_datetime(f.pop('time'),format='%Y%m%d%H%M').dt.tz_localize('Asia/Shanghai').dt.tz_convert('UTC')
    f.index=f.index.as_unit('ns')
    f=f.astype(float).sort_index()
    if f.index.has_duplicates:raise ValueError('Duplicate hourly candles')
    f['date']=f.index.tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
    f=f[(f.date>='2026-04-27')&(f.date<=cfg['end'])].copy()
    if f.empty:raise ValueError('Missing study hourly coverage')
    if not np.isfinite(f[['open','high','low','close','volume']].to_numpy()).all():raise ValueError('Nonfinite hourly data')
    if (np.abs(f[['open','high','low','close']].to_numpy()*100-np.rint(f[['open','high','low','close']].to_numpy()*100))>1e-5).any():
        raise ValueError('Hourly prices not raw cent prices')
    expected=d[(d.index>=pd.Timestamp('2026-04-27'))&(d.index<=pd.Timestamp(cfg['end']))&(d.volume>0)]
    agg=f.groupby('date').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),bars=('close','size'))
    mismatch=[]
    for date,r in expected.iterrows():
        key=str(date.date())
        if key not in agg.index or agg.loc[key,'bars']!=4:
            mismatch.append(dict(date=key,reason='incomplete_hourly_session'));continue
        h=agg.loc[key]
        # Daily auctions may have extra extremes; hourly prices cannot extend
        # beyond the daily range or disagree at the final close.
        if h.high>r.high+.011 or h.low<r.low-.011 or abs(h.close-r.close)>.011:
            mismatch.append(dict(date=key,reason='raw_daily_hourly_disagreement'))
    if mismatch:raise ValueError(f'Hourly quality failure: {mismatch[:4]} ({len(mismatch)} dates)')
    d=d.copy();d['ref']=d.close.shift(1)
    bydate={a['date']:a for a in actions}
    for date,a in bydate.items():
        if pd.Timestamp(date) in d.index:
            d.loc[date,'ref']=(d.loc[date,'ref']-a['cash_per_share'])/(1+a['bonus_ratio'])
    from yoyo.layers.l3_backtest.nautilus_ashare import cents
    for col,mul in [('limit_down',.9),('limit_up',1.1)]:
        values={str(t.date()):cents(v*mul) for t,v in d.ref.dropna().items()}
        f[col]=f.date.map(values)
    prior_vol=d.volatility.shift(1)
    buckets={str(t.date()):int(np.floor(np.log2(v)*4)) if np.isfinite(v) and v>0 else -999 for t,v in prior_vol.items()}
    f['vol_bucket']=f.date.map(buckets)
    f['session_end']=f.index.tz_convert('Asia/Shanghai').strftime('%H:%M')=='15:00'
    f['censor_after']=False
    for a in actions:
        if a['share_change']:
            before=np.flatnonzero((f.date<a['date']).to_numpy())
            if len(before):f.iloc[before[-1],f.columns.get_loc('censor_after')]=True
    if f[['limit_down','limit_up','vol_bucket']].isna().any().any():raise ValueError('Missing daily reference')
    return f,dict(rows=len(f),dates=len(agg),first=str(f.index[0]),last=str(f.index[-1]),daily_hourly_checks=len(expected),share_actions=[a for a in actions if a['share_change']])


def plans_for(symbol,d,actions,f,selections,cfg):
    rng=np.random.default_rng(cfg['seed']+zlib.crc32(symbol.encode()))
    selected={date:set(g.symbol) for date,g in selections.groupby('selection_date')}
    dates=sorted(selected);plans=[];opportunities=[]
    for monday,r in d[d.index.dayofweek==0].iterrows():
        start=max(monday+pd.Timedelta(days=1),pd.Timestamp(cfg['start']))
        end=min(monday+pd.Timedelta(days=4),pd.Timestamp(cfg['end']))
        if end<start:continue
        chosen=[x for x in dates if x<=str(start.date())]
        if not chosen or symbol not in selected[chosen[-1]]:continue
        width=r.high-r.low
        entry=math.floor((r.low-.618*width)*100+1e-8)/100
        target=math.ceil((r.high+.618*width)*100-1e-8)/100
        if width<=0 or entry<=0 or r.volume<=0:continue
        for action in actions:
            dt=pd.Timestamp(action['date'])
            if action['share_change'] and monday<dt<=end:end=min(end,dt-pd.Timedelta(days=1))
        if end<start:continue
        pair=f'{symbol}:{monday.date()}'
        common=dict(symbol=symbol,week=str(monday.date()),pair_id=pair,start_date=str(start.date()),
                    expire_date=str(end.date()),monday_high=r.high,monday_low=r.low,entry=entry,target=target,
                    target_return=target/entry-1)
        signal=dict(common,trade_id=pair+':s',population='signal',control_id=0)
        plans.append(signal)
        window=np.flatnonzero(((f.date>=str(start.date()))&(f.date<=str(end.date()))).to_numpy())
        touches=window[f.low.to_numpy()[window]<=entry]
        # A touch hint only conditions the matching stratum, never computes a
        # fill/exit or admits a trade based on profit. Native fills may differ.
        hint=int(touches[0]) if len(touches) else -1
        bucket=int(f.vol_bucket.iloc[hint]) if hint>=0 else -999
        signal['match_bucket']=bucket
        pool=[int(i) for i in window if int(f.vol_bucket.iloc[i])==bucket and i<len(f)-1]
        chosen_controls=rng.choice(pool,min(cfg['controls_per_signal'],len(pool)),replace=False) if pool else []
        for k,i in enumerate(chosen_controls,1):
            plans.append(dict(common,trade_id=pair+f':r{k}',population='random',control_id=k,
                              submit_i=int(i),match_bucket=bucket))
        opportunities.append(dict(common,touch_hint=hint,match_bucket=bucket,controls=len(chosen_controls)))
    return plans,opportunities


def accounting(t,actions,f,cfg):
    if t.empty:return t
    t=t.copy();t['dividend_gross']=0.;t['dividend_net']=0.;t['estimated_exit_fee']=0.
    for idx,r in t.iterrows():
        for a in actions:
            record=a.get('djr','')
            if record and r.entry_date<=record<r.exit_date and a['date']<=r.exit_date:
                gross=r.quantity*a['cash_per_share']
                rate=.2 if r.holding_days<=30 else .1 if r.holding_days<=365 else 0.
                t.loc[idx,'dividend_gross']+=gross;t.loc[idx,'dividend_net']+=gross*(1-rate)
        if not r.executed:
            amount=r.quantity*r.exit_price
            t.loc[idx,'estimated_exit_fee']=round(max(cfg['commission_min_cny'],amount*cfg['commission_rate'])+
                amount*(cfg['transfer_rate_each_side']+cfg['sell_stamp_rate']),2)
        path=f.iloc[int(r.entry_i):int(r.exit_i)+1]
        # Intrabar entry ordering is uncertain: MAE includes the whole fill bar,
        # deliberately conservative. No exit path is recomputed.
        t.loc[idx,'mae_pct']=(path.low.min()/r.fill_price-1)*100
    t['entry_notional']=t.fill_price*t.quantity
    t['net_pnl']=t.gross_pnl+t.dividend_net-t.fee
    t['net_pct']=t.net_pnl/t.entry_notional*100
    t['liquidation_estimate_pct']=(t.net_pnl-t.estimated_exit_fee)/t.entry_notional*100
    return t


def stats(t):
    if t.empty:return dict(n=0)
    closed=t[t.executed];opened=t[t.reason=='open_at_end'];censored=t[t.reason=='share_action_censored']
    valid=t[t.reason!='share_action_censored']
    return dict(n=len(t),evaluable=len(valid),closed=len(closed),target=int((t.reason=='target').sum()),
        stop=int((t.reason=='stop').sum()),open=len(opened),censored=len(censored),
        closed_win_pct=float((closed.net_pnl>0).mean()*100) if len(closed) else None,
        all_including_open_win_pct=float((valid.net_pnl>0).mean()*100) if len(valid) else None,
        mean_gross_pct=float((valid.gross_pnl/valid.entry_notional*100).mean()) if len(valid) else None,
        mean_net_pct=float(valid.net_pct.mean()) if len(valid) else None,
        net_pnl_cny=float(valid.net_pnl.sum()),realized_net_pnl_cny=float(closed.net_pnl.sum()),
        open_net_pnl_cny=float(opened.net_pnl.sum()),fees_cny=float(valid.fee.sum()),
        dividends_net_cny=float(valid.dividend_net.sum()),
        median_hold_days=float(valid.holding_days.median()) if len(valid) else None,
        worst_pct=float(valid.net_pct.min()) if len(valid) else None,
        worst_mae_pct=float(valid.mae_pct.min()) if len(valid) else None,
        mean_marked_liquidation_pct=float(valid.liquidation_estimate_pct.mean()) if len(valid) else None)


def summarize(cfg):
    frames=[]
    for p in sorted((EXP/'symbols').glob('*/trades.csv')):
        if p.stat().st_size>5:frames.append(pd.read_csv(p))
    t=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    t.to_csv(EXP/'trades.csv.gz',index=False)
    s=t[t.population=='signal'];r=t[t.population=='random']
    # Match only actual filled signals and retain group completeness separately.
    pairs=s[(s.reason!='share_action_censored')&(s.actual_bucket==s.match_bucket)].set_index('pair_id')
    controls=r[(r.pair_id.isin(pairs.index))&(r.reason!='share_action_censored')]
    group=controls.groupby('pair_id').net_pct.agg(['mean','count'])
    complete=group[group['count']==cfg['controls_per_signal']]
    paired=pairs[['net_pct']].join(complete,how='inner')
    delta=(paired.net_pct-paired['mean']).to_numpy()
    rng=np.random.default_rng(cfg['seed'])
    pval=None;ci=None
    if len(delta):
        draws=np.mean(rng.choice([-1,1],(10000,len(delta)))*delta,axis=1)
        pval=float((1+(np.abs(draws)>=abs(delta.mean())).sum())/10001)
        boot=np.mean(rng.choice(delta,(10000,len(delta)),replace=True),axis=1)
        ci=np.percentile(boot,[2.5,97.5]).tolist()
    by=[]
    for symbol,g in s.groupby('symbol'):
        rr=controls[controls.symbol==symbol]
        by.append(dict(symbol=symbol,name=g.name.iloc[0],**stats(g),random_mean_net_pct=stats(rr).get('mean_net_pct')))
    pd.DataFrame(by).sort_values('net_pnl_cny',ascending=False).to_csv(EXP/'by-symbol.csv',index=False)
    monthly=[]
    for month,g in s.groupby(s.entry_date.str[:7]):
        matching=controls[controls.pair_id.isin(g.pair_id)]
        monthly.append(dict(entry_month=month,**stats(g),random_mean_net_pct=stats(matching).get('mean_net_pct')))
    pd.DataFrame(monthly).to_csv(EXP/'by-month.csv',index=False)
    paired.to_csv(EXP/'paired-controls.csv')
    summary=dict(config=cfg,signal=stats(s),random_for_filled_signals=stats(controls),
        matched_complete_pairs=len(paired),signal_control_bucket_mismatches=int((s.actual_bucket!=s.match_bucket).sum()),
        paired_signal_mean_pct=float(paired.net_pct.mean()) if len(paired) else None,
        paired_random_mean_pct=float(paired['mean'].mean()) if len(paired) else None,
        paired_mean_excess_pct=float(delta.mean()) if len(delta) else None,paired_signflip_p=pval,
        iid_pair_bootstrap_ci_pct=ci, statistical_caveat='Signals overlap; pair tests are descriptive and not independent confirmatory evidence',
        framework='NautilusTrader '+__import__('nautilus_trader').__version__,
        built_at=datetime.now(timezone.utc).isoformat(),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        limitations=['Current listings and current ST exclusions: survivorship and historical membership incompleteness',
            'Hour OHLC, synthetic full liquidity away from limit locks; no queue, spread, slippage or capacity validation',
            'Frozen nominal unadjusted weekly levels; cash dividends net of estimated holding-period tax',
            'Outstanding share-count actions administratively censored, excluded from evaluable outcomes',
            'Independent 10000 CNY lots may overlap; summed PnL is not a 10000 CNY funded portfolio return',
            'No score filter, breakeven, leverage, adds, or tuning; original fixed -30 percent stop',
            'No ML model: AUC, model top-decile metrics and ML permutation test not applicable'],
        training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def run(cfg):
    for p in CODE:
        if subprocess.run(['git','diff','--quiet','HEAD','--',p]).returncode or subprocess.run(['git','ls-files','--error-unmatch',p],capture_output=True).returncode:
            raise RuntimeError('Commit builder/config first')
    data,names,selection,excluded=select(cfg)
    selection.to_csv(EXP/'selection.csv',index=False);dump(EXP/'universe-exclusions.json',excluded)
    symbols=sorted(selection.symbol.unique());status=[]
    print(json.dumps(dict(selected_unique=len(symbols),daily_valid=len(data),excluded=len(excluded))),flush=True)
    # Only missing selected hourly data are downloaded; no market replay yet.
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(hourly,s):s for s in symbols}
        for job in as_completed(jobs):
            try:job.result()
            except Exception as e:status.append(dict(symbol=jobs[job],status='hourly_unavailable',error=str(e)))
    unavailable={x['symbol'] for x in status}
    from yoyo.layers.l3_backtest.nautilus_ashare import run_native
    for n,symbol in enumerate(symbols,1):
        dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
        if (dest/'done.json').exists():
            status.append(json.loads((dest/'done.json').read_text()));continue
        if symbol in unavailable:continue
        d,actions=data[symbol]
        try:f,quality=prepare_frame(symbol,d,actions,cfg)
        except Exception as e:
            status.append(dict(symbol=symbol,status='quality_rejected',error=str(e)));continue
        plans,op=plans_for(symbol,d,actions,f,selection,cfg)
        dump(dest/'quality.json',quality);dump(dest/'actions.json',actions)
        dump(dest/'plans.json',plans);pd.DataFrame(op).to_csv(dest/'opportunities.csv',index=False)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            t,fills,audit=run_native(f,plans,cfg,symbol)
        if len(t):
            t['name']=names[symbol];t=accounting(t,actions,f,cfg)
        t.to_csv(dest/'trades.csv',index=False);fills.to_csv(dest/'fills.csv',index=False)
        dump(dest/'audit.json',audit)
        receipt=dict(symbol=symbol,status='complete',plans=len(plans),signals=sum(p['population']=='signal' for p in plans),trades=len(t),
                     source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
        dump(dest/'done.json',receipt);status.append(receipt)
        print(json.dumps(dict(done=n,total=len(symbols),**receipt)),flush=True)
    dump(EXP/'status.json',status)
    summarize(cfg)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--summarize-only',action='store_true')
    args=ap.parse_args();cfg=json.loads((EXP/'config.json').read_text())
    if args.summarize_only:summarize(cfg)
    else:run(cfg)

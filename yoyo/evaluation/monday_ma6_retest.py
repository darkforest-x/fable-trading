"""Owner-confirmed MA6 retest: causal preparation, native execution, frozen output.

The owner supplied cumulative three new extremes, an MA-envelope stop buffer
of0.5%, and full/full/half target comparisons on2026-10-24. Signal generation
reads completed15m OHLC and earlier data only. This runner does not calculate
barrier crossings: Nautilus1.231.0 owns every entry, cancellation and exit.
Two independent equal-risk native legs also establish the exact half-each
payoff, because no leg changes another leg's stop or order. No duplicate third
replay is necessary. Existing historical results and source files stay intact.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation.monday_15m_volume_rejection import load
from yoyo.evaluation.monday_ma6_signals import prepare

EXP = ROOT/'experiments/active/exp-monday-ma6-retest-20261024-v1'
ART = ROOT/'artifacts/monday-ma6-retest-20261024'
STEP = pd.Timedelta(minutes=5)
CODE = ['yoyo/evaluation/monday_ma6_retest.py',
        'yoyo/evaluation/monday_ma6_signals.py',
        'yoyo/layers/l3_backtest/nautilus_independent.py']


def finite(value):
    if isinstance(value, dict): return {str(k):finite(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [finite(v) for v in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, (float,np.floating)): return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp,Path)): return str(value)
    return value


def source_jobs(cfg):
    """Resolve known identities and one bounded supplement directory, no hashes."""
    supplement={p.name.split('.')[0]:str(p) for p in (ROOT/cfg['crypto_recent_supplement']).iterdir()
                if p.name.endswith(('.csv','.csv.gz'))}
    jobs=[]
    for identity in sorted((old.EXP/'symbols').glob('*/identity.json')):
        symbol=identity.parent.name; stored=json.loads(identity.read_text())
        if symbol=='XAUUSD':
            stored=json.loads((ROOT/'experiments/active/exp-monday-5m-wick-gold-clock-20261023-v2/identity.json').read_text())
            paths=[r['path'] for r in stored['inputs']]
        else:
            paths=[]
            for r in stored['inputs']:
                name=Path(r['path']).name
                p=ROOT/cfg['crypto_root']/'series'/name if name.startswith('binance_um_') else ROOT/cfg['crypto_tail']/name
                now=file_stamp(p)
                if any(now[k]!=r[k] for k in ['bytes','mtime_ns']):
                    raise ValueError(f'Frozen source changed: {symbol} {name}')
                paths.append(str(p))
            if symbol in supplement: paths.append(supplement[symbol])
        jobs.append((symbol,paths))
    if len(jobs)!=633: raise ValueError(f'Expected633 frozen identities, got{len(jobs)}')
    return jobs


def source_frame(symbol,paths):
    if symbol=='XAUUSD':
        from yoyo.evaluation.monday_5m_wick_gold_clock import read_minutes
        from yoyo.evaluation.monday_5m_wick_gold import clean_minutes, invalid_monday_weeks
        raw,_=read_minutes(paths);f,q=clean_minutes(raw)
        q['blocked_weeks']=sorted(invalid_monday_weeks(q))
        return f,q
    f=load(paths)  # Reject any conflicting overlap, including volume.
    gaps=np.flatnonzero(np.diff(f.index.asi8)!=STEP.value)
    return f,dict(rows=len(f),unexpected_gap_count=len(gaps),
        overlap_policy='Exact OHLCV agreement, then deduplicate; no source preference',
        source_files=len(paths),blocked_weeks=[])


def normalized_native(frame,orders,updates,symbol):
    """Expand two targets, then send all submitted opportunities to native once."""
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    decimals=max(2,min(9,int(np.ceil(-np.log10(frame[old.OHLC].to_numpy().min())))+7))
    tick=10.**(-decimals)
    x=orders.copy()
    for c in ['entry','stop','target_monday','target_far','cancel_boundary']:
        x[c]=x[c].round(decimals)
    x['risk']=x.side*(x.entry-x.stop)
    valid=(x.risk>=tick*.999)&(x.side*(x.cancel_boundary-x.entry)>=tick*.999)
    valid&=(x.side*(x.target_monday-x.entry)>=tick*.999)&(x.side*(x.target_far-x.entry)>=tick*.999)
    rejected=x[~valid].copy();rejected['status']='invalid_tick_geometry'
    x=x[valid].copy()
    if x.empty:return pd.DataFrame(),pd.DataFrame(),dict(opportunities=[],positions=0,fills=0),rejected
    u=updates[updates.trade_id.isin(x.trade_id)].copy() if len(updates) else pd.DataFrame()
    if len(u):
        for c in ['entry','stop']:
            if c in u:u[c]=u[c].round(decimals)
        lookup=x.set_index('trade_id')
        for j,row in u.iterrows():
            if row.action=='cancel':continue
            original=lookup.loc[row.trade_id]
            if (row.stop<=0 or original.side*(row.entry-row.stop)<tick*.999
                    or original.side*(original.cancel_boundary-row.entry)<tick*.999):
                u.loc[j,'action']='cancel';u.loc[j,'status']='invalid_amendment_geometry'
    legs=[]; amendments=[]
    for mode,col in [('monday_full','target_monday'),('opposite_1618_full','target_far')]:
        leg=x.copy();leg['parent_id']=leg.trade_id;leg['trade_id']=leg.trade_id+'-'+mode
        leg['policy']=mode;leg['target_price']=leg[col]
        leg['target_unreachable']=leg.target_price.le(0)
        leg.loc[leg.target_unreachable,'target_price']=np.nan
        legs.append(leg)
        if len(u):
            v=u.copy();v['trade_id']=v.trade_id+'-'+mode;amendments.append(v)
    e=pd.concat(legs,ignore_index=True)
    up=pd.concat(amendments,ignore_index=True) if amendments else None
    close=frame[old.OHLC].round(decimals).copy();close.index=close.index+STEP
    p=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
        target_from_entry=True,intrabar_path='OHLC',
        fill_maker_fee=0. if symbol=='XAUUSD' else .0002,
        fill_taker_fee=0. if symbol=='XAUUSD' else .0005)
    rows,fills,audit=run_native(close,np.full(len(close),np.nan),np.zeros(len(close)),e,p,
        tick=tick,symbol='XAU' if symbol=='XAUUSD' else symbol.removesuffix('USDT'),
        entry_order_mode='limit',entry_updates=up)
    audit['rounding_decimals']=decimals
    return rows,fills,audit,rejected


def describe_exposure(frame,rows):
    """Post-fill evidence only: conservative complete interior bars, no exit oracle."""
    if rows.empty:return rows
    rows=rows.copy();prices=frame[old.OHLC].to_numpy()
    for j,r in rows.iterrows():
        a=prices[int(r.entry_i)+1:int(r.exit_i)]
        mfe=mae=0.
        if len(a):
            favorable=float(a[:,1].max() if r.side==1 else a[:,2].min())
            adverse=float(a[:,2].min() if r.side==1 else a[:,1].max())
            mfe=max(0.,r.side*(favorable-r.entry)/r.risk)
            mae=max(0.,-r.side*(adverse-r.entry)/r.risk)
        rows.loc[j,'interior_mfe_r']=mfe;rows.loc[j,'interior_mae_r']=mae
        rows.loc[j,'interior_bars']=len(a)
        favorable_mark=r.entry+r.side*mfe*r.risk
        floating_cost=0. if r.symbol=='XAUUSD' else (.0002*r.entry+.0005*favorable_mark)/r.risk
        rows.loc[j,'interior_fee_positive_mfe']=bool(mfe>floating_cost)
    rows['closed']=rows.reason.ne(0)
    rows['exit_interval_start']=pd.to_datetime(rows.framework_exit_time,utc=True)-STEP
    rows['exit_interval_end']=pd.to_datetime(rows.framework_exit_time,utc=True)
    rows['holding_hours']=(rows.exit_interval_end-pd.to_datetime(rows.entry_time,utc=True)).dt.total_seconds()/3600
    rows['planned_rr']=rows.side*(rows.target_price-rows.entry)/rows.risk
    rows['return_r']=rows.net_r
    rows['return_pct']=rows.net_notional_bp/100
    rows['basis']=np.where(rows.symbol.eq('XAUUSD'),'gross','net')
    rows.loc[rows.symbol.eq('XAUUSD'),['net_r','net_notional_bp','fee_r']]=np.nan
    return rows


def combine_half(rows):
    """Linear combination of identical-entry native legs; not another simulator."""
    if rows.empty:return rows
    combined=[]
    for parent,g in rows.groupby('parent_id',sort=False):
        if len(g)!=2 or set(g.policy)!={'monday_full','opposite_1618_full'}:
            raise AssertionError('Missing native target sibling')
        for c in ['entry_i','entry','stop','risk','side','quantity']:
            if g[c].nunique()!=1:raise AssertionError(f'Different sibling{c}')
        a=g.sort_values('exit_interval_end').iloc[-1].to_dict()
        a.update(policy='half_each',trade_id=parent+'-half_each',reason=10,
                 closed=bool(g.closed.all()),leg_reasons=','.join(map(str,g.reason.astype(int))),
                 tp_legs=int(g.reason.eq(4).sum()),sl_legs=int(g.reason.eq(1).sum()),
                 censored_legs=int(g.reason.eq(0).sum()),
                 native_leg_ids='|'.join(g.trade_id),
                 framework_position_id='|'.join(g.framework_position_id))
        for c in ['gross_r','net_r','fee_r','return_r','return_pct','net_notional_bp','framework_commission']:
            a[c]=g[c].mean() if g[c].notna().all() else np.nan
        a['planned_rr']=g.planned_rr.mean() if g.planned_rr.notna().all() else np.nan
        a['interior_mfe_r']=g.interior_mfe_r.max()
        a['interior_mae_r']=g.interior_mae_r.max()
        a['excursion_scope']='Maximum price excursion until final leg; R uses original full position risk, not remaining weight'
        combined.append(a)
    return pd.concat([rows,pd.DataFrame(combined)],ignore_index=True)


def process(job):
    symbol,paths=job;started=time.monotonic();cfg=json.loads((EXP/'config.json').read_text())
    folder=EXP/'symbols'/symbol;folder.mkdir(parents=True,exist_ok=True)
    identity=finite(dict(config=cfg,inputs=[file_stamp(p) for p in paths],
                        code=[file_stamp(ROOT/p) for p in CODE]))
    if (folder/'done.json').exists():
        if json.loads((folder/'identity.json').read_text())!=identity:raise ValueError(f'Changed cached input {symbol}')
        return dict(symbol=symbol,cached=True)
    f,q=source_frame(symbol,paths)
    dump(folder/'data-quality.json',finite(q))
    all_o=[];all_e=[];all_t=[];all_f=[];audits=[]
    chunks=f.groupby('quality_segment',sort=True) if symbol=='XAUUSD' else [(0,f)]
    for segment,chunk in chunks:
        chunk=chunk.copy();d=prepare(chunk,symbol,cfg,q.get('blocked_weeks',[]))
        opp,orders,updates=d['opportunities'],d['orders'],d['updates']
        # Each quality segment is causally isolated. Preserve parent/pair links.
        for table in (opp,orders,updates):
            if not len(table):continue
            for c in ['trade_id','pair_id','opportunity_id','signal_trade_id']:
                if c in table:table[c]=f'q{segment}-'+table[c].astype(str)
            table['quality_segment']=int(segment)
        if len(opp):
            opp['first_touch_at']=opp['touch_open_time']
            for name,col in [('invalidation_at','invalid_i'),('confirmation_at','confirm_i')]:
                if col in opp:
                    opp[name]=[str(chunk.index[int(i)]+STEP) if pd.notna(i) and 0<=int(i)<len(chunk) else '' for i in opp[col]]
        if len(orders):
            orders['submitted_at']=orders['submit_time']
            touchmap=opp.set_index('pair_id').first_touch_at
            orders['first_touch_at']=orders.pair_id.map(touchmap)
        all_o.append(opp)
        dest=folder/f'segment-{segment}';dest.mkdir(exist_ok=True)
        dump(dest/'signal-diagnostics.json',finite(d['diagnostics']))
        opp.to_csv(dest/'opportunities.csv.gz',index=False)
        orders.to_csv(dest/'orders.csv.gz',index=False)
        updates.to_csv(dest/'pending-updates.csv.gz',index=False)
        if not len(orders):continue
        rows,fills,audit,rejected=normalized_native(chunk,orders,updates,symbol)
        rejected.to_csv(dest/'invalid-rounded-orders.csv.gz',index=False)
        native_ops=pd.DataFrame(audit['opportunities'])
        if len(native_ops):
            native_ops['parent_id']=native_ops.trade_id.str.replace(r'-(monday_full|opposite_1618_full)$','',regex=True)
            if native_ops.groupby('parent_id').status.nunique().gt(1).any():raise AssertionError('Target altered entry admission')
            statuses=native_ops.drop_duplicates('parent_id').set_index('parent_id').status
            orders['status']=orders.trade_id.map(statuses).fillna('invalid_tick_geometry')
        else:orders['status']='invalid_tick_geometry'
        orders.to_csv(dest/'orders.csv.gz',index=False);all_e.append(orders)
        rows=describe_exposure(chunk,rows)
        rows.to_csv(dest/'native-trades.csv.gz',index=False)
        fills.to_csv(dest/'native-fills.csv.gz',index=False)
        dump(dest/'native-audit.json',finite(audit));audits.append(audit)
        if len(rows):
            rows['quality_segment']=int(segment);all_t.append(combine_half(rows));all_f.append(fills)
    for name,parts in [('opportunities',all_o),('orders',all_e),('trades',all_t),('fills',all_f)]:
        nonempty=[x for x in parts if len(x)]
        out=pd.concat(nonempty,ignore_index=True) if nonempty else pd.DataFrame()
        out.to_csv(folder/f'{name}.csv.gz',index=False)
    done=dict(symbol=symbol,source_rows=len(f),first=str(f.index[0]),last=str(f.index[-1]),
              opportunities=sum(len(x) for x in all_o),submitted=sum(len(x) for x in all_e),
              native_positions=sum(a['positions'] for a in audits),native_fills=sum(a['fills'] for a in audits),
              seconds=time.monotonic()-started)
    dump(folder/'identity.json',identity);dump(folder/'done.json',finite(done));return done


def run(symbols,workers):
    for p in CODE:
        if subprocess.run(['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode:
            raise RuntimeError('Commit builder before historical execution')
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,'experiments/active/exp-monday-ma6-retest-20261024-v1/config.json'],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config first')
    cfg=json.loads((EXP/'config.json').read_text());jobs=source_jobs(cfg)
    if symbols: jobs=[j for j in jobs if j[0] in symbols]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fs={pool.submit(process,j):j[0] for j in jobs}
        for n,fu in enumerate(as_completed(fs),1):
            name=fs[fu]
            try:result=fu.result()
            except Exception as exc:
                dump(EXP/'symbols'/name/'failure.json',dict(symbol=name,error=repr(exc)))
                print(json.dumps(dict(symbol=name,error=repr(exc))),flush=True)
            else:print(json.dumps(dict(progress=f'{n}/{len(jobs)}',**result)),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',nargs='*');parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args();run(args.symbols,args.workers)


if __name__=='__main__':main()

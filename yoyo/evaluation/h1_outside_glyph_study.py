"""H1 Pine V2.43 outside glyphs: bounded stop study using native VectorBT.

Features are prepared separately from completed H1 bars. Entry is the next
observed 5m open exactly at H1 close; no entry if missing. Each glyph is an
independent trade (overlaps retained), NOT a shared capital account. Static
barriers use the existing VectorBT native OHLC kernels once. No custom candle
fill loop or second framework replay. All four stop candidates share entries,
targets and paired random times. Seven-day horizon, earlier gap/data censor.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import warnings

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation.h1_outside_glyph_signals import prepare
from yoyo.evaluation.monday_15m_volume_rejection import load
from yoyo.evaluation.monday_vectorbt_engine import native_exits, segments

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-h1-outside-glyph-stops-20261102-v1'
STEP = pd.Timedelta(minutes=5)
FILTERS = ['all', 'volume2', 'rsi_extreme', 'rsi_div', 'bb_reentry', 'trend4h', 'reclaim1618', 'planned_rr3']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False)+'\n')


def stamp(path):
    s = path.stat()
    return dict(path=str(path), bytes=s.st_size, mtime_ns=s.st_mtime_ns)


def jobs(cfg):
    base = ROOT/cfg['root']; meta=json.loads((base/'exchange_info.json').read_text())
    metadata={x['symbol']:x for x in meta['symbols']}; result=[]
    for p in sorted((base/'series').glob('*.csv')):
        symbol=p.name.split('_')[2]; paths=[p]
        for directory,suffix in [(cfg['tail'],'.csv'),(cfg['extension'],'.csv.gz')]:
            more=ROOT/directory/(symbol+suffix)
            if more.exists(): paths.append(more)
        info=metadata.get(symbol,{})
        result.append((symbol,[str(x) for x in paths],info.get('underlyingSubType',[])))
    return sorted(result,key=lambda x:(x[0] not in ('BTCUSDT','ETHUSDT','LPTUSDT'),x[0]))


def candidates(f, h, symbol, cfg):
    """Translate completed H1 signals and same-month/ATR-bucket random opens.

    Control stop/target percentage distances match the originating signal.
    Control times are eligible Tue-Fri H1 closes, same symbol/month/ATR14
    logarithmic bucket; they are not forced to be outside 1.618. This tests
    the entire location+glyph entry. Paired filters condition the signal only.
    """
    when=h.index+pd.Timedelta(hours=1); loc=f.index.get_indexer(when)
    bucket=np.floor(4*np.log2((h.atr14/h.close).where(h.atr14.gt(0)))).fillna(-999).astype(int)
    month=when.strftime('%Y-%m'); pools={}
    valid=h.eligible.to_numpy() & (loc>=0) & bucket.ne(-999).to_numpy() & h.volume.gt(0).to_numpy()
    for j in np.flatnonzero(valid): pools.setdefault((month[j],int(bucket.iloc[j])),[]).append(j)
    rng=np.random.default_rng(cfg['seed']); rows=[]; rejected=0; unmatched=0
    for side, col in [(1,'signal_long'),(-1,'signal_short')]:
        for j in np.flatnonzero(h[col].to_numpy() & valid):
            bar=h.iloc[j]; i=int(loc[j]); entry=float(f.open.iloc[i])
            target=float(bar.monday_low if side==1 else bar.monday_high)
            extreme=float(bar.low if side==1 else bar.high)
            wick_stop=extreme-side*entry*cfg['wick_buffer']
            if side*(target-entry)<=0 or side*(entry-wick_stop)<=0:
                rejected+=1; continue
            pair=f'{symbol}:{when[j].isoformat()}:{side}'
            pool=np.asarray(pools[(month[j],int(bucket.iloc[j]))]); pool=pool[pool!=j]
            choices=rng.choice(pool,min(cfg['controls'],len(pool)),replace=False)
            if len(choices)<cfg['controls']: unmatched+=1
            features=dict(volume2=bool(bar.volume_ratio>=2),
                rsi_extreme=bool(bar.rsi14<30 if side==1 else bar.rsi14>70),
                rsi_div=bool(bar.rsi_div_long if side==1 else bar.rsi_div_short),
                bb_reentry=bool(bar.bb_reentry_long if side==1 else bar.bb_reentry_short),
                trend4h=bool(bar.trend4h_long if side==1 else bar.trend4h_short),
                reclaim1618=bool(bar.close>bar.lower if side==1 else bar.close<bar.upper))
            for stop_id,pct in cfg['stops'].items():
                distance=side*(entry-wick_stop)/entry if pct is None else pct
                rr=side*(target-entry)/(entry*distance)
                for number,k in enumerate([j,*choices]):
                    k=int(k); ix=int(loc[k]); px=float(f.open.iloc[ix]); scale=px/entry
                    rows.append(dict(pair_id=pair,trade_key=f'{pair}:{stop_id}:{number}',symbol=symbol,
                        population='signal' if number==0 else 'random',stop_id=stop_id,
                        entry_i=ix,entry_time=str(when[k]),signal_time=str(when[j]),
                        side=side,entry=px,stop=px-side*px*distance,
                        target_price=px+(target-entry)*scale,risk=px*distance,
                        planned_rr=rr,planned_rr3=rr>=3,stop_pct=distance*100,
                        month=month[k],bucket=int(bucket.iloc[k]),
                        monday_high=float(bar.monday_high),monday_low=float(bar.monday_low),
                        signal_high=float(bar.high),signal_low=float(bar.low),signal_close=float(bar.close),
                        **features))
    return pd.DataFrame(rows),dict(rejected_geometry=rejected,unmatched=unmatched,
        raw_glyphs=int(h.signal_long.sum()+h.signal_short.sum()))


def execute(f,e,cfg):
    """Native static-barrier first hit, then native per-fill portfolio accounting.

    O-H-L-C is the declared 5m path. Stop-first sensitivity changes only the
    known ambiguous first exit bar; no second full replay. Market entry/exit
    receive adverse slippage; resting TP receives maker fees and no slippage.
    """
    if e.empty:return e,pd.DataFrame()
    e=e.reset_index(drop=True).copy(); prices=f[['open','high','low','close']].to_numpy(float)
    starts=e.entry_i.to_numpy(int); side=e.side.to_numpy(float); entry=e.entry.to_numpy(float)
    horizon=cfg['max_hours']*12
    timeout_ends=starts+horizon-1
    gaps=np.r_[np.flatnonzero(np.diff(f.index.as_unit('ns').asi8)!=STEP.value),len(f)-1]
    boundary=gaps[np.searchsorted(gaps,starts)]
    ends=np.minimum(timeout_ends,boundary)
    sl=e.risk.to_numpy()/entry;tp=side*(e.target_price.to_numpy()-entry)/entry
    eps=4*np.finfo(float).eps
    out=native_exits(*segments(prices),starts*3,ends*3+2,side,
        np.maximum(np.nextafter(0.,1.),sl-eps),np.maximum(np.nextafter(0.,1.),tp-eps))
    exit_i=(out[:,0]//3).astype(int); reason=out[:,2].astype(int)
    raw_px=out[:,1].copy()
    # Restore exact configured levels on ordinary hits, retaining gap-stop prices.
    raw_px=np.where(reason==4,e.target_price,raw_px)
    stop_gap=(side*(f.open.to_numpy()[exit_i]-e.stop.to_numpy())<0)&(out[:,0]%3==0)
    raw_px=np.where((reason==1)&~stop_gap,e.stop,raw_px)
    e['exit_i']=exit_i;e['exit_time']=[str(f.index[i]+STEP) for i in exit_i]
    e['reason']=np.where(reason==4,'target',np.where(reason==1,'stop',np.where(ends==timeout_ends,'timeout','censored')))
    e['raw_exit_price']=raw_px;e['censored']=e.reason.eq('censored')
    entry_px=entry*(1+side*cfg['market_slippage']);exit_px=raw_px*(1-side*np.where(reason==4,0,cfg['market_slippage']))
    fees=np.vstack([np.full(len(e),cfg['taker_fee']),np.where(reason==4,cfg['maker_fee'],cfg['taker_fee'])])
    px=np.vstack([entry_px,exit_px])
    pf=vbt.Portfolio.from_orders(px,size=np.vstack([side,-side]),price=px,size_type='amount',direction='both',
        fees=fees,init_cash=px.max(axis=0)*4,cash_sharing=False,allow_partial=False,raise_reject=True,max_orders=2*len(e))
    trades=pf.trades.records.sort_values('col');fills=pf.orders.records.copy()
    assert len(trades)==len(e) and trades.status.eq(1).all() and len(fills)==len(e)*2
    pnl=trades.pnl.to_numpy();fee=trades.entry_fees.to_numpy()+trades.exit_fees.to_numpy()
    np.testing.assert_allclose(pnl,side*(exit_px-entry_px)-fee,atol=1e-8,rtol=1e-9)
    e['entry_fill']=entry_px;e['exit_fill']=exit_px;e['fee_entry_rate']=fees[0];e['fee_exit_rate']=fees[1]
    e['net_bp']=pnl/entry*1e4;e['net_r']=pnl/e.risk;e['gross_bp']=side*(raw_px-entry)/entry*1e4
    e['fee_bp']=fee/entry*1e4;e['slippage_bp']=e.gross_bp-(pnl+fee)/entry*1e4
    last=prices[exit_i];both=(last[:,2]<=np.minimum(e.stop,e.target_price))&(last[:,1]>=np.maximum(e.stop,e.target_price))
    inside=(side*(last[:,0]-e.stop)>0)&(side*(last[:,0]-e.target_price)<0)
    amb=(reason!=0)&both&inside;e['ambiguous_bar']=amb
    alt_raw=np.where(amb,e.stop,raw_px);alt_exit=alt_raw*(1-side*np.where(amb,cfg['market_slippage'],np.where(reason==4,0,cfg['market_slippage'])))
    alt_fee=entry_px*cfg['taker_fee']+alt_exit*np.where(amb,cfg['taker_fee'],fees[1])
    alt_pnl=side*(alt_exit-entry_px)-alt_fee
    e['stop_first_net_bp']=alt_pnl/entry*1e4;e['stop_first_net_r']=alt_pnl/e.risk
    fills['trade_key']=e.trade_key.to_numpy()[fills.col.to_numpy(int)]
    return e,fills


def process(job,cfg):
    warnings.filterwarnings('ignore',category=FutureWarning)
    symbol,paths,tags=job;folder=EXP/'symbols'/symbol;folder.mkdir(parents=True,exist_ok=True)
    receipt=dict(inputs=[stamp(Path(p)) for p in paths],config=cfg)
    if (folder/'done.json').exists():
        assert json.loads((folder/'identity.json').read_text())==receipt
        return json.loads((folder/'done.json').read_text())
    f=load(paths);h=prepare(f);e,detail=candidates(f,h,symbol,cfg)
    ledger,fills=execute(f,e,cfg)
    if len(ledger):
        ledger.to_csv(folder/'trades.csv.gz',index=False)
        fills.to_csv(folder/'fills.csv.gz',index=False)
    # Real-data causal prefix check for the three named examples; no extra fills.
    prefixes=[]
    if symbol in ('BTCUSDT','ETHUSDT','LPTUSDT'):
        cut=len(f)//2; hp=prepare(f.iloc[:cut]); common=hp.index.intersection(h.index)
        pd.testing.assert_frame_equal(hp.loc[common],h.loc[common],check_freq=False)
        prefixes.append(dict(source_rows=cut,matched_h1_rows=len(common)))
    done=dict(symbol=symbol,tags=tags,bars5m=len(f),bars1h=len(h),start=str(f.index[0]),last=str(f.index[-1]),
        positions=len(ledger),signals=int(e.population.eq('signal').sum()/len(cfg['stops'])) if len(e) else 0,
        gaps=int((np.diff(f.index.asi8)!=STEP.value).sum()),prefix_checks=prefixes,**detail)
    assert receipt['inputs']==[stamp(Path(p)) for p in paths]
    dump(folder/'identity.json',receipt);dump(folder/'done.json',done)
    return done


def describe(q):
    if q.empty:return dict(n=0)
    r=q.net_r;wins=r[r>0];loss=r[r<0]
    payoff=float(wins.mean()/-loss.mean()) if len(wins) and len(loss) else None
    return dict(n=len(q),win_pct=float((r>0).mean()*100),mean_net_bp=float(q.net_bp.mean()),
        mean_net_r=float(r.mean()),sum_net_r=float(r.sum()),gross_bp=float(q.gross_bp.mean()),
        payoff=payoff,pf=float(wins.sum()/-loss.sum()) if len(loss) else None,
        median_stop_pct=float(q.stop_pct.median()),median_planned_rr=float(q.planned_rr.median()),
        stop_first_mean_bp=float(q.stop_first_net_bp.mean()),ambiguous=int(q.ambiguous_bar.sum()),
        target_pct=float(q.reason.eq('target').mean()*100),timeout_pct=float(q.reason.eq('timeout').mean()*100),
        mean_fee_bp=float(q.fee_bp.mean()),mean_slippage_bp=float(q.slippage_bp.mean()),
        without_top1pct_mean_r=float(r.sort_values().iloc[:max(1,int(len(r)*.99))].mean()))


def compare(signal,random):
    a=signal.set_index('pair_id');b=random.groupby('pair_id')[['net_r','net_bp']].mean();q=a.join(b,rsuffix='_control',how='inner')
    if q.empty:return dict(pairs=0)
    excess=q.net_r-q.net_r_control
    months=pd.to_datetime(q.signal_time,utc=True).dt.strftime('%Y-%m')
    sums=excess.groupby(months).sum().to_numpy();rng=np.random.default_rng(20261102)
    perm=(rng.choice([-1,1],size=(4999,len(sums)))*sums).sum(axis=1)
    p=(1+int((perm>=sums.sum()).sum()))/5000
    return dict(pairs=len(q),control_mean_r=float(q.net_r_control.mean()),
        control_mean_bp=float(q.net_bp_control.mean()),paired_signal_mean_r=float(q.net_r.mean()),
        excess_r=float(excess.mean()),excess_bp=float((q.net_bp-q.net_bp_control).mean()),month_signflip_p=p)


def analyze(cfg):
    frames=[pd.read_csv(p) for p in sorted((EXP/'symbols').glob('*/trades.csv.gz'))]
    alltrades=pd.concat(frames,ignore_index=True); entry=pd.to_datetime(alltrades.entry_time,utc=True)
    exits=pd.to_datetime(alltrades.exit_time,utc=True); split=pd.Timestamp(cfg['split'])
    alltrades['period']=np.where(entry>=split,'later',np.where(exits<split,'earlier','cross_split'))
    alltrades.to_csv(EXP/'trades.csv.gz',index=False)
    rows=[]
    for period in ['all','earlier','later']:
        q=alltrades[~alltrades.censored]
        if period!='all':q=q[q.period.eq(period)]
        for stop_id in cfg['stops']:
            t=q[q.stop_id.eq(stop_id)]
            for flt in FILTERS:
                z=t if flt=='all' else t[t[flt]]
                a=z[z.population.eq('signal')];b=z[z.population.eq('random')]
                rows.append(dict(period=period,stop_id=stop_id,filter=flt,**describe(a),**compare(a,b)))
    table=pd.DataFrame(rows);table.to_csv(EXP/'metrics.csv',index=False)
    by_symbol=[];by_year=[];by_side=[]
    s=alltrades[~alltrades.censored & alltrades.population.eq('signal')]
    for (symbol,stop_id),q in s.groupby(['symbol','stop_id']):by_symbol.append(dict(symbol=symbol,stop_id=stop_id,**describe(q)))
    for (year,stop_id),q in s.groupby([pd.to_datetime(s.entry_time,utc=True).dt.year,'stop_id']):by_year.append(dict(year=int(year),stop_id=stop_id,**describe(q)))
    for (side,stop_id),q in s.groupby(['side','stop_id']):by_side.append(dict(side=int(side),stop_id=stop_id,**describe(q)))
    pd.DataFrame(by_symbol).to_csv(EXP/'by_symbol.csv',index=False);pd.DataFrame(by_year).to_csv(EXP/'by_year.csv',index=False)
    pd.DataFrame(by_side).to_csv(EXP/'by_side.csv',index=False)
    eligible=table[(table.period=='earlier')&(table['filter']=='all')&(table.n>=100)]
    chosen=eligible.sort_values(['mean_net_r','stop_id'],ascending=[False,True]).iloc[0].stop_id
    later=table[(table.period=='later')&(table.stop_id==chosen)].copy()
    # Bonferroni accounts for the seven predeclared individual filter checks.
    later['corrected_p']=np.minimum(1,later.month_signflip_p*len(FILTERS))
    done=[json.loads(p.read_text()) for p in sorted((EXP/'symbols').glob('*/done.json'))]
    result=dict(experiment_id=cfg['experiment_id'],symbols=len(done),symbols_with_signals=sum(x['signals']>0 for x in done),
        signal_events=sum(x['signals'] for x in done),native_positions=len(alltrades),
        censored_signal_positions=int((alltrades.censored & alltrades.population.eq('signal')).sum()),
        cross_split_positions=int(alltrades.period.eq('cross_split').sum()),
        selected_on_earlier=chosen,selection_metric='mean net independent R before 2025',
        baseline=table[table['filter'].eq('all')].astype(object).where(pd.notna(table),None).to_dict('records'),
        selected_later_filters=later.astype(object).where(pd.notna(later),None).to_dict('records'),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        versions={x:version(x) for x in ['vectorbt','pandas','numpy','numba']},
        training_eligible=False,production_eligible=False,limitations=cfg['limitations'])
    dump(EXP/'summary.json',result)
    print(table[(table['filter']=='all')].to_string(index=False),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=4);p.add_argument('--analyze-only',action='store_true');args=p.parse_args()
    cfg=json.loads((EXP/'config.json').read_text())
    if not args.analyze_only:
        work=jobs(cfg);dump(EXP/'universe.json',[dict(symbol=s,inputs=p,tags=t) for s,p,t in work])
        failed=[]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(process,j,cfg):j[0] for j in work}
            for k,future in enumerate(as_completed(futures),1):
                symbol=futures[future]
                try:r=future.result();print(k,len(work),symbol,r['signals'],r['positions'],flush=True)
                except Exception as exc:failed.append(dict(symbol=symbol,error=repr(exc)));print('ERROR',symbol,repr(exc),flush=True)
        dump(EXP/'errors.json',failed)
        if failed:raise RuntimeError(f'{len(failed)} symbols failed; preserve and investigate')
    analyze(cfg)


if __name__=='__main__':main()

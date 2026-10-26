"""Owner-authorized 5m Monday1618 wick-R target/backstop grid, 2026-10-26.

Reuse frozen native limit fills and their three matched random submissions.
No outcome-selected symbols, no regenerated signals, and no new score fit.
Wick size uses the completed touch 5m candle; initial protection is a known
entry percentage. Portfolio columns are independent equal-notional outcomes,
not one funded account. The complete grid is committed before execution.
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

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-monday-5m-wick-r-20261026-v1'
SRC=ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1/symbols'
CODE=['yoyo/evaluation/monday_wick_r_engine.py','yoyo/evaluation/monday_wick_r_study.py']
BAD={'BABYUSDT':'2026-06-05 09:30:00+00:00','DASHUSDT':'2020-04-14 07:35:00+00:00'}
COLS=['event_id','trade_id','symbol','asset_group','week','side','population','control_id','reclaim_minutes','exit_mode',
      'entry_i','entry','level','end_i','bucket','net_bp','reason','closed','exit_i','monday_high','monday_low']


def dump(path,obj):
    def convert(v):
        if isinstance(v,dict):return {str(k):convert(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [convert(x) for x in v]
        if isinstance(v,np.generic):v=v.item()
        if isinstance(v,float) and not np.isfinite(v):return None
        return v
    Path(path).write_text(json.dumps(convert(obj),ensure_ascii=False,indent=2)+'\n')


def stamp(path):
    p=Path(path);s=p.stat()
    return dict(path=str(p),bytes=s.st_size,mtime_ns=s.st_mtime_ns,readable=p.exists())


def prepare(frame,ledger,job):
    """Preserve candidate identities and make causal close availability explicit.

Features: touch 5m high/low at touch close, confirmation close one bucket
later; prior14-bar volatility bucket is inherited from frozen submissions.
Existing matched controls learn their OWN touch wick at their OWN close.
"""
    t=ledger[(ledger.reclaim_minutes==5)&(ledger.exit_mode=='near_monday_edge')].copy()
    if t.empty:return t,dict(excluded_bad_pairs=0)
    assert t.trade_id.is_unique
    step=job['execution_minutes'];ts=frame.index.as_unit('ns').asi8
    a=t.entry_i.to_numpy(int)
    assert (a>=0).all() and (a<len(frame)).all()
    t['entry_ns']=ts[a]
    bucket_open=ts[a]//300_000_000_000*300_000_000_000
    touch_end=bucket_open+300_000_000_000-step*60_000_000_000
    confirm_end=bucket_open+600_000_000_000-step*60_000_000_000
    t['touch_end_i']=np.searchsorted(ts,touch_end)
    t['confirm_i']=np.searchsorted(ts,confirm_end)
    gaps=np.flatnonzero(np.diff(ts)!=step*60_000_000_000)
    bad_events=[]
    if job['symbol'] in BAD:
        bad_ns=pd.Timestamp(BAD[job['symbol']]).value
        bad_i=np.flatnonzero(ts==bad_ns)
        if len(bad_i):
            b=int(bad_i[0]);bad_events=t.loc[(t.population=='signal')&(t.entry_i==b),'event_id'].tolist()
            gaps=np.unique(np.r_[gaps,b-1])
    ends=t.end_i.to_numpy(dtype=int,copy=True)
    pos=np.searchsorted(gaps,a)
    ok=pos<len(gaps)
    ends[ok]=np.minimum(ends[ok],gaps[pos[ok]])
    t['end_i']=ends
    if bad_events:t=t[~t.event_id.isin(bad_events)].copy()
    # A boundary before the nominal close makes R unavailable to decisions.
    risk=[];wick_only=[]
    for r in t.itertuples():
        b=int(r.touch_end_i);start=np.searchsorted(ts,ts[int(r.entry_i)]//300_000_000_000*300_000_000_000)
        complete=(b<len(frame) and b<=r.end_i and b-start+1==5//step
                  and ts[b]==ts[int(r.entry_i)]//300_000_000_000*300_000_000_000+300_000_000_000-step*60_000_000_000)
        if complete:
            z=frame.iloc[start:b+1]
            extreme=z.low.min() if r.side==1 else z.high.max()
            risk.append(max(0.,r.side*(r.entry-extreme)))
            inside=(min(z.open.iloc[0],z.close.iloc[-1])>=r.entry if r.side==1 else max(z.open.iloc[0],z.close.iloc[-1])<=r.entry)
            wick_only.append(bool(inside and r.side*(z.close.iloc[-1]-r.entry)>0))
        else:risk.append(np.nan);wick_only.append(False)
    t['wick_r']=risk;t['wick_pct']=t.wick_r/t.entry*100;t['wick_only_reclaim']=wick_only
    t['baseline_net_bp']=t.net_bp
    t['baseline_after_slip_bp']=t.net_bp-np.where(t.reason.eq(4),0.,10.)
    t['baseline_exit_ns']=ts[t.exit_i.to_numpy(int)]+step*60_000_000_000
    t['baseline_closed']=t.closed;t['baseline_reason']=t.reason
    t=t.drop(columns=['net_bp','reason','closed','exit_i'])
    t.attrs['step_minutes']=step
    return t.reset_index(drop=True),dict(excluded_bad_pairs=len(bad_events),bad_event_ids=bad_events)


def process(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.evaluation.monday_wick_r_engine import execute
    import vectorbt
    cfg=json.loads((EXP/'config.json').read_text())
    dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    job=json.loads((SRC/symbol/'identity.json').read_text())['job']
    identity=dict(inputs=[stamp(x) for x in job['paths']],ledger=stamp(SRC/symbol/'trades.csv.gz'),
                  config=cfg,code=[stamp(ROOT/p) for p in CODE],engine=vectorbt.__version__)
    if (dest/'done.json').exists():
        assert json.loads((dest/'identity.json').read_text())==identity,'Frozen input/code changed'
        return json.loads((dest/'done.json').read_text())
    start=time.monotonic()
    ledger=pd.read_csv(SRC/symbol/'trades.csv.gz',usecols=COLS)
    ledger=ledger[(ledger.reclaim_minutes==5)&(ledger.exit_mode=='near_monday_edge')]
    if ledger.empty:
        done=dict(symbol=symbol,events=0,signals=0,seconds=time.monotonic()-start)
        dump(dest/'identity.json',identity);dump(dest/'done.json',done);return done
    f,quality=load_job(job)
    events,audit=prepare(f,ledger,job)
    events.attrs['step_minutes']=job['execution_minutes']
    stops=[np.nan if v is None else v/100 for v in cfg['initial_stop_percent']]
    out,fills=execute(f,events,stops,cfg['target_r'],cfg['maker_fee'],cfg['taker_fee'],cfg['non_target_slippage_bp'])
    np.testing.assert_allclose(out.gross_bp,out.side*(out.exit_price-out.entry)/out.entry*10000,equal_nan=False,
        atol=1e-7) if out.closed.all() else None
    assert len(out)==len(events)*len(stops)*len(cfg['target_r'])
    assert len(fills)==2*len(out)
    assert (out.exit_i>=out.entry_i).all()
    assert not ((out.reason==14)&(out.exit_i<out.touch_end_i)).any()
    assert not (out.fee_be_armed&~out.confirmed).any()
    events.to_parquet(dest/'events.parquet',index=False)
    out.to_parquet(dest/'trades.parquet',index=False)
    # Two native order records per independent parameter column, including
    # separately marked boundary valuations for censored positions.
    fills.to_parquet(dest/'fills.parquet',index=False)
    done=dict(symbol=symbol,source_rows=len(f),first=str(f.index[0]),last=str(f.index[-1]),
              events=len(events),signals=int(events.population.eq('signal').sum()),columns=len(out),fills=len(fills),
              zero_wick=int(events.wick_r.le(0).sum()),unknown_wick=int(events.wick_r.isna().sum()),
              seconds=time.monotonic()-start,**audit)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done)
    return done


def committed():
    for p in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git','diff','--quiet','HEAD','--',p],cwd=ROOT).returncode or subprocess.run(
            ['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode:
            raise RuntimeError('Commit builder and configuration before history execution')


def run(symbols=None,workers=2):
    committed()
    syms=[]
    for p in sorted(SRC.glob('*/done.json')):
        job=json.loads((p.parent/'identity.json').read_text())['job']
        if job['group']!='spot_gold' and (not symbols or p.parent.name in symbols):syms.append(p.parent.name)
    dump(EXP/'run_manifest.json',dict(symbols=syms,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    failures=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(process,s):s for s in syms}
        for i,f in enumerate(as_completed(futures),1):
            s=futures[f]
            try:d=f.result();print(json.dumps(dict(progress=f'{i}/{len(syms)}',**d)),flush=True)
            except Exception as exc:
                d=dict(symbol=s,error=repr(exc));failures.append(d);dump(EXP/'symbols'/s/'error.json',d);print(json.dumps(d),flush=True)
    dump(EXP/'execution.json',dict(expected=len(syms),failures=failures,complete=not failures))
    if failures:raise RuntimeError(f'{len(failures)} symbol failures')


def metrics(g):
    c=g[g.closed];v=c.net_after_slip_bp
    return dict(n=len(g),closed_n=len(c),censored_n=len(g)-len(c),mean_bp=float(v.mean()),sum_bp=float(v.sum()),
                mean_fee_only_bp=float(c.net_bp.mean()),mean_r=float(c.net_after_slip_r.mean()),
                sum_r=float(c.net_after_slip_r.sum()),win_pct=float(100*(v>1e-8).mean()),
                pf=float(v[v>0].sum()/-v[v<0].sum()) if (v<0).any() else np.nan,
                tp_n=int(c.reason.isin([4,14]).sum()),initial_stop_n=int(c.reason.eq(12).sum()),
                failed_confirm_n=int(c.reason.eq(11).sum()),fee_be_n=int(c.reason.eq(5).sum()),
                entry_stop_fee_loss_n=int(c.reason.eq(6).sum()),fee_be_armed_n=int(g.fee_be_armed.sum()),
                confirmed_n=int(g.confirmed.sum()),ambiguous_n=int(g.ambiguous.sum()))


def summarize():
    paths=sorted((EXP/'symbols').glob('*/trades.parquet'))
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    allrows=[];metadata=[]
    needed=['event_id','symbol','asset_group','week','side','population','control_id','entry_ns','exit_ns','closed',
            'stop_pct','target_r','net_bp','net_after_slip_bp','net_after_slip_r','reason','fee_be_armed','confirmed','ambiguous',
            'wick_pct','wick_only_reclaim']
    for p in paths:
        allrows.append(pd.read_parquet(p,columns=needed))
        metadata.append(pd.read_parquet(p.parent/'events.parquet'))
    t=pd.concat(allrows,ignore_index=True);meta=pd.concat(metadata,ignore_index=True)
    t['stop_label']=t.stop_pct.fillna(-1)
    split=pd.Timestamp('2026-01-01',tz='UTC').value;train=pd.Timestamp('2025-01-01',tz='UTC').value
    periods={'all':np.ones(len(t),bool),'pre2025':(t.entry_ns<train)&(t.exit_ns<train),
             'select2025':(t.entry_ns>=train)&(t.entry_ns<split)&(t.exit_ns<split),'check2026':t.entry_ns>=split}
    records=[]
    for period,pm in periods.items():
        for cohort,cm in [('altcoins',t.asset_group.eq('ALTCOINS')),('BTC',t.asset_group.eq('BTC')),('ETH',t.asset_group.eq('ETH'))]:
            z=t[pm&cm]
            for (sl,tp),g in z.groupby(['stop_label','target_r']):
                sig=g[g.population.eq('signal')];ctrl=g[g.population.eq('random')&g.closed]
                m=metrics(sig)
                c=ctrl.groupby('event_id').net_after_slip_bp.agg(['mean','count'])
                pair=sig[sig.closed].merge(c[c['count']==3],left_on='event_id',right_index=True)
                m.update(period=period,cohort=cohort,stop_pct=None if sl<0 else sl,target_r=tp,
                         matched_n=len(pair),matched_signal_bp=float(pair.net_after_slip_bp.mean()),
                         matched_random_bp=float(pair['mean'].mean()),
                         excess_bp=float((pair.net_after_slip_bp-pair['mean']).mean()))
                records.append(m)
    res=pd.DataFrame(records);res.to_csv(EXP/'metrics.csv',index=False)
    # The original framework baseline is read, never re-executed.
    base=[]
    for period in periods:
        pm={'all':np.ones(len(meta),bool),'pre2025':(meta.entry_ns<train)&(meta.baseline_exit_ns<train),
            'select2025':(meta.entry_ns>=train)&(meta.entry_ns<split)&(meta.baseline_exit_ns<split),
            'check2026':meta.entry_ns>=split}[period]
        for cohort,cm in [('altcoins',meta.asset_group.eq('ALTCOINS')),('BTC',meta.asset_group.eq('BTC')),('ETH',meta.asset_group.eq('ETH'))]:
            g=meta[pm&cm&meta.baseline_closed];s=g[g.population.eq('signal')];c=g[g.population.eq('random')]
            cc=c.groupby('event_id').baseline_after_slip_bp.agg(['mean','count']);p=s.merge(cc[cc['count']==3],left_on='event_id',right_index=True)
            base.append(dict(period=period,cohort=cohort,n=len(s),mean_bp=float(s.baseline_after_slip_bp.mean()),
                mean_fee_only_bp=float(s.baseline_net_bp.mean()),matched_n=len(p),matched_signal_bp=float(p.baseline_after_slip_bp.mean()),
                matched_random_bp=float(p['mean'].mean()),excess_bp=float((p.baseline_after_slip_bp-p['mean']).mean())))
    pd.DataFrame(base).to_csv(EXP/'baseline.csv',index=False)
    chosen={};details=[]
    for cohort in ['altcoins','BTC','ETH']:
        z=res[res.cohort.eq(cohort)]
        select=z[z.period.eq('select2025')].sort_values(['mean_bp','target_r'],ascending=[False,True]).iloc[0]
        full=z[z.period.eq('all')].sort_values(['mean_bp','target_r'],ascending=[False,True]).iloc[0]
        same=lambda d,sel:d.target_r.eq(sel.target_r)&(d.stop_pct.fillna(-1)==(-1 if pd.isna(sel.stop_pct) else sel.stop_pct))
        chosen[cohort]=dict(selected_on_2025=select.to_dict(),selected_evaluation=z[same(z,select)].to_dict('records'),
                           descriptive_full_best=full.to_dict())
        # Matched week-cluster bootstrap for the already selected candidate.
        sg=t[(t.asset_group==('ALTCOINS' if cohort=='altcoins' else cohort))&same(t,select)&(t.entry_ns>=split)&t.closed]
        s=sg[sg.population.eq('signal')];c=sg[sg.population.eq('random')].groupby('event_id').net_after_slip_bp.agg(['mean','count'])
        p=s.merge(c[c['count']==3],left_on='event_id',right_index=True)
        p['delta']=p.net_after_slip_bp-p['mean']
        weeks=p.groupby('week').delta.agg(['sum','count'])
        if len(weeks):
            rng=np.random.default_rng(20261026);idx=rng.integers(len(weeks),size=(3000,len(weeks)))
            boot=weeks['sum'].to_numpy()[idx].sum(1)/weeks['count'].to_numpy()[idx].sum(1)
            chosen[cohort]['check2026_excess_week_bootstrap_ci95']=np.quantile(boot,[.025,.975]).tolist()
        details.append(s.assign(cohort=cohort))
    pd.concat(details).to_parquet(EXP/'selected_2026_signals.parquet',index=False)
    sig=meta[meta.population.eq('signal')]
    out=dict(experiment_id=EXP.name,completed_symbols=len(done),expected_symbols=632,signals=len(sig),
             dates=[str(pd.to_datetime(sig.entry_ns.min(),utc=True)),str(pd.to_datetime(sig.entry_ns.max(),utc=True))],
             wick_pct_quantiles=sig.wick_pct.quantile([0,.1,.25,.5,.75,.9,.99,1]).to_dict(),
             unknown_wick_n=int(sig.wick_r.isna().sum()),zero_wick_n=int(sig.wick_r.le(0).sum()),
             chosen=chosen,baseline=base,engine='VectorBT1.2.0',
             limit='Independent equal-notional trade returns; no funding/capital/margin liquidation. Historical Binance candles with OKX account fees. 2026 is previously exposed history.',
             training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',out)
    print(json.dumps(dict(signals=out['signals'],symbols=out['completed_symbols'],chosen=chosen),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['run','summarize']);parser.add_argument('--symbols',nargs='*');parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args()
    if args.command=='run':run(args.symbols,args.workers)
    else:summarize()

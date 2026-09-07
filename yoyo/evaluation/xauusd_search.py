"""Reproducible finite XAUUSD system comparison, frozen before outcome runs.

Read-only raw HistData quotes live in memory. Only metadata, decisions,
trade ledgers and research equity summaries are saved. Validation nominates
one policy; confirmation never ranks the full policy universe. All fixed
rules and costs are declared in the experiment's preregistration.json.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.data.xauusd_histdata import fetch_range
from yoyo.evaluation.xauusd_systems import TIMEFRAMES,HIGH,CANDIDATES,aggregate_gold,features,attach_higher,candidate
from yoyo.evaluation.xauusd_execution import simulate
from yoyo.evaluation.xauusd_controls import matched_controls,holm

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'experiments/active/exp-xauusd-system-search-20260908-v1'
SOURCE_FILES=['yoyo/data/xauusd_histdata.py','yoyo/evaluation/xauusd_systems.py',
              'yoyo/evaluation/xauusd_execution.py','yoyo/evaluation/xauusd_controls.py',
              'yoyo/evaluation/xauusd_search.py','yoyo/evaluation/imacd_indicator_audit.py',
              'experiments/active/exp-xauusd-system-search-20260908-v1/preregistration.json']


def clean(value):
    if isinstance(value,dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [clean(v) for v in value]
    if isinstance(value,(np.integer,)): return int(value)
    if isinstance(value,(np.bool_,)): return bool(value)
    if isinstance(value,(float,np.floating)): return float(value) if np.isfinite(value) else None
    if isinstance(value,(datetime,pd.Timestamp)): return value.isoformat()
    return value


def save_json(path,value):
    path.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def source_receipt():
    subprocess.run(['git','diff','--quiet','HEAD','--',*SOURCE_FILES],cwd=ROOT,check=True)
    for path in SOURCE_FILES:
        subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    return dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                files={p:sha(ROOT/p) for p in SOURCE_FILES})


def gap_audit(m):
    diff=m.index.to_series().diff().dt.total_seconds().div(60)
    p=np.flatnonzero(diff.to_numpy()>1)
    gaps=pd.DataFrame({'previous':m.index[p-1],'next':m.index[p], 'missing_minutes':diff.iloc[p].to_numpy()-1})
    gaps['possible_weekend']=((gaps.previous.dt.weekday>=4)&gaps.next.dt.weekday.isin([6,0])&gaps.missing_minutes.between(24*60,96*60))
    gaps['possible_daily_closure']=gaps.previous.dt.hour.between(20,23)&gaps.missing_minutes.between(40,180)
    gaps['unclassified']=~(gaps.possible_weekend|gaps.possible_daily_closure)
    # These labels are audit hints, not a fabricated exchange calendar.
    return gaps


def passive(m,start,end,side=1):
    start=pd.Timestamp(start,tz='UTC')
    b=pd.DataFrame({'time_close':[start]},index=pd.DatetimeIndex([start-pd.Timedelta(minutes=1)]))
    return simulate(m,b,[side],[False],[False],start,end)


def audit_ledger(m,ledger,start,end):
    """Independently reconcile every actual fill, fee and equity transition."""
    previous=1.;checks=0
    for r in ledger.itertuples():
        i=int(r.entry_index);j=int(r.exit_index)
        assert i==m.index.searchsorted(r.decision_time)
        assert r.entry_time==m.index[i] and r.entry_price==m.open.iloc[i]
        assert pd.Timestamp(start,tz='UTC')<=r.decision_time<pd.Timestamp(end,tz='UTC')
        assert r.exit_time<=pd.Timestamp(end,tz='UTC') and r.exit_time>=r.entry_time
        if pd.notna(r.exit_decision_time):
            assert j==m.index.searchsorted(r.exit_decision_time)
            assert r.exit_price==m.open.iloc[j] and r.exit_time==m.index[j]
        else:
            assert r.exit_price==m.close.iloc[j] and r.exit_time==m.index[j]+pd.Timedelta(minutes=1)
        n=previous/1.001
        gross=r.side*n*(r.exit_price/r.entry_price-1)
        expected=max(0,previous+gross-0.002*n)
        np.testing.assert_allclose([r.equity_before,r.notional,r.entry_fee,r.exit_fee,r.equity_after],
                                   [previous,n,n*.001,n*.001,expected],rtol=1e-10,atol=1e-12)
        previous=expected;checks+=11
    return checks


def run():
    out=OUT/'results';out.mkdir(exist_ok=True)
    if (out/'nomination.json').exists():
        raise RuntimeError('Existing nomination: do not silently consume confirmation again or overwrite evidence.')
    receipt=source_receipt()
    save_json(out/'source_freeze.json',receipt)
    cfg=json.loads((OUT/'preregistration.json').read_text())
    assert cfg['timeframes']==list(TIMEFRAMES) and list(cfg['candidates'])==list(CANDIDATES)
    assert cfg['cost_bp_round_trip']==20 and len(CANDIDATES)*len(TIMEFRAMES)==189
    print('SOURCE_FROZEN '+receipt['commit'],flush=True)
    data=fetch_range(cfg['data_start'],cfg['data_end_exclusive'],
                     as_of='2026-09-08',progress_callback=lambda a:print('ARCHIVE '+str(a['archive_token'])+' rows='+str(a.get('rows',a.get('row_count','?'))),flush=True))
    m=data.frame
    save_json(out/'source_metadata.json',data.metadata)
    gaps=gap_audit(m);gaps.to_csv(out/'quote_gaps.csv.gz',index=False,compression='gzip')
    print(f'DATA_READY {len(m)} {m.index[0]} {m.index[-1]}',flush=True)
    # HTF precomputation is causal and contains no outcomes or fitted weights.
    high={}
    for duration in sorted(set(HIGH.values())):
        b=aggregate_gold(m,duration);high[duration]=(b,features(b))
    summary=[]; curves={};coverage=[]
    for duration in TIMEFRAMES:
        b=aggregate_gold(m,duration)
        f=attach_higher(b,features(b),*high[HIGH[duration]])
        eligible=f.ready&(b.time_close>=pd.Timestamp('2024-01-01',tz='UTC'))&(b.time_close<pd.Timestamp('2026-01-01',tz='UTC'))
        if not bool(eligible.any()) or not bool(f.loc[eligible,'h_known'].all()): raise RuntimeError(f'Incomplete coverage or HTF warmup for {duration}')
        coverage.append(dict(timeframe=duration,total_bars=len(b),selection_bars=int(eligible.sum()),
                             first=b.index[0],last_close=b.time_close.iloc[-1],
                             median_observed_minutes=b.observed_minutes.median(),
                             low_coverage_bars=int((b.observed_minutes<duration*.5).sum())))
        for name in CANDIDATES:
            e,xl,xs=candidate(b,f,name)
            r=simulate(m,b,e,xl,xs,*cfg['selection'])
            r['stats']['ledger_checks']=audit_ledger(m,r['ledger'],*cfg['selection'])
            c=matched_controls(m,b,f,e,xl,xs,r['ledger'],*cfg['selection'])
            key=f'{name}_{duration}m'
            summary.append(dict(candidate=name,timeframe=duration,name=CANDIDATES[name][0],**r['stats'],**c['summary']))
            r['ledger'].to_csv(out/f'selection_{key}_trades.csv.gz',index=False,compression='gzip')
            c['pairs'].to_csv(out/f'selection_{key}_controls.csv.gz',index=False,compression='gzip')
            curves[key]=r['daily_equity']
            print(f'SELECTION {key} return={r["stats"]["return_pct"]:.4f} dd={r["stats"]["max_drawdown_pct"]:.4f} n={r["stats"]["trades"]}',flush=True)
        pd.DataFrame(summary).to_csv(out/'selection_partial.csv',index=False)
    table=pd.DataFrame(summary)
    # Every attempted candidate remains in the family for multiplicity control.
    pcol='paired_p'
    if pcol in table: table['holm_p']=holm(table[pcol].to_numpy())
    table=table.sort_values(['return_pct','max_drawdown_pct','trades','candidate','timeframe'],ascending=[False,True,True,True,True])
    table.to_csv(out/'selection_all.csv',index=False)
    pd.DataFrame(curves).to_csv(out/'selection_daily_equity.csv.gz',compression='gzip')
    save_json(out/'coverage.json',coverage)
    winner=table.iloc[0]
    imacd=table.loc[~table.candidate.str.startswith(('T','R'))].iloc[0]
    passive_selection=passive(m,*cfg['selection'])
    overall_choices=[dict(candidate=winner.candidate,timeframe=int(winner.timeframe),return_pct=float(winner.return_pct),max_drawdown_pct=float(winner.max_drawdown_pct),trades=int(winner.trades)),
                     dict(candidate='BUY_HOLD',timeframe=0,**passive_selection['stats']),
                     dict(candidate='CASH',timeframe=0,return_pct=0,max_drawdown_pct=0,trades=0)]
    overall=sorted(overall_choices,key=lambda r:(-r['return_pct'],r['max_drawdown_pct'],r['trades'],r['candidate'],r['timeframe']))[0]
    nomination=dict(created_at=datetime.now(timezone.utc),candidate=winner.candidate,timeframe=int(winner.timeframe),
                    best_imacd=dict(candidate=imacd.candidate,timeframe=int(imacd.timeframe)),
                    best_including_passive=overall,passive_selection_stats=passive_selection['stats'],
                    ranking_window=cfg['selection'],selection_table_sha256=sha(out/'selection_all.csv'),
                    source_freeze=receipt,confirmation_economic_runs_before_nomination=0,
                    note='2026 price charts previously exposed for visual QA; no 2026 outcome ranking used.')
    save_json(out/'nomination.json',nomination)
    print('NOMINATED '+str(winner.candidate)+' '+str(winner.timeframe),flush=True)
    final=[];final_curves={}
    todo=list(dict.fromkeys([(str(winner.candidate),int(winner.timeframe)),
                            (str(imacd.candidate),int(imacd.timeframe)),('C02',240)]))
    for name,duration in todo:
        b=aggregate_gold(m,duration);f=attach_higher(b,features(b),*high[HIGH[duration]])
        e,xl,xs=candidate(b,f,name)
        for stage in (['confirmation','development'] if (name,duration)==todo[0] else ['confirmation']):
            r=simulate(m,b,e,xl,xs,*cfg[stage])
            r['stats']['ledger_checks']=audit_ledger(m,r['ledger'],*cfg[stage])
            c=matched_controls(m,b,f,e,xl,xs,r['ledger'],*cfg[stage])
            key=f'{name}_{duration}m'
            final.append(dict(stage=stage,candidate=name,timeframe=duration,**r['stats'],**c['summary']))
            r['ledger'].to_csv(out/f'{stage}_{key}_trades.csv',index=False)
            c['pairs'].to_csv(out/f'{stage}_{key}_controls.csv',index=False)
            final_curves[f'{stage}_{key}']=r['daily_equity']
            print(f'{stage.upper()} {key} return={r["stats"]["return_pct"]:.4f} dd={r["stats"]["max_drawdown_pct"]:.4f}',flush=True)
    for stage in ('selection','confirmation','development'):
        r=passive(m,*cfg[stage]);final.append(dict(stage=stage,candidate='BUY_HOLD',timeframe=0,**r['stats']))
        r['ledger'].to_csv(out/f'{stage}_buy_hold.csv',index=False)
        final_curves[stage+'_BUY_HOLD']=r['daily_equity']
        final.append(dict(stage=stage,candidate='CASH',timeframe=0,return_pct=0,max_drawdown_pct=0,trades=0))
    pd.DataFrame(final).to_csv(out/'final_summary.csv',index=False)
    pd.DataFrame(final_curves).to_csv(out/'final_daily_equity.csv.gz',compression='gzip')
    save_json(out/'completion.json',dict(completed_at=datetime.now(timezone.utc),source=receipt,
        nomination_sha256=sha(out/'nomination.json'),selection_count=len(table),
        confirmation_configurations=todo,confirmation_holdout_economic_use_each=1,
        raw_minute_files_written=0,latest_minute_close=m.index[-1]+pd.Timedelta(minutes=1)))
    print('COMPLETE',flush=True)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    run()

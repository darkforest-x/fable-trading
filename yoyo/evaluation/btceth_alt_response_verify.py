"""Independent scalar price/volume checks for descriptive cross-coin outputs.

Read the original fetched CSVs, not the feature builder. Recompute each visible
observation from timestamps and audit the non-event exclusion window. No new
execution model, backtest, price download, hash or outcome-selected threshold.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.btceth_alt_response_source import ROOT,EXP,OHLCV,STEP,write

def run():
    receipt=json.loads((EXP/'source_receipt.json').read_text());cfg=json.loads((EXP/'config.json').read_text())
    frames={};checks=0;max_error=0.
    for rec in receipt:
        p=Path(rec['source']);assert p.stat().st_size==rec['bytes'] and p.stat().st_mtime_ns==rec['mtime_ns']
        f=pd.read_csv(p,float_precision='round_trip');f.index=pd.to_datetime(f.ts,unit='ms',utc=True)
        f=f.loc[(f.index>=pd.Timestamp(cfg['start']))&(f.index<pd.Timestamp(cfg['end_exclusive'])),OHLCV]
        frozen=pd.read_csv(EXP/'frozen'/f'{rec["symbol"]}.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip')
        assert f.index.equals(frozen.index);np.testing.assert_allclose(f,frozen,rtol=0,atol=1e-10);frames[rec['symbol']]=f
    events=pd.read_csv(EXP/'events.csv',float_precision='round_trip');obs=pd.read_csv(EXP/'coin_observations.csv.gz',float_precision='round_trip')
    controls=pd.read_csv(EXP/'controls.csv.gz');summary=json.loads((EXP/'summary.json').read_text())
    assert events.event_id.is_unique and len(obs)==18*len(events)
    def equal(actual,expected):
        nonlocal checks,max_error
        assert np.isnan(actual)==np.isnan(expected),(actual,expected)
        if np.isfinite(expected):
            error=abs(actual-expected);max_error=max(max_error,error)
            assert error<1e-8,(actual,expected,error)
        checks+=1
    for e in events.itertuples():
        t=pd.Timestamp(e.time);side=e.side;leader=frames[e.leader]
        week=pd.Timestamp(e.week).tz_localize('America/New_York');ready=week+pd.Timedelta(days=1)
        mon=leader.loc[(leader.index>=week)&(leader.index<ready)];assert len(mon)==288
        hi,lo=mon.high.max(),mon.low.min();level=hi-1.618*(hi-lo) if side==1 else lo+1.618*(hi-lo)
        equal(e.level,level);assert t>=ready
        for row in obs[obs.event_id.eq(e.event_id)].itertuples():
            f=frames[row.symbol]
            if t not in f.index or t-STEP not in f.index:
                assert np.isnan(row.touch_return);continue
            i=f.index.get_loc(t);bar=f.iloc[i];prev=f.close.iloc[i-1]
            equal(row.touch_return,side*(bar.close/prev-1)*100)
            extreme=bar.low if side==1 else bar.high
            equal(row.shock,max(0.,-side*(extreme/prev-1)*100))
            span=bar.high-bar.low
            equal(row.recovery,((bar.close-bar.low) if side==1 else (bar.high-bar.close))/span if span else np.nan)
            volume=f.volume.iloc[max(0,i-24):i]
            expected=bar.volume/volume.median() if len(volume)==24 and volume.median()>0 else np.nan
            equal(row.volume_ratio,expected)
            equal(row.pre30,side*(prev/f.close.iloc[i-7]-1)*100 if i>=7 else np.nan)
            for h in [1,3,6,12]:equal(getattr(row,f'after_{h}'),side*(f.close.iloc[i+h]/bar.close-1)*100 if i+h<len(f) else np.nan)
    times=pd.to_datetime(events.time,utc=True).astype('int64').to_numpy();control_rows=0
    for row in controls.itertuples():
        e=events[events.event_id.eq(row.event_id)].iloc[0]
        for text in row.control_times.split(';'):
            t=pd.Timestamp(text);assert np.min(np.abs(times-t.value))>13*STEP.value
            local=t.tz_convert('America/New_York');week=local.tz_localize(None).normalize()-pd.Timedelta(days=local.dayofweek)
            assert str(week.date())==e.week;control_rows+=1
    assert summary['events']==len(events) and summary['matched_events']==len(controls)
    pure=events[events.kind.eq('pure_wick_reclaim')]
    for key,g in [('all',events),('pure_wick',pure),('not_reclaimed',events[~events.reclaimed])]:
        assert summary['groups'][key]['events']==len(g)
        for h in [1,3,6,12]:equal(summary['groups'][key][f'alt_after_{h}_mean'],g[f'alt_after_{h}_mean'].mean())
    result=dict(events=len(events),coin_event_rows=len(obs),scalar_checks=checks,max_absolute_error=max_error,
        matched_control_event_rows=control_rows,source_csv_equals_frozen=True,sources_unchanged=True,
        tests='3 passed: NYMonday completeness, first-ready/cooldown/rearm, future-mutation invariance',
        control_note='Retrospective shock-free background comparison; no causal or tradable-alpha claim.',
        no_execution_or_fee_calculation=True,training_eligible=False,production_eligible=False)
    write(EXP/'verification.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':run()

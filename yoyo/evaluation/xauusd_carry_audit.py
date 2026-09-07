"""Post-selection diagnostic separating carried inventory from a fresh account.

This diagnostic was requested by the research interpretation AFTER the first
2026 fresh-account result was observed. It cannot be called untouched OOS or
used to choose another winner. It extends the already frozen winning policy
from 2024 without the artificial 2025 year-end liquidation, then measures the
2026 equity segment on the SAME observed minute-close grid. No rule changes.
"""
from __future__ import annotations
import hashlib
import subprocess
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.xauusd_systems import HIGH,aggregate_gold,features,attach_higher,candidate
from yoyo.evaluation.xauusd_execution import simulate
from yoyo.evaluation.xauusd_search import OUT,ROOT,save_json,audit_ledger
import json


def reconstruct_marks(minutes,ledger,start,end):
    """Rebuild funded minute CLOSE equity from a completed trade ledger."""
    a=int(minutes.index.searchsorted(pd.Timestamp(start,tz='UTC')))
    z=int(minutes.index.searchsorted(pd.Timestamp(end,tz='UTC')-pd.Timedelta(minutes=1),side='right'))
    m=minutes.iloc[a:z];equity=np.ones(len(m));cash=1.;cursor=0
    for r in ledger.itertuples():
        i=int(r.entry_index)-a;j=int(r.exit_index)-a
        equity[cursor:i]=cash
        boundary=pd.isna(r.exit_decision_time)
        stop=j+1 if boundary else j
        equity[i:stop]=r.cash_after_entry_fee+r.units*(m.close.iloc[i:stop].to_numpy()-r.entry_price)
        cash=r.equity_after
        if boundary:
            equity[j]=cash
        cursor=stop
    equity[cursor:]=cash
    return pd.Series(equity,index=m.index,name='equity')


def run(data):
    path=ROOT/'yoyo/evaluation/xauusd_carry_audit.py'
    subprocess.run(['git','diff','--quiet','HEAD','--',str(path)],cwd=ROOT,check=True)
    out=OUT/'results'
    if (out/'carry_diagnostic.json').exists(): raise RuntimeError('Do not silently rerun carry diagnostic')
    nom=json.loads((out/'nomination.json').read_text())
    name,duration=nom['candidate'],int(nom['timeframe'])
    m=data.frame;b=aggregate_gold(m,duration);hb=aggregate_gold(m,HIGH[duration])
    f=attach_higher(b,features(b),hb,features(hb));e,xl,xs=candidate(b,f,name)
    result=simulate(m,b,e,xl,xs,'2024-01-01','2026-06-01')
    checks=audit_ledger(m,result['ledger'],'2024-01-01','2026-06-01')
    marks=reconstruct_marks(m,result['ledger'],'2024-01-01','2026-06-01')
    np.testing.assert_allclose(marks.iloc[-1],result['stats']['final_equity'],rtol=1e-10)
    split=pd.Timestamp('2026-01-01',tz='UTC')
    before=float(marks.loc[marks.index<split].iloc[-1])
    segment=marks.loc[marks.index>=split]/before
    curve=np.r_[1.,segment.to_numpy()]
    dd=float((1-curve/np.maximum.accumulate(curve)).max()*100)
    carried=result['ledger'].loc[(result['ledger'].entry_time<split)&(result['ledger'].exit_time>=split)]
    artifact=dict(generated_at=datetime.now(timezone.utc),diagnostic='post_hoc_carry_in_explanation_not_pristine_OOS',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),nomination_sha256=hashlib.sha256((out/'nomination.json').read_bytes()).hexdigest(),
        candidate=name,timeframe=duration,segment_start='2026-01-01',segment_end_exclusive='2026-06-01',
        return_pct=float((segment.iloc[-1]-1)*100),max_drawdown_pct=dd,
        equity_at_split=before,carried_positions=len(carried),fresh_entries_in_2026=int((result['ledger'].entry_time>=split).sum()),
        full_path_stats=result['stats'],ledger_checks=checks,
        source_raw_reused_in_memory=True,raw_minutes_written=0,
        holdout='Post-hoc carry configuration first economic use; same selected F01 rules, inventory clock differs. Fresh-account confirmation unchanged.',
        caveat='Conditional on already holding the historical position; not an executable fresh Jan2026 entry recommendation or a new winning strategy.',
        training_eligible=False,production_eligible=False)
    save_json(out/'carry_diagnostic.json',artifact)
    result['ledger'].to_csv(out/'carry_2024_2026_trades.csv',index=False)
    segment.resample('D').last().ffill().to_csv(out/'carry_daily_equity.csv')
    print(json.dumps({k:artifact[k] for k in ('candidate','timeframe','return_pct','max_drawdown_pct','carried_positions','fresh_entries_in_2026')},ensure_ascii=False),flush=True)
    return artifact

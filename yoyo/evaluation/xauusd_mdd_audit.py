"""Independent minute-close reconciliation of already reported portfolio MDDs.

No signals, parameters or candidate ranking are recomputed. Reconstruct marks
from persisted positions and the same in-memory source quotes, using the
separate inventory reconstruction implemented for the carry diagnostic.
"""
from pathlib import Path
import json
import hashlib
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.xauusd_search import OUT,ROOT,save_json
from yoyo.evaluation.xauusd_carry_audit import reconstruct_marks


def run(data):
    out=OUT/'results';rows=[]
    selection=pd.read_csv(out/'selection_all.csv')
    final=pd.read_csv(out/'final_summary.csv')
    cfg=json.loads((OUT/'preregistration.json').read_text())
    cases=[('selection',r) for r in selection.itertuples()]
    cases += [(r.stage,r) for r in final.itertuples() if r.candidate!='CASH']
    for stage,r in cases:
        if r.candidate=='BUY_HOLD':
            path=out/f'{stage}_buy_hold.csv'
        else:
            key=f'{r.candidate}_{int(r.timeframe)}m'
            path=out/f'{stage}_{key}_trades.csv'
            if stage=='selection': path=path.with_suffix('.csv.gz')
        ledger=pd.read_csv(path)
        ledger['exit_decision_time']=pd.to_datetime(ledger.exit_decision_time,utc=True)
        marks=reconstruct_marks(data.frame,ledger,*cfg[stage])
        a=np.r_[1.,marks.to_numpy()]
        expected=float((1-a/np.maximum.accumulate(a)).max()*100)
        net=float((marks.iloc[-1]-1)*100)
        np.testing.assert_allclose([expected,net],[r.max_drawdown_pct,r.return_pct],rtol=1e-9,atol=1e-8)
        rows.append(dict(stage=stage,candidate=r.candidate,timeframe=int(r.timeframe),trades=len(ledger),
                         reconstructed_mdd_pct=expected,reported_mdd_pct=r.max_drawdown_pct,
                         mdd_abs_error=abs(expected-r.max_drawdown_pct),return_abs_error=abs(net-r.return_pct)))
    source=ROOT/'yoyo/evaluation/xauusd_mdd_audit.py'
    result=dict(status='passed',portfolios=len(rows),trades=sum(r['trades'] for r in rows),
        max_mdd_abs_error=max(r['mdd_abs_error'] for r in rows),max_return_abs_error=max(r['return_abs_error'] for r in rows),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        method='Independent minute-close inventory reconstruction from saved ledgers and validated quotes in RAM; no parameter or candidate selection changes.',
        raw_minute_files_written=0,training_eligible=False,production_eligible=False,portfolios_detail=rows)
    save_json(out/'minute_mdd_audit.json',result)
    print({k:result[k] for k in ('status','portfolios','trades','max_mdd_abs_error','max_return_abs_error')},flush=True)
    return result

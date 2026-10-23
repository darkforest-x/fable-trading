"""Validate body-hold ledgers and immutable baseline without replaying fills."""
from __future__ import annotations
from importlib.metadata import version
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_body_study import EXP, CODE, previous
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp


def main():
    f=pd.read_csv(EXP/'all-trades.csv.gz',float_precision='round_trip')
    a=f[f.population.eq('signal')].copy()
    assert not f.duplicated(['config_id','symbol','trade_id']).any()
    assert a.risk.gt(0).all()
    assert a.mfe_r.ge(-1e-10).all() and a.mae_r.ge(-1e-10).all()
    assert (a.mfe_r+1e-8>=a.gross_r).all()
    assert (a.mae_r+1e-8>=-a.gross_r).all()
    assert (a.mfe_r<=a.planned_rr+1e-7).all(), 'Excursion includes price beyond live target'
    assert (a.mfe_before_exit_bar_r<=a.mfe_r+1e-10).all()
    elapsed=pd.to_datetime(a.entry_time,utc=True)-pd.to_datetime(a.pin_time,utc=True)
    assert (elapsed==pd.to_timedelta(a.confirmations+1,unit='h')).all()
    assert not a.duplicated(['config_id','symbol','source_segment','week','side']).any()
    crypto=f[~f.symbol.eq('XAUUSD')]; spot=f[f.symbol.eq('XAUUSD')]
    np.testing.assert_allclose(crypto.gross_r-crypto.net_r,crypto.fee_r,rtol=1e-9,atol=1e-9)
    np.testing.assert_allclose(crypto.fee_r,(crypto.entry*crypto.fee_entry_rate+crypto.exit_price*crypto.fee_exit_rate)/crypto.risk,rtol=1e-9,atol=1e-9)
    assert spot.net_r.isna().all() and spot.fee_r.isna().all()
    metrics=pd.read_csv(EXP/'metrics.csv')
    prior=pd.read_csv(previous.EXP/'metrics-entry-week.csv')
    prior=prior[prior.config_id.eq('case3-n3-v20')]
    ours=metrics[metrics.config_id.eq('strict-n3')]
    joined=ours.merge(prior,on=['group','period'],suffixes=('_new','_old'))
    for col in ['signals','closed','censored','win_rate','mean_r','total_r','mean_bp','profit_factor','paired_signal_r','paired_random_r']:
        np.testing.assert_allclose(joined[col+'_new'],joined[col+'_old'],rtol=1e-9,atol=1e-9,equal_nan=True)
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    cfg=json.loads((EXP/'config.json').read_text())
    jobs,_=previous.old.jobs(cfg)
    assert {d['symbol'] for d in done}=={j[0] for j in jobs}
    native_fills=0;inputs={};cache_stamps=[]
    for d in done:
        folder=EXP/'symbols'/d['symbol']; native=folder/'native-fills.csv.gz'
        count=len(pd.read_csv(native,usecols=['trade_key'])) if native.exists() else 0
        assert count==2*d['new_positions'];native_fills+=count
        identity=json.loads((folder/'identity.json').read_text())
        for stamp in identity['inputs']:
            assert file_stamp(stamp['path'])==stamp
            inputs[stamp['path']]=stamp
        if identity['prior_ledger']:
            assert file_stamp(identity['prior_ledger']['path'])==identity['prior_ledger']
            cache_stamps.append(identity['prior_ledger'])
        assert identity['code']==[file_stamp(ROOT/p) for p in CODE]
    result=dict(status='passed',symbols=len(done),metric_baseline_rows=len(joined),
        signal_rows=len(a),new_framework_positions=sum(d['new_positions'] for d in done),native_fills=native_fills,
        reused_positions=sum(d['reused_positions'] for d in done),strict_subset_checks=sum(d['strict_subset_checks'] for d in done),
        checks=['unique identities','same first-touch admission','causal next-open timing','exit-truncated excursion bounds',
        'spot net remains unknown','actual fill fee arithmetic','immutable strict baseline parity',
        'complete universe','two native fills per new position','unchanged source/cache stamps'],
        focused_tests=15,training_eligible=False,production_eligible=False)
    dump(EXP/'validation.json',result)
    receipt=dict(config=cfg,code=[file_stamp(ROOT/p) for p in CODE+[str(Path(__file__).relative_to(ROOT))]],
        inputs=list(inputs.values()),prior_ledgers=cache_stamps,
        versions={p:version(p) for p in ('vectorbt','numpy','pandas','numba')},
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        reproduction=['./scripts/backtest-monday-body --workers 3','/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_body_validation'],
        validation=result)
    dump(EXP/'completion.json',receipt)
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    from pathlib import Path
    main()

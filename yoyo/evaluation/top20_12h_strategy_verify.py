"""Read-only reconciliation of saved native fills; no execution replay."""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.top20_12h_strategies import EXP,ROOT,dump
from yoyo.evaluation.top20_12h_strategy_analysis import periods


def main():
    total_legs=total_fills=clock_violations=0;max_error=0.;sources=[]
    for done in sorted((EXP/'symbols').glob('*/done.json')):
        receipt=json.loads(done.read_text());source=receipt['source']
        p=ROOT/source['source'];stat=p.stat()
        assert stat.st_size==source['bytes'] and stat.st_mtime_ns==source['mtime_ns']
        sources.append(dict(symbol=receipt['symbol'],unchanged_size_and_mtime=True,
                            legs=receipt['legs'],fills=receipt['fills']))
        if not receipt['legs']:continue
        x=pd.read_csv(done.parent/'legs.csv.gz',float_precision='round_trip')
        fills=pd.read_csv(done.parent/'fills.csv.gz',float_precision='round_trip')
        assert x.leg_id.is_unique and len(x)==receipt['legs'] and len(fills)==receipt['fills']
        assert len(fills)==len(x)+x.closed.sum()
        count=fills.groupby('leg_id').size().reindex(x.leg_id).to_numpy()
        np.testing.assert_array_equal(count,1+x.closed.to_numpy(int))
        units=100/x.entry
        fee=(x.entry*x.entry_fee+np.where(x.closed,x.exit_price*np.where(x.reason.eq('target'),.0002,.0005),0))*x.weight*units
        pnl=x.side*(x.exit_price-x.entry)*x.weight*units-fee
        error=float(np.max(abs(pnl/(x.risk*units)-x.net_r)));max_error=max(max_error,error)
        assert error<1e-7
        sig=x[~x.control]
        violations=int((pd.to_datetime(sig.entry_time,utc=True)<pd.to_datetime(sig.signal_close,utc=True)).sum())
        assert violations==0;clock_violations+=violations
        assert x.risk.gt(0).all() and x.entry.gt(0).all() and x.exit_price.gt(0).all()
        assert (x.reason.eq('censored')==~x.closed).all()
        total_legs+=len(x);total_fills+=len(fills)
    trades=pd.read_csv(EXP/'trades.csv.gz',float_precision='round_trip')
    paths=pd.read_csv(EXP/'path_results.csv.gz',float_precision='round_trip')
    assert not trades.duplicated(['event_id','config']).any()
    minimum=paths.groupby(['event_id','config']).net_r.min()
    actual=trades.set_index(['event_id','config']).net_r
    np.testing.assert_allclose(actual.sort_index(),minimum.sort_index(),rtol=1e-9,atol=1e-9)
    valid=trades[periods(trades)['all']]
    assert (pd.to_datetime(valid.signal_close,utc=True)+pd.Timedelta(days=29)<=pd.to_datetime(valid.source_end,utc=True)).all()
    headline=pd.read_csv(EXP/'headline.csv',float_precision='round_trip')
    signal=trades[~trades.control]
    for r in headline.itertuples():
        selected=signal[periods(signal)[r.period]&signal.config.eq(r.config)]
        assert len(selected)==r.n
        np.testing.assert_allclose(selected.net_r.sum(),r.net_r,atol=1e-8)
        np.testing.assert_allclose(selected.net_r.gt(0).mean()*100,r.win_pct,atol=1e-8)
    result=dict(symbols=len(sources),native_legs=total_legs,native_fills=total_fills,
        max_fee_pnl_reconciliation_error_r=max_error,entry_clock_violations=clock_violations,
        headline_rows_reconciled=len(headline),native_positions_not_replayed=True,
        maturity_gate_verified=True,worse_path_aggregation_verified=True,
        synthetic_behavioral_tests=14,sources=sources,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
    dump(EXP/'verification.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()

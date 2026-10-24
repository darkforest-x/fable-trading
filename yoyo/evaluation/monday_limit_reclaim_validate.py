"""Reconcile saved native event ledgers without replaying prices or trades.

Only persisted native fills and positions are read. USDT commission precision
is eight decimal places in installed Nautilus 1.231.0. A half-unit rounding
tolerance verifies money fields; performance always uses actual commissions.
"""
from __future__ import annotations
import argparse
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_limit_reclaim import EXP
from yoyo.evaluation.monday_ma6_retest import finite
from yoyo.evaluation.range_framework import dump


def read(p):
    try:return pd.read_csv(p,float_precision='round_trip',low_memory=False)
    except pd.errors.EmptyDataError:return pd.DataFrame()


def validate(folder):
    done=json.loads((folder/'done.json').read_text());a=json.loads((folder/'audit.json').read_text())
    t=read(folder/'trades.csv.gz');f=read(folder/'fills.csv.gz');e=read(folder/'orders.csv.gz')
    assert len(t)==done['positions']==a['positions']
    assert len(f)==done['fills']==a['fills']==2*len(t)
    if t.empty:return dict(symbol=done['symbol'],positions=0,fills=0,passed=True)
    assert t.trade_id.is_unique and set(f.trade_id)==set(t.trade_id)
    native=f.groupby('trade_id',sort=False)
    assert native.size().eq(2).all()
    assert f[f.phase.eq('entry')].trade_id.is_unique
    opening=f[f.phase.eq('entry')].set_index('trade_id')
    closing=f[~f.phase.eq('entry')].set_index('trade_id')
    np.testing.assert_allclose(t.entry,t.trade_id.map(opening.price),rtol=1e-12,atol=1e-12)
    np.testing.assert_allclose(t.exit_price,t.trade_id.map(closing.price),rtol=1e-12,atol=1e-12)
    np.testing.assert_allclose(t.quantity,t.trade_id.map(opening.quantity))
    np.testing.assert_allclose(t.quantity,t.trade_id.map(closing.quantity))
    np.testing.assert_allclose(t.gross_bp,t.side*(t.exit_price-t.entry)/t.entry*10000,atol=1e-8)
    gold=done['symbol']=='XAUUSD'
    expected=f.price*f.quantity*np.where(f.phase.isin(['entry','target']),.0002,.0005)
    np.testing.assert_allclose(f.commission,0 if gold else expected,atol=5.1e-9,rtol=1e-10)
    if gold:
        assert t.fee_bp.isna().all() and t.net_bp.isna().all()
        np.testing.assert_allclose(t.return_bp,t.gross_bp,atol=1e-8)
    else:
        fees=t.trade_id.map(native.commission.sum())/t.quantity/t.entry*10000
        np.testing.assert_allclose(t.fee_bp,fees,atol=1e-8)
        np.testing.assert_allclose(t.return_bp,t.gross_bp-t.fee_bp,atol=1e-8)
        assert t[t.reason.eq(5)].return_bp.lt(0).all()
    assert set(t.reason).issubset({0,4,5,11})
    assert t.closed.eq(t.reason.ne(0)).all()
    assert t.normalization.eq('entry_notional_not_initial_risk').all()
    assert t.stop.isna().all()
    assert not {'gross_r','fee_r','net_r','peak_r'}&set(t.columns)
    assert t.gross_bp[t.reason.eq(5)].le(1e-8).all()
    assert t[t.reason.eq(5)].reclaim_status.eq('reclaimed').all()
    assert t[t.reason.eq(11)].reclaim_status.eq('failed').all()
    entry=pd.to_datetime(t.entry_interval_open,utc=True,format='mixed')
    end=pd.to_datetime(t.framework_exit_time,utc=True,format='mixed')
    for minutes,g in t.groupby('reclaim_minutes'):
        due=entry.loc[g.index].dt.floor(f'{int(minutes)}min')+pd.Timedelta(minutes=2*int(minutes))
        saved=pd.to_datetime(g.reclaim_due_ns,unit='ns',utc=True)
        assert (due==saved).all()
        failed=g.reason.eq(11)
        assert (end.loc[g.index][failed]==due[failed]).all()
        pending=g.reclaim_status.eq('pending')
        assert (end.loc[g.index][pending]<=due[pending]).all()
    assert set(t.trade_id).issubset(set(e.trade_id))
    dispositions=pd.DataFrame(a['opportunities'])
    assert dispositions.trade_id.is_unique and set(dispositions.trade_id)==set(e.trade_id)
    assert set(dispositions.loc[dispositions.status.eq('filled'),'trade_id'])==set(t.trade_id)
    # Changing target or confirmation does not change the original limit fill.
    cohorts=t.groupby(['event_id','population','control_id'])
    assert cohorts.entry_i.nunique().eq(1).all()
    assert cohorts.entry.nunique().eq(1).all()
    return dict(symbol=done['symbol'],positions=len(t),fills=len(f),orders=len(e),
                native_fee_precision=8,passed=True,
                signal_rows=int(t.population.eq('signal').sum()),
                censored=int(t.reason.eq(0).sum()),
                before_confirmation_targets=int((t.reason.eq(4)&t.reclaim_status.eq('pending')).sum()))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--partial',action='store_true');args=parser.parse_args()
    folders=sorted(p.parent for p in (EXP/'symbols').glob('*/done.json'))
    if len(folders)!=633 and not args.partial:raise RuntimeError(f'Coverage incomplete {len(folders)}/633')
    rows=[validate(p) for p in folders]
    result=dict(status='complete' if len(rows)==633 else 'partial',validated_assets=len(rows),expected_assets=633,
                positions=sum(r['positions'] for r in rows),fills=sum(r['fills'] for r in rows),assets=rows,
                execution_replays=0,fee_source='Saved actual native commissions; USDT8decimal monetary precision')
    dump(EXP/('validation-partial.json' if args.partial else 'validation.json'),finite(result))
    print(json.dumps({k:v for k,v in result.items() if k!='assets'}))


if __name__=='__main__':main()

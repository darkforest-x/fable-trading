from __future__ import annotations
import pandas as pd
from yoyo.evaluation.spike_eth_lowtf_cost_diagnostic import COST, _bucket, _summary


def _rows() -> pd.DataFrame:
    return pd.DataFrame({
        'stream':['ETH 3m OKX']*4, 'period':['later']*4, 'net_r':[10.0,-1.0,1.0,-2.0],
        'net_return':[0.10,-0.20,0.01,-0.01], 'fee_r':[0.25,0.5,1.0,1.1],
        'initial_risk_frac':[COST/.25,COST/.5,COST/1.0,COST/1.1],
    })


def test_fee_r_bucket_boundaries_are_fixed_and_non_overlapping() -> None:
    got=_bucket(_rows().fee_r).astype(str).tolist()
    assert got == ['<=0.25','0.25-0.5','0.5-1','>1']


def test_summary_keeps_r_and_nominal_return_pf_distinct_and_best_sensitivity() -> None:
    got=_summary(_rows(),['stream','period']).iloc[0]
    assert got.n == 4
    assert got.pf_net_r == 11/3
    assert abs(got.pf_net_return - 0.11/0.21) < 1e-12
    assert got.realized_ge10r == 1
    assert got.sum_without_best_net_r == -2.0


def test_cost_budget_definition_equals_minimum_entry_risk_fraction() -> None:
    rows=_rows()
    assert rows.initial_risk_frac.iloc[1] == 0.004
    assert rows.fee_r.le(.5).tolist() == [True,True,False,False]

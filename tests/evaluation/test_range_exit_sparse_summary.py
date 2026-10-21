"""A one-pair delta has no observations in the other side/half groups."""
import json

import pandas as pd

from yoyo.evaluation.range_v230_exit_compare import summaries


def test_one_pair_summary_excludes_empty_groups_and_serializes():
    rows = pd.DataFrame([dict(trade_id=p+'-1', pair_id=1, population=p,
        side=1, segment=1, week=100, net_r=1., gross_r=2., fee_r=1.,
        net_notional_bp=10., boundary_weight=0., boundary_net_r=0.,
        missing_target_weight=0., ambiguous_legs=0, be_effective=0)
        for p in ['signal', 'random']])
    result = summaries(rows, rows, 'ETH', 'fixed_1r', 20261021)
    assert [r['group'] for r in result] == ['all', 'long', 'second_half']
    assert all(r['n'] == 1 for r in result)
    json.dumps(result, allow_nan=False)

"""Synthetic completeness, censoring and attribution guards for V9 reports."""
from copy import deepcopy

import pandas as pd
import pytest

from yoyo.evaluation.spike_v9_full_report import (
    REQUIRED_FILES, attribution, block_statistics, control_metrics, validate_receipt,
)


def receipt():
    return dict(key='stream', baseline_parity=True, files={k: 'digest' for k in REQUIRED_FILES},
                summaries=[dict(stream_key='stream', arm=a, events=0, closed=0, censored=0,
                                net_r=0, realized_ge10=0, candidates=0) for a in ('v8', 'v9')])


def test_receipt_requires_both_arms_all_files_and_parity():
    original = receipt()
    assert set(validate_receipt(original, 'stream')) == {'v8', 'v9'}
    for mutate in (lambda r: r['files'].pop('v9.fills.csv.gz'),
                   lambda r: r['summaries'].pop(),
                   lambda r: r.update(baseline_parity=False),
                   lambda r: r.update(key='other')):
        altered = deepcopy(original)
        mutate(altered)
        with pytest.raises(ValueError):
            validate_receipt(altered, 'stream')


def test_control_denominator_excludes_unmatched_and_cross_period_without_resampling():
    controls = pd.DataFrame(dict(arm=['v9']*3, event_key=['a','b','c'], matched=[True,True,False],
        control_entry_time=pd.to_datetime(['2025-09-08']*3, utc=True),
        control_exit_time=pd.to_datetime(['2025-09-09','2025-09-11','2025-09-09'], utc=True),
        entry_time=pd.to_datetime(['2025-09-08']*3, utc=True),
        target_net_r=[3.,100.,200.], control_net_r=[1.,2.,4.],
        target_net_return=[.03,1.,2.], control_net_return=[.01,.02,.04]))
    result = control_metrics(controls[['arm','event_key']], controls, 'earlier')
    assert result['matched_pairs'] == 1
    assert result['unmatched_or_cross_period'] == 2
    assert result['paired_excess_r'] == 2
    assert result['paired_target_mean_r'] == 3


def test_month_blocks_are_reproducible_and_sparse_blocks_are_not_significant():
    assert block_statistics([1.], ['2025-01'])['blocks'] == 1
    result = block_statistics([1.,2.,3.], ['2025-01','2025-02','2025-03'])
    assert result == block_statistics([1.,2.,3.], ['2025-01','2025-02','2025-03'])
    assert result['mean_ci_low'] > 0


def test_serial_attribution_preserves_shared_winners_and_reconciles_reentry():
    trades = pd.DataFrame(dict(arm=['v8','v8','v9','v9'], event_key=['shared','old','shared','new'],
        censored=[False]*4, exit_time=pd.to_datetime(['2025-01-02']*4,utc=True),
        exit_reason=['stop']*4, net_r=[12.,-4.,12.,2.], gross_r=[13.,-3.,13.,3.]))
    decisions = pd.DataFrame(dict(event_key=['shared','old','new'],v9=[True,False,True],
        signal_atr_pct=[.01]*3, v9_bundle_reasons=['allowed','utc_sunday','allowed']))
    detail, _, _, _ = attribution(trades, decisions)
    assert detail['serial_delta_r'] == 6
    assert detail['retained_original_10r'] == 1
    assert detail['gross_r_delta'] + detail['cost_r_reduction'] == 6
    trades.loc[trades.arm.eq('v9') & trades.event_key.eq('shared'),'net_r'] = 11
    with pytest.raises(ValueError, match='shared-event netR'):
        attribution(trades, decisions)

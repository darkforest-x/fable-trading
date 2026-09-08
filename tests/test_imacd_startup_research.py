"""Research integration checks use synthetic prices only, before source freeze."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.imacd_startup_research import (
    aggregate, policy_masks, matched_indexes, monthly_increment, inference,
)
from yoyo.evaluation.imacd_startup_quality import build_features
from yoyo.evaluation.imacd_startup_accounting import outcome_arrays, compound_portfolio


def bars(n=900):
    c = np.full(n, 100.)
    c[400:420] = np.linspace(100, 125, 20)
    c[420:480] = 125
    c[480:] = 100
    return pd.DataFrame(dict(open=c, high=c+1, low=c-1, close=c, volume=np.ones(n)),
                        index=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'))


def test_complete_aggregation_and_gap_rejection():
    b = bars(903)
    a = aggregate(b, 60)
    assert len(a) == 225
    assert a.volume.eq(4).all()
    assert a.index[-1] + pd.Timedelta(hours=1) <= b.index[-1]
    with pytest.raises(ValueError, match='gap'):
        aggregate(b.drop(b.index[5]), 60)


def test_calendar_cutpoint_cannot_create_extra_independent_month():
    idx = pd.date_range('2025-01-01', '2026-01-01', freq='4h', tz='UTC', inclusive='right')
    a = pd.Series(np.linspace(1., 1.5, len(idx)), index=idx)
    b = pd.Series(1., index=idx)
    increments = monthly_increment(a, b)
    assert len(increments) == 12
    assert increments.index[-1] == '2025-12'
    assert increments.sum() == pytest.approx(5000)
    with pytest.raises(ValueError, match='calendars'):
        monthly_increment(a.iloc[1:], b)


def test_two_months_do_not_claim_one_percent_significance():
    result = inference([10, 20], ['2026-05', '2026-06'])
    assert result['p'] == .25
    assert result['p_resolution'] == .25


def test_synthetic_features_matching_labels_and_portfolio_share_clock():
    b = bars()
    f = build_features(b)
    f['volbin'] = np.ceil((f.atr/b.close).rolling(240).rank(pct=True)*5).clip(1, 5)
    masks = policy_masks(f)
    eligible = np.arange(340, len(f)-1)
    mapping = matched_indexes(f, eligible, 123)
    assert mapping
    control_i = [c for values in mapping.values() for c in values]
    assert len(set(control_i)) == len(control_i)
    assert f.release_side.iloc[control_i].eq(0).all()
    assert not (masks['P03'] & ~masks['P02']).any()
    labels = outcome_arrays(b, f, mapping.keys(), f.release_side.iloc[list(mapping)], len(b)-1)
    for i, label in zip(mapping, labels):
        assert label['entry_i'] == i+1
        assert pd.Timestamp(label['entry_open_time']) == b.index[i]+pd.Timedelta(minutes=15)
    metrics, eq, accepted = compound_portfolio(b, labels, 0, len(b)-1)
    close = eq.loc[eq.kind.eq('close')]
    assert close.time.is_unique and len(close) == len(b)
    assert close.iloc[-1].equity == metrics['ending_equity']
    assert accepted and metrics['trades'] <= len(labels)

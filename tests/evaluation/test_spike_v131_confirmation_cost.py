"""Economic accounting and information-boundary tests for confirmation attribution."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v131_confirmation_cost import (
    COMPONENTS, attribution, feature_values, known_status, paired, temporal_period,
)


@pytest.mark.parametrize('side',[1,-1])
def test_bridge_reconciles_entry_exit_fees_and_equal_risk(side):
    early=100.;stop=early-side*10;old_exit=early+side*20
    late=early+side*5;new_exit=early+side*30
    r=attribution(side,early,stop,old_exit,late,new_exit)
    assert r['parent_r']==pytest.approx(1.98)
    assert r['entry_price_effect_r']==pytest.approx(-.5)
    assert r['exit_price_effect_r']==pytest.approx(1)
    assert r['fee_change_r']==pytest.approx(-.001*side)
    assert r['risk_ratio']==pytest.approx(1.5)
    assert r['delayed_r']==pytest.approx((25-.002*late)/15)
    assert sum(r[k] for k in COMPONENTS)==pytest.approx(r['delayed_r']-r['parent_r'])


def test_skipping_a_winner_is_negative_selection_and_zero_delayed_fill():
    r=attribution(1,100,90,120)
    assert r['delayed_r']==0 and not r['confirmed']
    assert r['selection_effect_r']==pytest.approx(-1.98)


def test_skipping_a_loser_saves_net_loss_including_cost():
    r=attribution(-1,100,110,110)
    assert r['selection_effect_r']==pytest.approx(1.02)


def test_close_features_do_not_change_with_future_open():
    a=feature_values(1,100,90,103,104,6)
    b=feature_values(1,100,90,103,110,6)
    for k in ['chase_decision_r','fee_decision_r','delay_bars']:
        assert a[k]==b[k]
    assert a['chase_fill_r']!=b['chase_fill_r']


def test_invalid_risk_never_becomes_unbounded_profit():
    with pytest.raises(ValueError):attribution(1,100,100,120)
    with pytest.raises(ValueError):attribution(1,100,90,120,89,110)


def test_mature_temporal_split_excludes_confirmation_crossing():
    stamp=lambda x:pd.Timestamp(x,tz='UTC')
    cut=stamp('2025-01-01')
    assert temporal_period(stamp('2024-12-30'),stamp('2025-01-01'),cut)=='crossing'
    assert temporal_period(stamp('2024-12-01'),stamp('2024-12-31'),cut)=='early'
    assert temporal_period(cut,stamp('2025-01-02'),cut)=='later'


def test_missing_outcome_is_not_a_zero_fill():
    assert known_status('cancel_stop_touch') and known_status('expired')
    assert not known_status('censored_gap') and not known_status('pending_boundary')
    assert not known_status('no_next_bar')


def test_pairs_keep_known_zero_fills_and_do_not_replace_missing_draws():
    t=pd.DataFrame(dict(trade_key=['a','b','c'],anchor_time=['2024-01-01','2024-01-08','2024-01-08'],delayed_r=[2.,0.,5.]))
    c=pd.DataFrame(dict(trade_key=['a','b'],anchor_time=['2024-01-01','2024-01-08'],delayed_r=[0.,1.]))
    r=paired(t,c,dict(seed=1,bootstrap=50),['delayed_r'])
    assert r['n']==2 and r['coverage']==pytest.approx(2/3)
    assert r['delayed_r']['target']['sum']==2
    assert r['delayed_r']['random']['sum']==1

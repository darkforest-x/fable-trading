"""Meaningful null and economic-denominator cases for saved-filter research."""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_six_filter_statistics import fixed_effect, matched_deletion


def frame():
    t = pd.Timestamp('2026-01-01', tz='UTC')
    return pd.DataFrame(dict(venue=['okx']*4, asset=['ABC']*4, timeframe_min=[60]*4,
        side=[1]*4, entry_time=[t]*4, exit_time=[t+pd.Timedelta(hours=1)]*4,
        event_key=['a','b','c','d'], signal_atr_pct=[.01]*4, censored=[False]*4,
        net_r=[-1.,-1.,2.,10.], net_return=[-.01,-.01,.02,.10], gate_rejected=[True]*4))


def test_whole_asset_exclusion_is_not_identified_by_within_asset_null():
    result = matched_deletion(frame(), iterations=99)
    assert not result['null_identified']
    assert np.isnan(result['p_matched_deletion'])
    assert result['forced_rejections'] == 4


def test_filtered_censored_loss_is_not_realized_savings():
    f = frame()
    f.loc[0, ['censored','net_r']] = [True, -999.]
    f['gate_rejected'] = [True, True, False, False]
    result = fixed_effect(f)
    assert result['fixed_delta_r'] == 1.
    assert result['base_closed'] == 3
    assert result['retained_original_ge10'] == 1


def test_losing_winner_is_charged_against_saved_losses():
    f = frame(); f['gate_rejected'] = [True, False, False, True]
    result = fixed_effect(f)
    assert result['saved_loss_r'] == 1.
    assert result['lost_winner_r'] == 10.
    assert result['fixed_delta_r'] == -9.
    assert result['retained_original_ge10'] == 0

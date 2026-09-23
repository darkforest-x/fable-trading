"""Protect win denominators and established-versus-ambiguous MFE accounting."""
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v128_recent_report import enrich, metrics, block_inference


def sample():
    return pd.DataFrame({'trade_key':['a','b','c'], 'signal_close':['2026-08-01']*3,
        'entry_time':['2026-08-01']*3, 'exit_time':['2026-08-02']*3,
        'net_return':[-.012,-.001,.018], 'gross_return':[-.01,.001,.02],
        'net_r':[-1.2,-.1,1.8], 'gross_r':[-1.,.1,2.], 'initial_risk_frac':[.01]*3,
        'mfe_known_r':[1.,.1,3.], 'mfe_upper_r':[2.,.1,3.],
        'close_peak_r':[.8,.1,2.1], 'exit_reason':['initial_stop','opposite_v6_next_open','trailing_stop']})


def test_fee_covering_profit_and_stop_bar_ambiguity_stay_separate():
    result=metrics(enrich(sample(),pd.Timestamp('2026-08-23',tz='UTC')))
    assert result['closed']==3 and result['wins']==1 and result['losses']==2
    assert result['fee_only_losses']==1
    assert result['float_to_stop_loss_1r']==1
    assert result['float_to_stop_loss_2r']==0
    assert result['ambiguous_stop_profit_2r']==1
    assert result['net_float_to_any_loss']==1
    assert np.isclose(result['mean_gross_bp']-result['mean_net_bp'],20)


def test_week_sign_flip_uses_exact_small_sample_resolution():
    result=block_inference([1.,2.],['week1','week2'],bootstrap=20)
    assert result['p_one_sided']==.25
    assert result['minimum_p_resolution']==.25
    assert result['mean_excess_bp']==1.5

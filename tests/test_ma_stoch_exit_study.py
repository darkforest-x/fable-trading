"""Development ranking, fixed validation set and non-optimistic control sampling."""
import pytest
from yoyo.evaluation.ma_stoch_exit_study import select,validation_arms,matched_controls
from test_ma_stoch_exit_engine import fixture


def test_selection_uses_equity_then_drawdown_then_count_then_name():
    row=lambda equity,dd,n:dict(final_equity=equity,mtm_max_drawdown_pct=dd,n=n)
    arms={'tiny':row(9999,0,2),'loss':row(800,1,50),'a':row(900,10,40),'b':row(900,8,40),'c':row(900,8,35),'d':row(900,8,35)}
    assert select(arms)=='c'
    with pytest.raises(ValueError,match='enough'):select({'tiny':arms['tiny']})


def test_validation_is_fixed_set_and_deduplicates_anchor():
    assert validation_arms({'selected':'partial_1r'})==['baseline','stop_2','partial_1r']
    assert validation_arms({'selected':'stop_2'})==['baseline','stop_2']
    with pytest.raises(ValueError):validation_arms({'selected':'not_registered'})


def test_one_draw_control_censoring_is_retained_and_repeatable():
    from yoyo.evaluation.ma_stoch_exit_engine import simulate
    import pandas as pd
    c,s,t=fixture();c['arrow'][140]=-1
    targets=simulate(c,s,t,'baseline')['trades']
    cfg={'volatility_bins':[.001,.002,.004,.008],'control_seed':9152401,'tick':.01}
    first=matched_controls(c,targets,s,t,'baseline',cfg)
    second=matched_controls(c,targets,s,t,'baseline',cfg)
    pd.testing.assert_frame_equal(first,second)
    assert len(first)==1
    r=first.iloc[0]
    assert r.signal_i!=r.control_signal_i
    assert r.day==str(r.control_entry_time.date())
    # No redraw to obtain a convenient closed trade.
    expected='matched' if r.control_signal_i<140 else 'censored'
    assert r.reason==expected

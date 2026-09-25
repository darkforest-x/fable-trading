"""Guard the event-clock boundary in cross-timeframe explanatory joins."""
import pandas as pd

from yoyo.evaluation.spike_manual_diagnostics import contexts, max_loss_streak


def test_event_context_can_see_simultaneous_but_never_later_signal():
    t=pd.Timestamp('2025-01-01T12:00Z')
    trades=pd.DataFrame([dict(trade_key='a',symbol='BTCUSDT',signal_close=t,side=1)])
    rows=[]
    for minutes in (5,15,60):
        rows.extend([dict(symbol='BTCUSDT',timeframe_min=minutes,signal_close=t,side=1,arm='ordinary'),
            dict(symbol='BTCUSDT',timeframe_min=minutes,signal_close=t+pd.Timedelta(minutes=5),side=-1,arm='ordinary')])
    result=contexts(trades,pd.DataFrame(rows))
    for minutes in (5,15,60):
        assert result.iloc[0][f'last_{minutes}m_side']==1
        assert result.iloc[0][f'last_{minutes}m_age_hours']==0
    assert max_loss_streak([-1,-2,3,-1,-2,-3,0,-1])==3

"""Direction admission must preserve occupancy, censoring and comparison clocks."""
import pandas as pd
import pytest

from yoyo.evaluation import spike_v13_direction_split as study


def candidate(key, signal, exit_i, side, status='closed', net_r=1.):
    return dict(trade_key=key, policy='retest', arm='v9_both', symbol='TEST',
                timeframe_min=15, side=side, signal_i=signal, anchor_i=signal-4,
                entry_i=signal+1, exit_i=exit_i, status=status, net_r=net_r,
                net_return=net_r*.01, gross_return=net_r*.01+.002,
                entry_price=100., initial_stop=100-side, initial_risk=1.,
                entry_time='2024-01-01T00:00Z', exit_time='2024-01-02T00:00Z')


def test_direction_filter_replays_released_entries_and_displaces_later_entries():
    rows=pd.DataFrame([candidate('long',10,20,1),candidate('short-new',12,30,-1),
                       candidate('short-old',22,40,-1),candidate('short-after',40,45,-1)])
    both=study.select_direction(rows)
    assert both.trade_key.tolist()==['long','short-old','short-after']
    assert study.select_direction(rows,-1).trade_key.tolist()==['short-new','short-after']
    # Same-confirmation ordering is frozen by anchor, never a return ranking.
    changed=rows.copy();changed.net_r=[-100,100,-100,100]
    assert study.select_direction(changed,-1).trade_key.tolist()==['short-new','short-after']


def test_warmup_position_blocks_post_window_signal_before_date_filtering():
    rows=pd.DataFrame([candidate('carry',1,20,-1),candidate('blocked',10,30,-1),candidate('free',21,35,-1)])
    rows.loc[0,'entry_time']='2022-12-31T00:00Z'
    selected=study.select_direction(rows,-1)
    selected.entry_time=pd.to_datetime(selected.entry_time,utc=True)
    selected=study.in_window(selected,{'analysis_start':'2023-01-01T00:00Z','end':'2025-01-01T00:00Z'})
    assert selected.trade_key.tolist()==['free']


def test_censored_boundary_never_releases_occupancy_but_gap_does():
    rows=pd.DataFrame([candidate('cut',1,3,-1,'censored_boundary'),candidate('after',4,9,-1)])
    assert study.select_direction(rows).trade_key.tolist()==['cut']
    rows.loc[0,'status']='censored_gap'
    assert study.select_direction(rows).trade_key.tolist()==['cut','after']


def test_parity_rejects_missing_or_repriced_trades():
    rows=pd.DataFrame([candidate('a',1,3,-1)])
    study.check_parity(rows,rows.copy())
    with pytest.raises(ValueError,match='occupancy'):
        study.check_parity(rows,rows.iloc[:0])
    changed=rows.copy();changed.entry_price=101.
    with pytest.raises(AssertionError):
        study.check_parity(rows,changed)


def test_yearly_control_must_enter_in_same_beijing_year_and_counts_exclude_censoring():
    frame=pd.DataFrame([candidate('same',1,3,-1),candidate('wrong-year',4,7,-1),
                        candidate('pending',8,9,-1,'censored_boundary')])
    frame.entry_time=pd.to_datetime(['2024-01-01T00:00Z']*3)
    frame.exit_time=pd.to_datetime(['2024-01-02T00:00Z']*3)
    frame=study.clock.clock(frame,'Asia/Shanghai')
    controls=frame.iloc[:2].copy();controls.loc[1,'entry_time']=pd.Timestamp('2023-12-30T00:00Z')
    cfg={'timezone':'Asia/Shanghai','split':'2025-01-01T00:00Z','bootstrap':10,'statistics_seed':1}
    out=study.summary_row(frame,controls,'year_2024',cfg)
    assert out['taken']==3 and out['closed']==2 and out['censored']==1
    assert out['matched']==1 and out['mean_excess_bp']==0


def test_cross_split_trade_is_not_earlier_and_directions_reconcile():
    rows=pd.DataFrame([candidate('cross',1,3,-1),candidate('later',4,6,1)])
    rows.loc[0,['entry_time','exit_time']]=['2024-12-31T00:00Z','2025-01-02T00:00Z']
    rows.loc[1,['entry_time','exit_time']]=['2025-01-03T00:00Z','2025-01-04T00:00Z']
    rows=study.clock.clock(rows,'Asia/Shanghai');split=pd.Timestamp('2025-01-01T00:00Z')
    assert study.clock.cohort(rows,'earlier',split).empty
    assert study.clock.cohort(rows,'cross',split).trade_key.tolist()==['cross']
    assert study.clock.cohort(rows,'later',split).trade_key.tolist()==['later']

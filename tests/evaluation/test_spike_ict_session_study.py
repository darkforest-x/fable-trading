"""Calendar boundaries and causal session admission, without market data."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_ict_session_study import session_mask, serial, stats


@pytest.mark.parametrize('session,expected',[
    ('london',[False,True,True,False,False,False,False]),
    ('lunch',[False,False,False,True,True,False,False]),
    ('new_york',[False,False,False,False,False,True,False]),
    ('union',[False,True,True,True,True,True,False]),
    ('london_new_york',[False,True,True,False,False,True,False])])
def test_exact_entry_boundaries(session,expected):
    times=pd.to_datetime(['2026-09-13 '+t for t in ['13:57','14:00','16:57','17:00','19:57','20:00','23:00']]).tz_localize('Asia/Shanghai').tz_convert('UTC')
    assert list(session_mask(times,session))==expected


@pytest.mark.parametrize('day,starts',[
    ('2025-03-08','15:00'),('2025-03-10','14:00'),
    ('2025-11-01','14:00'),('2025-11-03','15:00')])
def test_ny_dst_across_spring_and_fall(day,starts):
    entry=pd.Timestamp(day+' '+starts,tz='Asia/Shanghai').tz_convert('UTC')
    assert session_mask([entry],'london')[0]
    assert not session_mask([entry-pd.Timedelta(minutes=3)],'london')[0]


def row(signal,exit_i,side=1,net=-1.,reason='initial_stop',censored=False):
    return dict(signal_i=signal,entry_i=signal+1,exit_i=exit_i,side=side,
                exit_at_open=False,net_r=net,gross_r=net+.2,exit_reason=reason,censored=censored)


def test_excluded_candidate_has_no_shadow_position():
    blocked=row(0,30);a=row(10,15);b=row(20,25)
    assert serial([blocked,a,b])==[blocked]
    assert serial([a,b])==[a,b]


def test_skipped_signal_does_not_reset_losses_and_censor_not_natural():
    rows=[row(10,15),row(20,25),row(30,40,net=2.,censored=True)]
    result=stats(rows)
    assert result['max_net_loss_streak']==2
    assert result['natural']==2 and result['open']==1


def test_be_is_separate_from_win_and_breaks_net_loss_streak():
    result=stats([row(0,2),row(3,5,net=1e-5,reason='cost_be'),row(6,8)])
    assert result['be']==1 and result['wins']==0
    assert result['strict_net_positive']==1
    assert result['max_net_loss_streak']==1


def test_unknown_session_rejected():
    with pytest.raises(ValueError):session_mask(pd.date_range('2025-01-01',periods=2,tz='UTC'),'x')

"""Boundary and serialization checks independent of live/source price data."""
import pandas as pd
import pytest
from yoyo.data.spike_fanshen_prefix import read_prefix, aggregate
from yoyo.evaluation.spike_fanshen_study import serial, stats, pair_delta


def test_prefix_does_not_parse_rejected_prices(tmp_path):
    p=tmp_path/"bars.csv"
    a=int(pd.Timestamp("2026-04-30T23:55Z").timestamp()*1000)
    b=int(pd.Timestamp("2026-05-01T00:00Z").timestamp()*1000)
    p.write_text(f"ts,open,high,low,close,volume\n{a},100,102,99,101,1\n{b},POISON")
    frame, receipt=read_prefix(p,5,"2026-05-01T00:00Z")
    assert len(frame)==1
    assert receipt["restricted_price_rows_parsed"]==0
    assert receipt["first_excluded_timestamp_only"]=="2026-05-01T00:00:00+00:00"


def test_prefix_rejects_new_holdout_before_opening(tmp_path):
    with pytest.raises(ValueError,match="holdout"):
        read_prefix(tmp_path/"does_not_exist",5,"2026-05-05T00:00Z")


def test_aggregate_complete_utc_groups_and_no_three_to_five():
    idx=pd.date_range("2026-01-01T00:05Z",periods=6,freq="5min",name="open_time")
    f=pd.DataFrame(dict(open=[1,2,3,4,5,6],high=[2,3,4,5,6,7],
                        low=[.5,1,2,3,4,5],close=[1.5,2.5,3.5,4.5,5.5,6.5],volume=1),index=idx)
    result,receipt=aggregate(f,5,15)
    assert result.index.tolist()==[pd.Timestamp("2026-01-01T00:15Z")]
    assert result.iloc[0].to_dict()==dict(open=3.,high=6.,low=2.,close=5.5,volume=3.)
    assert receipt["discarded_partial_edge_groups"]==2
    with pytest.raises(ValueError,match="multiple"): aggregate(f,3,5)


def test_serial_reopens_only_after_actual_exit():
    rows=[dict(signal_i=5,entry_i=6,exit_i=10,side=1,exit_at_open=True),
          dict(signal_i=7,entry_i=8,exit_i=8,side=-1,exit_at_open=False),
          dict(signal_i=11,entry_i=12,exit_i=14,side=1,exit_at_open=False)]
    assert [x["signal_i"] for x in serial(rows)]==[5,11]


def test_stats_keeps_boundary_and_initial_stop_separate():
    rows=[dict(censored=False,net_r=-1.2,gross_r=-1.,exit_reason="initial_stop",mfe_r=0.),
          dict(censored=False,net_r=-.1,gross_r=.1,exit_reason="fanshen_arrows_next_open",mfe_r=1.2),
          dict(censored=True,net_r=3.,gross_r=3.2,exit_reason="boundary_mark",mfe_r=4.)]
    result=stats(rows)
    assert result["natural"]==2 and result["open"]==1
    assert result["sum_net_r"]==pytest.approx(-1.3)
    assert result["max_net_loss_streak"]==2 and result["max_initial_stop_streak"]==1
    assert result["legacy_mfe1r_then_net_loss"]==1 and result["boundary_net_r"]==3


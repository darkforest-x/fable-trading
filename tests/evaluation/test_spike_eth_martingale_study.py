"""Synthetic boundary checks for ETH martingale study; no market data read."""
import csv
import pandas as pd
import pytest
from yoyo.evaluation.spike_eth_martingale_study import read_visible, records


def test_reader_stops_before_parsing_future_numeric_values(tmp_path):
    p=tmp_path/'bars.csv'
    p.write_text('ts,open,high,low,close,volume,open_time\n'
                 '1777766340000,1,2,0.5,1.5,3,unused\n'
                 '1777766400000,POISON,POISON,POISON,POISON,POISON,unused\n')
    frame=read_visible(p,'2026-05-03T00:00:00Z')
    assert len(frame)==1
    assert frame.index.max()<pd.Timestamp('2026-05-03T00:00:00Z')


def test_reader_rejects_nonmonotonic_visible_times(tmp_path):
    p=tmp_path/'bars.csv'
    p.write_text('ts,open,high,low,close,volume,open_time\n'
                 '2,1,2,0.5,1.5,3,unused\n1,1,2,0.5,1.5,3,unused\n')
    with pytest.raises(ValueError,match='increasing'): read_visible(p,'2026-05-01T00:00:00Z')


def test_records_preserve_utc_exit_clock():
    source=pd.DataFrame([dict(entry_time='2025-01-01T00:00:00Z',exit_time='2025-01-01T00:03:00Z')])
    row=records(source)[0]
    assert row['entry_time'].endswith('+00:00')
    assert pd.Timestamp(row['exit_time'])-pd.Timestamp(row['entry_time'])==pd.Timedelta(minutes=3)

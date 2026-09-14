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


def test_selection_rejects_csv_not_reproduced_by_pinned_context(tmp_path,monkeypatch):
    import json
    import yoyo.evaluation.spike_eth_martingale_study as study
    pre=tmp_path/'pre';pre.mkdir()
    config_file=tmp_path/'config.json';config_file.write_text('{}')
    (pre/'preparation.json').write_text(json.dumps({'config_sha256':study.sha(config_file)}))
    original=pd.DataFrame([{'net_return':.2,'trade_id':'a'}])
    (pre/'eth_development.csv').write_text(original.to_csv(index=False))
    monkeypatch.setattr(study,'RESULTS',tmp_path)
    monkeypatch.setattr(study,'CONFIG',config_file)
    monkeypatch.setattr(study,'context_for',lambda name:{})
    monkeypatch.setattr(study,'window',lambda ctx,*bounds:(original,{}))
    frame,digest=study.verified_development_csv('eth',{'development':['a','b']})
    assert frame.net_return.iloc[0]==.2
    (pre/'eth_development.csv').write_text('net_return,trade_id\n20,a\n')
    with pytest.raises(ValueError,match='differs'):
        study.verified_development_csv('eth',{'development':['a','b']})

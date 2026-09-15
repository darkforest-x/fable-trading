import pandas as pd
import pytest

from yoyo.evaluation.chartart_bbrsi_study import read_prefix


def test_timestamp_guard_does_not_parse_restricted_price(tmp_path):
    path=tmp_path/'bars.csv'
    allowed=int(pd.Timestamp('2026-04-30T23:59Z').timestamp()*1000)
    banned=allowed+60000
    path.write_text(f'ts,open,high,low,close,volume\n{allowed},100,101,99,100,1\n{banned},THIS MUST NOT BE PARSED\n')
    frame,receipt=read_prefix(path,1,'2026-05-01T00:00Z')
    assert len(frame)==1
    assert receipt['restricted_price_rows_parsed']==0
    assert not receipt['holdout_consumed']


def test_holdout_endpoint_rejected_before_file_open(tmp_path):
    with pytest.raises(ValueError,match='restricted'):
        read_prefix(tmp_path/'not_there.csv',1,'2026-05-04T00:01Z')


def test_missing_candle_fails(tmp_path):
    path=tmp_path/'bars.csv'
    first=int(pd.Timestamp('2026-01-01T00:00Z').timestamp()*1000)
    path.write_text(f'ts,open,high,low,close,volume\n{first},100,101,99,100,1\n{first+120000},100,101,99,100,1\n')
    with pytest.raises(ValueError,match='missing'):
        read_prefix(path,1,'2026-05-01T00:00Z')

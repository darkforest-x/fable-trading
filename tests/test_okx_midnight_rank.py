"""Midnight ranking causality, complete aggregation and archive boundaries."""
import io
import zipfile
import numpy as np
import pandas as pd
import pytest
from yoyo.data.okx_midnight_rank import MINUTE, aggregate, daily_rows, month_bounds, parse_archive


def minutes(count=1440):
    start=month_bounds('2024-01')[0]
    return np.array([[start+i*MINUTE,100+i*.01,101+i*.01,99+i*.01,100.5+i*.01,2] for i in range(count)],float)


def test_midnight_is_beijing_and_restricted_month_fails_before_io():
    assert pd.Timestamp(month_bounds('2024-01')[0],unit='ms',tz='UTC')==pd.Timestamp('2023-12-31T16:00Z')
    assert month_bounds('2026-04')[1] < pd.Timestamp('2026-05-01T00:00Z').value//1_000_000
    with pytest.raises(ValueError): month_bounds('2026-05')


def test_rank_uses_only_first_ten_minutes_and_next_minute_entry():
    a=minutes(); r=daily_rows(a,[10],[15,'day']).iloc[0]
    assert r.rank_end_price==a[9,4] and r.rank_start_price==a[0,1]
    assert r.entry_ms==a[11,0] and r.entry_price==a[11,1]
    assert r.gross_15==pytest.approx(a[26,1]/a[11,1]-1)
    b=a.copy();b[10:,1:5]*=50
    s=daily_rows(b,[10],[15,'day']).iloc[0]
    for k in ['rank_return','rank_range','rank_volume','rank_available_ms']:
        assert r[k]==s[k]


def test_missing_future_keeps_rank_but_censors_label():
    a=minutes(); a=np.delete(a,20,axis=0)
    r=daily_rows(a,[10],[15,'day']).iloc[0]
    assert np.isfinite(r.rank_return) and np.isnan(r.gross_15) and np.isnan(r.gross_day)


def test_missing_early_minute_is_not_replaced_by_later_bar():
    assert daily_rows(np.delete(minutes(),9,axis=0),[10],[15]).empty


def test_complete_aggregation_never_borrows_after_the_bucket():
    a=minutes(10); g=aggregate(a,3)
    assert len(g)==3 and g[0,4]==a[2,4]
    assert len(aggregate(np.delete(a,1,axis=0),3))==2
    assert len(aggregate(a,5))==2


def packed(a,confirm=1):
    f=pd.DataFrame(a,columns=['open_time','open','high','low','close','vol'])
    f['instrument_name']='ETH-USDT-SWAP';f['confirm']=confirm
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,'w') as z:z.writestr('data.csv',f.to_csv(index=False))
    return buff.getvalue()


def test_archive_checks_symbol_confirmation_and_month():
    a=minutes(10); out,receipt=parse_archive(packed(a),'ETH-USDT-SWAP','2024-01')
    assert np.array_equal(out,a) and receipt['minute_rows']==10
    with pytest.raises(ValueError):parse_archive(packed(a),'BTC-USDT-SWAP','2024-01')
    with pytest.raises(ValueError):parse_archive(packed(a,0),'ETH-USDT-SWAP','2024-01')
    with pytest.raises(ValueError):parse_archive(packed(a),'ETH-USDT-SWAP','2024-02')


def test_only_exact_duplicate_minutes_can_be_dropped():
    a=minutes(10); out,r=parse_archive(packed(np.vstack([a,a[0]])),'ETH-USDT-SWAP','2024-01')
    assert len(out)==10 and r['exact_duplicates_dropped']==1
    extra=a[0].copy();extra[-1]=3
    with pytest.raises(ValueError):parse_archive(packed(np.vstack([a,extra])),'ETH-USDT-SWAP','2024-01')

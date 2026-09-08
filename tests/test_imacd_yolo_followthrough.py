"""Synthetic clock and long/short symmetry checks; no market-data access."""
import pandas as pd
import pytest
from yoyo.evaluation.imacd_yolo_followthrough import observe


def bars():
    return pd.DataFrame({'open':[100,100,102,104], 'high':[999,103,105,108],
        'low':[1,98,100,102], 'close':[100,102,104,106]},
        index=pd.date_range('2026-01-01',periods=4,freq='h',tz='UTC'))


def test_excludes_anchor_uses_exact_h_future_bars():
    b=bars(); r=observe(b,0,1,2,b.index[-1]+pd.Timedelta(hours=1),2.)
    assert r['entry_i']==1 and r['end_i']==2
    assert r['gross_bp']==pytest.approx(400)
    assert r['mfe_bp']==pytest.approx(500)
    assert r['mae_bp']==pytest.approx(200)
    assert r['net_bp']==pytest.approx(380)
    assert r['exit_at']==b.index[3].isoformat()


def test_short_uses_low_for_favorable_and_high_for_adverse():
    b=bars(); r=observe(b,0,-1,2,b.index[-1]+pd.Timedelta(hours=1),2.)
    assert r['gross_bp']==pytest.approx(-400)
    assert r['mfe_atr']==1. and r['mae_atr']==2.5


def test_cutoff_includes_bar_closing_on_boundary_without_looking_beyond():
    b=bars()
    assert observe(b,0,1,2,b.index[3],2.)['complete']
    r=observe(b,0,1,3,b.index[3],2.)
    assert not r['complete'] and r['observed_bars']==2
    assert all(r[k] is None for k in ['gross_bp','net_bp','mfe_bp','mae_bp','entry_price','exit_price'])


def test_future_mutation_changes_only_in_window_labels():
    b=bars(); end=b.index[-1]+pd.Timedelta(hours=1)
    first=observe(b,0,1,2,end,2.)
    b.iloc[3]=[1e6,1e6,1e6,1e6]
    assert observe(b,0,1,2,end,2.)==first

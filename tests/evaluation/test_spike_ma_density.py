"""Causal and adversarial MA geometry cases; synthetic fixtures are not gold."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_ma_density import diagnostics, MAS


def fixture():
    t=np.arange(470)
    f=pd.DataFrame(index=pd.date_range("2025-01-01",periods=len(t),freq="15min",tz="UTC"))
    f["open"]=f["close"]=100.;f["high"]=101.;f["low"]=99.;f["atr"]=1.;f["ready"]=True
    for j,name in enumerate(MAS):f[name]=100.+(j-2.5)*.02+np.sin(t/2+j)*.02
    return f


def test_repeated_same_pair_cannot_certify_three_groups():
    f=fixture()
    f.s20=99.+np.sin(np.arange(len(f))*2)*.05;f.e20=99.
    f.s60=100.;f.e60=100.1;f.s120=101.;f.e120=101.1
    row=diagnostics(f,15).iloc[-1]
    assert row.legacy and row.cross_count12>=2 and row.distinct_pairs12==1
    assert row.group_edges12==0 and not row.group_edges_2
    assert not row.width_1


def test_atr_inflation_can_create_legacy_density_without_ma_convergence():
    f=fixture()
    f.s20=98.+np.sin(np.arange(len(f))*2)*.05;f.e20=98.
    f.s60=100.;f.e60=100.1;f.s120=102.;f.e120=102.1
    f.loc[f.index[-13:-1],"atr"]=2.
    row=diagnostics(f,15).iloc[-1]
    assert row.legacy and row.stable_mean12>3 and not row.stable_atr_3
    assert row.contraction_ratio==pytest.approx(1.,abs=.02)


def test_prefix_and_price_scale_invariance():
    f=fixture();full=diagnostics(f,15)
    pd.testing.assert_frame_equal(full.iloc[:410],diagnostics(f.iloc[:410],15))
    altered=f.copy(); altered.loc[altered.index[410:],list(MAS)]=999.
    pd.testing.assert_frame_equal(full.iloc[:410],diagnostics(altered,15).iloc[:410])
    scaled=f.copy();scaled.loc[:,[*MAS,"open","high","low","close","atr"]]*=1e-5
    other=diagnostics(scaled,15)
    assert full.legacy.equals(other.legacy)
    np.testing.assert_allclose(full.stable_mean12,other.stable_mean12,rtol=1e-9,atol=1e-9,equal_nan=True)


def test_current_launch_bar_is_excluded_from_core():
    f=fixture();a=diagnostics(f,15)
    f.loc[f.index[-1],list(MAS)]=np.arange(6)*100+100
    b=diagnostics(f,15)
    for name in a.columns:
        if name!='width_now_atr':
            pd.testing.assert_series_equal(a[name],b[name])


def test_gap_requires_rewarm_not_false():
    f=fixture().drop(fixture().index[400]);d=diagnostics(f,15)
    assert d.known.iloc[390]
    assert not d.known.iloc[400:].any()


def test_tight_parallel_is_compact_but_has_no_cross():
    f=fixture()
    for i,name in enumerate(MAS):f[name]=100+i*.02
    d=diagnostics(f,15).iloc[-1]
    assert d.stable_max12<1 and d.cross_count12==0 and not d.legacy

"""Check semantic identities and causal timing of the descriptive IMACD audit."""
import numpy as np
import pandas as pd
from yoyo.evaluation.imacd_indicator_audit import indicator, smma, aggregate


def fixture():
    x = 100 + np.arange(1000)*.003 + 3*np.sin(np.arange(1000)/17)
    return pd.DataFrame(dict(open=x, high=x+.3, low=x-.3, close=x), index=pd.date_range('2024-01-01', periods=1000, freq='15min', tz='UTC'))


def test_smma_seed_and_update():
    x=pd.Series(np.arange(1.,41))
    s=smma(x)
    assert s[:33].isna().all()
    assert s.iloc[33] == 17.5
    assert s.iloc[34] == (17.5*33+35)/34


def test_constant_null_and_signal_identity():
    b=fixture()*0+100
    f=indicator(b)
    assert not f.up.any() and not f.dn.any() and f.md.eq(0).all()
    f=indicator(fixture())
    assert f.up.equals((f.md>f.sb)&(f.md.shift()<=f.sb.shift()))
    assert f.dn.equals((f.md<f.sb)&(f.md.shift()>=f.sb.shift()))


def test_prefix_affine_and_mirror():
    b=fixture(); f=indicator(b)
    pd.testing.assert_frame_equal(indicator(b.iloc[:701]),f.iloc[:701])
    g=indicator(b*7+500)
    assert f.up.equals(g.up) and f.dn.equals(g.dn)
    np.testing.assert_allclose(g.md,f.md*7,atol=1e-10)
    m=500-b
    m['high'],m['low']=500-b.low,500-b.high
    z=indicator(m)
    np.testing.assert_allclose(z.md,-f.md,atol=1e-10)
    assert z.up.equals(f.dn) and z.dn.equals(f.up)


def test_incomplete_bucket_dropped():
    b=fixture().iloc[:8]
    assert len(aggregate(b,60,15))==2
    assert len(aggregate(b.drop(b.index[2]),60,15))==1

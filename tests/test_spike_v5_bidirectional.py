"""Directional structural and risk contracts; synthetic prices, no returns fit."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_burst_v5_structure import detect
from yoyo.evaluation.spike_burst_replay import risk_reference, path_reference


def short_fixture():
    f = pd.DataFrame(dict(open=101., high=102., low=99., close=99.8,
        md=0.5, sb=0.6, atr=1., ropeLow=100., legacy_confirmed=False,
        legacy_parent_low=100., legacy_parent_high=103., ready=True,
        data_gap=False, confirmed=True), index=range(12))
    f.loc[2, 'legacy_confirmed'] = True
    # An internal range retest above support does not destroy short provenance.
    f.loc[3, ['close','md']] = [101., 0.4]
    f.loc[4, ['open','high','low','close','md','sb']] = [99.5,102.,98.,99.,0.3,0.4]
    return f


def test_short_waits_for_body_allows_upper_wick_and_range_retest():
    f=short_fixture();out=detect(f,side=-1)
    assert out.pending.iloc[3]
    assert out.confirmed[out.confirmed].index.tolist()==[4]
    assert out.legacy_i.iloc[4]==2
    assert f.high.iloc[4]>f.ropeLow.iloc[4]


def test_short_body_can_precede_legacy_and_flat_md_cannot_confirm():
    f=short_fixture();f['legacy_confirmed']=False
    f.loc[2,['open','high','low','close']]=[99.5,102.,98.,99.]
    f.loc[3,'close']=99.8
    f.loc[4,'legacy_confirmed']=True
    f.loc[4,['open','md','sb']]=[101.,0.,0.]
    f.loc[5,['md','sb']]=[-0.1,0.]
    out=detect(f,side=-1)
    assert not out.confirmed.iloc[4] and out.confirmed.iloc[5]
    assert out.body_support_i.iloc[5]==2


@pytest.mark.parametrize('failure',['range','gap','unknown'])
def test_short_invalidated_provenance_cannot_revive(failure):
    f=short_fixture()
    if failure=='range': f.loc[3,['high','close']]=[104.,103.1]
    if failure=='gap': f.loc[3,'data_gap']=True
    if failure=='unknown': f.loc[3,'ropeLow']=np.nan
    assert not detect(f,side=-1).confirmed.any()


def test_short_prefix_and_price_reflection_match_long():
    f=short_fixture();s=detect(f,side=-1)
    # Reflection is only a synthetic algebra check, never a model augmentation.
    m=f.copy();m['open']=200-f.open;m['close']=200-f.close
    m['high']=200-f.low;m['low']=200-f.high;m['ropeHigh']=200-f.ropeLow
    m['legacy_parent_high']=200-f.legacy_parent_low
    m['legacy_parent_low']=200-f.legacy_parent_high
    m['md']=-f.md;m['sb']=-f.sb
    r=detect(m)
    for col in ['confirmed','pending','legacy_i','body_support_i','why_pending']:
        pd.testing.assert_series_equal(s[col],r[col])
    pd.testing.assert_frame_equal(detect(f.iloc[:5],side=-1),s.iloc[:5])
    f.loc[11,'confirmed']=False
    assert not detect(f,side=-1).confirmed.iloc[-1]


def test_short_stop_and_ratchet_use_upside_risk_and_downside_profit():
    risk=risk_reference(-1,100.,101.5,1.,tick=.01)
    assert risk.valid and risk.stop==102. and risk.risk==2.
    armed=path_reference(-1,100.,2.,102.,0.,False,100.,101.,95.,96.,1.,tick=.01)
    assert armed.alive and armed.armed and armed.peak_r==2.5 and armed.current_r==2.
    assert armed.protection==100.
    next_bar=path_reference(-1,100.,2.,armed.protection,armed.peak_r,True,96.,97.,90.,92.,1.,tick=.01)
    assert next_bar.alive and next_bar.protection==96. and next_bar.peak_r==5.
    stopped=path_reference(-1,100.,2.,96.,5.,True,98.,101.,85.,87.,1.,tick=.01)
    assert not stopped.alive and stopped.exit_price==98.
    assert stopped.peak_r==5.  # Stop-bar lows do not count as captured profit.


def test_invalid_direction_is_rejected():
    with pytest.raises(ValueError,match='side'):
        detect(short_fixture(),side=0)


def test_candidate_and_confirmation_engine_mirror_uses_same_chronology():
    from yoyo.evaluation.spike_burst_replay import features
    from yoyo.evaluation.spike_burst_early_warning import detect as long_legacy
    from yoyo.evaluation.spike_v5_bidirectional_check import short_legacy
    n=540;steps=np.arange(n);close=100.+0.15*np.sin(steps/3.)
    close[500:]+=np.minimum(steps[500:]-499,10)*.4
    opened=np.r_[close[0],close[:-1]]
    raw=pd.DataFrame(dict(open=opened,high=np.maximum(opened,close)+.1,
        low=np.minimum(opened,close)-.1,close=close,volume=np.where(steps>=500,1000.,100.)),
        index=pd.date_range('2024-01-01',periods=n,freq='h',tz='UTC'))
    f=features(raw);m=f.copy()
    for col in ['open','close','s20','e20','s60','e60','s120','e120','middle']:
        m[col]=200.-f[col]
    m['high']=200.-f.low;m['low']=200.-f.high
    m['ropeHigh']=200.-f.ropeLow;m['ropeLow']=200.-f.ropeHigh
    m['md']=-f.md;m['sb']=-f.sb
    long=long_legacy(f);short=short_legacy(m)
    assert long.confirmed.any()
    assert long.confirmed.tolist()==short.confirmed.tolist()
    assert long.parent_i.fillna(-1).tolist()==short.parent_i.fillna(-1).tolist()

import numpy as np
import pytest
from yoyo.evaluation import monday_retest_flow as r


@pytest.mark.parametrize("side", [1,-1])
def test_distinct_recovery_closes_and_touch_clocks(side):
    bars=np.tile([0,102,104,101,102,10,2,5],(180,1)).astype(float)
    bars[:,r.TS]=np.arange(len(bars))*r.MINUTE
    bars[7,r.LOW]=99
    bars[14,r.CLOSE]=103
    bars[23,r.LOW]=98
    bars[59,r.CLOSE]=103
    bars[60,r.LOW]=97
    if side==-1:
        bars[:,[r.LOW,r.HIGH]]=200-bars[:,[r.HIGH,r.LOW]]
        bars[:,r.CLOSE]=200-bars[:,r.CLOSE]
    assert r.find_retest(bars,7,179,1,15,side,100,3)==(14,23)
    assert r.find_retest(bars,7,179,1,60,side,100,3)==(59,60)
    assert r.find_retest(bars,7,58,1,60,side,100,3)==(None,None)
    assert r.find_retest(bars,7,20,1,15,side,100,3)==(14,None)


def test_cvd_sign_absorption_at_retest(monkeypatch):
    bars=np.tile([0,100.1,101,99.85,100.1,1,2,0],(300,1)).astype(float)
    bars[:,r.TS]=np.arange(len(bars))*r.MINUTE
    bars[250,r.LOW]=99.75
    bars[270,r.OPEN]=100
    monkeypatch.setattr(r.flow,'touch_tick',lambda *args:(None,"no_tape"))
    out=r.classify("TEST",bars,249,254,270,1,15,1,100)
    assert out['label']=='absorption'
    assert out['cvd_max']<0
    assert out['first_extreme']==99.75
    # Future prints after the retest cannot affect its label.
    bars[271:,r.BUY]=1
    assert r.classify("TEST",bars,249,254,270,1,15,1,100)==out

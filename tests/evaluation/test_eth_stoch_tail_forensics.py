"""Tail diagnostics distinguish stop ordering and pre-exit price visibility."""
from types import SimpleNamespace
import numpy as np

from yoyo.evaluation.eth_stoch_tail_forensics import first_passage, held_mfe, bracket_oracle
from yoyo.evaluation.eth_stoch_confirm_exits import trace, Policy


def ctx(bars):
    a=np.array(bars,float)
    return dict(o=a[:,0],h=a[:,1],l=a[:,2],c=a[:,3],arrow=np.zeros(len(a)//5+1))


def test_stop_minute_only_high_is_ambiguous_not_a_certain_tail():
    z=first_passage(ctx([[100,111,97,100]]),0,1,1,100,99,levels=(10,))
    assert not z['hit_10r'] and z['ambiguous_10r']
    assert z['static_mfe_r_lower']==0 and z['static_mfe_r_upper']==11


def test_target_at_open_is_before_later_same_bar_stop():
    z=first_passage(ctx([[100,101,99.5,100],[111,112,98,99]]),0,2,1,100,99,levels=(10,))
    assert z['hit_10r'] and z['first_10r_i']==1 and not z['ambiguous_10r']


def test_gap_stop_excludes_later_high():
    z=first_passage(ctx([[100,101,99.5,100],[98,120,97,110]]),0,2,1,100,99,levels=(10,))
    assert not z['hit_10r'] and not z['ambiguous_10r']
    assert z['static_mfe_r_upper']==1


def test_open_exit_cannot_claim_that_minutes_later_low():
    c=ctx([[100,101,99,100],[100,101,70,75]])
    r=SimpleNamespace(side=-1,entry_i=0,exit_i=1,entry=100,exit_price=100,risk=2,reason='opposite',exit_at_open=True)
    assert held_mfe(c,r)==(.5,.5)


def test_signed_short_first_passage():
    z=first_passage(ctx([[100,100.5,89,90],[90,102,89,100]]),0,2,-1,100,101,levels=(10,20))
    assert z['hit_10r'] and not z['hit_20r'] and z['static_mfe_r_lower']==11


def test_no_opposite_bracket_preserves_later_target():
    c=ctx([[100,101,99.5,100]]*6+[[100,111,99.5,110]])
    c['arrow'][0]=-1;r=SimpleNamespace(entry_i=0,side=1,entry=100,stop=99,risk=1)
    assert bracket_oracle(c,r,7,10,True)==(5,100.,'opposite')
    assert bracket_oracle(c,r,7,10,False)==(6,110.,'target')


def test_tick_exact_integer_r_target():
    c=ctx([[2300,2301,2299.99,2300.5]])
    z=trace(c,0,1,2300,2299.9,Policy('target',target=10,target_tick=.01))
    assert z['reason']=='target' and z['exit_price']==2301

"""Focused state-machine tests for the receipt-bound exit-policy engine."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as study


def test_partial_request_conserves_original_quantity_on_one_bar_jump() -> None:
    assert study._partial_request("partial_1_25_3_35", 3.1, False, False) == (.60, "partial_1r_25_and_3r_35_next_open", True, True)
    assert study._partial_request("partial_1_25_3_35", 3.1, True, False) == (.35, "partial_3r_35_next_open", True, True)


def test_cost_breakeven_is_not_entry_price() -> None:
    assert study._be_level("be1_cost", 100.0, 1, 1.0) == (100.2, "be1_cost")
    assert study._be_level("be1_cost", 100.0, -1, 1.0) == (99.8, "be1_cost")


def test_fast_ma_and_md_cross_require_a_close_confirmation() -> None:
    index = pd.date_range("2024-09-10", periods=2, freq="h", tz="UTC")
    frame = pd.DataFrame({"close": [101., 99.], "s20": [100., 100.], "e20": [100., 100.],
                          "md": [2., 0.], "sb": [1., 1.]}, index=index)
    assert not study._fast_ma_wrong_side(frame, 0, 1)
    assert study._fast_ma_wrong_side(frame, 1, 1)
    assert study._md_cross_wrong_side(frame, 1, 1)


def test_frozen_representative_baseline_matches_legacy_closed_ledger() -> None:
    root = study.SOURCE_STREAMS
    folder = next(path for path in sorted(root.iterdir()) if path.is_dir())
    context = study.load_verified_stream(folder)
    new, fills, _ = study.replay_policy(context, cohort="v1_common_long", policy="baseline")
    old = pd.read_csv(folder / "trades.csv.gz")
    old = old.loc[old.variant.eq("v1_common_execution_long") & ~old.censored.astype(bool)].copy()
    new = new.loc[~new.censored.astype(bool)].copy()
    columns = ["signal_i", "entry_i", "side", "exit_i", "exit_reason"]
    assert new[columns].sort_values("signal_i").reset_index(drop=True).equals(old[columns].sort_values("signal_i").reset_index(drop=True))
    assert len(fills.loc[fills.kind.eq("entry")]) == len(new)
    assert (fills.groupby("trade_id").cost_return.sum() <= .002 + 1e-12).all()
    assert math.isclose(float(fills.groupby("trade_id").cost_return.sum().max()), .002, rel_tol=0, abs_tol=1e-12)


def _synthetic():
    import numpy as np
    ix=pd.date_range('2024-09-10',periods=10,freq='h',tz='UTC')
    b=pd.DataFrame({'open':100.,'high':100.5,'low':99.,'close':100.,'atr':1.,'s20':99.,'e20':99.,'md':2.,'sb':1.},index=ix)
    sig=pd.DataFrame({'long_signal':False,'short_signal':False},index=ix);sig.iloc[4,0]=True
    bb=pd.DataFrame({'v7_ready':True,'prior_squeeze_run3':True},index=ix)
    cache=dict(bars=b,signals=sig,v1_signals=sig.copy(),data_gap=pd.Series(False,index=ix),bb=bb,tick=.01)
    ledger=pd.DataFrame({'signal_bar_open':[ix[4]],'signal_i':[4]})
    return study.StreamContext(Path('.'),'synthetic',{},cache,ledger,60,dict(venue='test',symbol='A',asset='A',timeframe_min=60))


def test_new_breakeven_never_stops_earlier_in_trigger_bar():
    c=_synthetic();b=c.cache['bars'];b.iloc[5,b.columns.get_loc('close')]=102.;b.iloc[5,b.columns.get_loc('high')]=103.
    b.iloc[5,b.columns.get_loc('low')]=99. # old SL98 survives; new BE100 not retroactive
    t,f,e=study.replay_policy(c,cohort='v6_both',policy='be1_price')
    assert t.exit_i.iloc[0]==6
    assert t.net_return.iloc[0]==pytest.approx(-.002)
    assert not f.loc[f.kind.eq('exit'),'bar_open'].eq(b.index[5]).any()


def test_cost_breakeven_and_partial_gap_use_real_next_open():
    c=_synthetic();b=c.cache['bars'];b.iloc[5,b.columns.get_loc('close')]=102.;b.iloc[5,b.columns.get_loc('high')]=103.
    b.iloc[6]=[100.3,100.5,100.1,100.4,1,99,99,2,1]
    t,f,e=study.replay_policy(c,cohort='v6_both',policy='be1_cost')
    assert t.net_return.iloc[0]==pytest.approx(0,abs=1e-12)
    c=_synthetic();b=c.cache['bars'];b.iloc[5,b.columns.get_loc('close')]=106.;b.iloc[5,b.columns.get_loc('high')]=107.
    b.iloc[6]=[107,108,97,100,1,99,99,2,1]
    t,f,e=study.replay_policy(c,cohort='v6_both',policy='partial_1_25_3_35')
    exits=f.loc[f.kind.ne('entry')]
    assert exits.qty_fraction.tolist()==pytest.approx([.6,.4])
    assert exits.price.iloc[0]==107
    assert f.cost_return.sum()==pytest.approx(.002)
    assert exits.execution_phase.tolist()==['open','intrabar']


def test_trailing_protection_remains_armed_below_two_r():
    c=_synthetic();b=c.cache['bars'];b.iloc[5]=[100,105,99,104,1,99,99,2,1]
    b.iloc[6]=[103,103,100.5,101.5,.1,99,99,2,1]
    b.iloc[7]=[101,102,100,101,1,99,99,2,1]
    t,f,e=study.replay_policy(c,cohort='v6_both',policy='baseline')
    assert t.exit_i.iloc[0]==7
    assert t.exit_price.iloc[0]==101
    assert t.exit_reason.iloc[0]=='trailing_stop_gap'


def test_early_exit_changes_reentry_opportunity():
    c=_synthetic();b=c.cache['bars'];b.iloc[5,b.columns.get_loc('md')]=0
    sig=c.cache['signals'];sig.iloc[7,0]=True
    c.signals_ledger.loc[1]=[b.index[7],7]
    new,_,_=study.replay_policy(c,cohort='v6_both',policy='md_cross_exit')
    old,_,_=study.replay_policy(c,cohort='v6_both',policy='baseline')
    assert len(new)==2 and len(old)==1
    assert new.exit_reason.iloc[0]=='md_sb_reverse_cross_next_open'

@pytest.mark.parametrize('policy',study.POLICIES)
@pytest.mark.parametrize('side',[1,-1])
def test_extending_future_cannot_change_already_executable_fills(policy,side):
    from dataclasses import replace
    from pandas.testing import assert_frame_equal
    c=_synthetic();b=c.cache['bars']
    b.iloc[5]=[100,105,99,104,1,99,99,2,1]
    b.iloc[6]=[103,108,102,107,1,99,99,2,1]
    b.iloc[7]=[108,109,107,108,1,99,99,2,1]
    b.iloc[8]=[90,91,80,81,1,99,99,0,1]
    if side==-1:
        old=b.copy()
        for col in ['open','close','s20','e20']:b[col]=200-old[col]
        b['high']=200-old.low;b['low']=200-old.high
        b['md']=-old.md;b['sb']=-old.sb
        c.cache['signals'].iloc[4]=[False,True]
    cutoff=b.index[8]
    c.cache['signals'].iloc[8]=[side==-1,side==1]
    cache={k:(v.iloc[:8].copy() if isinstance(v,(pd.DataFrame,pd.Series)) else v) for k,v in c.cache.items()}
    prefix=replace(c,cache=cache)
    _,full_f,full_e=study.replay_policy(c,cohort='v6_both',policy=policy)
    _,pre_f,pre_e=study.replay_policy(prefix,cohort='v6_both',policy=policy)
    for full,pre in [(full_f,pre_f),(full_e,pre_e)]:
        if 'kind' in full:
            full=full.loc[full.kind.ne('censor')];pre=pre.loc[pre.kind.ne('censor')]
        full=full.loc[full.event_time.le(cutoff) if 'event_kind' in full else full.event_time.lt(cutoff)]
        assert_frame_equal(full.reset_index(drop=True),pre.reset_index(drop=True),check_dtype=False)

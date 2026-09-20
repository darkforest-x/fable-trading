"""Behavioral checks for family-owned reversal, controls and immutable risk."""
from dataclasses import replace
import hashlib

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v9_htf_sma_study as old
from yoyo.evaluation.trend_baseline_study import execute, compare_prior, config, validate_prior_receipt


def prepared():
    ix = pd.date_range("2025-01-06", periods=48, freq="h", tz="UTC")
    frame = pd.DataFrame({"open":100.,"high":100.2,"low":99.8,"close":100.,"atr":1.,"ready":True},index=ix)
    p=source.prepared_arm(frame,np.zeros(len(ix),bool),np.zeros(len(ix),int),"synthetic",{"timeframe":"1h"},60,.01)
    return replace(p,ordinal=dict(zip(ix,range(len(ix)))))


def test_family_owns_reverse_exit_with_no_future_fill_and_unchanged_cost():
    p=prepared();raw=p.raw_side.copy();raw[6]=1;raw[12]=-1
    allowed=np.zeros(len(raw),bool);allowed[6]=True
    _,t,_=execute(p,allowed,raw,"dc20")
    assert len(t)==1 and t.entry_i.iloc[0]==7 and t.exit_i.iloc[0]==13
    assert t.exit_reason.iloc[0]=="opposite_signal_next_open"
    assert t.gross_return.iloc[0]==0 and t.net_return.iloc[0]==-.002
    no_exit=raw.copy();no_exit[12]=0
    _,unfinished,_=execute(p,allowed,no_exit,"dc20")
    assert unfinished.censored.iloc[0]
    assert not p.raw_side.any()


def test_controls_share_draw_but_keep_family_exit_outcomes_separate():
    p=prepared();cfg=config()
    # Choose a target whose deterministic draw leaves two observed bars.
    for i in range(6,20):
        key=f"synthetic|{p.frame.index[i].isoformat()}|1"
        options=np.delete(np.arange(len(p.frame)),i)
        j=int(options[int(hashlib.sha256(f"{cfg['seed']}|{key}".encode()).hexdigest(),16)%len(options)])
        if j+2<len(p.frame): break
    targets=pd.DataFrame([{"event_key":key,"signal_i":i,"side":1}])
    slow=old.controls(p,targets,cfg)
    fast=old.controls(replace(p,raw_side=np.full(len(p.frame),-1)),targets,cfg)
    assert slow.control_signal_i.iloc[0]==fast.control_signal_i.iloc[0]==j
    assert slow.control_censored.iloc[0] and not fast.control_censored.iloc[0]
    assert fast.control_exit_time.iloc[0]<slow.control_exit_time.iloc[0]


def test_gap_stop_precedes_scheduled_reverse():
    p=prepared();raw=p.raw_side.copy();raw[6]=1;raw[12]=-1
    oa=p.open.copy();oa[13]=95.
    la=p.low.copy();la[13]=94.
    p=replace(p,open=oa,low=la)
    allowed=np.zeros(len(raw),bool);allowed[6]=True
    _,t,_=execute(p,allowed,raw,"sma20_60")
    assert t.exit_reason.iloc[0]=="initial_stop_gap"
    assert t.exit_price.iloc[0]==95.


def test_prior_parity_rejects_different_cash_result():
    import pytest
    p=prepared();raw=p.raw_side.copy();raw[6]=1;raw[12]=-1
    _,t,_=execute(p,raw!=0,raw,"v9")
    compare_prior(t,t.copy())
    changed=t.copy();changed.loc[0,"net_r"]+=.1
    with pytest.raises(AssertionError): compare_prior(t,changed)


def test_self_consistent_prior_receipt_must_belong_to_frozen_input():
    import pytest
    receipt = {"identity_hash":"identity", "symbol":"BTCUSDT", "source_sha256":"raw"}
    validate_prior_receipt(receipt, "identity", "BTCUSDT", "raw")
    for field in receipt:
        replaced = {**receipt, field:"different"}
        with pytest.raises(AssertionError):
            validate_prior_receipt(replaced, "identity", "BTCUSDT", "raw")

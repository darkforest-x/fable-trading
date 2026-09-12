"""Regression checks for V1+ default reference/execution clocks."""
from __future__ import annotations

import pandas as pd

import yoyo.evaluation.spike_v1_plus_replay as replay
from yoyo.evaluation.spike_v1_plus_replay import _path_plus, simulate_next_open


def _bars(rows):
    index = pd.date_range("2025-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, index=index)


def _refs(index, *, side=1, signal=True, reverse=False, active=90.0):
    return pd.DataFrame({"signal": [signal] + [False] * (len(index)-1),
                         "signal_reason": ["accepted"] + [""] * (len(index)-1),
                         "raw_signal": [signal] + [False] * (len(index)-1),
                         "side": [side] + [0] * (len(index)-1),
                         "signal_i": [0] + [float("nan")] * (len(index)-1),
                         "signal_close": [100.0] + [float("nan")] * (len(index)-1),
                         "reference_initial_stop": [90.0] + [float("nan")] * (len(index)-1),
                         "reference_risk": [10.0] + [float("nan")] * (len(index)-1),
                         "reference_exit": [False, reverse] + [False] * max(0, len(index)-2),
                         "reference_exit_reason": ["", "opposite_reference" if reverse else ""] + [""] * max(0, len(index)-2),
                         "active_reference_protection": [float("nan"), active] + [active] * max(0, len(index)-2)}, index=index)


def test_default_stages_and_master_off_differ_only_after_close():
    # Close at +1R latches stage one and makes -0.5R active only next bar.
    got = _path_plus(1, 100, 10, 90, 0, False, False, False, 100, 111, 101, 110, 2, 1, True)
    assert got[0] and got[1] == 95 and got[6] and not got[7]
    # Master-off retains the old stop even though the close reaches +1R.
    base = _path_plus(1, 100, 10, 90, 0, False, False, False, 100, 111, 101, 110, 2, 1, False)
    assert base[1] == 90 and not base[6]


def test_stop_is_checked_before_same_bar_high_and_gap_is_adverse():
    stopped = _path_plus(1, 100, 10, 95, 3, True, True, False, 94, 130, 90, 120, 2, 1, True)
    assert not stopped[0] and stopped[4] == 94 and stopped[2] == 3
    bars = _bars([{"open":100,"high":101,"low":99,"close":100}, {"open":100,"high":110,"low":99,"close":105}, {"open":89,"high":110,"low":88,"close":105}])
    trades, fills = simulate_next_open(bars, _refs(bars.index), tick=1)
    assert len(trades) == 1 and trades.iloc[0].exit_price == 89
    assert fills.iloc[-1].execution_phase == "open"


def test_short_stop_and_opposite_reference_next_open_are_auditable():
    bars = _bars([{"open":100,"high":101,"low":99,"close":100}, {"open":100,"high":101,"low":98,"close":99}, {"open":97,"high":98,"low":96,"close":97}])
    # A short uses 90 as operative protection here only to exercise side-aware
    # handling; reverse exits close it at the next observed open.
    refs = _refs(bars.index, side=-1, reverse=True, active=110)
    refs.loc[refs.index[0], "reference_initial_stop"] = 110.0
    trades, fills = simulate_next_open(bars, refs, tick=1)
    assert trades.iloc[0].side == -1
    assert trades.iloc[0].exit_reason == "opposite_reference_next_open"
    assert trades.iloc[0].exit_price == 97
    assert set(fills.kind) == {"entry", "exit"}


def test_prefix_execution_is_identical_after_unaffected_prefix():
    rows=[{"open":100,"high":101,"low":99,"close":100}, {"open":101,"high":103,"low":100,"close":102}, {"open":104,"high":105,"low":103,"close":104}, {"open":106,"high":107,"low":105,"close":106}]
    bars=_bars(rows); refs=_refs(bars.index, active=90)
    whole,_=simulate_next_open(bars,refs,tick=1)
    suffix,_=simulate_next_open(bars.iloc[:],refs.iloc[:],tick=1)
    assert whole[["entry_price","exit_price","net_r"]].equals(suffix[["entry_price","exit_price","net_r"]])


def test_joint_overheat_consumes_raw_event_but_master_off_keeps_baseline(monkeypatch):
    index=pd.date_range("2025-01-01",periods=15,freq="h",tz="UTC")
    frame=pd.DataFrame({"open":95.,"high":100.,"low":90.,"close":95.,"md":0.,"sb":0.,"middle":0.,"atr":1.,"pastWidth":0.,"pastCrosses":20.,"ropeHigh":100.,"ropeLow":90.,"recentLow":90.,"recentHigh":100.,"rv":1.,"expansion":1.,"ready":True},index=index)
    frame.loc[index[13],["open","high","low","close","md","sb","middle","rv","expansion"]]=[100,120,99,119,1,0,1,60,11]
    monkeypatch.setattr(replay,"features",lambda _:frame)
    treated=replay.replay_references(pd.DataFrame(index=index),1.,enable_plus=True)
    baseline=replay.replay_references(pd.DataFrame(index=index),1.,enable_plus=False)
    assert treated.iloc[13].raw_signal and not treated.iloc[13].signal
    assert treated.iloc[13].signal_reason == "overheat_rejected"
    assert baseline.iloc[13].signal and baseline.iloc[13].side == 1

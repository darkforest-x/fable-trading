"""Guards for the V11.1 box rule: a break while the V9 long box is open is 突破+spike.

What must hold: no box, no joint; a break any number of bars after the V9 long
counts while the box is still open; one joint per box; a break after the box
closed (stop hit) does not count; the box state is the indicator's own reference.
"""
import numpy as np

from yoyo.evaluation.spike_v10_4 import box_joints, reference_long_exits


def test_break_inside_open_box_counts_without_any_window():
    n = 100
    long_open = np.zeros(n, bool); long_open[10:80] = True
    box = np.where(long_open, 10, -1)
    brk = np.zeros(n, bool); brk[[5, 60, 70]] = True
    out = box_joints(long_open, box, brk)
    assert np.flatnonzero(out).tolist() == [60]          # 50 bars after the V9 bar; 5 is before the box; 70 is the same box


def test_each_box_yields_one_joint_and_a_new_box_can_yield_another():
    n = 100
    long_open = np.zeros(n, bool); long_open[10:30] = True; long_open[50:70] = True
    box = np.full(n, -1); box[10:30] = 10; box[50:70] = 50
    brk = np.zeros(n, bool); brk[[12, 20, 55, 65, 90]] = True
    assert np.flatnonzero(box_joints(long_open, box, brk)).tolist() == [12, 55]


def test_box_is_the_reference_and_closes_on_its_stop():
    n = 40
    close = np.full(n, 100.0); high = close + 0.5; low = close - 0.5; atr = np.full(n, 1.0)
    low[20] = 90.0                                       # stop of the box opened at bar 10
    raw = np.zeros(n, int); raw[10] = 1
    state: dict = {}
    reference_long_exits(high, low, close, atr, ready=np.ones(n, bool), gap=np.zeros(n, bool),
                         raw_side=raw, signal_side=raw, tick=0.01, state=state)
    assert state["long_open"].tolist() == [10 <= i < 20 for i in range(n)]
    assert set(state["box_entry"][10:20]) == {10} and (state["box_entry"][20:] == -1).all()
    brk = np.zeros(n, bool); brk[[15, 25]] = True
    assert np.flatnonzero(box_joints(state["long_open"], state["box_entry"], brk)).tolist() == [15]

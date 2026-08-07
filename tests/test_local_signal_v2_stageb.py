"""Unit tests for Stage-B causal local-signal V2 sampling arithmetic."""
from __future__ import annotations

from scripts.audit_w20_midbox_causality import (
    box_inside_decision,
    decision_bar,
    future_bars,
    is_causal,
    visible_end_bar,
)
from scripts.build_local_signal_v2_stageb import (
    BOX_LEFT,
    event_id_of,
)


def test_stage_b_window_ends_on_decision():
    anchor, delay, win_len = 1000, 2, 24
    dec = decision_bar(anchor, delay)
    win_start = dec - win_len + 1
    assert visible_end_bar(win_start, win_len) == dec
    assert future_bars(win_start, win_len, anchor, delay) == 0
    assert is_causal(win_start, win_len, anchor, delay)


def test_box_never_past_decision_mode_c():
    anchor, delay = 500, 1
    dec = decision_bar(anchor, delay)
    s0, s1 = anchor - BOX_LEFT, dec
    assert box_inside_decision(s1, anchor, delay)
    assert s1 == dec
    assert s0 == anchor - 2


def test_event_id_stable():
    a = event_id_of("ETH_USDT_SWAP", 123, "ETH_USDT_SWAP_0001_pad200")
    b = event_id_of("ETH_USDT_SWAP", 123, "ETH_USDT_SWAP_0001_pad200")
    c = event_id_of("ETH_USDT_SWAP", 124, "ETH_USDT_SWAP_0001_pad200")
    assert a == b
    assert a != c
    assert len(a) == 16

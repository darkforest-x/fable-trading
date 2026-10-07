"""Mechanics of the frozen-entry exit replay on hand-built bars."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_exit_giveback import POLICIES, reproduced, simulate

TICK = 0.01


def bars(rows):
    o, h, l, c = (np.array(col, float) for col in zip(*rows))
    return dict(o=o, h=h, l=l, c=c, atr=np.full(len(o), 1.0))


def run(policy, rows, *, x, exit_price, exit_reason, side=1, entry=100.0, stop0=95.0):
    return simulate(policy, side=side, entry=entry, stop0=stop0, tick=TICK, e=0, x=x,
                    exit_price=exit_price, exit_reason=exit_reason, **bars(rows))


# entry 100, stop 95 (R=5). Rally to a 112 close (2.4R), then fade to the old stop.
RALLY_THEN_FADE = [
    (100, 101, 99, 100),     # 0 entry bar
    (100, 106, 100, 105),    # 1 close 1R
    (105, 113, 105, 112),    # 2 close 2.4R -> incumbent arms, trail = 112-4 = 108
    (112, 112, 108.5, 109),  # 3
    (109, 109, 107.5, 108),  # 4 low 107.5 breaches the 108 trail -> incumbent exit 108
]


def test_incumbent_reproduces_the_recorded_trailing_exit():
    out = run("incumbent", RALLY_THEN_FADE, x=4, exit_price=108.0, exit_reason="trailing_stop")
    assert out["parity"] == "ok" and out["exit_kind"] == "incumbent_stop"
    assert out["net_r"] == pytest.approx((0.08 - 0.002) / 0.05)


def test_trail2_tightens_and_exits_earlier_at_a_better_price():
    out = run("trail2", RALLY_THEN_FADE, x=4, exit_price=108.0, exit_reason="trailing_stop")
    # after the 112 close the 2ATR trail is 110; bar 3 low 108.5 hits it at 110
    assert out["exit_kind"] == "policy_stop" and out["gross_r"] == pytest.approx(2.0)


def test_lock1_caps_giveback_to_one_r_from_the_close_peak():
    out = run("lock1", RALLY_THEN_FADE, x=4, exit_price=108.0, exit_reason="trailing_stop")
    # close peak 2.4R -> lock at 1.4R = 107; never touched before the incumbent's 108 exit
    assert out["exit_kind"] == "incumbent_stop" and out["gross_r"] == pytest.approx(1.6)


def test_half2_scales_out_at_the_next_open_and_keeps_the_rest():
    out = run("half2", RALLY_THEN_FADE, x=4, exit_price=108.0, exit_reason="trailing_stop")
    assert out["acted"]
    assert out["gross_return"] == pytest.approx(0.5 * 0.12 + 0.5 * 0.08)


def test_be1_turns_a_winner_that_fades_into_a_scratch_instead_of_a_full_loss():
    fade = [(100, 101, 99, 100), (100, 106, 100, 105.5), (105, 105, 99, 99.5), (99, 99, 94, 94.5)]
    inc = run("incumbent", fade, x=3, exit_price=95.0, exit_reason="initial_stop")
    be = run("be1", fade, x=3, exit_price=95.0, exit_reason="initial_stop")
    assert inc["gross_r"] == pytest.approx(-1.0)
    assert be["exit_kind"] == "policy_stop" and be["gross_r"] == pytest.approx(0.0)


def test_opposite_signal_exit_is_taken_from_the_record():
    rows = RALLY_THEN_FADE[:3] + [(111, 111.5, 110, 111)]
    out = run("arm1", rows, x=3, exit_price=111.0, exit_reason="opposite_v6_next_open")
    assert out["exit_kind"] == "incumbent_opposite" and out["gross_r"] == pytest.approx(2.2)


def test_short_side_mirrors():
    rows = [(100, 101, 99, 100), (100, 100, 94, 95), (95, 95, 88, 88), (88, 92.5, 88, 92)]
    out = run("trail2", rows, x=3, exit_price=93.0, exit_reason="trailing_stop", side=-1, stop0=105.0)
    # short R=5; 88 close is 2.4R, 2ATR trail = 90, breached by the 92.5 high
    assert out["exit_kind"] == "policy_stop" and out["gross_r"] == pytest.approx(2.0)


def test_unreproduced_incumbent_flags_parity():
    out = run("incumbent", RALLY_THEN_FADE, x=3, exit_price=108.0, exit_reason="trailing_stop")
    assert out["parity"] == "recorded_stop_not_reproduced"


@pytest.mark.parametrize("policy", [p for p in POLICIES if p != "half2"])
def test_no_candidate_ever_loosens_protection(policy):
    out = run(policy, RALLY_THEN_FADE, x=4, exit_price=108.0, exit_reason="trailing_stop")
    assert out["gross_r"] >= 1.6 - 1e-9


def test_statistics_use_only_reproduced_trades():
    rows = []
    for key, parity in (("a", "ok"), ("b", "recorded_stop_not_reproduced")):
        for policy in ("incumbent", "be1"):
            rows.append({"cohort": "signal", "version": "baseline", "timeframe_min": 15, "trade_key": key,
                         "entry_time": "t", "policy": policy, "parity": parity if policy == "incumbent" else "ok"})
    kept = reproduced(pd.DataFrame(rows))
    assert set(kept.trade_key) == {"a"} and len(kept) == 2

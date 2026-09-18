"""Guards for the V10.4 1h increment audit (three arms, trace, boundary, exit timing).

What must hold: no 5m row at/after 2026-05-01 is read; the record-only trace
never changes a born/break/joint event; the exit a trade gets does not depend on
which arm admitted it (a raw V9 short exits at the next open in every arm); the
fill is the next bar's open and the stop is frozen from bars up to the signal;
a trail computed on a close only protects from the following bar; every
candidate ends in exactly one status and taken = closed + censored.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v10_4 import joint_events

# Imported as a sibling module (pytest prepends this directory); `tests.` imports are
# shadowed by a site-packages package, as noted in test_spike_v10_long.py.
from test_spike_v10_4 import default_facts, staircase


def _csv(tmp_path, stamps):
    rows = ["ts,open,high,low,close,volume,open_time"]
    for s in stamps:
        t = pd.Timestamp(s)
        rows.append(f"{t.value // 10**6},1,1.1,0.9,1,10,{t}")
    path = tmp_path / "binance_um_TESTUSDT_5m_3.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def test_boundary_refuses_any_row_at_or_after_data_end(tmp_path):
    ok = _csv(tmp_path, ["2026-04-30T23:50:00Z", "2026-04-30T23:55:00Z"])
    assert len(inc.guarded_5m(ok, pd.Timestamp("2026-04-30T00:00:00Z"))) == 2
    bad = _csv(tmp_path, ["2026-04-30T23:55:00Z", "2026-05-01T00:00:00Z"])
    with pytest.raises(ValueError, match="at or after"):
        inc.guarded_5m(bad, pd.Timestamp("2026-04-30T00:00:00Z"))
    holdout = _csv(tmp_path, ["2026-05-04T00:00:00Z"])
    with pytest.raises(ValueError):
        study.load_5m(holdout, pd.Timestamp("2026-05-01T00:00:00Z"))


def test_trace_is_record_only():
    o, h, lo, c, a = staircase(periods=2400, seed=5)
    n = len(c)
    confirmed = np.zeros(n, bool)
    confirmed[np.arange(300, n, 7)] = True
    facts = default_facts(n, confirmed_long=confirmed, parent_high=np.full(n, -1e9))
    plain = joint_events(o, h, lo, c, a, tick=0.01, **facts)
    trace: dict = {}
    traced = joint_events(o, h, lo, c, a, tick=0.01, trace=trace, **facts)
    for name in ("born_event", "break_event", "joint_event"):
        assert np.array_equal(getattr(plain, name), getattr(traced, name))
    assert plain.joints == [{k: v for k, v in j.items() if k != "displayed_main_before"} for j in traced.joints]
    assert len(trace["lines"]) >= int(plain.born_event.sum()) > 0
    paired = [e for e in trace["spike_events"] if e["event"] == "paired"]
    assert sorted(e["spike_i"] for e in paired) == sorted(j["spike_i"] for j in plain.joints)
    assert all(e["event"] in ("saved", "paired", "pending_at_data_end") or e["event"].startswith("dropped:")
               for e in trace["spike_events"])


def _prepared(open_, high, low, close, raw_side=None, gap=None):
    n = len(close)
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "atr": 1.0,
                          "ready": True}, index=index)
    side = np.zeros(n, int) if raw_side is None else np.asarray(raw_side)
    g = np.zeros(n, bool) if gap is None else np.asarray(gap)
    return study.prepared_arm(frame, g, side, "t:TEST:1h", {"venue": "t", "symbol": "TEST", "asset": "TEST",
                                                            "timeframe": "1h", "timeframe_min": 60}, 60, 0.01)


def test_fill_is_next_open_and_stop_is_frozen_from_signal_history():
    n = 40
    close = np.full(n, 100.0); open_ = close.copy(); high = close + 0.5; low = close - 0.5
    open_[11], high[11] = 100.7, 101.0                # next-bar open differs from the signal close
    status, row = inc.attempt(_prepared(open_, high, low, close), 10)
    assert status == "censored_boundary" and row["entry_i"] == 11 and row["entry_price"] == 100.7
    assert row["initial_stop"] == pytest.approx(98.0)  # min(99.5-0.2, 100-2) floored to tick
    later = low.copy(); later[20] = 99.0              # future bars cannot move the frozen stop
    _, again = inc.attempt(_prepared(open_, high, later, close), 10)
    assert again["initial_stop"] == row["initial_stop"] and again["initial_risk"] == pytest.approx(100.7 - 98.0)


def test_raw_short_exits_at_next_open_whatever_arm_admitted_the_trade():
    n = 40
    close = np.full(n, 100.0); open_ = close.copy(); high = close + 0.5; low = close - 0.5
    raw = np.zeros(n, int); raw[15] = -1
    open_[16] = 100.3
    prepared = _prepared(open_, high, low, close, raw_side=raw)
    results = {}
    for arm in inc.ARM_KEYS:
        trades, statuses = inc.serial(prepared, np.array([10]), "t:TEST:1h", arm)
        results[arm] = (trades[0]["exit_i"], trades[0]["exit_reason"], trades[0]["exit_price"], trades[0]["net_r"])
    assert len(set(results.values())) == 1
    assert results["joint"][:3] == (16, "opposite_v6_next_open", 100.3)


def test_a_trail_set_on_a_close_only_protects_from_the_next_bar():
    n = 40
    close = np.full(n, 100.0); open_ = close.copy(); high = close + 0.5; low = close - 0.5
    close[12], high[12] = 105.0, 105.2               # entry 100 at bar 11, risk 2 -> close is 2.5R
    low[12] = 100.9                                   # below the new trail (105-4=101) but above the old stop
    open_[13], close[13], high[13], low[13] = 105.0, 104.0, 105.1, 100.5
    status, row = inc.attempt(_prepared(open_, high, low, close), 10)
    assert row["exit_i"] == 13 and row["exit_reason"] == "trailing_stop" and row["exit_price"] == pytest.approx(101.0)


def test_every_candidate_gets_exactly_one_status():
    n = 60
    close = np.full(n, 100.0); open_ = close.copy(); high = close + 0.5; low = close - 0.5
    raw = np.zeros(n, int); raw[30] = -1
    gap = np.zeros(n, bool); gap[[46, 51]] = True
    prepared = _prepared(open_, high, low, close, raw_side=raw, gap=gap)
    candidates = np.array([10, 12, 35, 47, 50, 59])
    trades, statuses = inc.serial(prepared, candidates, "t:TEST:1h", "joint")
    table = pd.DataFrame(statuses)
    assert table.signal_i.tolist() == candidates.tolist()
    assert table.status.tolist() == ["closed", "skipped_in_position", "censored_gap", "risk_invalid",
                                     "next_bar_is_gap", "no_next_bar"]
    assert len(trades) == 2 and trades[1]["exit_i"] == 46

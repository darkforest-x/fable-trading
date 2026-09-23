"""Behavioral checks for the bounded V12.8 replay ledger."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as recent


def _bars(periods=3200, freq="5min"):
    index = pd.date_range("2025-11-15", periods=periods, freq=freq, tz="UTC")
    close = 100 + np.linspace(0, 5, periods) + np.sin(np.arange(periods) / 17)
    return pd.DataFrame({"open": close - .1, "high": close + .4, "low": close - .5,
                         "close": close, "volume": 100 + (np.arange(periods) % 23)}, index=index)


def test_15m_facts_reuses_v126_reference_exactly():
    base = _bars()
    chart, _ = recent.complete_bars(base, 15)
    ours = recent.facts_for(chart, base, "BTC", .1, 15)
    reference = recent.pine_facts(chart, base, "BTC", .1)
    for field in ("gap", "side", "v9", "v9_long", "ref_long_exit", "ready", "can_run", "long_alive"):
        assert np.array_equal(ours[field], reference[field]), field
    assert np.array_equal(ours["box"]["box_entry"], reference["box"]["box_entry"])


def test_hour_facts_do_not_add_the_15m_h1_gate(monkeypatch):
    bars = _bars(1000, "1h")
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("the 15m H1 gate must not run for 1h")

    monkeypatch.setattr(recent, "pine_facts", forbidden)
    facts = recent.facts_for(bars, bars, "BTC", .1, 60)
    assert not called
    assert "h1" not in facts
    assert facts["v9"].dtype == bool


def _prepared_for_mfe():
    index = pd.date_range("2026-08-01", periods=4, freq="15min", tz="UTC")
    return SimpleNamespace(
        frame=pd.DataFrame(index=index), gap=np.zeros(4, dtype=bool),
        open=np.array([100., 100., 99., 95.]), high=np.array([101., 104., 101., 110.]),
        low=np.array([99., 98., 96., 90.]), close=np.array([100., 103., 97., 96.]),
    )


def test_stop_bar_wick_is_only_upper_bound_and_short_is_symmetric():
    prepared = _prepared_for_mfe()
    long = recent._mfe_fields({"side": 1, "entry_price": 100., "initial_risk": 2., "entry_i": 1,
                                "exit_i": 2, "censored": False, "exit_reason": "initial_stop"}, prepared)
    short = recent._mfe_fields({"side": -1, "entry_price": 100., "initial_risk": 2., "entry_i": 1,
                                 "exit_i": 2, "censored": False, "exit_reason": "initial_stop"}, prepared)
    assert long["mfe_known_r"] == 2
    assert long["mfe_upper_r"] == 2
    assert not long["stop_bar_excursion_ambiguous"]
    # For a short stopped at 102, the terminal low of 96 might have preceded the stop.
    assert short["mfe_known_r"] == 1
    assert short["mfe_upper_r"] == 2
    assert short["stop_bar_excursion_ambiguous"]


def test_short_attempt_uses_the_original_fixed_exit_engine():
    index = pd.date_range("2026-08-01", periods=8, freq="15min", tz="UTC")
    frame = pd.DataFrame({"open": [100.] * 8, "high": [101., 101., 101., 101., 101., 100.5, 103., 100.],
                          "low": [99.] * 8, "close": [100.] * 8, "atr": [1.] * 8}, index=index)
    prepared = recent.source.prepared_arm(frame, np.zeros(8, bool), np.zeros(8, int), "x", {"symbol": "X"}, 15, .1)
    row = recent._initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low, prepared.close,
                                        prepared.atr, prepared.gap, 4, -1, prepared.spec)
    assert row is not None
    direct = recent.fixed.replay_fixed_entry(prepared.context, pd.Series(row), arm="v8", enable_be=False, prepared=prepared)
    status, actual = recent.attempt(prepared, 4, -1)
    assert status == "closed"
    assert actual is not None
    for column in ("exit_i", "exit_price", "exit_reason", "gross_return", "net_return", "net_r"):
        assert actual[column] == direct[column]


def test_gap_stop_does_not_read_terminal_wick():
    prepared = _prepared_for_mfe()
    result = recent._mfe_fields({"side": 1, "entry_price": 100., "initial_risk": 2., "entry_i": 1,
                                 "exit_i": 3, "censored": False, "exit_reason": "initial_stop_gap"}, prepared)
    assert result["mfe_known_r"] == 2
    assert result["mfe_upper_r"] == 2
    assert not result["stop_bar_excursion_ambiguous"]


def test_controls_match_week_fold_and_atr_bin_without_redraw(monkeypatch):
    index = pd.date_range("2026-08-24", periods=8, freq="15min", tz="UTC")
    frame = pd.DataFrame({"ready": True}, index=index)
    prepared = SimpleNamespace(frame=frame, context=SimpleNamespace(minutes=15), atr=np.ones(8), close=np.full(8, 100.), gap=np.zeros(8, bool))
    calls = []

    def fake_attempt(_prepared, i, side):
        calls.append((i, side))
        return "closed", {"censored": False, "net_r": 1., "net_return": .01,
                            "exit_time": index[-1], "exit_reason": "boundary"}

    monkeypatch.setattr(recent, "attempt", fake_attempt)
    cfg = {"start": "2026-08-24T00:00:00Z", "end": "2026-08-24T02:00:00Z", "split": "2026-08-24T01:00:00Z",
           "vol_bins": [.005, .01, .02], "control_seed": 7}
    trades = [{"signal_i": 1, "side": -1, "trade_key": "one", "arm": "v9_both", "symbol": "X",
               "censored": False, "net_return": .02}]
    out = recent.matched_controls(prepared, trades, cfg)
    assert len(calls) == 1 and out.iloc[0].matched
    chosen = int(out.iloc[0].control_sig)
    assert chosen != 1
    assert out.iloc[0].utc_week == (index[1] + pd.Timedelta(minutes=15)).strftime("%G-W%V")
    assert out.iloc[0].fold == "earlier"


def test_serial_replays_prefix_but_only_emits_window_rows(monkeypatch):
    index = pd.date_range("2026-07-22", periods=5, freq="15min", tz="UTC")
    prepared = SimpleNamespace(frame=pd.DataFrame(index=index))

    def fake_attempt(_prepared, i, side):
        return "closed", {"signal_i": i, "signal_bar_open": index[i], "entry_i": i + 1, "entry_time": index[min(i + 1, 4)],
                            "side": side, "entry_price": 100., "initial_stop": 98., "initial_risk": 2., "initial_risk_frac": .02,
                            "exit_i": i + 1, "exit_time": index[min(i + 1, 4)], "exit_price": 101., "exit_reason": "opposite_v6_next_open",
                            "gross_return": .01, "net_return": -.01, "gross_r": .5, "net_r": -.5, "censored": False,
                            "mfe_r": .5, "mfe_known_r": .5, "mfe_upper_r": .5, "close_peak_r": .2,
                            "stop_bar_excursion_ambiguous": False}

    monkeypatch.setattr(recent, "attempt", fake_attempt)
    prefix = {"signal_i": 0, "side": 1, "signal_close": index[0], "in_window": False}
    emitted = {"signal_i": 1, "side": -1, "signal_close": index[1], "in_window": True}
    trades, statuses = recent.serial(prepared, [prefix, emitted], "v9_both", "x", )
    assert len(trades) == len(statuses) == 1
    assert trades[0]["signal_i"] == 1

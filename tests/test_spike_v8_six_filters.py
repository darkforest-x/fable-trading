"""Focused causal contracts for the independent V8 admission filters."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as be05
from yoyo.evaluation import spike_v8_six_filters as study


def _context(*, asset: str = "SYN", start: str = "2026-01-01", side: int = 1) -> base.StreamContext:
    index = pd.date_range(start, periods=11, freq="30min", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 100.5, "low": 99., "close": 100., "atr": 1., "rv": 1., "expansion": 1.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ropeHigh": 100., "ropeLow": 100.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[4], "long_signal" if side == 1 else "short_signal"] = True
    cache = {"bars": bars, "signals": signals.copy(), "v1_signals": signals.copy(), "data_gap": pd.Series(False, index=index),
             "bb_ready": pd.Series(True, index=index), "bb": pd.DataFrame({"prior_squeeze_run3": True, "v7_ready": True}, index=index), "tick": .01}
    ledger = pd.DataFrame({"signal_bar_open": [index[4]], "signal_i": [4]})
    return base.StreamContext(path=Path("."), key="binance_30m_six_filters_synthetic", receipt={"source_sha256":"synthetic", "cache_sha256":"synthetic"}, cache=cache, signals_ledger=ledger, minutes=30, identity={"venue":"binance", "symbol":"SYNUSDT", "asset":asset, "timeframe_min":30})


def _catalog(context: base.StreamContext) -> dict[tuple[str, str], dict]:
    return {(context.identity["venue"], context.identity["symbol"]): {"venue":"binance", "symbol":"SYNUSDT", "raw":{"underlyingType":"COIN"}}}


def test_rv_is_final_confirmation_bar_and_missing_is_unknown_not_rejected() -> None:
    context = _context(); prepared = be05.prepare_arm(context, arm="v8")
    context.cache["bars"].loc[prepared.frame.index[4], "rv"] = 50.1
    rejected = study.decision_frame(prepared, "rv_gt50", catalog=_catalog(context)).iloc[0]
    assert (rejected.gate_state, rejected.gate_reason) == ("rejected", "rv_gt50")
    context.cache["bars"].loc[prepared.frame.index[4], "rv"] = float("nan")
    unknown = study.decision_frame(prepared, "rv_gt50", catalog=_catalog(context)).iloc[0]
    assert (unknown.gate_state, unknown.gate_reason) == ("unknown_untested", "rv_missing")


def test_actual_next_open_risk_gate_is_30_percent_not_point_three_percent() -> None:
    context = _context(); bars = context.cache["bars"]
    bars.loc[bars.index[1:5], "low"] = 50.  # known stop window before the signal; no future OHLC
    prepared = be05.prepare_arm(context, arm="v8")
    decisions = study.decision_frame(prepared, "risk_gt30pct", catalog=_catalog(context))
    assert decisions.iloc[0].actual_initial_risk_pct > .30
    trades, _, actual = study.replay_serial(context, policy="risk_gt30pct", prepared=prepared, catalog=_catalog(context))
    assert trades.empty
    assert actual.iloc[0][["gate_state", "entry_attempted", "entry_filled"]].tolist() == ["rejected", True, False]


def test_rejected_opposite_still_closes_existing_position() -> None:
    context = _context(); bars, signals, index = context.cache["bars"], context.cache["signals"], context.cache["bars"].index
    signals.loc[index[5], "short_signal"] = True; context.cache["v1_signals"].loc[:, :] = signals
    context = replace(context, signals_ledger=pd.DataFrame({"signal_bar_open": [index[4], index[5]], "signal_i": [4, 5]}))
    bars.loc[index[5], "rv"] = 60.  # the opposite cannot open, but must close the old long
    bars.loc[index[6], ["open", "high", "low", "close"]] = [100., 101., 99., 100.]
    prepared = be05.prepare_arm(context, arm="v8")
    trades, _, decisions = study.replay_serial(context, policy="rv_gt50", prepared=prepared, catalog=_catalog(context))
    assert trades.loc[0, "exit_reason"] == "opposite_v6_next_open"
    assert len(trades) == 1
    assert decisions.loc[decisions.side.eq(-1), "gate_state"].item() == "rejected"


def test_baseline_noop_is_exact_clean_v8_contract() -> None:
    context = _context(); prepared = be05.prepare_arm(context, arm="v8")
    expected, _, _ = be05.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    actual, _, _ = study.replay_serial(context, policy="baseline_noop", prepared=prepared, catalog=_catalog(context))
    study.assert_baseline_parity(actual, expected)


def test_fixed_event_keeps_rejected_original_net_r_and_marks_unknown() -> None:
    context = _context(); prepared = be05.prepare_arm(context, arm="v8")
    baseline, _, _ = be05.replay_serial(context, arm="v8", enable_be=False, prepared=prepared)
    decisions = study.decision_frame(prepared, "usdc_base", catalog=_catalog(context))
    decisions.loc[:, ["gate_state", "gate_reason"]] = ["rejected", "synthetic"]
    fixed = study.fixed_event(baseline, decisions, policy="usdc_base")
    assert fixed.loc[0, "fixed_event_status"] == "rejected"
    assert fixed.loc[0, "gate_rejected"]
    assert pd.isna(fixed.loc[0, "net_r"]) and pd.isna(baseline.loc[0, "net_r"])

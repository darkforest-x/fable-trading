"""V12.8 monitor H1 admission is causal, directional, and fail closed."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import v128_signals, v9_signals


H1_MS = 3_600_000
M15_MS = 900_000


@pytest.mark.parametrize("timeframe", ["15m", "30m", "1H", "4H"])
def test_new_contract_with_one_closed_candle_is_warming_not_an_error(timeframe):
    row = {"t": 1790150400000, "o": 4.05, "h": 4.16, "l": 3.832, "c": 3.91, "v": 150257.0}
    result = v128_signals.analyze([row], [], timeframe, tick=.0001, base_asset="CYPH")
    assert result["events"] == []
    assert result["state"]["phase"] == "loading"
    assert result["state"]["bars"] == 1 and result["state"]["ready"] is False
    assert result["chart"][0]["c"] == row["c"]
    assert result["chart"][0]["burst"] is False


def candles(count=200, *, start="2025-01-06T00:00:00Z", step=M15_MS, close=None, base=100.0, slope=.1):
    origin = int(pd.Timestamp(start).timestamp() * 1000)
    result = []
    for i in range(count):
        value = float(close) if close is not None else base + i * slope
        delta = max(abs(value) * .01, 1e-9)
        result.append({"t": origin + i * step, "o": value, "h": value + delta,
                       "l": value - delta, "c": value, "v": 10.0})
    return result


def hourly(count=200, *, start="2025-01-01T20:00:00Z", close=100.0):
    origin = int(pd.Timestamp(start).timestamp() * 1000)
    delta = max(abs(close) * .01, 1e-9)
    return [{"t": origin + i * H1_MS, "o": close, "h": close + delta, "l": close - delta,
             "c": close, "v": 10.0}
            for i in range(count)]


def install_raw_signals(monkeypatch, signals, *, rv=1.0):
    original_features = v9_signals.features

    def prepared(frame):
        result = original_features(frame)
        result["rv"] = rv
        return result

    def raw(frame, minutes):
        del minutes
        result = pd.DataFrame({"long_signal": False, "short_signal": False}, index=frame.index)
        for index, side in signals.items():
            result.iloc[index, result.columns.get_loc("long_signal" if side == 1 else "short_signal")] = True
        return result

    def bb(frame, *, data_gap):
        del data_gap
        return pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=frame.index)

    monkeypatch.setattr(v9_signals, "features", prepared)
    monkeypatch.setattr(v9_signals, "v6_signals", raw)
    monkeypatch.setattr(v9_signals, "v7_diagnostics", bb)


def set_close(rows, index, close):
    delta = max(abs(close) * .01, 1e-9)
    rows[index].update(o=close, h=close + delta, l=close - delta, c=close)


def test_neiro_example_is_rejected_by_strict_prior_h1_sma60(monkeypatch):
    install_raw_signals(monkeypatch, {130: -1})
    chart = candles(base=.00009495, slope=.00000001)
    set_close(chart, 130, .00009625)
    h1 = hourly(close=.0000959465)

    result = v128_signals.analyze(chart, h1, "15m", tick=.000000001, base_asset="NEIRO")
    marker = result["chart"][130]

    assert result["events"] == []
    assert marker["v9_admitted"] is True
    assert marker["v128_admitted"] is False
    assert marker["h1_sma60_ready"] is True
    assert marker["h1_sma60"] == pytest.approx(.0000959465, abs=1e-12)
    assert marker["h1_sma60_direction_allowed"] is False
    assert result["state"]["v128_evidence"]["h1_sma60_applies"] is True
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("side,close", [(1, 113.0), (-1, 113.0)])
def test_equal_to_h1_sma_is_rejected_for_both_directions(monkeypatch, side, close):
    install_raw_signals(monkeypatch, {130: side})
    chart = candles()
    set_close(chart, 130, close)
    result = v128_signals.analyze(chart, hourly(close=113.0), "15m", tick=.001, base_asset="ETH")

    assert result["events"] == []
    assert result["chart"][130]["v9_admitted"] is True
    assert result["chart"][130]["v128_admitted"] is False


@pytest.mark.parametrize("side,h1_close", [(1, 112.0), (-1, 114.0)])
def test_strictly_correct_side_of_h1_sma_passes(monkeypatch, side, h1_close):
    install_raw_signals(monkeypatch, {130: side})
    chart = candles()
    result = v128_signals.analyze(chart, hourly(close=h1_close), "15m", tick=.001, base_asset="ETH")

    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["v128_admitted"] is True
    assert event["side"] == ("long" if side == 1 else "short")
    assert event["v128_evidence"]["h1_sma60_direction_allowed"] is True
    marker = result["chart"][130]
    assert marker["burst"] and marker["v128_admitted"]
    assert marker["burst_up"] is (side == 1)
    assert marker["burst_down"] is (side == -1)
    assert event["protocol"] == "spike-burst-v128-monitor-v1"
    assert event["kind"] == "spike_burst_v128"
    assert event["strategy_version"] == "spike-v12.8-monitor-20260923-v1"
    assert event["source_sha256"] == v128_signals.V128_SOURCE_SHA256
    assert result.event_performance[event["bar_close_ms"]] is event["performance"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("higher", [None, hourly(count=59)])
def test_missing_or_insufficient_h1_history_fails_closed(monkeypatch, higher):
    install_raw_signals(monkeypatch, {130: 1})
    result = v128_signals.analyze(candles(), higher, "15m", tick=.001, base_asset="ETH")

    assert result["events"] == []
    assert result["chart"][130]["v9_admitted"] is True
    assert result["chart"][130]["v128_admitted"] is False


def test_h1_gap_in_the_required_sixty_bar_window_fails_closed(monkeypatch):
    install_raw_signals(monkeypatch, {130: 1})
    h1 = hourly()
    del h1[131]  # The required prior H1 bar closing at the chart-hour boundary is absent.
    result = v128_signals.analyze(candles(), h1, "15m", tick=.001, base_asset="ETH")

    assert result["events"] == []
    assert result["chart"][130]["h1_sma60_ready"] is False
    assert result["chart"][130]["v128_admitted"] is False


def test_only_h1_candles_closed_by_chart_open_affect_the_sma(monkeypatch):
    install_raw_signals(monkeypatch, {130: 1})
    chart = candles()
    original_h1 = hourly()
    mutated_h1 = deepcopy(original_h1)
    # H1 history starts 100 hours before the chart. Bar 132 is still developing
    # at chart open 32h30m, and bar 180 is later than the signal.
    mutated_h1[132].update(o=100.0, h=1_000_000.0, l=99.0, c=1_000_000.0)
    mutated_h1[180].update(o=100.0, h=1_000_000.0, l=99.0, c=1_000_000.0)

    original = v128_signals.analyze(chart, original_h1, "15m", tick=.001, base_asset="ETH")
    mutated = v128_signals.analyze(chart, mutated_h1, "15m", tick=.001, base_asset="ETH")

    assert original["chart"][130]["h1_sma60"] == pytest.approx(100.0)
    assert mutated["chart"][130]["h1_sma60"] == pytest.approx(100.0)
    assert original["events"] == mutated["events"]


def test_future_h1_gap_and_invalid_bar_do_not_rewrite_a_prefix(monkeypatch):
    install_raw_signals(monkeypatch, {130: 1})
    chart = candles(count=131)
    full_h1 = hourly()
    # Rows after the 60-bar event window contain both a missing slot and an
    # invalid candle. Neither can affect a value already knowable at chart open.
    prefix = full_h1[:132]
    extended = deepcopy(full_h1[:142])
    del extended[135]
    extended[137].update(h=1.0, l=2.0)

    before = v128_signals.analyze(chart, prefix, "15m", tick=.001, base_asset="ETH")
    after = v128_signals.analyze(chart, extended, "15m", tick=.001, base_asset="ETH")

    assert before["events"] == after["events"]
    assert before["chart"] == after["chart"]


def test_rejected_raw_reversal_still_closes_existing_reference(monkeypatch):
    install_raw_signals(monkeypatch, {130: 1, 135: -1})
    chart = candles()
    # The short is raw V6 evidence but fails the strict short-side H1 gate.
    result = v128_signals.analyze(chart, hourly(close=112.0), "15m", tick=.001, base_asset="ETH")

    assert [event["side"] for event in result["events"]] == ["long"]
    assert result["chart"][135]["v9_admitted"] is True
    assert result["chart"][135]["v128_admitted"] is False
    assert result["chart"][135]["h1_sma60_direction_allowed"] is False
    event = result["events"][0]
    assert event["performance"]["status"] in {"profit", "loss", "breakeven"}
    assert event["performance"]["exit_time_ms"] is not None
    assert event["performance"]["exit_reason"]


@pytest.mark.parametrize("timeframe,step", [("30m", 1_800_000), ("1H", H1_MS), ("4H", 14_400_000)])
def test_other_timeframes_keep_v9_admissions_without_h1_gate(monkeypatch, timeframe, step):
    install_raw_signals(monkeypatch, {130: 1})
    chart = candles(step=step)
    result = v128_signals.analyze(chart, None, timeframe, tick=.001, base_asset="ETH")

    assert len(result["events"]) == 1
    assert result["events"][0]["v128_admitted"] is True
    assert result["chart"][130]["h1_sma60_applies"] is False
    assert result["chart"][130]["v128_admitted"] == result["chart"][130]["v9_admitted"]

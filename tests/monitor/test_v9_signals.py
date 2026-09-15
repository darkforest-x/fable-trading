"""V9 supplied-candle adapter boundaries and causal serialization."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import v9_signals


def candles(count=200, *, start="2025-01-04T00:00:00Z", step=3_600_000):
    origin = int(pd.Timestamp(start).timestamp() * 1000)
    return [
        {"t": origin + index * step, "o": 100. + index * .1,
         "h": 101. + index * .1, "l": 99. + index * .1,
         "c": 100. + index * .1, "v": 10.}
        for index in range(count)
    ]


def install_raw_signal(monkeypatch, *, index=130, side=1, rv=1.):
    """Keep the production feature pipeline while making one V6/V7 fixture."""
    original_features = v9_signals.features

    def prepared(frame):
        result = original_features(frame)
        result["rv"] = rv
        return result

    def raw(frame, minutes):
        del minutes
        result = pd.DataFrame({"long_signal": False, "short_signal": False}, index=frame.index)
        result.iloc[index, result.columns.get_loc("long_signal" if side == 1 else "short_signal")] = True
        return result

    def bb(frame, *, data_gap):
        del data_gap
        return pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=frame.index)

    monkeypatch.setattr(v9_signals, "features", prepared)
    monkeypatch.setattr(v9_signals, "v6_signals", raw)
    monkeypatch.setattr(v9_signals, "v7_diagnostics", bb)


def test_pipeline_uses_v9_admission_and_confirmation_reference_not_a_fill(monkeypatch):
    install_raw_signal(monkeypatch, side=1)
    supplied = candles()
    result = v9_signals.analyze(supplied, [], "1H", tick=.01, base_asset="ETH")
    frame = v9_signals._validate_frame(supplied, "1H")
    _, expected = v9_signals._decision_frame(frame, "1H", tick=.01, base_asset="ETH")
    event = result["events"][0]
    gate = expected.iloc[130]
    assert bool(gate.v9) and gate.risk_status == "known"
    assert event["price"] == gate.reference_price
    assert event["initial_stop"] == gate.reference_initial_stop
    assert event["risk"] == gate.reference_initial_risk
    assert event["base_asset"] == "ETH"
    assert event["reference_cost_r"] == gate.reference_cost_r
    assert event["entry_reference"] == "confirmation_close_reference_not_fill"
    assert event["is_trade"] is False and event["performance"] == "not_tracked"
    assert event["v9_evidence"]["v9_reason"] == "passed"
    # V7 readiness is an indicator-history property, not an event flag.
    assert result["chart"][129]["ready"] is True
    assert result["chart"][129]["burst"] is False
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("base_asset,rv,expected", [
    ("ETH", 50., True), ("ETH", 50.001, False), ("USDC", 1., False), (None, 1., False),
])
def test_base_and_volume_gates_fail_closed_in_adapter(monkeypatch, base_asset, rv, expected):
    install_raw_signal(monkeypatch, rv=rv)
    result = v9_signals.analyze(candles(), [], "1H", tick=.01, base_asset=base_asset)
    assert bool(result["events"]) is expected
    evidence = result["state"]["v9_evidence"]
    assert evidence["base_asset"] == (base_asset.upper() if isinstance(base_asset, str) else None)


def test_utc_sunday_gate_uses_confirmation_close(monkeypatch):
    # Tuesday 00:00 plus 119 hours is Saturday 23:00, after the six-MA warmup.
    install_raw_signal(monkeypatch, index=119)
    supplied = candles(start="2024-12-31T00:00:00Z")
    result = v9_signals.analyze(supplied, [], "1H", tick=.01, base_asset="ETH")
    assert result["events"] == []
    gate = result["state"]["v9_evidence"]
    assert gate["scheduled_open_utc"] is not None
    # The actual Saturday 23:00 confirmation closes at Sunday 00:00 UTC.
    frame = v9_signals._validate_frame(supplied, "1H")
    _, evidence = v9_signals._decision_frame(frame, "1H", tick=.01, base_asset="ETH")
    assert evidence.iloc[119].v9_reason == "scheduled_open_utc_sunday"


def test_short_signal_is_symmetric_and_chart_limit_does_not_change_events(monkeypatch):
    install_raw_signal(monkeypatch, side=-1, index=130)
    supplied = candles(start="2025-01-06T00:00:00Z")
    full = v9_signals.analyze(supplied, [], "1H", tick=.01, base_asset="ETH")
    limited = v9_signals.analyze(supplied, [], "1H", tick=.01, base_asset="ETH", chart_limit=12)
    assert full["events"][0]["side"] == "short"
    assert limited["events"] == full["events"]
    assert limited["state"] == full["state"]
    assert len(limited["chart"]) == 12


def test_prefix_is_unchanged_by_future_mutation_and_v7_warmup_stays_loading():
    supplied = candles(750, start="2025-01-06T00:00:00Z")
    for index, row in enumerate(supplied):
        close = 100 + 2 * np.sin(index / 8) + .4 * np.sin(index / 2)
        row.update(o=close, h=close + 1, l=close - 1, c=close)
    original = v9_signals.analyze(supplied, [], "1H", tick=.01, base_asset="ETH")
    altered_candles = deepcopy(supplied)
    for row in altered_candles[700:]:
        row.update(o=900., h=1001., l=1., c=1000., v=9000.)
    altered = v9_signals.analyze(altered_candles, [], "1H", tick=.01, base_asset="ETH")
    prefix = v9_signals.analyze(supplied[:700], [], "1H", tick=.01, base_asset="ETH")
    assert original["chart"][:700] == altered["chart"][:700] == prefix["chart"]
    cutoff = supplied[700]["t"]
    assert [event for event in original["events"] if event["bar_open_ms"] < cutoff] == [event for event in prefix["events"]]
    assert prefix["state"]["ready"] is False
    assert original["protocol"]["source_sha256"] == v9_signals.V9_SOURCE_SHA256

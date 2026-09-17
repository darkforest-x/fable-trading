"""V9 card projection: same engine as the backtest, no borrowed or invented R."""
import pytest

from yoyo.monitor import v9_performance, v9_signals
from test_v9_signals import candles, install_raw_signal


def _decision(monkeypatch, *, index=130, side=1, count=200, tick=.01, base_asset="ETH"):
    install_raw_signal(monkeypatch, index=index, side=side)
    frame = v9_signals._validate_frame(candles(count), "1H")
    built, evidence = v9_signals._decision_frame(frame, "1H", tick=tick, base_asset=base_asset)
    return built, evidence


def test_projection_is_keyed_by_signal_close_and_marks_the_open_position(monkeypatch):
    built, evidence = _decision(monkeypatch)
    projections = v9_performance.project(built, evidence, minutes=60, tick=.01,
                                         data_gap=built.attrs["data_gap"])
    close_ms = int(built.index[130].value // 1_000_000) + 3_600_000
    assert set(projections) == {close_ms}
    card = projections[close_ms]
    # The synthetic series never stops out, so the card must be a mark, not an exit.
    assert card["status"] == "active"
    assert card["exit_r"] is None and card["exit_price"] is None and card["exit_time_ms"] is None
    assert card["mark"] == "last_closed_bar_close"
    assert card["entry_time_ms"] == int(built.index[131].value // 1_000_000)
    assert card["basis"] == v9_performance.BASIS


def test_reported_r_is_net_of_the_same_round_trip_cost_the_backtest_uses(monkeypatch):
    built, evidence = _decision(monkeypatch)
    card = next(iter(v9_performance.project(built, evidence, minutes=60, tick=.01,
                                            data_gap=built.attrs["data_gap"]).values()))
    assert card["round_trip_cost"] == 0.002
    entry, risk = card["entry_price"], card["initial_risk"]
    expected = (built.close.iloc[-1] - entry) / risk - 0.002 / (risk / entry)
    assert card["net_r"] == pytest.approx(expected, rel=0, abs=1e-9)
    assert card["current_r"] == card["net_r"] and card["gross_r"] > card["net_r"]


def test_entry_is_the_next_open_and_never_the_confirmation_close(monkeypatch):
    built, evidence = _decision(monkeypatch)
    card = next(iter(v9_performance.project(built, evidence, minutes=60, tick=.01,
                                            data_gap=built.attrs["data_gap"]).values()))
    assert card["entry_price"] == float(built.open.iloc[131])
    assert card["entry_price"] != float(built.close.iloc[130])


def test_a_later_bar_cannot_change_an_earlier_projection(monkeypatch):
    """Causality: extending the supplied prefix must not rewrite settled fields."""
    built, evidence = _decision(monkeypatch, count=180)
    short = v9_performance.project(built, evidence, minutes=60, tick=.01,
                                   data_gap=built.attrs["data_gap"])
    built_long, evidence_long = _decision(monkeypatch, count=200)
    long = v9_performance.project(built_long, evidence_long, minutes=60, tick=.01,
                                  data_gap=built_long.attrs["data_gap"])
    key = next(iter(short))
    assert key in long
    for field in ("entry_price", "entry_time_ms", "initial_stop", "initial_risk"):
        assert short[key][field] == long[key][field], field
    # Only the running mark may move with new closed bars.
    assert long[key]["bars_held"] > short[key]["bars_held"]


def test_an_admitted_signal_the_serial_engine_never_opened_reports_unknown(monkeypatch):
    """A second same-side signal inside an open position must not borrow its R."""
    install_raw_signal(monkeypatch, index=130, side=1)
    original = v9_signals.v6_signals

    def two(frame, minutes):
        result = original(frame, minutes)
        result.iloc[130, result.columns.get_loc("long_signal")] = True
        result.iloc[150, result.columns.get_loc("long_signal")] = True
        return result

    monkeypatch.setattr(v9_signals, "v6_signals", two)
    frame = v9_signals._validate_frame(candles(200), "1H")
    built, evidence = v9_signals._decision_frame(frame, "1H", tick=.01, base_asset="ETH")
    projections = v9_performance.project(built, evidence, minutes=60, tick=.01,
                                         data_gap=built.attrs["data_gap"])
    step = 3_600_000
    first = projections[int(built.index[130].value // 1_000_000) + step]
    second = projections[int(built.index[150].value // 1_000_000) + step]
    assert first["status"] == "active"
    assert second["status"] == "unknown" and second["reason"] == "serial_position_already_open"
    assert second["current_r"] is None and second["peak_r"] is None


def test_the_event_payload_carries_the_projection_the_ledger_can_read(monkeypatch):
    """signal_analytics.performance only accepts this exact shape."""
    from yoyo.monitor.signal_analytics import performance

    install_raw_signal(monkeypatch, index=130, side=1)
    result = v9_signals.analyze(candles(200), [], "1H", tick=.01, base_asset="ETH")
    event = result["events"][0]
    status, value = performance(event)
    assert status == "active" and value == event["performance"]["current_r"]
    assert result.event_performance[event["bar_close_ms"]] is event["performance"]


def test_rejected_inputs_are_refused_rather_than_silently_projected(monkeypatch):
    built, evidence = _decision(monkeypatch)
    gap = built.attrs["data_gap"]
    with pytest.raises(ValueError):
        v9_performance.project(built, evidence, minutes=0, tick=.01, data_gap=gap)
    with pytest.raises(ValueError):
        v9_performance.project(built, evidence, minutes=60, tick=0., data_gap=gap)
    with pytest.raises(ValueError):
        v9_performance.project(built, evidence.iloc[:-1], minutes=60, tick=.01, data_gap=gap)
    assert v9_performance.project(built.iloc[:0], evidence.iloc[:0], minutes=60, tick=.01,
                                  data_gap=gap.iloc[:0]) == {}

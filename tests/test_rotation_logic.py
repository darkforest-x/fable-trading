"""Synthetic causality, lifecycle and fail-closed tests; no market data reads."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.data.rotation_features import closed_prefix, daily_metrics, market_regime
from yoyo.layers.l1_detection.rotation_setups import detect_setup
from yoyo.layers.l2_judgment.rotation_judgment import rank_candidates


def candles(n=80, interval="1d"):
    frequency = {"1d": "1D", "4h": "4h", "15m": "15min"}[interval]
    return pd.DataFrame(
        {"open": 100., "high": 101., "low": 99., "close": 100.,
         "volume": 100., "quote_volume": 10000., "confirmed": True},
        index=pd.date_range("2024-01-01", periods=n, freq=frequency, tz="UTC"),
    )


def closed_at(bars, interval="1d", position=-1):
    delta = {"1d": pd.Timedelta(days=1), "4h": pd.Timedelta(hours=4),
             "15m": pd.Timedelta(minutes=15)}[interval]
    return bars.index[position] + delta


def set_candle(bars, position, values):
    bars.loc[bars.index[position], ["open", "high", "low", "close", "volume"]] = values


def breakout_frame(n=49, interval="4h"):
    bars = candles(n, interval)
    set_candle(bars, 44, [100, 103, 99.5, 102, 250])
    for position in range(45, n):
        set_candle(bars, position, [103, 104, 102.5, 103.5, 110])
    return bars


def metric(ret30=0.1, *, ret7=0.03, above=True, state="ok", date="2024-03-01T00:00:00+00:00"):
    return {"state": state, "return_30d": ret30, "return_7d": ret7,
            "above_sma20": above, "above_sma50": above, "last_bar_at": date}


def supportive_regime():
    return market_regime({"BTCUSDT": metric(), "ETHUSDT": metric(.2), "ALTUSDT": metric(.3)})


def candidate(symbol="ALTUSDT", **changes):
    result = {"symbol": symbol, "sector": "owner_supplied", "daily": metric(.3),
              "setup": {"state": "breakout", "reasons": []},
              "trigger": {"state": "watch", "reasons": []},
              "event_risk": "clear", "liquidity_eligible": True}
    result.update(changes)
    return result


def test_daily_windows_use_elapsed_days_and_rebase_only_the_available_tail():
    bars = candles(85)
    for column in ("open", "high", "low", "close"):
        bars[column] = np.arange(100., 185.)
    bars.quote_volume = np.arange(85.)
    result = daily_metrics(bars, as_of=closed_at(bars, position=70))
    assert result["state"] == "ok"
    assert result["return_7d"] == pytest.approx(170 / 163 - 1)
    assert result["return_30d"] == pytest.approx(170 / 140 - 1)
    assert result["sma20"] == np.arange(151., 171.).mean()
    assert result["sma50"] == np.arange(121., 171.).mean()
    assert result["median_quote_volume_30d"] == np.median(np.arange(41., 71.))
    assert result["last_bar_at"] == closed_at(bars, position=70).isoformat()
    assert len(result["sparkline"]) == 60
    assert result["sparkline"][0]["value"] == 100
    assert result["sparkline"][-1]["value"] == pytest.approx(170 / 111 * 100)


@pytest.mark.parametrize("count,expected", [(0, "insufficient_data"), (50, "insufficient_data"), (51, "ok")])
def test_daily_requires_51_completed_candles(count, expected):
    bars = candles(60)
    result = daily_metrics(bars, as_of=bars.index[count])
    assert result["state"] == expected
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("interval", ["1d", "4h", "15m"])
def test_future_mutation_including_malformed_prices_cannot_change_available_snapshot(interval):
    bars = candles(85, interval) if interval == "1d" else breakout_frame(85, interval)
    as_of = closed_at(bars, interval, 65 if interval == "1d" else 46)
    function = daily_metrics if interval == "1d" else lambda data, **kwargs: detect_setup(data, interval=interval, **kwargs)
    expected = function(bars, as_of=as_of)
    future = bars.index >= as_of
    altered = bars.copy()
    altered.loc[future, "open"] = np.nan
    altered.loc[future, "high"] = -100
    altered.loc[future, "volume"] = np.inf
    altered.loc[future, "confirmed"] = False
    assert function(altered, as_of=as_of) == expected
    assert function(bars.loc[~future], as_of=as_of) == expected


@pytest.mark.parametrize("interval", ["1d", "4h", "15m"])
def test_unfinished_and_unconfirmed_tail_is_excluded_before_validation(interval):
    bars = candles(65, interval)
    bars.iloc[-1, bars.columns.get_loc("confirmed")] = False
    bars.iloc[-1, bars.columns.get_loc("close")] = np.nan
    just_before_close = closed_at(bars, interval) - pd.Timedelta(microseconds=1)
    prefix = closed_prefix(bars, as_of=just_before_close, interval=interval)
    assert len(prefix) == len(bars) - 1
    with pytest.raises(ValueError, match="unconfirmed"):
        closed_prefix(bars, as_of=closed_at(bars, interval), interval=interval)


@pytest.mark.parametrize("bad", ["gap", "reverse", "duplicate", "off_grid", "naive", "timezone", "nan_price",
                                  "nan_volume", "nan_quote", "negative_volume", "negative_quote", "invalid_geometry",
                                  "missing_flag", "unconfirmed", "missing_column"])
def test_malformed_available_data_fails_closed(bad):
    bars = candles()
    as_of = closed_at(bars)
    if bad == "gap": bars = bars.drop(bars.index[25])
    elif bad == "reverse": bars = bars.iloc[::-1]
    elif bad == "duplicate": bars = pd.concat([bars, bars.iloc[[-1]]])
    elif bad == "off_grid": bars.index += pd.Timedelta(minutes=1)
    elif bad == "naive": bars.index = bars.index.tz_localize(None)
    elif bad == "timezone": bars.index = bars.index.tz_convert("Asia/Shanghai")
    elif bad == "nan_price": bars.iloc[25, 0] = np.nan
    elif bad == "nan_volume": bars.iloc[25, 4] = np.nan
    elif bad == "nan_quote": bars.iloc[25, 5] = np.nan
    elif bad == "negative_volume": bars.iloc[25, 4] = -1
    elif bad == "negative_quote": bars.iloc[25, 5] = -1
    elif bad == "invalid_geometry": bars.iloc[25, 1] = 1
    elif bad == "missing_flag": bars = bars.drop(columns="confirmed")
    elif bad == "unconfirmed": bars.iloc[25, 6] = False
    elif bad == "missing_column": bars = bars.drop(columns="quote_volume")
    with pytest.raises(ValueError):
        daily_metrics(bars, as_of=as_of)


def test_okx_confirmation_string_is_explicit_not_python_truthiness():
    bars = candles().drop(columns="confirmed")
    bars["confirm"] = "1"
    assert daily_metrics(bars, as_of=closed_at(bars))["state"] == "ok"
    bars.loc[bars.index[0], "confirm"] = "0"
    with pytest.raises(ValueError, match="unconfirmed"):
        daily_metrics(bars, as_of=closed_at(bars))


def test_zero_volume_is_missing_confirmation_not_infinity():
    bars = breakout_frame()
    bars.volume = 0.
    bars.quote_volume = 0.
    result = detect_setup(bars, as_of=closed_at(bars, "4h"))
    assert result["relative_volume"] is None
    assert result["breakout_at"] is None
    assert result["state"] == "watch"
    json.dumps(result, allow_nan=False)
    daily = candles()
    daily.quote_volume = 0.
    assert daily_metrics(daily, as_of=closed_at(daily))["median_quote_volume_30d"] == 0


def test_breakout_range_excludes_breakout_candle_and_freezes_afterward():
    bars = breakout_frame()
    at_breakout = detect_setup(bars, as_of=closed_at(bars, "4h", 44))
    assert at_breakout["state"] == "breakout"
    assert at_breakout["range_high"] == 101
    assert at_breakout["range_low"] == 99
    assert at_breakout["relative_volume"] == 2.5
    assert at_breakout["breakout_at"] == at_breakout["decision_at"]
    assert at_breakout["first_pullback_at"] is None
    later = detect_setup(bars, as_of=closed_at(bars, "4h"))
    assert later["range_high"] == at_breakout["range_high"]
    assert later["range_low"] == at_breakout["range_low"]
    assert later["breakout_at"] == at_breakout["breakout_at"]


def test_changed_prior_boundary_rejects_the_previously_valid_breakout():
    bars = breakout_frame(45)
    bars.loc[bars.index[34], "high"] = 110
    result = detect_setup(bars, as_of=closed_at(bars, "4h"))
    assert result["state"] == "watch"
    assert result["range_high"] == 110
    assert result["breakout_at"] is None


def test_fake_wick_and_unconfirmed_volume_do_not_break_out():
    bars = breakout_frame(45)
    bars.loc[bars.index[44], ["high", "close"]] = [130, 100]
    result = detect_setup(bars, as_of=closed_at(bars, "4h"))
    assert result["state"] == "watch"
    assert result["breakout_at"] is None
    bars.loc[bars.index[44], ["close", "volume"]] = [102, 199]
    assert detect_setup(bars, as_of=closed_at(bars, "4h"))["state"] == "watch"


def test_first_pullback_is_later_than_breakout_and_is_not_repeated():
    bars = breakout_frame(49)
    set_candle(bars, 46, [103, 103.5, 100.5, 102, 90])
    set_candle(bars, 47, [102, 103.5, 100.5, 102, 80])
    assert detect_setup(bars, as_of=closed_at(bars, "4h", 44))["state"] == "breakout"
    first = detect_setup(bars, as_of=closed_at(bars, "4h", 46))
    assert first["state"] == "pullback"
    assert first["first_pullback_at"] == first["decision_at"]
    assert first["first_pullback_at"] > first["breakout_at"]
    later = detect_setup(bars, as_of=closed_at(bars, "4h", 47))
    assert later["state"] == "watch"
    assert later["first_pullback_at"] == first["first_pullback_at"]


def test_retest_without_a_preceding_volume_breakout_is_only_watch():
    bars = breakout_frame(47)
    bars.loc[bars.index[44], "volume"] = 100
    set_candle(bars, 46, [103, 103.5, 100.5, 102, 90])
    result = detect_setup(bars, as_of=closed_at(bars, "4h"))
    assert result["state"] == "watch"
    assert result["first_pullback_at"] is None


def test_structural_low_wick_invalidates_even_when_close_recovers():
    bars = breakout_frame(47)
    set_candle(bars, 46, [103, 104, 98, 103.5, 90])
    result = detect_setup(bars, as_of=closed_at(bars, "4h"))
    assert result["state"] == "invalidated"
    assert result["stop_reference"] == 99
    assert result["first_pullback_at"] is None


def test_extended_retest_does_not_consume_first_eligible_pullback():
    bars = breakout_frame(48)
    set_candle(bars, 46, [103, 111, 101, 110, 90])
    set_candle(bars, 47, [104, 105, 101, 102, 80])
    extended = detect_setup(bars, as_of=closed_at(bars, "4h", 46))
    assert extended["state"] == "extended"
    assert extended["first_pullback_at"] is None
    assert detect_setup(bars, as_of=closed_at(bars, "4h", 47))["state"] == "pullback"


def test_episode_has_finite_lifetime_and_does_not_reuse_an_ancient_breakout():
    bars = breakout_frame(59)
    within = detect_setup(bars, as_of=closed_at(bars, "4h", 56))
    expired = detect_setup(bars, as_of=closed_at(bars, "4h", 57))
    assert within["breakout_at"] is not None
    assert expired["breakout_at"] is None
    assert expired["state"] == "watch"


def test_declustering_requires_full_finite_dependency_coverage():
    bars = breakout_frame(60)
    cutoff = closed_at(bars, "4h")
    for length in (21, 32, 44):
        result = detect_setup(bars.iloc[-length:], as_of=cutoff)
        assert result["state"] == "insufficient_data"
        assert result["required_completed_candles"] == 45
    assert detect_setup(bars.iloc[-45:], as_of=cutoff)["state"] != "insufficient_data"
    assert detect_setup(bars, as_of=cutoff, lookback=30)["required_completed_candles"] == 55


def _continuously_breaking_trend(n=250):
    bars = candles(n, "4h")
    close = 100 * 1.01 ** np.arange(n)
    bars.open = .995 * close
    bars.high = 1.002 * close
    bars.low = .994 * close
    bars.close = close
    bars.volume = 100 * 1.08 ** np.arange(n)
    bars.quote_volume = bars.volume * bars.close
    return bars


def test_continuous_raw_breakouts_do_not_reset_episode_phase_at_api_window_start():
    bars = _continuously_breaking_trend()
    cutoff = closed_at(bars, "4h")
    full = detect_setup(bars, as_of=cutoff)
    for length in (45, 46, 59, 98, 99, 100, 101, 175):
        assert detect_setup(bars.iloc[-length:], as_of=cutoff) == full
    assert full["state"] == "watch"
    assert full["breakout_at"] is None
    assert "raw_breakout_cluster_needs_12_quiet_candles_before_a_new_anchor" in full["reasons"]


def test_rolling_hundred_candle_requests_never_move_a_false_breakout_forward():
    bars = _continuously_breaking_trend()
    for tip in range(120, 151):
        cutoff = closed_at(bars, "4h", tip)
        rolling = detect_setup(bars.iloc[tip - 99:tip + 1], as_of=cutoff)
        full = detect_setup(bars, as_of=cutoff)
        assert rolling == full
        assert rolling["breakout_at"] is None


def test_genuine_anchor_range_and_first_retest_are_invariant_to_available_origin():
    bars = candles(180, "4h")
    set_candle(bars, 150, [100, 103, 99.5, 102, 250])
    for index in range(151, 163):
        set_candle(bars, index, [103, 104, 102.5, 103.5, 110])
    set_candle(bars, 153, [103, 103.5, 100.5, 102, 90])
    expected_anchor = closed_at(bars, "4h", 150).isoformat()
    expected_pullback = closed_at(bars, "4h", 153).isoformat()
    for tip in (150, 153, 154, 162, 163):
        cutoff = closed_at(bars, "4h", tip)
        full = detect_setup(bars, as_of=cutoff)
        for length in (45, 60, 99, 100, 130):
            available = bars.iloc[tip - length + 1:tip + 1]
            assert detect_setup(available, as_of=cutoff) == full
        if tip <= 162:
            assert full["breakout_at"] == expected_anchor
            assert full["range_high"] == 101 and full["range_low"] == 99
            if tip >= 153:
                assert full["first_pullback_at"] == expected_pullback
        else:
            assert full["breakout_at"] is None


def test_invalidation_cannot_be_erased_by_a_later_recovery_within_same_anchor():
    bars = breakout_frame(50)
    set_candle(bars, 46, [103, 104, 98, 103.5, 90])
    later = detect_setup(bars, as_of=closed_at(bars, "4h", 49))
    assert later["state"] == "invalidated"
    assert later["invalidated_at"] == closed_at(bars, "4h", 46).isoformat()
    assert later["first_pullback_at"] is None


@pytest.mark.parametrize("interval", ["4h", "15m"])
def test_price_rescaling_does_not_change_dimensionless_setup(interval):
    bars = breakout_frame(47, interval)
    set_candle(bars, 46, [103, 103.5, 100.5, 102, 90])
    scaled = bars.copy()
    for column in ("open", "high", "low", "close"):
        scaled[column] *= 10
    before = detect_setup(bars, as_of=closed_at(bars, interval), interval=interval)
    after = detect_setup(scaled, as_of=closed_at(bars, interval), interval=interval)
    for field in ("state", "relative_volume", "breakout_at", "first_pullback_at"):
        assert before[field] == after[field]
    assert after["range_high"] == before["range_high"] * 10


@pytest.mark.parametrize("missing", ["BTCUSDT", "ETHUSDT"])
def test_missing_benchmark_is_unknown(missing):
    metrics = {"BTCUSDT": metric(), "ETHUSDT": metric(.2), "ALTUSDT": metric(.3)}
    del metrics[missing]
    result = market_regime(metrics)
    assert result["state"] == "unknown"
    assert any(missing in reason for reason in result["reasons"])


def test_breadth_denominator_excludes_benchmarks_insufficient_and_stale_symbols():
    metrics = {"BTCUSDT": metric(), "ETHUSDT": metric(.2),
               "AUSDT": metric(), "BUSDT": metric(above=False),
               "CUSDT": metric(state="insufficient_data"), "DUSDT": metric(date="2024-02-01")}
    result = market_regime(metrics)
    assert result["coverage_count"] == 2
    assert result["breadth_above_sma20"] == .5
    assert result["eth_btc_return_30d"] == pytest.approx(1.2 / 1.1 - 1)
    assert result["state"] == "mixed"


def test_empty_breadth_and_mismatched_benchmarks_are_unknown():
    assert market_regime({"BTCUSDT": metric(), "ETHUSDT": metric(.2)})["state"] == "unknown"
    assert market_regime({"BTCUSDT": metric(), "ETHUSDT": metric(.2, date="2024-02-01"),
                          "AUSDT": metric()})["state"] == "unknown"


def test_supportive_and_defensive_are_descriptive_rules():
    assert supportive_regime()["state"] == "supportive"
    assert market_regime({"BTCUSDT": metric(-.1, above=False), "ETHUSDT": metric(-.2),
                          "AUSDT": metric()})["state"] == "defensive"


def test_rank_ties_are_symbol_stable_all_candidates_retained_and_inputs_untouched():
    inputs = [candidate("BUSDT"), candidate("AUSDT")]
    original = deepcopy(inputs)
    ranked = rank_candidates(inputs, regime=supportive_regime())
    assert [item["symbol"] for item in ranked] == ["AUSDT", "BUSDT"]
    assert [item["rank"] for item in ranked] == [1, 2]
    assert ranked[0]["rs_30d"] == pytest.approx(1.3 / 1.1 - 1)
    assert all(0 <= item["score"] <= 1 for item in ranked)
    assert all(item["review_status"] == "ready" and item["status"] == "breakout" for item in ranked)
    assert inputs == original
    json.dumps(ranked, allow_nan=False)


@pytest.mark.parametrize("change", [
    {"event_risk": "block"}, {"liquidity_eligible": False},
    {"daily": metric(state="insufficient_data")},
    {"setup": {"state": "invalidated"}}, {"setup": {"state": "insufficient_data"}},
    {"daily": metric(date="2024-02-01")},
])
def test_blockers_never_become_eligible_observation_candidates(change):
    ranked = rank_candidates([candidate("BLOCKUSDT", **change), candidate("GOODUSDT")], regime=supportive_regime())
    assert len(ranked) == 2
    assert ranked[-1]["symbol"] == "BLOCKUSDT"
    assert ranked[-1]["status"] == "avoid"
    assert ranked[-1]["review_status"] == "blocked"


def test_unknown_events_keep_setup_visible_but_require_review():
    ranked = rank_candidates([candidate(event_risk="unknown", sector="unknown")], regime=supportive_regime())
    assert ranked[0]["status"] == "breakout"
    assert ranked[0]["review_status"] == "review"
    assert "sector_metadata_unknown_not_invented" in ranked[0]["reasons"]


def test_defensive_market_blocks_and_missing_benchmark_never_claims_ready():
    regime = supportive_regime()
    regime["state"] = "defensive"
    assert rank_candidates([candidate()], regime=regime)[0]["review_status"] == "blocked"
    unknown = rank_candidates([candidate()], regime=market_regime({}))[0]
    assert unknown["review_status"] == "review"
    assert unknown["rs_7d"] is None and unknown["rs_30d"] is None


@pytest.mark.parametrize("trigger,reason", [
    (None, "trigger_15m_missing"), ({}, "trigger_15m_missing"),
    ({"state": "insufficient_data"}, "trigger_15m_insufficient_data"),
    ({"state": "invalidated"}, "trigger_15m_invalidated"),
    ({"state": "unexpected_schema_state"}, "trigger_15m_unknown_state"),
    ({"state": []}, "trigger_15m_unknown_state"),
])
@pytest.mark.parametrize("setup_state", ["breakout", "pullback"])
def test_unavailable_or_invalidated_15m_requires_review_without_changing_4h_score(trigger, reason, setup_state):
    good = candidate(setup={"state": setup_state})
    baseline = rank_candidates([good], regime=supportive_regime())[0]
    bad = candidate(setup={"state": setup_state}, trigger=trigger)
    actual = rank_candidates([bad], regime=supportive_regime())[0]
    assert baseline["review_status"] == "ready"
    assert actual["review_status"] == "review"
    assert actual["status"] == baseline["status"] == setup_state
    assert actual["score"] == baseline["score"]
    assert actual["setup"] == baseline["setup"]
    assert reason in actual["reasons"]


def test_absent_15m_trigger_key_requires_review_and_preserves_candidate():
    item = candidate()
    del item["trigger"]
    ranked = rank_candidates([item], regime=supportive_regime())
    assert len(ranked) == 1
    assert ranked[0]["status"] == "breakout"
    assert ranked[0]["review_status"] == "review"
    assert "trigger_15m_missing" in ranked[0]["reasons"]


@pytest.mark.parametrize("trigger_state", ["watch", "breakout", "pullback", "extended"])
def test_15m_auxiliary_data_need_not_match_the_4h_breakout(trigger_state):
    result = rank_candidates([candidate(trigger={"state": trigger_state})], regime=supportive_regime())[0]
    assert result["status"] == "breakout"
    assert result["review_status"] == "ready"
    assert not any(reason.startswith("trigger_15m_") for reason in result["reasons"])


def test_15m_review_reason_does_not_weaken_an_existing_hard_blocker():
    result = rank_candidates([candidate(trigger={"state": "invalidated"}, event_risk="block")],
                             regime=supportive_regime())[0]
    assert result["status"] == "avoid"
    assert result["review_status"] == "blocked"
    assert "trigger_15m_invalidated" in result["reasons"]
    assert "blocking_event_risk" in result["reasons"]

"""Synthetic ex-BTC score, exact clocks, support and prefix invariance tests."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse import BAR_COLUMNS
from yoyo.data.hourly_impulse_breadth import (
    BREADTH_COLUMNS, BREADTH_SYMBOLS, TRACE_COLUMNS, add_breadth_context,
)


START = pd.Timestamp("2024-01-01", tz="UTC")
HOUR = pd.Timedelta(hours=1)


def raw(kind="up", n=65):
    if isinstance(kind, str):
        curve = (100 + np.arange(n) if kind == "up" else
                 200 - np.arange(n) if kind == "down" else np.full(n, 100))
    else:
        curve = np.asarray(kind)
        n = len(curve)
    values = np.repeat(curve.astype(float), 12)
    return pd.DataFrame({
        "open_time": pd.date_range(START, periods=n * 12, freq="5min"),
        "open": values, "high": values + 1, "low": values - 1,
        "close": values, "volume": np.ones(n * 12), "segment_id": "ignored",
    })


def assets(kind="up", n=65):
    return {symbol: raw(kind, n) for symbol in BREADTH_SYMBOLS}


def requests(hours=(51,), directions=None):
    return pd.DataFrame({
        "event_id": ["e%d" % i for i in range(len(hours))],
        "signal_time": [START + h * HOUR for h in hours],
        "decision_time": [START + (h + 1) * HOUR for h in hours],
        "direction": directions if directions is not None else [1] * len(hours),
    })


@pytest.mark.parametrize("kind,score", [("up", 1), ("down", -1), ("flat", 1)])
def test_source_score_50_lags_and_ties_bullish(kind, score):
    context, trace = add_breadth_context(requests((50, 51, 52), [1, 1, -1]), assets(kind))
    assert context.breadth_known.tolist() == [False, True, True]
    assert context.breadth_reason.iloc[0] == "insufficient_history"
    assert context.breadth_source_count.tolist() == [0, 4, 4]
    assert pd.isna(context.breadth_available_at.iloc[0])
    assert context.breadth_score.iloc[1] == context.breadth_score.iloc[2] == score
    assert context.breadth_gate_state.tolist() == (["unknown", "accepted", "abstain"] if score == 1
                                                  else ["unknown", "abstain", "accepted"])
    for symbol in BREADTH_SYMBOLS:
        assert context["breadth_%s_score" % symbol].iloc[1] == score * 50
        assert context["breadth_%s_count" % symbol].tolist() == [50, 51, 52]
        assert context["breadth_%s_available_at" % symbol].iloc[1] == START + 51 * HOUR
        assert context["breadth_%s_window_start" % symbol].iloc[1] == START
        part = trace[trace.symbol.eq(symbol)]
        assert part.trscore.iloc[:50].isna().all()
        assert part.window_start.iloc[:50].isna().all()
        assert part.trscore.iloc[50] == score * 50
        assert part.available_at.max() == START + 52 * HOUR


@pytest.mark.parametrize("construction", ["within_asset", "across_assets"])
def test_known_zero_is_neutral_abstain_not_missing(construction):
    if construction == "within_asset":
        basket = assets(np.r_[np.full(25, 90), np.full(25, 110), 100])
    else:
        basket = {s: raw("up" if i < 2 else "down") for i, s in enumerate(BREADTH_SYMBOLS)}
    context, _ = add_breadth_context(requests((51, 51), [1, -1]), basket)
    assert context.breadth_score.eq(0).all()
    assert context.breadth_known.all()
    assert context.breadth_source_count.eq(4).all()
    assert context.breadth_gate_state.eq("abstain").all()
    assert context.breadth_reason.eq("neutral").all()


def test_exact_unrounded_four_asset_mean_not_majority_vote():
    basket = {s: raw(np.r_[np.full(26, 90), np.full(24, 110), 100]) for s in BREADTH_SYMBOLS}
    context, _ = add_breadth_context(requests(), basket)
    assert context.breadth_ETHUSDT_score.iloc[0] == 2
    assert context.breadth_score.iloc[0] == .04


@pytest.mark.parametrize("symbol", BREADTH_SYMBOLS)
@pytest.mark.parametrize("missing", ["hour", "subbar", "warmup", "empty"])
def test_any_single_asset_without_exact_complete_support_is_unknown(symbol, missing):
    basket = assets()
    if missing == "hour":
        basket[symbol] = basket[symbol].drop(index=range(50 * 12, 51 * 12))
    elif missing == "subbar":
        basket[symbol] = basket[symbol].drop(index=50 * 12 + 11)
    elif missing == "warmup":
        basket[symbol] = basket[symbol].iloc[12:]
    else:
        basket[symbol] = basket[symbol].iloc[:0]
    context, _ = add_breadth_context(requests(), basket)
    row = context.iloc[0]
    assert not row.breadth_known and pd.isna(row.breadth_score)
    assert row.breadth_gate_state == "unknown" and row.breadth_source_count == 3
    assert pd.isna(row.breadth_available_at)
    assert row.breadth_reason == ("insufficient_history" if missing == "warmup" else "missing_external_hour")
    assert pd.isna(row["breadth_%s_score" % symbol])
    if missing != "warmup":
        assert row["breadth_%s_count" % symbol] == 0
        assert pd.isna(row["breadth_%s_available_at" % symbol])


def test_gap_resets_whole_51_history_and_never_compares_segment_spaces():
    basket = assets(n=115)
    basket["SOLUSDT"] = basket["SOLUSDT"].drop(index=[52 * 12 + 1, 52 * 12 + 8])
    context, trace = add_breadth_context(requests((52, 53, 54, 103, 104)), basket)
    assert context.breadth_known.tolist() == [True, False, False, False, True]
    assert context.breadth_SOLUSDT_count.tolist() == [52, 0, 1, 50, 51]
    assert context.breadth_SOLUSDT_window_start.iloc[-1] == START + 53 * HOUR
    part = trace[trace.symbol.eq("SOLUSDT")]
    assert part[part.open_time.eq(START + 53 * HOUR)].segment_id.iloc[0] == 1


def test_external_k1_hour_excluded_even_when_complete_and_price_reverses():
    basket = assets(np.r_[100 + np.arange(51), np.full(14, 10)])
    context, trace = add_breadth_context(requests(), basket)
    assert context.breadth_score.iloc[0] == 1
    assert trace.open_time.max() == START + 50 * HOUR
    assert trace.available_at.max() == context.signal_time.iloc[0]
    assert context.breadth_available_at.iloc[0] + HOUR == context.decision_time.iloc[0]


def test_close_or_open_does_not_replace_hl2_formula():
    basket = assets("flat")
    for frame in basket.values():
        frame["high"] = 150
        frame["low"] = 50
        frame["open"] = np.repeat(51 + np.arange(65), 12)
        frame["close"] = np.repeat(149 - np.arange(65), 12)
    context, trace = add_breadth_context(requests(), basket)
    assert trace.hl2.eq(100).all()
    assert context.breadth_score.iloc[0] == 1


def test_native_high_low_use_all_subbars_not_last_subbar_hl2():
    basket = assets("flat")
    for frame in basket.values():
        frame.loc[50 * 12, "high"] = 121
        frame.loc[50 * 12 + 1, "low"] = 89
    context, trace = add_breadth_context(requests(), basket)
    assert trace[trace.open_time.eq(START + 50 * HOUR)].hl2.eq(105).all()
    assert context.breadth_score.iloc[0] == 1


def test_own_controls_different_times_no_parent_gate_copy_and_originals_preserved():
    basket = assets(np.r_[100 + np.arange(51), np.full(14, 10)])
    query = requests((52, 51, 51, 10), [-1, 1, -1, 1])
    query.index = pd.Index([4, 4, 0, 99], name="original")
    query["parent_event_id"] = "shared-parent"
    query["signal_close"] = np.nan  # Own BTC prices are not external features.
    query["net_return"] = np.inf
    query.attrs = {"source": {"ids": "unchanged"}}
    before = query.copy(deep=True)
    source_before = {s: f.copy(deep=True) for s, f in basket.items()}
    out, _ = add_breadth_context(query, basket)
    assert out.breadth_gate_state.tolist() == ["accepted", "accepted", "abstain", "unknown"]
    assert_frame_equal(out[query.columns], query)
    assert_frame_equal(query, before)
    assert out.attrs == query.attrs
    for s in basket:assert_frame_equal(basket[s], source_before[s])


@pytest.mark.parametrize("cutoff", [0, 1, 49, 50, 51, 52, 60])
def test_prefix_and_future_ohlcv_mutations_do_not_change_past_context(cutoff):
    basket = assets()
    query = requests((cutoff,))
    original, trace = add_breadth_context(query, basket)
    shorter = {s: frame.iloc[:cutoff * 12] for s, frame in basket.items()}
    short_context, short_trace = add_breadth_context(query, shorter)
    assert_frame_equal(original, short_context)
    assert_frame_equal(trace, short_trace)
    changed = {s: frame.copy() for s, frame in basket.items()}
    for frame in changed.values():frame.loc[cutoff * 12:, BAR_COLUMNS[1:]] = np.nan
    altered, altered_trace = add_breadth_context(query, changed)
    assert_frame_equal(original, altered)
    assert_frame_equal(trace, altered_trace)
    batch, _ = add_breadth_context(requests((cutoff, 65)), basket)
    assert_frame_equal(original[BREADTH_COLUMNS], batch.iloc[:1][BREADTH_COLUMNS])


def test_trace_and_scores_match_direct_nested_loop_oracle():
    generator = np.random.default_rng(123)
    basket = {s: raw(generator.integers(80, 120, size=80)) for s in BREADTH_SYMBOLS}
    _, trace = add_breadth_context(requests((80,)), basket)
    for _, g in trace.groupby("symbol"):
        h = g.hl2.to_numpy()
        for i in range(50, len(g)):
            expected = sum(1 if h[i] >= h[i - lag] else -1 for lag in range(1, 51))
            assert g.trscore.iloc[i] == expected
            assert g.window_start.iloc[i] == g.open_time.iloc[i - 50]
    assert trace.groupby(["symbol", "open_time"]).size().eq(1).all()
    assert set(trace.columns) == set(TRACE_COLUMNS)


@pytest.mark.parametrize("key", ["signal_time", "decision_time"])
@pytest.mark.parametrize("value", ["2024-01-01", 1704067200000, 1.0, True, pd.NaT, None])
def test_bad_request_clocks_rejected(key, value):
    query = requests();query[key] = [value]
    with pytest.raises(ValueError):add_breadth_context(query, assets())


@pytest.mark.parametrize("value", [0, True, np.bool_(True), "1", 1+0j, np.nan, np.inf])
def test_bad_direction_rejected(value):
    query = requests();query.direction = [value]
    with pytest.raises(ValueError):add_breadth_context(query, assets())


@pytest.mark.parametrize("value", [None, "", "  ", True, 1, np.nan])
def test_bad_event_id_rejected(value):
    query = requests();query.event_id = [value]
    with pytest.raises(ValueError):add_breadth_context(query, assets())


@pytest.mark.parametrize("kind", ["naive", "numeric", "duplicate", "reverse", "subgrid", "null"])
def test_bad_source_clock_rejected(kind):
    basket = assets();frame = basket["ETHUSDT"]
    if kind == "naive":frame.open_time = frame.open_time.dt.tz_localize(None)
    elif kind == "numeric":frame.open_time = np.arange(len(frame))
    elif kind == "duplicate":frame.loc[1, "open_time"] = frame.open_time.iloc[0]
    elif kind == "reverse":basket["ETHUSDT"] = frame.iloc[::-1]
    elif kind == "subgrid":frame.open_time += pd.Timedelta(nanoseconds=1)
    else:frame.loc[0, "open_time"] = pd.NaT
    with pytest.raises(ValueError):add_breadth_context(requests(), basket)


@pytest.mark.parametrize("column,value", [("open", 0), ("close", np.nan), ("low", 1000),
    ("high", np.inf), ("volume", -1), ("open", True)])
def test_invalid_prefix_ohlcv_rejected(column, value):
    basket = assets();frame = basket["BNBUSDT"]
    frame[column] = frame[column].astype(object);frame.loc[0, column] = value
    with pytest.raises(ValueError):add_breadth_context(requests(), basket)


@pytest.mark.parametrize("kind", ["missing_asset", "extra_btc", "extra_asset", "not_mapping",
    "missing_request", "duplicate_column", "duplicate_id", "offhour", "wrong_decision", "stack_structure", "stack_breadth"])
def test_input_contracts_rejected(kind):
    query = requests();basket = assets()
    if kind == "missing_asset":basket.pop("SOLUSDT")
    elif kind == "extra_btc":basket["BTCUSDT"] = raw()
    elif kind == "extra_asset":basket["DOGEUSDT"] = raw()
    elif kind == "not_mapping":basket = list(basket.values())
    elif kind == "missing_request":query = query.drop(columns="event_id")
    elif kind == "duplicate_column":query = pd.concat([query, query[["direction"]]], axis=1)
    elif kind == "duplicate_id":query = pd.concat([query, query])
    elif kind == "offhour":query.signal_time += pd.Timedelta(minutes=5);query.decision_time += pd.Timedelta(minutes=5)
    elif kind == "wrong_decision":query.decision_time += HOUR
    elif kind == "stack_structure":query["structure_state"] = 1
    else:query["breadth_score"] = 0
    with pytest.raises(ValueError):add_breadth_context(query, basket)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "not_frame"])
def test_source_schema_rejected(kind):
    basket = assets();frame = basket["XRPUSDT"]
    basket["XRPUSDT"] = (frame.drop(columns="high") if kind == "missing" else
        pd.concat([frame, frame[["high"]]], axis=1) if kind == "duplicate" else None)
    with pytest.raises(ValueError):add_breadth_context(requests(), basket)


def test_timezone_normalization_retains_original_request_fields():
    query = requests();query.signal_time = query.signal_time.dt.tz_convert("Asia/Shanghai")
    query.decision_time = query.decision_time.dt.tz_convert("America/New_York")
    basket = assets()
    for frame in basket.values():frame.open_time = frame.open_time.dt.tz_convert("Asia/Shanghai")
    out, _ = add_breadth_context(query, basket)
    assert_frame_equal(out[query.columns], query)
    assert out.breadth_available_at.iloc[0] == START + 51 * HOUR


def test_empty_requests_and_empty_sources_have_fixed_schema():
    query = requests(())
    query.attrs = {"preserved": True}
    context, trace = add_breadth_context(query, {s: None for s in BREADTH_SYMBOLS})
    assert context.empty and trace.empty
    assert context.attrs == query.attrs
    assert list(context.columns) == list(query.columns) + BREADTH_COLUMNS
    assert list(trace.columns) == TRACE_COLUMNS
    assert context.breadth_source_count.dtype == "Int64"
    context, trace = add_breadth_context(requests(), assets(n=0))
    assert not context.breadth_known.iloc[0]
    assert context.breadth_source_count.iloc[0] == 0
    assert trace.empty


def test_dictionary_order_and_unused_labels_do_not_change_results():
    basket = assets();before, trace_before = add_breadth_context(requests(), basket)
    reverse = {s: basket[s].copy() for s in reversed(BREADTH_SYMBOLS)}
    for frame in reverse.values():
        frame.segment_id = np.arange(len(frame));frame["net_return"] = np.inf;frame["ma"] = np.nan
    after, trace_after = add_breadth_context(requests(), reverse)
    assert_frame_equal(before, after)
    assert_frame_equal(trace_before, trace_after)

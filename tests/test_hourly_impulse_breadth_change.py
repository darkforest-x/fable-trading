"""Synthetic V21 rank observations only: no raw prices or saved outcomes."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse_breadth_change import (
    BREADTH_CHANGE_COLUMNS, BREADTH_SYMBOLS, HOUR, TRACE_COLUMNS,
    add_breadth_change_context,
)


START = pd.Timestamp("2024-01-01", tz="UTC")


def requests(hours=(52,), directions=None):
    return pd.DataFrame({"event_id": ["e%d" % i for i in range(len(hours))],
        "signal_time": [START+h*HOUR for h in hours],
        "decision_time": [START+(h+1)*HOUR for h in hours],
        "direction": directions or [1]*len(hours)})


def trace(n=65, *, previous=(-20,)*4, now=(-10,)*4, gap=None, segment_break=None):
    """Legal saved-rank shape/history fixtures; this is not a rank replay test.

    The pure consumer trusts the caller-pinned V21 rank values. Independent
    HL2->rank correctness is audited separately and is not claimed here.
    """
    rows = []
    for j, symbol in enumerate(BREADTH_SYMBOLS):
        count, segment = 0, 10+j
        for i in range(n):
            if gap == (symbol, i):
                count = 0
                segment += 1
                continue
            if segment_break == (symbol, i):
                count = 0
                segment += 1
            count += 1
            score = np.nan if count < 51 else previous[j] if count == 51 else now[j]
            opened = START+i*HOUR
            rows.append(dict(symbol=symbol, open_time=opened, open=100., high=101., low=99.,
                close=100., volume=1., hl2=100., trscore=score, count=count, segment_id=segment,
                available_at=opened+HOUR, window_start=opened-50*HOUR if count >= 51 else pd.NaT))
    return pd.DataFrame(rows, columns=TRACE_COLUMNS)


def test_51_versus52_full_hours_and_exact_lag():
    result = add_breadth_change_context(requests((50, 51, 52)), trace())
    assert result.breadth_known.tolist() == [False, False, True]
    assert result.breadth_source_count.tolist() == [0, 0, 4]
    assert result.breadth_gate_state.tolist() == ["unknown", "unknown", "accepted"]
    assert result.breadth_score.iloc[2] == .1
    assert result.breadth_change.iloc[2] == .2
    assert result.breadth_raw_sum_change.iloc[2] == 40
    assert result.breadth_mean_now.iloc[2] == -.2
    assert result.breadth_mean_previous.iloc[2] == -.4
    assert result.breadth_mean_now.iloc[:2].isna().all()
    for symbol in BREADTH_SYMBOLS:
        prefix = "breadth_"+symbol+"_"
        assert result[prefix+"count"].tolist() == [50, 51, 52]
        assert result[prefix+"previous_count"].tolist() == [49, 50, 51]
        assert result[prefix+"bar_open"].iloc[2] == START+51*HOUR
        assert result[prefix+"previous_bar_open"].iloc[2] == START+50*HOUR
        assert result[prefix+"available_at"].iloc[2] == START+52*HOUR
        assert result[prefix+"previous_available_at"].iloc[2] == START+51*HOUR
        assert result[prefix+"window_start"].iloc[2] == START+HOUR
        assert result[prefix+"previous_window_start"].iloc[2] == START
    assert result.breadth_available_at.iloc[:2].isna().all()
    assert result.breadth_available_at.iloc[2]+HOUR == result.decision_time.iloc[2]


@pytest.mark.parametrize("previous,now,direction,state", [
    ((-30,)*4, (-20,)*4, 1, "accepted"),  # negative absolute score, improving
    ((30,)*4, (20,)*4, -1, "accepted"),   # positive absolute score, deteriorating
    ((30,)*4, (20,)*4, 1, "abstain"),
    ((-30,)*4, (-20,)*4, -1, "abstain"),
    ((50,)*4, (50,)*4, 1, "abstain"),
    ((-50,)*4, (-50,)*4, -1, "abstain"),
])
def test_change_not_absolute_v21_or_static_trend(previous, now, direction, state):
    row = add_breadth_change_context(requests(directions=[direction]), trace(previous=previous, now=now)).iloc[0]
    assert row.breadth_known and row.breadth_gate_state == state
    assert row.breadth_raw_sum_change == sum(now)-sum(previous)


@pytest.mark.parametrize("previous,now,expected", [((-50,)*4, (50,)*4, 2), ((50,)*4, (-50,)*4, -2)])
def test_full_delta_and_half_delta_boundary(previous, now, expected):
    row = add_breadth_change_context(requests(), trace(previous=previous, now=now)).iloc[0]
    assert row.breadth_change == expected
    assert row.breadth_score == expected/2
    assert row.breadth_raw_sum_change == expected*200


def test_sum_mean_not_majority_vote():
    row = add_breadth_change_context(requests(), trace(previous=(0,)*4, now=(50, -2, -2, -2))).iloc[0]
    assert row.breadth_change == .22
    assert row.breadth_score == .11
    assert row.breadth_gate_state == "accepted"


def test_integer_total_cancellation_exact_zero_abstains_both_directions():
    previous = (2, 4, 6, -10)
    current = (4, 6, -10, 2)
    result = add_breadth_change_context(requests((52, 52), [1, -1]), trace(previous=previous, now=current))
    assert result.breadth_raw_sum_change.eq(0).all()
    assert result.breadth_change.eq(0).all() and result.breadth_score.eq(0).all()
    assert result.breadth_known.all() and result.breadth_reason.eq("neutral").all()
    assert result.breadth_gate_state.eq("abstain").all()


@pytest.mark.parametrize("symbol", BREADTH_SYMBOLS)
@pytest.mark.parametrize("missing", ["current", "previous", "asset"])
def test_missing_either_hour_or_asset_unknown_not_asof(symbol, missing):
    source = trace(gap=(symbol, 51 if missing == "current" else 50))
    if missing == "asset":
        source = source.loc[~source.symbol.eq(symbol)]
    row = add_breadth_change_context(requests(), source).iloc[0]
    assert row.breadth_source_count == 3 and not row.breadth_known
    assert row.breadth_reason == "missing_external_hour" and row.breadth_gate_state == "unknown"
    for field in ("breadth_change", "breadth_score", "breadth_mean_now", "breadth_mean_previous", "breadth_raw_sum_change", "breadth_available_at"):
        assert pd.isna(row[field])


def test_gap_restarts52_hour_union():
    source = trace(n=110, gap=("SOLUSDT", 52))
    result = add_breadth_change_context(requests((52, 53, 54, 104, 105)), source)
    assert result.breadth_known.tolist() == [True, False, False, False, True]
    assert result.breadth_SOLUSDT_count.tolist() == [52, 0, 1, 51, 52]
    assert result.breadth_SOLUSDT_previous_count.tolist() == [51, 52, 0, 50, 51]
    assert result.breadth_SOLUSDT_previous_window_start.iloc[-1] == START+53*HOUR


def test_contiguous_clock_but_new_segment_never_reuses_previous_score():
    source = trace(segment_break=("BNBUSDT", 51))
    row = add_breadth_change_context(requests(), source).iloc[0]
    assert row.breadth_reason == "source_gap" and row.breadth_source_count == 3
    assert row.breadth_BNBUSDT_count == 1


def test_all_rows_index_attrs_own_controls_and_inputs_preserved():
    query = requests((52, 52, 51, 53), [1, -1, 1, 1])
    query.index = pd.Index([7, 7, 2, 99], name="original")
    query.attrs = {"custom": {"nested": [1, 2]}}
    query["population"] = ["case", "control", "case", "control"]
    query["parent_event_id"] = "same-parent"
    query["net_return"] = np.inf  # The function must not inspect outcomes.
    original, source = query.copy(deep=True), trace()
    original_source = source.copy(deep=True)
    result = add_breadth_change_context(query, source)
    assert_frame_equal(query, original)
    assert_frame_equal(source, original_source)
    assert_frame_equal(result[query.columns], query)
    assert result.attrs == query.attrs
    assert result.breadth_gate_state.tolist() == ["accepted", "abstain", "unknown", "abstain"]
    assert len(result) == 4


def test_future_scores_prices_and_developing_k1_hour_ignored():
    query, source = requests(), trace()
    before = add_breadth_change_context(query, source)
    future = source.open_time.ge(query.signal_time.iloc[0])
    numeric = ["trscore", "count", "segment_id", "open", "high", "low", "close", "volume", "hl2"]
    source[numeric] = source[numeric].astype(float)
    source.loc[future, numeric] = np.inf
    source.loc[future, ["window_start", "available_at"]] = pd.NaT
    after = add_breadth_change_context(query, source)
    assert_frame_equal(before, after)
    prefix = source.loc[source.open_time.lt(query.signal_time.iloc[0])]
    assert_frame_equal(before, add_breadth_change_context(query, prefix))


def test_request_prefix_invariance():
    query, source = requests((51, 52, 60), [1, -1, 1]), trace()
    whole = add_breadth_change_context(query, source)
    for count in (1, 2):
        part = query.iloc[:count]
        expected = whole.iloc[:count]
        assert_frame_equal(add_breadth_change_context(part, source), expected)


def test_empty_requests_do_not_inspect_trace():
    query = requests(())
    query.attrs["empty"] = True
    result = add_breadth_change_context(query, object())
    assert result.empty and result.attrs == query.attrs
    assert list(result) == list(query)+BREADTH_CHANGE_COLUMNS


def test_empty_trace_keeps_every_request_unknown():
    query = requests((51, 52), [1, -1])
    result = add_breadth_change_context(query, trace().iloc[:0])
    assert len(result) == 2 and result.breadth_gate_state.eq("unknown").all()
    assert result.breadth_source_count.eq(0).all()
    assert result.breadth_raw_sum_change.isna().all()


@pytest.mark.parametrize("change", ["id_duplicate", "id_missing", "bool", "string", "naive", "nan_clock", "off_hour", "decision", "breadth", "structure", "duplicate_column"])
def test_invalid_request_contract(change):
    query = requests((52, 52), [1, -1])
    if change == "id_duplicate": query["event_id"] = "same"
    elif change == "id_missing": query.loc[0, "event_id"] = None
    elif change == "bool": query["direction"] = [True, False]
    elif change == "string": query["direction"] = ["1", "-1"]
    elif change == "naive": query["signal_time"] = query.signal_time.dt.tz_localize(None)
    elif change == "nan_clock": query.loc[0, "signal_time"] = pd.NaT
    elif change == "off_hour": query["signal_time"] += pd.Timedelta(minutes=5)
    elif change == "decision": query["decision_time"] += HOUR
    elif change == "breadth": query["breadth_known"] = True
    elif change == "structure": query["structure_state"] = 1
    else: query.columns = ["event_id", "event_id", "decision_time", "direction"]
    with pytest.raises(ValueError):
        add_breadth_change_context(query, trace())


@pytest.mark.parametrize("change", ["schema", "asset", "duplicate", "order", "naive", "off_hour", "score_bool", "score_odd", "score_bound", "score_inf", "score_missing", "early_score", "window", "availability", "count", "segment", "gap_without_reset"])
def test_invalid_saved_trace_contract(change):
    source = trace()
    known = source.index[(source.symbol == "ETHUSDT") & source["count"].eq(51)][0]
    if change == "schema": source["unexpected"] = 0
    elif change == "asset": source.loc[0, "symbol"] = "BTCUSDT"
    elif change == "duplicate": source = pd.concat([source.iloc[:1], source], ignore_index=True)
    elif change == "order": source = source.iloc[[1, 0]+list(range(2, len(source)))]
    elif change == "naive": source["open_time"] = source.open_time.dt.tz_localize(None)
    elif change == "off_hour": source.loc[0, "open_time"] += pd.Timedelta(minutes=5)
    elif change == "score_bool": source["trscore"] = source.trscore.astype(object); source.loc[known, "trscore"] = True
    elif change == "score_odd": source.loc[known, "trscore"] = 1
    elif change == "score_bound": source.loc[known, "trscore"] = 52
    elif change == "score_inf": source.loc[known, "trscore"] = np.inf
    elif change == "score_missing": source.loc[known, "trscore"] = np.nan
    elif change == "early_score": source.loc[0, "trscore"] = 0
    elif change == "window": source.loc[known, "window_start"] += HOUR
    elif change == "availability": source.loc[known, "available_at"] += HOUR
    elif change == "count": source.loc[known, "count"] = 52
    elif change == "segment": source.loc[known, "segment_id"] = 0
    else: source = source.drop(index=[known-2])
    with pytest.raises(ValueError):
        add_breadth_change_context(requests(), source)


def test_timezone_equivalence_and_no_unit_guessing():
    source, query = trace(), requests()
    before = add_breadth_change_context(query, source)
    for field in ("signal_time", "decision_time"):
        query[field] = query[field].dt.tz_convert("Asia/Shanghai")
    for field in ("open_time", "window_start", "available_at"):
        source[field] = source[field].dt.tz_convert("Asia/Shanghai")
    after = add_breadth_change_context(query, source)
    assert_frame_equal(before[BREADTH_CHANGE_COLUMNS], after[BREADTH_CHANGE_COLUMNS])
    source["open_time"] = source.open_time.astype("int64")
    with pytest.raises(ValueError):
        add_breadth_change_context(query, source)


def test_symbol_interleaving_accepted_when_each_asset_clock_is_sorted():
    source = trace()
    query = requests()
    expected = add_breadth_change_context(query, source)
    interleaved = source.sort_values(["open_time", "symbol"]).reset_index(drop=True)
    assert_frame_equal(add_breadth_change_context(query, interleaved), expected)

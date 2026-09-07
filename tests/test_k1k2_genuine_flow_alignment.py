"""Synthetic V35 causal-alignment properties; deterministic seeds, not Hypothesis.

No market data, economic labels, files or network are read by these tests.
The independent oracle walks declared slots instead of using the implementation's
searchsorted/subframe algorithm. Generated values obey dependent flow identities.
"""
import math
import random

import numpy as np
import pandas as pd
import pytest

from yoyo.data.k1k2_genuine_flow_alignment import align_windows


START = pd.Timestamp("2024-01-01T00:00:00Z")
STEP = pd.Timedelta(minutes=5)
AMOUNTS = ["quote_volume", "taker_buy_quote_volume", "taker_sell_quote_volume",
           "delta_quote_volume", "trade_count"]
RESULT_COLUMNS = ["expected_bars", "observed_bars", "available_bars", "missing_bars",
                  "unavailable_bars", "zero_volume_bars", "status", "quote_volume_sum",
                  "delta_quote_volume_sum", "imbalance", "directional_imbalance",
                  "flow_defined", "max_source_available_at"]
AGGREGATES = ["quote_volume_sum", "delta_quote_volume_sum", "imbalance",
              "directional_imbalance"]


def make_flow(quantities=(10, 20, 30), buys=None, *, start=START):
    totals = list(quantities)
    bought = list(buys) if buys is not None else [q / 2 for q in totals]
    assert len(totals) == len(bought)
    opens = pd.date_range(start, periods=len(totals), freq="5min")
    return pd.DataFrame({
        "open_time": opens,
        "earliest_available_at": opens + STEP,
        "quote_volume": np.asarray(totals, dtype=float),
        "taker_buy_quote_volume": np.asarray(bought, dtype=float),
        "taker_sell_quote_volume": np.asarray([q - b for q, b in zip(totals, bought)], dtype=float),
        "delta_quote_volume": np.asarray([2 * b - q for q, b in zip(totals, bought)], dtype=float),
        "trade_count": np.asarray([0 if q == 0 else 3 for q in totals], dtype=np.int64),
    })


def make_windows(specs=((0, 3, 3, 1),)):
    return pd.DataFrame([
        dict(window_id=f"window-{i}", event_id=f"event-{i // 2}",
             window_kind="k1" if i % 2 == 0 else "k1_to_k2",
             window_start=START + a * STEP, window_end=START + b * STEP,
             decision_time=START + d * STEP, direction=side)
        for i, (a, b, d, side) in enumerate(specs)
    ], columns=["window_id", "event_id", "window_kind", "window_start", "window_end",
                "decision_time", "direction"])


def slow_oracle(flow, windows, mode):
    """Enumerate each expected slot and derive effective source time independently."""
    lookup = {pd.Timestamp(row["open_time"]): row for row in flow.to_dict("records")}
    result = []
    for window in windows.to_dict("records"):
        expected = observed = available = zeros = 0
        values = []
        times = []
        slot = window["window_start"]
        while slot < window["window_end"]:
            expected += 1
            source = lookup.get(slot)
            if source is not None:
                observed += 1
                at = source["earliest_available_at"]
                if mode == "observed_delivery":
                    arrival = source.get("observed_at", pd.NaT)
                    at = pd.NaT if pd.isna(arrival) else max(at, arrival)
                if not pd.isna(at) and at <= window["decision_time"]:
                    available += 1
                    zeros += source["quote_volume"] == 0
                    values.append((source["quote_volume"], source["delta_quote_volume"]))
                    times.append(at)
            slot += STEP
        missing = expected - observed
        unavailable = observed - available
        status = "missing_bars" if missing else "unavailable_bars" if unavailable else "complete"
        total = delta = imbalance = directional = float("nan")
        defined = False
        if missing == unavailable == 0:
            total = math.fsum(v[0] for v in values)
            delta = math.fsum(v[1] for v in values)
            if total == 0:
                status = "zero_volume"
            else:
                imbalance = delta / total
                directional = window["direction"] * imbalance
                defined = True
        result.append(dict(expected_bars=expected, observed_bars=observed,
                           available_bars=available, missing_bars=missing,
                           unavailable_bars=unavailable, zero_volume_bars=zeros,
                           status=status, quote_volume_sum=total,
                           delta_quote_volume_sum=delta, imbalance=imbalance,
                           directional_imbalance=directional, flow_defined=defined,
                           max_source_available_at=max(times) if times else pd.NaT))
    answer = pd.DataFrame(result, columns=RESULT_COLUMNS)
    answer["max_source_available_at"] = pd.to_datetime(answer.max_source_available_at, utc=True)
    return answer


def assert_partial(row, *, status, expected, observed, available):
    assert row.status == status
    assert row.expected_bars == expected
    assert row.observed_bars == observed
    assert row.available_bars == available
    assert row.missing_bars == expected - observed
    assert row.unavailable_bars == observed - available
    assert not row.flow_defined
    assert all(pd.isna(row[col]) for col in AGGREGATES)


def test_weighted_imbalance_is_ratio_of_sums_not_mean_of_bar_ratios():
    flow = make_flow((1, 99), (1, 0))
    row = align_windows(flow, make_windows(((0, 2, 2, -1),))).iloc[0]
    assert row.status == "complete"
    assert row.quote_volume_sum == 100
    assert row.delta_quote_volume_sum == -98
    assert row.imbalance == pytest.approx(-.98)
    assert row.directional_imbalance == pytest.approx(.98)
    assert row.imbalance != np.mean(flow.delta_quote_volume / flow.quote_volume)
    assert row.flow_defined


def test_exact_boundary_includes_last_closed_bar_and_excludes_next_open():
    flow = make_flow((10, 20, 30, 999), (10, 0, 30, 0))
    flow.loc[3, AMOUNTS] = np.nan
    row = align_windows(flow, make_windows()).iloc[0]
    assert row.observed_bars == row.available_bars == row.expected_bars == 3
    assert row.quote_volume_sum == 60
    assert row.delta_quote_volume_sum == 20
    assert row.max_source_available_at == START + 3 * STEP


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_missing_first_middle_last_is_not_filled_or_partially_aggregated(missing):
    flow = make_flow().drop(index=missing)
    row = align_windows(flow, make_windows()).iloc[0]
    assert_partial(row, status="missing_bars", expected=3, observed=2, available=2)


def test_empty_source_is_missing_not_zero_volume():
    row = align_windows(make_flow(()), make_windows()).iloc[0]
    assert_partial(row, status="missing_bars", expected=3, observed=0, available=0)
    assert row.zero_volume_bars == 0
    assert pd.isna(row.max_source_available_at)


def test_empty_schema_with_default_object_columns_still_means_missing():
    flow = pd.DataFrame(columns=make_flow().columns)
    row = align_windows(flow, make_windows()).iloc[0]
    assert_partial(row, status="missing_bars", expected=3, observed=0, available=0)


def test_known_zero_bars_mix_into_complete_sum_without_becoming_missing():
    row = align_windows(make_flow((0, 10, 0), (0, 8, 0)), make_windows()).iloc[0]
    assert row.status == "complete"
    assert row.available_bars == 3 and row.zero_volume_bars == 2
    assert row.missing_bars == row.unavailable_bars == 0
    assert row.quote_volume_sum == 10 and row.delta_quote_volume_sum == 6
    assert row.imbalance == pytest.approx(.6) and row.flow_defined


def test_whole_zero_is_known_but_directional_flow_is_undefined():
    row = align_windows(make_flow((0, 0, 0)), make_windows()).iloc[0]
    assert row.status == "zero_volume" and row.zero_volume_bars == 3
    assert row.quote_volume_sum == row.delta_quote_volume_sum == 0
    assert pd.isna(row.imbalance) and pd.isna(row.directional_imbalance)
    assert row.missing_bars == row.unavailable_bars == 0
    assert not row.flow_defined


@pytest.mark.parametrize("kind", ["absent", "nat", "late"])
def test_delivery_is_not_inferred_from_historical_boundary(kind):
    flow = make_flow()
    if kind == "nat":
        flow["observed_at"] = pd.Series(pd.NaT, index=flow.index, dtype="datetime64[ns, UTC]")
    elif kind == "late":
        flow["observed_at"] = START + 3 * STEP + pd.Timedelta(seconds=1)
    row = align_windows(flow, make_windows(), availability_mode="observed_delivery").iloc[0]
    assert_partial(row, status="unavailable_bars", expected=3, observed=3, available=0)
    assert pd.isna(row.max_source_available_at)
    assert align_windows(flow, make_windows()).iloc[0].status == "complete"


def test_delivery_at_decision_is_allowed_but_one_nanosecond_later_is_not():
    flow = make_flow()
    flow["observed_at"] = START + 3 * STEP
    windows = make_windows()
    ontime = align_windows(flow, windows, availability_mode="observed_delivery").iloc[0]
    assert ontime.status == "complete" and ontime.available_bars == 3
    assert ontime.max_source_available_at == START + 3 * STEP
    flow.loc[2, "observed_at"] += pd.Timedelta(nanoseconds=1)
    late = align_windows(flow, windows, availability_mode="observed_delivery").iloc[0]
    assert_partial(late, status="unavailable_bars", expected=3, observed=3, available=2)


def test_early_delivery_stamp_does_not_move_the_complete_bar_boundary():
    flow = make_flow()
    flow["observed_at"] = flow.open_time - pd.Timedelta(seconds=7)
    row = align_windows(flow, make_windows(), availability_mode="observed_delivery").iloc[0]
    assert row.status == "complete"
    assert row.max_source_available_at == START + 3 * STEP


def test_missing_priority_preserves_unavailable_and_known_zero_counts():
    flow = make_flow((0, 10, 20), (0, 5, 20)).drop(index=1)
    flow["observed_at"] = flow.earliest_available_at
    flow.loc[2, "observed_at"] = START + 4 * STEP
    row = align_windows(flow, make_windows(), availability_mode="observed_delivery").iloc[0]
    assert_partial(row, status="missing_bars", expected=3, observed=2, available=1)
    assert row.zero_volume_bars == 1
    assert row.max_source_available_at == START + STEP


def test_unavailable_numeric_unknown_is_not_misread_as_observed_zero_or_invalid_past():
    flow = make_flow((0, 10, 20), (0, 5, 20))
    flow["observed_at"] = flow.earliest_available_at
    flow.loc[2, "observed_at"] = pd.NaT
    flow.loc[2, AMOUNTS] = np.nan
    row = align_windows(flow, make_windows(), availability_mode="observed_delivery").iloc[0]
    assert_partial(row, status="unavailable_bars", expected=3, observed=3, available=2)
    assert row.zero_volume_bars == 1


@pytest.mark.parametrize("mode", ["historical_boundary", "observed_delivery"])
def test_future_edit_and_append_invariance_including_nan_future_values(mode):
    flow = make_flow((2, 3, 4, 5, 6, 7), (1, 3, 0, 1, 3, 7))
    flow["observed_at"] = flow.earliest_available_at
    windows = make_windows(((0, 2, 2, 1), (1, 3, 3, -1)))
    baseline = align_windows(flow.iloc[:3], windows, availability_mode=mode)
    changed = flow.copy(deep=True)
    changed.loc[3:, AMOUNTS] = np.nan
    extra = make_flow((9, 10), (0, 10), start=START + 6 * STEP)
    extra["observed_at"] = extra.earliest_available_at + pd.Timedelta(days=100)
    extra[AMOUNTS] = np.nan
    extended = pd.concat([changed, extra], ignore_index=True)
    pd.testing.assert_frame_equal(baseline, align_windows(extended, windows, availability_mode=mode))
    changed.loc[3:, AMOUNTS] = [float("inf"), -99, 999, float("nan"), -.5]
    pd.testing.assert_frame_equal(baseline, align_windows(changed, windows, availability_mode=mode))


@pytest.mark.parametrize("mode", ["historical_boundary", "observed_delivery"])
def test_full_and_per_decision_prefix_results_equal(mode):
    flow = make_flow(tuple(range(1, 13)), (1, 0, 1, 4, 0, 6, 4, 0, 9, 2, 8, 12))
    flow["observed_at"] = flow.earliest_available_at + pd.Timedelta(seconds=1)
    windows = make_windows(((0, 3, 4, 1), (2, 5, 5, -1), (4, 9, 10, 1)))
    full = align_windows(flow, windows, availability_mode=mode)
    for i, row in windows.iterrows():
        prefix = flow.loc[flow.open_time < row.decision_time]
        actual = align_windows(prefix, windows.iloc[[i]], availability_mode=mode)
        pd.testing.assert_frame_equal(full.iloc[[i]].reset_index(drop=True), actual)


def test_input_objects_not_mutated_and_event_row_order_and_extra_columns_preserved():
    flow = make_flow().iloc[[2, 0, 1]].copy()
    windows = make_windows(((1, 3, 3, -1), (0, 1, 1, 1), (0, 3, 3, -1)))
    windows.index = [99, 3, 41]
    windows["caller_note"] = ["last", "first", "whole"]
    original_flow = flow.copy(deep=True)
    original_windows = windows.copy(deep=True)
    result = align_windows(flow, windows)
    pd.testing.assert_frame_equal(flow, original_flow)
    pd.testing.assert_frame_equal(windows, original_windows)
    pd.testing.assert_frame_equal(result[windows.columns], windows.reset_index(drop=True))
    assert result.columns.tolist() == windows.columns.tolist() + RESULT_COLUMNS
    assert result.window_id.tolist() == windows.window_id.tolist()


def test_empty_windows_return_empty_full_schema():
    windows = make_windows().iloc[:0]
    result = align_windows(make_flow(), windows)
    assert result.empty
    assert result.columns.tolist() == windows.columns.tolist() + RESULT_COLUMNS


@pytest.mark.parametrize("which", ["source", "window"])
def test_duplicate_identity_rejected_without_silent_deduplication(which):
    flow, windows = make_flow(), make_windows()
    if which == "source":
        flow = pd.concat([flow, flow.iloc[[0]]], ignore_index=True)
    else:
        windows = pd.concat([windows, windows], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        align_windows(flow, windows)


@pytest.mark.parametrize("column", ["open_time", "earliest_available_at"])
@pytest.mark.parametrize("change", ["off_grid", "naive", "missing", "numeric"])
def test_bad_source_clocks_rejected(column, change):
    flow = make_flow()
    if change == "off_grid":
        flow.loc[0, column] += pd.Timedelta(seconds=1)
    elif change == "naive":
        flow[column] = flow[column].dt.tz_localize(None)
    elif change == "missing":
        flow.loc[0, column] = pd.NaT
    else:
        flow[column] = flow[column].astype("int64")
    with pytest.raises(ValueError):
        align_windows(flow, make_windows())


def test_wrong_source_duration_rejected_even_if_grid_aligned():
    flow = make_flow()
    flow.loc[0, "earliest_available_at"] += STEP
    with pytest.raises(ValueError, match="boundary"):
        align_windows(flow, make_windows())


@pytest.mark.parametrize("unit", ["s", "ms", "us"])
def test_same_zoned_timestamps_in_other_resolution_have_identical_alignment(unit):
    flow, windows = make_flow(), make_windows()
    expected = align_windows(flow, windows)
    for column in ["open_time", "earliest_available_at"]:
        flow[column] = flow[column].dt.as_unit(unit)
    for column in ["window_start", "window_end", "decision_time"]:
        windows[column] = windows[column].dt.as_unit(unit)
    pd.testing.assert_frame_equal(align_windows(flow, windows), expected)


def test_explicit_non_utc_timezone_normalizes_same_instant_without_guessing():
    flow, windows = make_flow(), make_windows()
    expected = align_windows(flow, windows)
    for column in ["open_time", "earliest_available_at"]:
        flow[column] = flow[column].dt.tz_convert("Asia/Shanghai")
    for column in ["window_start", "window_end", "decision_time"]:
        windows[column] = windows[column].dt.tz_convert("Asia/Shanghai")
    pd.testing.assert_frame_equal(align_windows(flow, windows), expected)


@pytest.mark.parametrize("column", ["window_start", "window_end", "decision_time"])
@pytest.mark.parametrize("change", ["off_grid", "naive", "missing", "numeric"])
def test_bad_window_clocks_rejected(column, change):
    windows = make_windows()
    if change == "off_grid":
        windows.loc[0, column] += pd.Timedelta(seconds=1)
    elif change == "naive":
        windows[column] = windows[column].dt.tz_localize(None)
    elif change == "missing":
        windows.loc[0, column] = pd.NaT
    else:
        windows[column] = windows[column].astype("int64")
    with pytest.raises(ValueError):
        align_windows(make_flow(), windows)


@pytest.mark.parametrize("spec", [(1, 1, 2, 1), (2, 1, 3, 1), (0, 3, 2, 1)])
def test_empty_reversed_or_future_window_rejected(spec):
    with pytest.raises(ValueError, match="window"):
        align_windows(make_flow(), make_windows((spec,)))


@pytest.mark.parametrize("direction", [0, 2, -2, True, False, np.nan, "1"])
def test_non_signed_direction_rejected(direction):
    windows = make_windows()
    windows["direction"] = direction
    with pytest.raises(ValueError, match="direction"):
        align_windows(make_flow(), windows)


@pytest.mark.parametrize("column", ["window_id", "event_id", "window_kind"])
@pytest.mark.parametrize("value", [None, "", "  "])
def test_missing_window_identity_rejected(column, value):
    windows = make_windows()
    windows[column] = value
    with pytest.raises(ValueError, match="identity"):
        align_windows(make_flow(), windows)


@pytest.mark.parametrize("column", RESULT_COLUMNS)
def test_output_name_collision_rejected(column):
    windows = make_windows()
    windows[column] = "must not overwrite"
    with pytest.raises(ValueError, match="collide"):
        align_windows(make_flow(), windows)


@pytest.mark.parametrize("source", [True, False])
def test_missing_required_columns_rejected(source):
    flow, windows = make_flow(), make_windows()
    if source:
        flow = flow.drop(columns="delta_quote_volume")
    else:
        windows = windows.drop(columns="decision_time")
    with pytest.raises(ValueError, match="required"):
        align_windows(flow, windows)


def test_unknown_availability_mode_rejected():
    with pytest.raises(ValueError, match="availability"):
        align_windows(make_flow(), make_windows(), availability_mode="assume_realtime")


@pytest.mark.parametrize("column,value", [
    ("quote_volume", np.nan), ("quote_volume", np.inf),
    ("taker_buy_quote_volume", -1), ("taker_sell_quote_volume", -1),
    ("delta_quote_volume", np.nan), ("delta_quote_volume", np.inf),
    ("trade_count", -1), ("trade_count", .5), ("trade_count", float(2**63)),
    ("trade_count", np.nan), ("quote_volume", 11),
    ("taker_buy_quote_volume", 11), ("delta_quote_volume", 1),
    ("trade_count", 0),
])
def test_available_amounts_counts_and_conservation_fail_closed(column, value):
    flow = make_flow()
    flow[column] = flow[column].astype(float)
    flow.loc[0, column] = value
    with pytest.raises(ValueError):
        align_windows(flow, make_windows())


@pytest.mark.parametrize("column", ["taker_buy_quote_volume", "taker_sell_quote_volume",
                                  "delta_quote_volume", "trade_count"])
def test_zero_volume_cannot_contain_nonzero_activity(column):
    flow = make_flow((0, 0, 0))
    flow.loc[0, column] = 1
    with pytest.raises(ValueError):
        align_windows(flow, make_windows())


def test_cancellation_uses_operand_scale_without_hiding_material_delta_error():
    flow = make_flow((200000000.01,), (100000000.01,))
    flow.loc[0, "taker_sell_quote_volume"] = 100000000.0
    flow.loc[0, "delta_quote_volume"] = .01
    windows = make_windows(((0, 1, 1, 1),))
    row = align_windows(flow, windows).iloc[0]
    assert row.delta_quote_volume_sum == .01
    flow.loc[0, "delta_quote_volume"] = 1
    with pytest.raises(ValueError, match="conservation"):
        align_windows(flow, windows)


def test_swapping_buy_sell_negates_raw_imbalance_and_direction_mirror_preserves_alignment():
    flow = make_flow((10, 20, 30), (9, 3, 25))
    windows = make_windows(((0, 3, 3, 1), (1, 3, 3, -1)))
    original = align_windows(flow, windows)
    mirrored = flow.copy(deep=True)
    mirrored["taker_buy_quote_volume"] = flow.taker_sell_quote_volume
    mirrored["taker_sell_quote_volume"] = flow.taker_buy_quote_volume
    mirrored["delta_quote_volume"] = -flow.delta_quote_volume
    flipped_windows = windows.copy(deep=True)
    flipped_windows["direction"] = -windows.direction
    result = align_windows(mirrored, flipped_windows)
    np.testing.assert_allclose(result.imbalance, -original.imbalance)
    np.testing.assert_allclose(result.directional_imbalance, original.directional_imbalance)
    pd.testing.assert_frame_equal(result[RESULT_COLUMNS[:7]], original[RESULT_COLUMNS[:7]])


@pytest.mark.parametrize("seed", [0, 1, 7, 31, 101, 20260907])
@pytest.mark.parametrize("mode", ["historical_boundary", "observed_delivery"])
def test_seeded_windows_match_independent_slot_oracle(seed, mode):
    rng = random.Random(seed)
    totals = [rng.randrange(0, 1000) if rng.random() > .15 else 0 for _ in range(48)]
    buys = [rng.randrange(total + 1) for total in totals]
    flow = make_flow(totals, buys)
    delivery = []
    for boundary in flow.earliest_available_at:
        choice = rng.randrange(5)
        delivery.append(pd.NaT if choice == 0 else boundary + pd.Timedelta(seconds=[0, -1, 0, 1, 600][choice]))
    flow["observed_at"] = pd.to_datetime(delivery, utc=True)
    removed = [i for i in range(len(flow)) if rng.random() < .12]
    flow = flow.drop(index=removed).sample(frac=1, random_state=seed).reset_index(drop=True)
    specs = []
    for _ in range(24):
        start = rng.randrange(0, 45)
        end = rng.randrange(start + 1, 49)
        decision = end + rng.randrange(0, 4)
        specs.append((start, end, decision, rng.choice([-1, 1])))
    windows = make_windows(specs)
    original_flow, original_windows = flow.copy(deep=True), windows.copy(deep=True)
    actual = align_windows(flow, windows,availability_mode=mode)
    expected = slow_oracle(flow, windows, mode)
    pd.testing.assert_frame_equal(actual[RESULT_COLUMNS], expected, check_exact=False,
                                  rtol=1e-13, atol=1e-13)
    assert (actual.expected_bars == actual.missing_bars + actual.observed_bars).all()
    assert (actual.observed_bars == actual.available_bars + actual.unavailable_bars).all()
    assert (actual.zero_volume_bars <= actual.available_bars).all()
    assert actual.loc[actual.flow_defined, "imbalance"].between(-1, 1).all()
    pd.testing.assert_frame_equal(flow, original_flow)
    pd.testing.assert_frame_equal(windows, original_windows)

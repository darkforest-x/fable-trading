"""Causal Monday 1.618 / SMA50 signal-candidate preparation tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_ma50_signals import _sma50, prepare


MONDAY_OPEN = pd.Timestamp("2026-01-05T05:00:00Z")
TOUCH_OPEN = pd.Timestamp("2026-01-06T05:00:00Z")
STEP = pd.Timedelta(minutes=5)


def frame(end: pd.Timestamp | None = None) -> pd.DataFrame:
    """One complete NY Monday followed by a touch and mirrored SMA50 setups."""
    if end is None:
        end = TOUCH_OPEN + 35 * STEP
    index = pd.date_range(MONDAY_OPEN, end, freq=STEP, inclusive="left")
    n = len(index)
    out = pd.DataFrame(
        {
            "open": np.full(n, 100.5),
            "high": np.full(n, 101.0),
            "low": np.full(n, 100.0),
            "close": np.full(n, 100.5),
            "count": np.full(n, 5),
        },
        index=index,
    )

    # The first Tuesday candle touches both levels. Its close is a genuine
    # short-side close/SMA cross; the later long-side cross is separate.
    if TOUCH_OPEN in out.index:
        out.loc[TOUCH_OPEN, ["open", "high", "low", "close"]] = [
            100.5, 102.0, 99.0, 100.2,
        ]
    if TOUCH_OPEN + STEP in out.index:
        out.loc[TOUCH_OPEN + STEP, ["open", "high", "low", "close"]] = [
            100.2, 100.6, 99.7, 100.2,
        ]
    if TOUCH_OPEN + 2 * STEP in out.index:
        out.loc[TOUCH_OPEN + 2 * STEP, ["open", "high", "low", "close"]] = [
            100.2, 100.8, 99.9, 100.7,
        ]
    if TOUCH_OPEN + 3 * STEP in out.index:
        out.loc[TOUCH_OPEN + 3 * STEP, ["open", "high", "low", "close"]] = [
            100.7, 100.8, 100.4, 100.65,
        ]
    later = out.index >= TOUCH_OPEN + 4 * STEP
    out.loc[later, ["open", "high", "low", "close"]] = [
        100.2, 101.5, 99.5, 100.3,
    ]
    return out


def opportunity(result: dict, side: int) -> pd.Series:
    rows = result["opportunities"].query(
        "week == '2026-01-05' and side == @side"
    )
    assert len(rows) == 1
    return rows.iloc[0]


def test_mirrored_first_touch_cross_retest_geometry_and_random_schema():
    bars = frame()
    result = prepare(bars, "BTCUSDT", {"seed": 21})
    long = opportunity(result, 1)
    short = opportunity(result, -1)
    touch_i = bars.index.get_loc(TOUCH_OPEN)

    assert long.status == short.status == "signal"
    assert (long.touch_i, short.touch_i) == (touch_i, touch_i)
    assert long.cross_i == touch_i + 2 and long.confirm_i == touch_i + 3
    assert short.cross_i == touch_i and short.confirm_i == touch_i + 1
    assert long.confirm_i > long.cross_i and short.confirm_i > short.cross_i
    assert long.entry_i > long.touch_i and short.entry_i > short.touch_i
    assert long.stop == 99.0 and short.stop == 102.0
    assert long.target_price == 101.0 and short.target_price == 100.0
    assert long.risk > 0 and short.risk > 0

    entries = result["entries"]
    signals = entries[entries.population.eq("signal")]
    assert set(signals.side) == {1, -1}
    assert set(entries.population) <= {"signal", "random"}
    assert (entries.entry_i > entries.touch_i).all()
    assert (entries.entry_i < entries.end_i).all()
    assert (entries.control_id[entries.population.eq("signal")] == 0).all()
    assert (entries.entry_time == [bars.index[i] + STEP for i in entries.entry_i]).all()

    controls = entries[entries.population.eq("random")]
    for signal in signals.itertuples():
        matched = controls[controls.pair_id.eq(signal.pair_id)]
        assert matched.side.eq(signal.side).all()
        assert matched.week.eq(signal.week).all()
        assert matched.bucket.eq(signal.bucket).all()
        assert (matched.control_id.between(1, 3)).all()
        assert matched.entry_i.ne(signal.touch_i).all()
    funnel = result["diagnostics"]["funnel"]
    random = result["diagnostics"]["random_controls"]
    assert funnel["first_touches"] == 2
    assert funnel["signal"] == 2
    assert random["available"] == len(controls)
    assert random["requested"] == random["available"] + random["shortfall"]


def test_cross_is_strict_and_retest_must_be_a_later_bar():
    bars = frame()
    # Equality to SMA is not a cross. A strict cross is invalidated by a close
    # back above the line; the next strict cross can then confirm only on a
    # later candle's retest.
    bars.loc[TOUCH_OPEN, "close"] = 100.5
    bars.loc[TOUCH_OPEN + STEP, ["open", "high", "low", "close"]] = [
        100.5, 100.6, 100.4, 100.5,
    ]
    bars.loc[TOUCH_OPEN + 2 * STEP, ["open", "high", "low", "close"]] = [
        100.5, 100.6, 100.1, 100.2,
    ]
    bars.loc[TOUCH_OPEN + 3 * STEP, ["open", "high", "low", "close"]] = [
        100.2, 100.8, 100.1, 100.7,
    ]
    bars.loc[TOUCH_OPEN + 4 * STEP, ["open", "high", "low", "close"]] = [
        100.7, 100.8, 100.1, 100.2,
    ]
    bars.loc[TOUCH_OPEN + 5 * STEP, ["open", "high", "low", "close"]] = [
        100.2, 100.6, 100.1, 100.2,
    ]

    short = opportunity(prepare(bars, "BTCUSDT"), -1)
    assert short.status == "signal"
    assert short.cross_i == bars.index.get_loc(TOUCH_OPEN + 4 * STEP)
    assert short.confirm_i == bars.index.get_loc(TOUCH_OPEN + 5 * STEP)
    assert short.cross_count == 2
    assert short.confirm_i > short.cross_i


def test_signal_fields_do_not_change_when_later_ohlc_changes():
    bars = frame()
    original = prepare(bars, "BTCUSDT", {"seed": 8})
    changed = bars.copy()
    cutoff = TOUCH_OPEN + 4 * STEP
    changed.loc[changed.index >= cutoff, ["open", "high", "low", "close"]] = [
        125.0, 127.0, 120.0, 126.0,
    ]
    perturbed = prepare(changed, "BTCUSDT", {"seed": 8})

    columns = ["touch_i", "cross_i", "confirm_i", "entry_i", "entry",
               "stop", "risk", "target_price", "bucket"]
    for side in (1, -1):
        a, b = opportunity(original, side), opportunity(perturbed, side)
        assert a[columns].to_dict() == b[columns].to_dict()
    original_signals = original["entries"].query("population == 'signal'").set_index("pair_id")
    changed_signals = perturbed["entries"].query("population == 'signal'").set_index("pair_id")
    pd.testing.assert_frame_equal(
        original_signals[columns[1:]], changed_signals[columns[1:]],
        check_dtype=False,
    )


def test_crypto_gap_censors_monitor_and_resets_sma_but_gold_keeps_quality_segment():
    bars = frame()
    gap_time = TOUCH_OPEN + STEP
    crypto = bars.drop(gap_time)
    result = prepare(crypto, "BTCUSDT")
    short = opportunity(result, -1)
    assert short.status == "censored"
    assert short.reason == "no_retest"
    assert short.monitor_end_reason == "source_gap"
    assert short.confirm_i == -1

    # The crypto SMA restarts after a timestamp gap. Gold keeps its 50-close
    # history inside one quality segment across an ordinary market closure.
    gap_end = TOUCH_OPEN + 7 * STEP
    gapped = bars.drop(bars.loc[TOUCH_OPEN + STEP:gap_end].index)
    gapped = gapped.assign(quality_segment=0)
    gold_sma = _sma50(gapped, gapped["count"].to_numpy() == 5, "XAUUSD")
    crypto_sma = _sma50(gapped, gapped["count"].to_numpy() == 5, "BTCUSDT")
    reopen_i = gapped.index.get_loc(gap_end + STEP)
    assert np.isfinite(gold_sma[reopen_i])
    assert np.isnan(crypto_sma[reopen_i])


def test_incomplete_monday_prefix_keeps_touch_as_invalid_opportunity():
    bars = frame()
    bars.iloc[10, bars.columns.get_loc("count")] = 4
    result = prepare(bars, "BTCUSDT")
    assert result["diagnostics"]["funnel"]["invalid_prefix"] == 2
    assert opportunity(result, -1).reason == "unknown_monday_prefix"
    assert result["entries"].empty


def test_next_monday_close_is_outside_entry_window():
    week_end = pd.Timestamp("2026-01-12T05:00:00Z")
    bars = frame(week_end + 2 * STEP)
    later = bars.index >= TOUCH_OPEN
    bars.loc[later, ["open", "high", "low", "close"]] = [
        100.5, 101.0, 100.0, 100.5,
    ]
    bars.loc[TOUCH_OPEN, ["open", "high", "low", "close"]] = [
        100.5, 102.0, 99.0, 100.5,
    ]
    boundary_i = bars.index.get_loc(week_end - STEP)
    cross_i = boundary_i - 1
    bars.iloc[cross_i, bars.columns.get_indexer(["open", "high", "low", "close"])] = [
        100.5, 100.5, 100.1, 100.2,
    ]
    bars.iloc[boundary_i, bars.columns.get_indexer(["open", "high", "low", "close"])] = [
        100.2, 100.6, 100.1, 100.2,
    ]

    result = prepare(bars, "BTCUSDT")
    short = opportunity(result, -1)
    assert short.status == "expired"
    assert short.reason == "no_retest"
    assert short.monitor_end_reason == "week_expiry"
    assert short.confirm_i == -1
    assert short.scan_end_i == boundary_i - 1


def test_crypto_gap_across_the_week_is_not_mislabeled_as_expiry():
    week_end = pd.Timestamp("2026-01-12T05:00:00Z")
    bars = frame(week_end + 2 * STEP)
    keep = (bars.index <= TOUCH_OPEN) | (bars.index >= week_end)
    gapped = bars.loc[keep]

    short = opportunity(prepare(gapped, "BTCUSDT"), -1)
    assert short.status == "censored"
    assert short.reason == "no_retest"
    assert short.monitor_end_reason == "source_gap"


def test_invalid_first_confirmation_is_retained_and_not_retried():
    bars = frame()
    monday = bars.index.tz_convert("America/New_York").dayofweek == 0
    bars.loc[monday, "low"] = 100.2  # The short TP equals its first entry.
    # Later bars form another possible short cross/retest with favorable space.
    bars.loc[TOUCH_OPEN + 4 * STEP, ["open", "high", "low", "close"]] = [
        101.0, 101.2, 100.8, 101.0,
    ]
    bars.loc[TOUCH_OPEN + 5 * STEP, ["open", "high", "low", "close"]] = [
        100.5, 100.7, 100.1, 100.3,
    ]
    bars.loc[TOUCH_OPEN + 6 * STEP, ["open", "high", "low", "close"]] = [
        100.3, 100.7, 100.1, 100.3,
    ]

    result = prepare(bars, "BTCUSDT")
    short = opportunity(result, -1)
    assert short.status == "invalid_geometry"
    assert short.reason == "target_not_favorable"
    assert short.confirm_i == bars.index.get_loc(TOUCH_OPEN + STEP)
    assert not result["entries"].query("population == 'signal' and side == -1").shape[0]


def test_signal_survives_when_random_pool_is_too_small_and_last_bar_is_not_entry():
    bars = frame(TOUCH_OPEN + 3 * STEP)
    result = prepare(bars, "BTCUSDT", {"seed": 2})
    short = opportunity(result, -1)
    signals = result["entries"].query("population == 'signal' and side == -1")

    assert short.status == "signal"
    assert len(signals) == 1
    assert signals.iloc[0].entry_i == short.confirm_i
    assert signals.iloc[0].entry_i < signals.iloc[0].end_i
    assert result["diagnostics"]["random_controls"]["requested"] == 3
    assert result["diagnostics"]["random_controls"]["available"] == 0
    assert result["diagnostics"]["random_controls"]["shortfall"] == 3


def test_confirmation_on_source_last_bar_is_censored_without_native_entry():
    bars = frame(TOUCH_OPEN + 2 * STEP)
    result = prepare(bars, "BTCUSDT")
    short = opportunity(result, -1)

    assert short.confirm_i == len(bars) - 1
    assert short.status == "censored"
    assert short.reason == "no_observed_post_entry"
    assert result["diagnostics"]["funnel"]["no_observed_post_entry"] == 1
    assert not result["entries"].query("population == 'signal' and side == -1").shape[0]

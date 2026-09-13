"""Synthetic contracts for development-only SPIKE breadth matched controls."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_market_breadth_matched_controls import (
    DEVELOPMENT_END,
    _read_targets,
    _quartile_buckets,
    match_stream_controls,
    month_cluster_sign_flip_p,
    paired_sign_flip_p,
    summarize_controls,
)


def _cache(n: int = 150) -> tuple[dict, pd.DatetimeIndex]:
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                         "atr": np.linspace(1.0, 2.0, n), "ready": True}, index=index)
    bars.attrs["minutes"] = 60
    raw = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    v1 = raw.copy()
    return {"bars": bars, "raw_v6": raw, "v1": v1,
            "data_gap": pd.Series(False, index=index), "bb_ready": pd.Series(False, index=index), "tick": .1}, index


def _target(index: pd.DatetimeIndex, i: int = 130, variant: str = "v1_common_execution_long") -> pd.DataFrame:
    return pd.DataFrame({"target_id": ["one"], "signal_bar_open": [index[i]], "side": [1], "net_r": [1.5],
                         "net_return": [.03], "joint_delta_60m": [.1], "joint_breadth": [.5]})


def test_controls_match_prior_120_bar_atr_quartile_and_reuse_single_event_execution():
    cache, index = _cache()
    targets = _target(index)
    # Keep exactly one legal draw. Its next entry bar crosses the stop that was
    # derived from five preceding low=99 bars, making the outcome realized.
    cache["bars"]["ready"] = False
    cache["bars"].loc[index[120], "ready"] = True
    cache["bars"].loc[index[121], "low"] = 90.0
    pairs = match_stream_controls(cache, targets, variant="v1_common_execution_long",
                                  fold_start=index[120], fold_end=index[-1] + pd.Timedelta(hours=1))
    assert len(pairs) == 1 and bool(pairs.matched.iloc[0])
    bucket, ready = _quartile_buckets(cache["bars"])
    target_i = cache["bars"].index.get_loc(index[130])
    control_i = cache["bars"].index.get_loc(pairs.candidate_time.iloc[0])
    assert ready[target_i] and bucket[target_i] == bucket[control_i]
    # The deliberate entry-bar low means the frozen initial stop is hit; this proves the reused
    # single-event replay produced a realized, costed control rather than an account return.
    assert pairs.control_net_r.iloc[0] < 0 and pairs.net_r_difference.iloc[0] == pytest.approx(1.5 - pairs.control_net_r.iloc[0])


def test_v7_controls_require_bb_ready_but_v1_common_execution_does_not():
    cache, index = _cache()
    targets = _target(index, variant="v7_bb_long")
    cache["bars"]["ready"] = False
    cache["bars"].loc[index[120], "ready"] = True
    cache["bars"].loc[index[121], "low"] = 90.0
    v1 = match_stream_controls(cache, targets, variant="v1_common_execution_long",
                               fold_start=index[120], fold_end=index[-1] + pd.Timedelta(hours=1))
    v7 = match_stream_controls(cache, targets, variant="v7_bb_long",
                               fold_start=index[120], fold_end=index[-1] + pd.Timedelta(hours=1))
    assert bool(v1.matched.iloc[0])
    assert not bool(v7.matched.iloc[0]) and v7.reason.iloc[0] == "no_exact_causal_match"


def test_controls_are_without_replacement_and_exclude_all_target_bars():
    cache, index = _cache(155)
    cache["bars"]["ready"] = False
    cache["bars"].loc[[index[120], index[121], index[130], index[131]], "ready"] = True
    cache["bars"].loc[index[122], "low"] = 90.0
    cache["bars"].loc[index[123], "low"] = 90.0
    targets = pd.concat([_target(index, 130), _target(index, 131)], ignore_index=True)
    targets["target_id"] = ["one", "two"]
    pairs = match_stream_controls(cache, targets, variant="v1_common_execution_long",
                                  fold_start=index[120], fold_end=index[-1] + pd.Timedelta(hours=1))
    matched = pairs.loc[pairs.matched]
    assert matched.candidate_time.is_unique
    assert not set(matched.candidate_time).intersection(set(targets.signal_bar_open))


def test_controls_can_exclude_target_bars_from_another_variant():
    cache, index = _cache(155)
    cache["bars"]["ready"] = False
    cache["bars"].loc[[index[120], index[130]], "ready"] = True
    cache["bars"].loc[index[121], "low"] = 90.0
    target = _target(index, 130)
    excluded = pd.Series([index[120], index[130]])
    pairs = match_stream_controls(
        cache,
        target,
        variant="v1_common_execution_long",
        fold_start=index[120],
        fold_end=index[-1] + pd.Timedelta(hours=1),
        excluded_target_times=excluded,
    )
    assert not bool(pairs.matched.iloc[0])
    assert pairs.reason.iloc[0] == "no_exact_causal_match"


def test_target_reader_rejects_boundary_row_before_outcome_csv_parse(tmp_path, monkeypatch):
    import gzip
    path = tmp_path / "candidate_context.csv.gz"
    header = ["variant", "timeframe_min", "signal_bar_open", "entry_time", "exit_time", "side",
              "censored", "net_r", "net_return", "venue", "symbol", "segment"]
    safe = ["v1_common_execution_long", "60", "2025-09-09T22:00:00Z", "2025-09-09T23:00:00Z",
            "2025-09-09T23:30:00Z", "1", "False", "1.0", ".01", "binance", "BTCUSDT", "0"]
    future = ["v1_common_execution_long", "60", "2025-09-10T00:00:00Z", "2025-09-10T01:00:00Z",
              "2025-09-10T02:00:00Z", "1", "False", "\udcff", "\udcff", "binance", "BTCUSDT", "0"]
    payload = ",".join(header).encode() + b"\n" + ",".join(safe).encode() + b"\n" + ",".join(future).encode("utf-8", "surrogateescape") + b"\n"
    path.write_bytes(gzip.compress(payload))
    parsed = False

    def forbidden(*args, **kwargs):
        nonlocal parsed
        parsed = True
        raise AssertionError("full outcome parser must not run")

    monkeypatch.setattr(pd, "read_csv", forbidden)
    with pytest.raises(ValueError, match="outside the development fold"):
        _read_targets(path)
    assert not parsed


def test_summary_exposes_baseline_quartiles_frozen_rule_reasons_and_deterministic_signflip():
    targets = pd.DataFrame({"target_id": ["a", "b", "c", "d"], "joint_breadth": [.1, .2, .8, .9],
                            "joint_delta_60m": [-1., .1, .2, .3]})
    pairs = pd.DataFrame({"target_id": ["a", "b", "c", "d"], "matched": [True, False, True, True],
                          "reason": ["matched", "fail_closed:receipt missing", "matched", "matched"],
                          "calendar_month": ["2025-01", "2025-01", "2025-02", "2025-03"],
                          "target_net_r": [1., np.nan, 2., 3.], "control_net_r": [0., np.nan, 1., 1.],
                          "net_r_difference": [1., np.nan, 1., 2.]})
    summary = summarize_controls(pairs, targets)
    assert {("baseline", "all"), ("joint_breadth", "bottom_quartile"),
            ("joint_breadth", "top_quartile"), ("joint_delta_60m", "positive_rule")} <= set(zip(summary.metric, summary.slice))
    baseline = summary.loc[(summary.metric == "baseline") & (summary.slice == "all")].iloc[0]
    assert baseline.targets == 4 and baseline.matched == 3 and baseline.match_rate == pytest.approx(.75)
    assert "fail_closed:receipt missing" in baseline.unmatched_reasons
    assert paired_sign_flip_p(pd.Series([1., 2.])) == paired_sign_flip_p(pd.Series([1., 2.]))
    assert np.isnan(paired_sign_flip_p(pd.Series([1.])))
    assert month_cluster_sign_flip_p(pairs.loc[pairs.matched]) == paired_sign_flip_p(pd.Series([1., 1., 2.]))


def test_month_cluster_signflip_uses_sums_to_match_event_weighted_effect():
    pairs = pd.DataFrame({
        "calendar_month": ["2025-01"] * 100 + ["2025-02"],
        "net_r_difference": [1.0] * 100 + [-10.0],
    })
    assert month_cluster_sign_flip_p(pairs) == paired_sign_flip_p(pd.Series([100.0, -10.0]))


def test_signflip_batches_match_the_former_single_array_seeded_draw_order():
    values, seed, draws = np.array([-.5, .25, 1.5, 2.]), 37, 97
    observed = abs(float(values.mean()))
    signs = np.random.default_rng(seed).choice((-1.0, 1.0), size=(draws, len(values)))
    expected = float((1 + np.sum(np.abs((signs * values).mean(axis=1)) >= observed)) / (draws + 1))
    assert paired_sign_flip_p(pd.Series(values), seed=seed, draws=draws, batch_draws=7) == expected

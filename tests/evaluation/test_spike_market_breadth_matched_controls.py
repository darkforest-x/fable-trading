"""Synthetic contracts for development-only SPIKE breadth matched controls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import yoyo.evaluation.spike_market_breadth_matched_controls as controls

from yoyo.evaluation.spike_market_breadth_matched_controls import (
    DEVELOPMENT_END,
    _ActiveSourcePrefix,
    _read_targets,
    _quartile_buckets,
    match_stream_controls,
    month_cluster_sign_flip_p,
    paired_sign_flip_p,
    summarize_controls,
)


def _reference_match_stream_controls(cache: dict, targets: pd.DataFrame, *, variant: str,
                                     seed: int = 0, fold_start: pd.Timestamp = DEVELOPMENT_END - pd.Timedelta(days=365),
                                     fold_end: pd.Timestamp = DEVELOPMENT_END,
                                     excluded_target_times: pd.Series | None = None) -> pd.DataFrame:
    """The pre-pool implementation retained as an exact semantic reference."""
    context = controls._variant_cache(cache, variant)
    bars, signals = context["bars"], context["signals"]
    bucket, bucket_ready = controls._quartile_buckets(bars)
    cadence = pd.Timedelta(minutes=int(bars.attrs["minutes"]))
    gap = pd.Series(context["data_gap"], index=bars.index).fillna(True).astype(bool)
    valid = np.isfinite(bars[["open", "high", "low", "close", "atr"]].to_numpy(float)).all(axis=1)
    next_contiguous = ~gap.shift(-1, fill_value=True).to_numpy(bool)
    next_valid = np.r_[valid[1:], False]
    history_continuous = ~gap.rolling(5, min_periods=5).max().fillna(1).to_numpy(bool)
    eligible = (bars.ready.fillna(False).to_numpy(bool) & bucket_ready & valid & next_valid & next_contiguous
                & history_continuous & (bars.atr.to_numpy(float) > 0) & (bars.close.to_numpy(float) > 0)
                & ((bars.index + cadence) >= fold_start) & ((bars.index + cadence) < fold_end))
    months = bars.index.strftime("%Y-%m").to_numpy()
    excluded_times = targets.signal_bar_open if excluded_target_times is None else excluded_target_times
    target_positions = {int(bars.index.get_loc(pd.Timestamp(stamp))) for stamp in excluded_times if pd.Timestamp(stamp) in bars.index}
    used_controls, replayed, rows = set(), {}, []
    for target in targets.sort_values(["signal_bar_open", "target_id"], kind="stable").itertuples(index=False):
        stamp, side = pd.Timestamp(target.signal_bar_open), int(target.side)
        identity = {name: getattr(target, name) for name in controls.IDENTITY if hasattr(target, name)}
        base = {**identity, "target_id": target.target_id, "target_time": stamp, "side": side, "variant": variant,
                "matched": False, "reason": "unmatched"}
        if stamp not in bars.index:
            rows.append({**base, "reason": "target_not_in_rebuilt_segment"}); continue
        i = int(bars.index.get_loc(stamp))
        if not bucket_ready[i]:
            rows.append({**base, "reason": "target_bucket_unavailable"}); continue
        allowed = eligible.copy()
        opposite = signals.short_signal.to_numpy(bool) if side == 1 else signals.long_signal.to_numpy(bool)
        allowed &= ~opposite
        if target_positions:
            allowed[np.fromiter(target_positions, dtype=int)] = False
        if used_controls:
            allowed[np.fromiter(used_controls, dtype=int)] = False
        choices = np.flatnonzero(allowed & (months == months[i]) & (bucket == bucket[i]))
        if not len(choices):
            rows.append({**base, "reason": "no_exact_causal_match"}); continue
        selected = int(choices[int(hashlib.sha256(
            f"{seed}|{target.target_id}|{stamp.isoformat()}".encode("utf-8")).hexdigest(), 16) % len(choices)])
        used_controls.add(selected)
        key = (selected, side)
        if key not in replayed:
            replayed[key] = controls.evaluate_single_control(context, selected, side, tick=float(context["tick"]),
                                                              fold_start=fold_start, fold_end=fold_end)
        control = replayed[key]
        if control is None or bool(control.get("censored", False)):
            rows.append({**base, "reason": "control_unresolved"}); continue
        rows.append({**base, "matched": True, "reason": "matched", "candidate_time": bars.index[selected],
                     "calendar_month": months[i], "volatility_quartile": int(bucket[i]),
                     "target_net_r": float(target.net_r), "control_net_r": float(control["net_r"]),
                     "net_r_difference": float(target.net_r) - float(control["net_r"]),
                     "target_net_return": float(target.net_return), "control_net_return": float(control["net_return"]),
                     "net_return_difference": float(target.net_return) - float(control["net_return"])})
    return pd.DataFrame(rows)


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


def test_pooled_controls_match_reference_for_multiple_targets_cross_variant_exclusions_and_no_match(monkeypatch):
    cache, index = _cache(360)
    # Side -1 has no legal candidate because every bar has its opposite long
    # signal. Side +1 still has a broad, deterministic pool.
    cache["v1"]["long_signal"] = True
    targets = pd.DataFrame({
        "target_id": ["late", "first", "no-match"],
        "signal_bar_open": [index[190], index[180], index[200]],
        "side": [1, 1, -1], "net_r": [1.5, 2.0, -1.0], "net_return": [.03, .04, -.02],
    })
    excluded = pd.Series([index[170], index[190], index[200]])

    def evaluated(_context, selected, side, **_kwargs):
        return {"censored": False, "net_r": selected / 100., "net_return": side * selected / 10_000.}

    monkeypatch.setattr(controls, "evaluate_single_control", evaluated)
    kwargs = dict(variant="v1_common_execution_long", seed=17, fold_start=index[120],
                  fold_end=index[-1] + pd.Timedelta(hours=1), excluded_target_times=excluded)
    expected = _reference_match_stream_controls(cache, targets, **kwargs)
    actual = match_stream_controls(cache, targets, **kwargs)
    assert not set(actual.loc[actual.matched, "candidate_time"]).intersection(set(excluded))
    assert actual.reason.tolist()[-1] == "no_exact_causal_match"
    columns = ["target_id", "candidate_time", "reason", "matched", "control_net_r", "control_net_return",
               "net_r_difference", "net_return_difference"]
    pd.testing.assert_frame_equal(actual.reindex(columns=columns), expected.reindex(columns=columns))


def test_active_source_prefix_loads_once_across_timeframes_and_replaces_on_source_change(tmp_path, monkeypatch):
    import hashlib
    import yoyo.evaluation.spike_market_breadth_matched_controls as controls

    first, second = tmp_path / "first.csv.gz", tmp_path / "second.csv.gz"
    first_expected = hashlib.sha256(b"first-development-prefix").hexdigest()
    second_expected = hashlib.sha256(b"second-development-prefix").hexdigest()
    first_raw, second_raw = pd.DataFrame({"source": ["first"]}), pd.DataFrame({"source": ["second"]})
    calls: list[str] = []

    def fake_load(path: str, *, prefix_digest):
        calls.append(path)
        if Path(path) == first.resolve():
            prefix_digest.update(b"first-development-prefix")
            return first_raw
        assert Path(path) == second.resolve()
        prefix_digest.update(b"second-development-prefix")
        return second_raw

    def fake_rebuild(raw, *, minutes, segment, tick):
        return {"raw": raw, "minutes": minutes, "segment": segment, "tick": tick}

    monkeypatch.setattr(controls, "_load_bars", fake_load)
    monkeypatch.setattr(controls, "_rebuild_cache", fake_rebuild)
    active = _ActiveSourcePrefix()
    first_row, second_row = {"source_path": str(first)}, {"source_path": str(second)}
    rebuilt = [active.rebuild(first_row, minutes=minutes, segment=segment, tick=.1,
                              expected_prefix_sha256=first_expected)
               for minutes, segment in ((30, 0), (60, 1), (240, 2))]

    assert calls == [str(first.resolve())]
    assert [item["raw"] for item in rebuilt] == [first_raw, first_raw, first_raw]
    replacement = active.rebuild(second_row, minutes=30, segment=0, tick=.01,
                                 expected_prefix_sha256=second_expected)
    assert calls == [str(first.resolve()), str(second.resolve())]
    assert active.source_path == str(second.resolve())
    assert active.bars is second_raw
    assert replacement["raw"] is second_raw


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


def test_target_reader_normalizes_decimal_identity_fields_to_integers(tmp_path):
    import gzip
    path = tmp_path / "candidate_context.csv.gz"
    header = ["variant", "timeframe_min", "signal_bar_open", "entry_time", "exit_time", "side",
              "censored", "net_r", "net_return", "venue", "symbol", "segment"]
    safe = ["v1_common_execution_long", "60.0", "2025-09-09T22:00:00Z", "2025-09-09T23:00:00Z",
            "2025-09-09T23:30:00Z", "1", "False", "1.0", ".01", "binance", "BTCUSDT", "0.0"]
    path.write_bytes(gzip.compress((",".join(header) + "\n" + ",".join(safe) + "\n").encode()))

    table = _read_targets(path)

    assert table.timeframe_min.tolist() == [60]
    assert table.segment.tolist() == [0]
    assert pd.api.types.is_integer_dtype(table.timeframe_min)
    assert pd.api.types.is_integer_dtype(table.segment)


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


def test_run_manifest_pins_pairs_summary_receipts_and_generator_code(tmp_path, monkeypatch):
    stage, v7_raw, output = tmp_path / "stage", tmp_path / "v7", tmp_path / "output"
    stage.mkdir(); v7_raw.mkdir()
    (stage / "candidate_context.csv.gz").write_bytes(b"opaque candidates")
    (stage / "source_manifest.csv").write_text("opaque source\n")
    (v7_raw / "input_manifest.json").write_text(json.dumps({"streams": []}))
    empty_targets = pd.DataFrame(columns=["venue", "symbol", "timeframe_min", "segment", "variant"])
    monkeypatch.setattr(controls, "_read_targets", lambda _path: empty_targets)
    monkeypatch.setattr(controls, "_source_rows", lambda _path: {})
    monkeypatch.setattr(controls, "_v7_streams", lambda _path: {})

    controls.run(stage, v7_raw, output)

    manifest = json.loads((output / "manifest.json").read_text())
    assert set(manifest["outputs"]) == {"matched_control_pairs.csv.gz", "matched_control_summary.csv", "control_receipts.csv"}
    for name, expected in manifest["outputs"].items():
        assert expected == hashlib.sha256((output / name).read_bytes()).hexdigest()
    assert manifest["study_code_sha256"] == hashlib.sha256(Path(controls.__file__).read_bytes()).hexdigest()

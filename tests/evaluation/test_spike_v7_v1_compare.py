"""Focused contracts for the receipt-bound V1/V6/V7 comparison runner."""
from __future__ import annotations

import json
import hashlib

import numpy as np
import pandas as pd
import pytest

import yoyo.evaluation.spike_v7_v1_compare as compare


def _bars(n: int = 720) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 100.0 + np.sin(np.arange(n) / 9)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1.0, "quote_volume": 100.0}, index=index)


def test_v7_requires_all_twelve_prior_thresholds_and_excludes_signal_bar():
    bars = _bars()
    diagnostic = compare.v7_diagnostics(bars, data_gap=pd.Series(False, index=bars.index))
    # BB200 plus the prior 500-width threshold becomes available at 699.  The
    # signal at 711 is the first one with threshold-ready bars 699..710.
    assert not diagnostic.v7_ready.iloc[710]
    assert diagnostic.v7_ready.iloc[711]
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=bars.index)
    signals.iloc[711, 0] = True
    assert compare.v7_admission(signals, diagnostic).iloc[711]
    # A compression created on the signal bar itself cannot supply the prior
    # window.  The 12-bar history remains the only admission evidence.
    only_current = diagnostic.copy()
    only_current["prior_squeeze_run3"] = False
    only_current.iloc[711, only_current.columns.get_loc("bb_compressed")] = True
    assert not compare.v7_admission(signals, only_current).iloc[711]


def test_variant_admissions_keep_raw_v6_opposite_events_available_for_exit():
    index = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    v1 = pd.DataFrame({"long_signal": [True, False, False], "short_signal": False}, index=index)
    v6 = pd.DataFrame({"long_signal": [True, False, False], "short_signal": [False, True, False]}, index=index)
    v1["short_signal"] = v6.short_signal  # replay_stream installs the shared raw V6 exit feed
    v7 = pd.Series([True, False, False], index=index)
    variants = compare._variant_admissions(v1, v6, v7, pd.Series([True, True, True], index=index))
    signals, admission = variants["v7_bb_long"]
    assert signals.short_signal.iloc[1]  # simulator can still schedule its reverse exit
    assert admission.tolist() == [True, False, False]
    _, both = variants["v7_bb_both"]
    assert both.tolist() == [True, False, False]
    v1_signals, v1_admission = variants["v1_common_execution_long"]
    assert v1_signals.short_signal.iloc[1]
    assert not v1_admission.iloc[1]  # raw V6 short is exit feed, never a V1 short admission


def test_v1_common_same_bar_raw_short_is_suppressed_and_explicitly_diagnosed():
    index = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    native = pd.DataFrame({"long_signal": [True, True], "short_signal": False}, index=index)
    raw_short = pd.Series([True, False], index=index)
    common, conflicts = compare.v1_common_with_v6_exit_feed(native, raw_short)
    assert conflicts.tolist() == [True, False]
    assert common.native_v1_long_event.tolist() == [True, True]
    assert common.raw_v6_short_exit_feed.tolist() == [True, False]
    assert common.suppressed_v1_entry_conflict.tolist() == [True, False]
    assert common.long_signal.tolist() == [False, True]
    assert common.short_signal.tolist() == [True, False]


def test_replay_conflicts_align_to_truncated_featured_clock_when_source_has_end_tail(monkeypatch):
    index = pd.date_range(compare.END - pd.Timedelta(minutes=60), periods=4, freq="30min", tz="UTC")
    source = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=index)

    def fake_features(frame):
        out = frame.copy()
        out["atr"] = 1.0
        return out

    def fake_v6(frame, minutes):
        return pd.DataFrame({"long_signal": False, "short_signal": [True, False, False, False]}, index=frame.index)

    def fake_diag(frame, *, data_gap):
        return pd.DataFrame({"bb_basis": 100.0, "bb_std_ddof0": 1.0, "bb_width": .04,
                             "bb_width_p10_prior500": .03, "bb_compressed": False,
                             "v7_ready": False, "prior_squeeze_run3": False}, index=frame.index)

    def fake_v1(frame, tick):
        return pd.DataFrame({"long_signal": [True, False, False, False], "short_signal": False}, index=frame.index)

    def fake_simulator(*args, **kwargs):
        return pd.DataFrame({"side": pd.Series(dtype=int)}), pd.DataFrame()

    monkeypatch.setattr(compare, "v1_features", fake_features)
    monkeypatch.setattr(compare, "v6_signals", fake_v6)
    monkeypatch.setattr(compare, "v7_diagnostics", fake_diag)
    monkeypatch.setattr(compare, "v7_admission", lambda signals, diagnostic: pd.Series(False, index=signals.index))
    monkeypatch.setattr(compare, "v1_common_signals", fake_v1)
    monkeypatch.setattr(compare, "simulate_v6_variant", fake_simulator)
    stream = {"venue": "binance", "symbol": "TEST", "asset": "TEST", "minutes": 30, "tick": .01,
              "segment": 0, "source_path": "/tmp/source", "source_sha256": "a" * 64, "bars": source,
              "coverage_receipt": {"path": "/tmp/receipt", "sha256": "b" * 64, "source_sha256": "a" * 64}}
    _, _, conflicts, receipt, cache = compare.replay_stream(stream)
    assert len(conflicts) == 1
    assert conflicts.signal_bar_open.iloc[0] == compare.END - pd.Timedelta(minutes=60)
    assert receipt.v1_long_raw_v6_short_conflicts.iloc[0] == 1
    assert cache["bb"].columns.tolist() == ["bb_basis", "bb_std_ddof0", "bb_width", "bb_width_p10_prior500",
                                              "bb_compressed", "prior_squeeze_run3", "v7_ready"]


def test_evaluation_window_uses_confirm_time_and_prevents_carry_in():
    start = compare.START
    index = pd.date_range(start - pd.Timedelta(minutes=120), periods=7, freq="30min", tz="UTC")
    in_window = compare.evaluation_window(index, 30)
    assert not in_window.iloc[1]  # close is one bar before START
    assert in_window.iloc[3]  # close is exactly START
    end_index = pd.DatetimeIndex([compare.END - pd.Timedelta(minutes=30)])
    assert not compare.evaluation_window(end_index, 30).iloc[0]

    # A raw event before START would create a position without the admission
    # mask.  Feeding only permitted events into the shared executor leaves no
    # pre-window position to carry through the evaluation boundary.
    close = np.linspace(100.0, 106.0, len(index))
    frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                          "close": close, "atr": 1.0}, index=index)
    frame.attrs["minutes"] = 30
    raw = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    raw.iloc[1, raw.columns.get_loc("long_signal")] = True
    masked = raw.copy()
    masked.loc[~in_window, ["long_signal", "short_signal"]] = False
    _, trades = compare.simulate_v6_variant(
        frame, masked, admission=masked.long_signal, variant="boundary", data_gap=pd.Series(False, index=index),
        spec=compare.ExecutionSpec(tick=.01),
    )
    assert trades.empty


def test_uncovered_catalog_row_without_tick_does_not_mask_evaluated_tick():
    catalog = pd.DataFrame([
        {"venue": "binance", "symbol": "COVERED", "eligible": True, "tick": .01},
        {"venue": "binance", "symbol": "UNUSED", "eligible": True, "tick": np.nan},
    ])
    assert compare._catalog_tick_map(catalog) == {("binance", "COVERED"): .01}


def test_pre_registered_exclusions_must_exactly_match_selected_invalid_ticks():
    coverage = pd.DataFrame([
        {"venue": "okx", "symbol": "SATS", "timeframe_min": minutes, "status": "evaluated"}
        for minutes in (30, 60, 240)
    ])
    catalog = pd.DataFrame([{"venue": "okx", "symbol": "SATS", "eligible": True, "tick": 0.0}])
    config = {"excluded_cells": [
        {"venue": "okx", "symbol": "SATS", "minutes": minutes, "reason": "frozen tick is zero"}
        for minutes in (30, 60, 240)
    ]}
    excluded = compare._excluded_cells(config, coverage, catalog)
    assert set(excluded) == {("okx", "SATS", 30), ("okx", "SATS", 60), ("okx", "SATS", 240)}
    config["excluded_cells"].pop()
    with pytest.raises(ValueError, match="exactly three"):
        compare._excluded_cells(config, coverage, catalog)


def test_tail_retention_records_same_entry_rate_and_realized_ten_r():
    stamp = pd.Timestamp("2025-01-01T00:00:00Z")
    signals = pd.DataFrame([
        {"variant": "v6_unfiltered_long", "side": 1, "signal_bar_open": stamp,
         "signal_role": "entry_candidate", "admitted_for_entry": True},
        {"variant": "v7_bb_long", "side": 1, "signal_bar_open": stamp,
         "signal_role": "entry_candidate", "admitted_for_entry": True},
        {"variant": "v6_unfiltered_both", "side": 1, "signal_bar_open": stamp,
         "signal_role": "entry_candidate", "admitted_for_entry": True},
        {"variant": "v7_bb_both", "side": 1, "signal_bar_open": stamp,
         "signal_role": "entry_candidate", "admitted_for_entry": False},
    ])
    trades = pd.DataFrame([
        {"variant": "v6_unfiltered_long", "side": 1, "signal_bar_open": stamp, "censored": False,
         "net_r": 11.0, "net_return": .1, "gross_return": .102, "mfe_r": 12.0},
        {"variant": "v6_unfiltered_both", "side": 1, "signal_bar_open": stamp, "censored": False,
         "net_r": 11.0, "net_return": .1, "gross_return": .102, "mfe_r": 12.0},
    ])
    stream = {"venue": "binance", "symbol": "TEST", "asset": "TEST", "minutes": 30, "segment": 0}
    _, _, retention = compare._stream_summaries(stream, signals, trades)
    long = retention.loc[retention.baseline.eq("v6_unfiltered_long")].iloc[0]
    both = retention.loc[retention.baseline.eq("v6_unfiltered_both")].iloc[0]
    assert (long.baseline_entry_admitted, long.same_entry_v7_admitted, long.same_entry_v7_admission_rate) == (1, 1, 1.0)
    assert (long.baseline_realized_net_r_ge_10, long.same_realized_trade_v7_admitted) == (1, 1)
    assert (both.same_entry_v7_admitted, both.same_realized_trade_v7_admitted) == (0, 0)


def test_all_empty_trade_arms_write_headered_stream_summaries():
    stream = {"venue": "okx", "symbol": "SATS", "asset": "SATS", "minutes": 30, "segment": 0}
    signals = pd.DataFrame(columns=["variant", "side", "signal_role", "admitted_for_entry", "signal_bar_open"])
    signal_summary, trade_summary, retention = compare._stream_summaries(stream, signals, pd.DataFrame())
    assert signal_summary.columns.tolist() == list(compare.SIGNAL_SUMMARY_COLUMNS)
    assert trade_summary.columns.tolist() == list(compare.TRADE_SUMMARY_COLUMNS)
    assert retention.columns.tolist() == list(compare.RETENTION_COLUMNS)
    assert len(retention) == 2


def test_actual_eight_arm_replay_tags_variant_and_actual_admission_before_summary():
    index = pd.date_range("2025-01-01", periods=8, freq="30min", tz="UTC")
    frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0}, index=index)
    frame.attrs["minutes"] = 30
    v6 = pd.DataFrame({"long_signal": [False, False, False, False, True, False, False, False],
                       "short_signal": [False, False, False, False, False, False, True, False]}, index=index)
    v1 = pd.DataFrame({"long_signal": v6.long_signal, "short_signal": v6.short_signal}, index=index)
    v7 = pd.Series(False, index=index)  # rejects the raw V6 long at i=4
    stream = {"venue": "binance", "symbol": "TEST", "asset": "TEST", "minutes": 30,
              "segment": 0, "source_sha256": "a" * 64}
    ledgers, trades = [], []
    for variant, (signals, admission) in compare._variant_admissions(v1, v6, v7, pd.Series(True, index=index)).items():
        ledger, executed = compare.simulate_v6_variant(
            frame, signals, admission=admission, variant=variant, data_gap=pd.Series(False, index=index),
            spec=compare.ExecutionSpec(tick=.01),
        )
        compare._tag_variant_results(ledger, executed, variant=variant, admission=admission,
                                     signal_index=signals.index, stream=stream)
        ledger["signal_role"] = "entry_candidate"
        if variant.startswith("v1_"):
            ledger.loc[ledger.side.eq(-1), "signal_role"] = "raw_v6_short_exit_feed"
        ledgers.append(ledger)
        trades.append(executed)
    signals_out, trades_out = pd.concat(ledgers, ignore_index=True), pd.concat(trades, ignore_index=True)
    assert set(signals_out.variant) == set(compare.VARIANTS)
    rejected = signals_out.loc[(signals_out.variant == "v7_bb_long") & signals_out.side.eq(1)]
    assert len(rejected) == 1 and not rejected.admitted_for_entry.iloc[0]
    closed_long = trades_out.loc[(trades_out.variant == "v6_unfiltered_long") & ~trades_out.censored]
    assert closed_long.side.tolist() == [1]
    signal_summary, trade_summary, _ = compare._stream_summaries(stream, signals_out, trades_out)
    assert set(signal_summary.variant) == set(compare.VARIANTS)
    assert "v6_unfiltered_long" in set(trade_summary.variant)


def test_run_identity_refuses_cross_builder_resume_and_allows_empty_preflight_retry(tmp_path):
    config = json.loads(compare.CONFIG_PATH.read_text())
    identity = compare.run_identity(config)
    assert identity["config_sha256"] == compare.sha256(compare.CONFIG_PATH)
    assert identity["pine_sha256"] == config["pine_sha256"]
    assert len(identity["source_code_sha256"]) == 8

    empty_retry = tmp_path / "empty_retry"
    empty_retry.mkdir()
    compare.ensure_run_identity(empty_retry, identity)
    assert json.loads((empty_retry / "run_identity.json").read_text()) == identity
    compare.ensure_run_identity(empty_retry, identity)
    changed = {**identity, "config_sha256": "0" * 64}
    with pytest.raises(ValueError, match="identity changed"):
        compare.ensure_run_identity(empty_retry, changed)

    old_output = tmp_path / "old_output"
    old_output.mkdir()
    (old_output / "input_manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="no run_identity"):
        compare.ensure_run_identity(old_output, identity)


def test_completed_requires_identity_completion_and_all_stream_files(tmp_path):
    streams_root = tmp_path / "streams"
    folder = streams_root / "stream_a"
    folder.mkdir(parents=True)
    for filename in compare.STREAM_REQUIRED_FILES:
        (folder / filename).write_text("ok")
    identity_sha = "a" * 64
    (folder / "completion.json").write_text(json.dumps({
        "key": "stream_a", "status": "complete", "run_identity_sha256": identity_sha,
    }))
    # A progress record alone never participates in the resume decision.
    (tmp_path / "progress.jsonl").write_text(json.dumps({"key": "missing_stream"}) + "\n")
    assert compare._completed(streams_root, identity_sha) == {"stream_a"}
    staging = streams_root / ".stream_b.staging"
    staging.mkdir()
    for filename in compare.STREAM_REQUIRED_FILES:
        (staging / filename).write_text("ok")
    (staging / "completion.json").write_text(json.dumps({
        "key": "stream_b", "status": "complete", "run_identity_sha256": identity_sha,
    }))
    wrong_name = streams_root / "not_its_key"
    wrong_name.mkdir()
    for filename in compare.STREAM_REQUIRED_FILES:
        (wrong_name / filename).write_text("ok")
    (wrong_name / "completion.json").write_text(json.dumps({
        "key": "stream_c", "status": "complete", "run_identity_sha256": identity_sha,
    }))
    assert compare._completed(streams_root, identity_sha) == {"stream_a"}
    (folder / "trades.csv.gz").unlink()
    assert compare._completed(streams_root, identity_sha) == set()
    (folder / "trades.csv.gz").write_text("ok")
    (folder / "completion.json").write_text(json.dumps({
        "key": "stream_a", "status": "complete", "run_identity_sha256": "b" * 64,
    }))
    assert compare._completed(streams_root, identity_sha) == set()


def test_covered_streams_reads_only_evaluated_receipt_bound_cells(tmp_path, monkeypatch):
    source_data, source_results = tmp_path / "data", tmp_path / "results"
    (source_data / "market_receipts" / "binance").mkdir(parents=True)
    normalized = source_data / "normalized" / "binance" / "TESTUSDT_30m.csv.gz"
    normalized.parent.mkdir(parents=True)
    raw = _bars(800).copy()
    raw.index = pd.date_range("2024-01-01", periods=len(raw), freq="30min", tz="UTC")
    raw.to_csv(normalized, compression="gzip")
    receipt = {"venue": "binance", "symbol": "TESTUSDT", "asset": "TEST", "tick": 0.01,
               "status": "complete", "path": str(normalized)}
    (source_data / "market_receipts" / "binance" / "TESTUSDT.json").write_text(json.dumps(receipt))
    pd.DataFrame([{"venue": "binance", "symbol": "TESTUSDT", "asset": "TEST", "eligible": True, "tick": 0.01}]).to_json(
        source_data / "catalog.json", orient="records"
    )
    source_results.mkdir()
    pd.DataFrame([
        {"venue": "binance", "symbol": "TESTUSDT", "asset": "TEST", "timeframe_min": 30, "status": "evaluated"},
        {"venue": "binance", "symbol": "TESTUSDT", "asset": "TEST", "timeframe_min": 60, "status": "source_gapped"},
    ]).to_csv(source_results / "coverage_limited.csv", index=False)
    coverage_receipt = source_results / "covered_ledgers" / "binance" / "TESTUSDT_30m.csv.receipt.json"
    coverage_receipt.parent.mkdir(parents=True)
    coverage_receipt.write_text(json.dumps({"source_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest()}))
    monkeypatch.setattr(compare, "SOURCE_DATA", source_data)
    monkeypatch.setattr(compare, "SOURCE_RESULTS", source_results)
    streams = list(compare.covered_streams())
    assert len(streams) == 1
    assert streams[0]["minutes"] == 30
    assert streams[0]["source_path"] == str(normalized.resolve())
    assert len(streams[0]["bars"]) == 800
    # Per-cell historical receipt is authoritative: recomputing only a new
    # current hash cannot silently bless changed source bytes.
    raw.iloc[0, raw.columns.get_loc("close")] += 1
    raw.to_csv(normalized, compression="gzip")
    with pytest.raises(ValueError, match="source hash drift"):
        list(compare.covered_streams())

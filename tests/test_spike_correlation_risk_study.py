"""Focused causal tests for the frozen SPIKE correlation-risk study."""
from __future__ import annotations

import hashlib
import json
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_correlation_risk_study as study
from yoyo.evaluation.spike_correlation_risk_study import ReturnSource, apply_policy, trailing_returns


CONFIG = {
    "source_timeframe_min": 30,
    "lookback_calendar_days": 30,
    "lookback_observations": 1440,
    "max_open_positions": 5,
    "correlation_threshold": 0.8,
}


def _source(asset: str, *, values: np.ndarray | None = None) -> ReturnSource:
    index = pd.date_range("2024-01-01T00:00:00Z", periods=80 * 48, freq="30min")
    data = np.linspace(-0.02, 0.02, len(index)) if values is None else values
    return ReturnSource(asset=asset, venue="binance", stream_key=f"binance-{asset}", returns=pd.Series(data, index=index))


def _candidate(key: str, asset: str, entry: str, exit_time: str, *, net_r: float = 1.0) -> dict:
    return {
        "candidate_key": key, "source_event_id": key, "event_id": f"event-{key}", "arm": "v7",
        "period": "development", "asset": asset, "side": 1, "timeframe_min": 30,
        "stream_key": key, "signal_bar_open": pd.Timestamp(entry) - pd.Timedelta(minutes=30),
        "availability_time": pd.Timestamp(entry), "entry_time": pd.Timestamp(entry), "exit_time": pd.Timestamp(exit_time),
        "trade_id": f"trade-{key}", "executed": True, "censored": False, "net_r": net_r,
        "net_return": net_r / 10, "entry_price": 100.0, "initial_risk": 10.0, "exit_reason": "frozen",
        "trade_clock_available": True, "admission_clock_available": True, "input_unavailable_reason": None,
    }


def test_trailing_window_excludes_return_available_exactly_at_entry():
    entry = pd.Timestamp("2024-02-01T00:00:00Z")
    source = _source("A")
    source = ReturnSource("A", "binance", "a", source.returns.copy())
    source.returns.loc[entry] = 99.0  # This bar closes exactly at entry; it is not available strictly before it.
    values = trailing_returns(source, entry, CONFIG)
    assert values is not None
    assert len(values) == 1440
    assert values[-1] != 99.0


def test_correlation_gate_rejects_perfectly_correlated_open_different_asset_without_outcomes():
    entry_a = "2024-02-01T00:00:00Z"
    entry_b = "2024-02-01T01:00:00Z"
    candidates = pd.DataFrame([
        _candidate("a", "A", entry_a, "2024-02-05T00:00:00Z", net_r=-99.0),
        _candidate("b", "B", entry_b, "2024-02-05T00:00:00Z", net_r=999.0),
    ])
    common = _source("A").returns
    sources = {"A": ReturnSource("A", "binance", "a", common), "B": ReturnSource("B", "okx", "b", common.copy())}
    first = apply_policy(candidates, "event_leaders_max_open_5_corr_30d", sources, CONFIG).set_index("candidate_key")
    changed = candidates.copy()
    changed.loc[:, "net_r"] = [-1_000_000.0, 1_000_000.0]
    second = apply_policy(changed, "event_leaders_max_open_5_corr_30d", sources, CONFIG).set_index("candidate_key")
    assert first.loc["a", "accepted"]
    assert first.loc["b", "blocked_reason"] == "correlation_ge_0_80"
    assert first.loc["b", "max_trailing_correlation"] == 1.0
    assert first.accepted.to_dict() == second.accepted.to_dict()


def test_missing_history_fails_closed_only_for_the_correlation_policy():
    candidates = pd.DataFrame([_candidate("a", "A", "2024-02-01T00:00:00Z", "2024-02-02T00:00:00Z")])
    cap = apply_policy(candidates, "event_leaders_max_open_5", {}, CONFIG)
    corr = apply_policy(candidates, "event_leaders_max_open_5_corr_30d", {}, CONFIG)
    assert cap.iloc[0].accepted
    assert corr.iloc[0].blocked_reason == "correlation_history_unavailable"
    assert corr.iloc[0].correlation_history_status == "candidate_history_unavailable"


def test_max_five_cap_uses_stable_candidate_key_tie_breaking():
    rows = [
        _candidate(key, f"asset-{key}", "2024-02-01T00:00:00Z", "2024-02-02T00:00:00Z")
        for key in ("f", "b", "e", "a", "d", "c")
    ]
    audit = apply_policy(pd.DataFrame(rows), "event_leaders_max_open_5", {}, CONFIG).set_index("candidate_key")
    assert audit.accepted.sum() == 5
    assert not audit.loc["f", "accepted"]
    assert audit.loc["f", "blocked_reason"] == "max_open_positions_5"


def test_receipt_csv_tamper_fails_before_source_selection(tmp_path):
    streams = tmp_path / "streams"
    folder = streams / "binance_30m_deadbeef"
    folder.mkdir(parents=True)
    cache_payload = b"frozen-cache"
    (folder / "control_cache.pkl.gz").write_bytes(cache_payload)
    source_sha = "a" * 64
    control = {"key": folder.name, "cache_sha256": hashlib.sha256(cache_payload).hexdigest(), "source_sha256": source_sha}
    control_bytes = json.dumps(control).encode()
    (folder / "control_cache.receipt.json").write_bytes(control_bytes)
    metadata = "venue,symbol,asset,minutes,segment,source_sha256\nbinance,TESTUSDT,TEST,30,0," + source_sha + "\n"
    (folder / "receipt.csv").write_text(metadata)
    cache_inventory = [{"stream_key": folder.name, "receipt_sha256": hashlib.sha256(control_bytes).hexdigest(), "cache_sha256": control["cache_sha256"]}]
    metadata_inventory = [{"stream_key": folder.name, "receipt_csv_sha256": hashlib.sha256(metadata.encode()).hexdigest()}]
    config = {
        "expected_streams": 1,
        "cache_receipt_aggregate_sha256": study._canonical_sha(cache_inventory),
        "stream_metadata_aggregate_sha256": study._canonical_sha(metadata_inventory),
    }
    assert study.inventory_caches(streams, config)[folder.name]["asset"] == "TEST"
    (folder / "receipt.csv").write_text(metadata.replace(",TEST,30,", ",ALTERED,30,"))
    with pytest.raises(ValueError, match="metadata inventory"):
        study.inventory_caches(streams, config)


def test_result_manifest_rejects_a_changed_bound_result(tmp_path):
    output = tmp_path / "results"
    output.mkdir()
    for name in study.RESULT_FILES:
        (output / name).write_bytes(name.encode())
    entries = study.result_file_inventory(output)
    manifest = {"result_files": entries, "result_file_aggregate_sha256": study._canonical_sha(entries)}
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    study.verify_result_outputs(manifest_path)
    (output / "summary.csv").write_text("changed")
    with pytest.raises(ValueError, match="result file identity"):
        study.verify_result_outputs(manifest_path)

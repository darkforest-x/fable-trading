"""Focused regression tests for the bounded Profit3R morphology miner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import yoyo.datasets.ma_launch_snapshot_scan as snapshot
import yoyo.datasets.ma_profit_miner as miner
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_launch_owner_autofill_review import Profile


def _gates() -> dict[str, float]:
    return {
        "min_post1_progress_atr": 0.01,
        "min_post2_progress_atr": 0.02,
        "min_post3_progress_atr": 0.03,
        "min_post5_progress_atr": 0.05,
        "max_ma_envelope_atr": 99.0,
        "max_ma_spread_end_atr": 99.0,
        "max_core_body_atr": 99.0,
        "min_core_progress_atr": -99.0,
        "max_core_progress_atr": 99.0,
        "min_aligned_ma_slope_atr": -99.0,
        "max_minimum_close_to_ma_atr": 99.0,
        "max_close_to_ma_envelope_atr": 99.0,
        "max_body_to_ma_envelope_atr": 99.0,
    }


def _frame(periods: int = 80) -> pd.DataFrame:
    times = pd.date_range("2025-01-01T00:00:00Z", periods=periods, freq="15min")
    close = 100.0 + np.arange(periods, dtype=float) * 0.25
    frame = add_candidate_features(pd.DataFrame({
        "open_time": times, "open": close - 0.05, "high": close + 0.1,
        "low": close - 0.15, "close": close, "volume": 1.0,
    }))
    frame["_segment_id"] = 1
    return frame


def test_vectorized_coarse_and_snapshot_scan_have_same_synthetic_candidates(monkeypatch) -> None:
    """The speedup preserves the original c/c+2/c+5 continuation semantics."""

    frame = _frame()
    gates = _gates()
    profile = Profile(features=np.zeros(14), sequence=np.zeros((4, 10)))
    monkeypatch.setattr(miner, "morphology_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr(snapshot, "morphology_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr(miner, "profile_distance", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(snapshot, "profile_distance", lambda *args, **kwargs: 0.0)
    autofill = {
        "morphology_gate": gates,
        "reference_family": {"feature_scales": [1.0] * 14, "feature_weight": 1.0, "sequence_weight": 0.0, "max_distance": 1.0},
        "render": {"pre_core_context_bars": [12]},
    }
    context = miner.ScoreContext(autofill, {}, [profile], [], [], {}, [])
    spec = {"source_path": "synthetic.csv", "symbol": "SYN", "venue": "TEST", "bar_minutes": 15}
    cutoff = pd.Timestamp("2025-01-02T00:00:00Z")
    ours, _ = miner.scan_weak_source(frame, spec, cutoff=cutoff, context=context)
    start = frame.open_time.iloc[0] + pd.Timedelta(minutes=15 * 6)
    theirs, _ = snapshot.scan_weak_source(frame, {**spec, "path": spec["source_path"]}, start=start, end=cutoff - pd.Timedelta(minutes=15), autofill=autofill, references=[profile])
    assert [(row["direction"], row["core_bars"], row["confirm_i"]) for row in ours] == [
        (row["direction"], row["core_bars"], row["confirm_i"]) for row in theirs
    ]


def test_process_source_passes_only_confirmation_bar_to_strict_profile(tmp_path, monkeypatch) -> None:
    """Strict scoring receives no future candle even if the source has one."""

    source = tmp_path / "source.csv"
    raw = _frame(35).loc[:, ["open_time", "open", "high", "low", "close", "volume"]].copy()
    raw.to_csv(source, index=False)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = {"source_path": str(source), "symbol": "SYN", "venue": "TEST", "bar_minutes": 15, "sha256": source_sha}
    row = {
        "sample_id": "row", "event_id": "row", "profile_id": "row", "source_path": str(source),
        "symbol": "SYN", "venue": "TEST", "bar_minutes": 15, "direction": "LONG", "core_bars": 4,
        "source_core_start_i": 10, "source_core_end_i": 13, "source_comparison_anchor_i": 15,
        "confirm_i": 18, "box": {"h_norm": 0.0}, "similarity_distance": 0.0, "features": {},
        "core_start_time": pd.Timestamp(raw.open_time.iloc[10]).isoformat(),
        "core_end_time": pd.Timestamp(raw.open_time.iloc[13]).isoformat(),
        "confirmation_close_utc": (pd.Timestamp(raw.open_time.iloc[18]) + pd.Timedelta(minutes=15)).isoformat(),
    }
    monkeypatch.setattr(miner, "_repo_path", lambda value: Path(str(value)))
    monkeypatch.setattr(miner, "_relative", lambda path: str(path))
    monkeypatch.setattr(miner, "scan_weak_source", lambda *args, **kwargs: ([row], {}))
    seen: list[int] = []
    def fake_extract(frame, candidate, **kwargs):
        seen.append(len(frame))
        assert kwargs["visibility_end_exclusive"] == pd.Timestamp(row["confirmation_close_utc"]) + pd.Timedelta(nanoseconds=1)
        return object()
    monkeypatch.setattr(miner, "extract_profile", fake_extract)
    monkeypatch.setattr(miner, "_score_all", lambda ready, *args: ([{**ready[0], "quality_tier": "PERFECT_CANDIDATE", "reference_gate_pass": True}], {"scored_references": []}))
    plan = {"discovery": {"data_end_exclusive": "2025-01-02T00:00:00Z"}}
    context = miner.ScoreContext({}, {}, [], [], [], {}, [])
    summary = miner.process_source(spec, plan=plan, plan_sha="plan", output_root=tmp_path / "out", context=context)
    assert summary["strict_grade_a"] == 1
    assert seen == [19]


def test_receipt_binding_rejects_source_sha_drift(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ts,open,high,low,close,volume\n1,1,1,1,1,1\n", encoding="utf-8")
    monkeypatch.setattr(miner, "_repo_path", lambda value: Path(str(value)))
    spec = {"source_path": str(source), "symbol": "S", "venue": "V", "bar_minutes": 15, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    original = miner._receipt_binding("plan", spec)
    output = miner._source_output_dir(tmp_path, spec)
    output.mkdir()
    (output / "receipt.json").write_text(
        json.dumps({"binding": original, "summary": {}, "artifacts": {}}), encoding="utf-8"
    )
    source.write_text("ts,open,high,low,close,volume\n2,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(miner.ProfitMinerError, match="SHA drift"):
        miner._receipt_binding("plan", spec)
    replacement = {**spec, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    assert miner._source_output_dir(tmp_path, replacement) == output
    with pytest.raises(miner.ProfitMinerError, match="stale source output binding"):
        miner._resume_or_reject(output, miner._receipt_binding("plan", replacement))


def test_failed_source_writes_a_receipt_without_counting_as_empty(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing.csv"
    monkeypatch.setattr(miner, "_repo_path", lambda value: Path(str(value)))
    monkeypatch.setattr(miner, "_relative", lambda path: str(path))
    spec = {
        "source_path": str(missing), "symbol": "S", "venue": "V", "bar_minutes": 15,
        "sha256": "not-a-real-sha",
    }
    result = miner._process_source_safely(
        spec,
        plan={"discovery": {"data_end_exclusive": "2025-01-02T00:00:00Z"}},
        plan_sha="plan",
        output_root=tmp_path / "out",
        context=miner.ScoreContext({}, {}, [], [], [], {}, []),
    )
    assert result["status"] == "failed"
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["summary"]["error_type"] == "FileNotFoundError"


def test_receipt_binds_every_imported_rule_dependency(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ts,open,high,low,close,volume\n1,1,1,1,1,1\n", encoding="utf-8")
    monkeypatch.setattr(miner, "_repo_path", lambda value: Path(str(value)))
    spec = {
        "source_path": str(source), "symbol": "S", "venue": "V", "bar_minutes": 15,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    binding = miner._receipt_binding("plan", spec)
    expected = {str(path.relative_to(miner.ROOT)) for path in miner.RULE_DEPENDENCY_PATHS}
    assert set(binding["rule_dependency_sha256"]) == expected

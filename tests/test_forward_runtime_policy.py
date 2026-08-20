"""Fail-closed contracts for production forward candidate provenance."""
from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.judgment.forward_scan as fs
from yoyo.layers.l1_detection import scan as _l1
from src.judgment.forward_types import (
    ForwardExit,
    ForwardScanInput,
    validate_candidate_source,
)


def _frame(n_bars: int = 650) -> pd.DataFrame:
    rng = np.random.default_rng(28)
    open_time = pd.date_range("2026-07-01", periods=n_bars, freq="15min", tz="UTC")
    opens = 100.0 + np.cumsum(rng.normal(0.0, 0.25, n_bars))
    closes = opens + rng.normal(0.0, 0.15, n_bars)
    spread = np.abs(rng.normal(0.35, 0.05, n_bars)) + 0.1
    return pd.DataFrame(
        {
            "ts": open_time.as_unit("ms").astype("int64"),
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
            "volume": np.abs(rng.normal(1000.0, 80.0, n_bars)),
            "open_time": open_time.astype(str),
        }
    )


def _artifact() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        threshold=0.5,
        relative_model_path="models/stub.txt",
        dataset_sha256="stub",
        model_path="models/stub.txt",
        best_iteration=1,
        sizing_tiers=None,
        feature_semantics="legacy_unaligned",
    )


def _protocol() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        protocol_version="short_test_v1",
        strategy_id="dense_start_short_15m",
        side="short",
        feature_semantics="legacy_unaligned",
        threshold=0.5,
        execution_eligible=False,
        detector_path=Path("models/det.pt"),
        model_sha256="model-sha",
        detector_sha256="detector-sha",
        passes_threshold=lambda score: score >= 0.5,
    )


class _Booster:
    def predict(self, rows, num_iteration=None):  # noqa: ANN001, ARG002
        return np.full(len(rows), 0.9)


def _scan_input(frame: pd.DataFrame, existing: pd.DataFrame | None = None) -> ForwardScanInput:
    return ForwardScanInput(
        artifact=_artifact(),
        booster=_Booster(),
        detected_at="2026-07-07T18:30:00+00:00",
        start_time=pd.Timestamp("2026-07-01", tz="UTC"),
        existing_log=existing if existing is not None else pd.DataFrame(),
        protocol=_protocol(),
    )


def test_candidate_source_contract_rejects_production_rules() -> None:
    with pytest.raises(RuntimeError, match="requires candidate_source=yolo"):
        validate_candidate_source("rules", "production")


def test_candidate_source_contract_allows_research_rules() -> None:
    assert validate_candidate_source("rules", "research") == "rules"


def test_scan_enforces_contract_before_reading_series(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(fs, "RUNTIME_MODE", "production")
    monkeypatch.setattr(
        fs,
        "iter_series",
        lambda **kwargs: pytest.fail("production rules must fail before data scanning"),
    )

    with pytest.raises(RuntimeError, match="legacy rules are research-only"):
        fs.scan_forward_records(_scan_input(_frame()))


def test_research_scan_retains_rules_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(fs, "RUNTIME_MODE", "research")
    monkeypatch.setattr(fs, "iter_series", lambda **kwargs: iter(()))

    result = fs.scan_forward_records(_scan_input(_frame()))

    assert result.records == []
    assert result.scanned_series == 0


def test_protocol_side_cannot_disagree_with_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan = _scan_input(_frame())
    scan.artifact.side = "long"
    monkeypatch.setattr(
        fs,
        "iter_series",
        lambda **kwargs: pytest.fail("mismatch must fail before series are read"),
    )

    with pytest.raises(RuntimeError, match="does not match protocol side"):
        fs.scan_forward_records(scan)


def test_detection_and_decision_times_are_recorded_at_their_own_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(fs, "RUNTIME_MODE", "research")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kwargs: iter([("okx", "TEST_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(
        _l1, "candidate_indices", lambda enriched, **kwargs: [len(frame) - 2]
    )
    # Two clocks, in two layers, which is the point: detection happens in L1 and
    # the decision in L2, minutes apart across a 344-symbol scan. They share one
    # iterator here so the ordering assertion still means something.
    clock = iter(["candidate-finished", "decision-finished"])
    monkeypatch.setattr(_l1, "_utc_now_iso", lambda: next(clock))
    monkeypatch.setattr(fs, "_utc_now_iso", lambda: next(clock))

    result = fs.scan_forward_records(
        _scan_input(frame),
        exit_resolver=lambda enriched, i: ForwardExit("open", "", -1, 0, "", np.nan),
    )

    assert len(result.records) == 1
    assert result.records[0]["detected_at"] == "candidate-finished"
    assert result.records[0]["decision_at"] == "decision-finished"
    assert result.records[0]["detected_at"] != result.records[0]["decision_at"]


def test_missing_detector_discovers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _frame()
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "yolo")
    monkeypatch.setattr(fs, "RUNTIME_MODE", "production")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kwargs: iter([("okx", "TEST_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(
        fs,
        "load_yolo_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(ImportError("ultralytics missing")),
    )

    result = fs.scan_forward_records(_scan_input(frame))

    assert result.records == []
    assert result.candidates_seen == 0
    assert result.threshold_signals_seen == 0


def test_missing_detector_still_resolves_tracked_open_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _frame()
    signal_i = len(frame) - 2
    signal_time = str(pd.Timestamp(frame["open_time"].iloc[signal_i]))
    existing = pd.DataFrame(
        [
            {
                "source": "okx",
                "symbol": "TEST_USDT_SWAP",
                "signal_time": signal_time,
                "status": "open",
            }
        ]
    )
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "yolo")
    monkeypatch.setattr(fs, "RUNTIME_MODE", "production")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kwargs: iter([("okx", "TEST_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(
        fs,
        "load_yolo_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("weights missing")),
    )

    result = fs.scan_forward_records(
        _scan_input(frame, existing),
        exit_resolver=lambda enriched, i: ForwardExit("open", "", -1, 0, "", np.nan),
    )

    assert len(result.records) == 1
    assert result.records[0]["signal_time"] == signal_time
    assert result.records[0]["side"] == "short"
    assert result.records[0]["protocol_version"] == "short_test_v1"
    assert result.records[0]["model_sha256"] == "model-sha"


def test_forward_pulse_has_static_fail_closed_gate() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "forward_pulse.sh"
    ).read_text(encoding="utf-8")

    assert "FABLE_CANDIDATE_SOURCE=rules" not in script
    assert "export FABLE_RUNTIME_MODE=production" in script
    assert "export FABLE_CANDIDATE_SOURCE=yolo" in script
    assert 'if "$PY" scripts/forward_track.py; then' in script
    assert 'if [ "$forward_track_ok" -eq 1 ]; then' in script
    assert script.index('if "$PY" scripts/forward_track.py; then') < script.index(
        'if [ "$forward_track_ok" -eq 1 ]; then'
    )


def _run_isolated_pulse(
    tmp_path: Path,
    *,
    import_status: int,
    forward_status: int,
) -> tuple[Path, Path]:
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    scripts_dir.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts" / "forward_pulse.sh"
    pulse = scripts_dir / "forward_pulse.sh"
    pulse.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        """#!/bin/sh
if [ "$1" = "-c" ]; then
  exit "$FAKE_IMPORT_STATUS"
fi
if [ "$1" = "scripts/forward_track.py" ]; then
  printf '%s,%s\n' "$FABLE_RUNTIME_MODE" "$FABLE_CANDIDATE_SOURCE" > "$FAKE_ENV_MARKER"
  exit "$FAKE_FORWARD_STATUS"
fi
if [ "$1" = "-m" ] && [ "$2" = "src.execution" ]; then
  : > "$FAKE_EXECUTOR_MARKER"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env_marker = tmp_path / "forward-env.txt"
    executor_marker = tmp_path / "executor-called"
    env = os.environ.copy()
    env.update(
        {
            "PY": str(fake_python),
            "SKIP_UPDATE_OKX": "1",
            "FABLE_COLLECT_REAL_TIPS": "0",
            "FAKE_IMPORT_STATUS": str(import_status),
            "FAKE_FORWARD_STATUS": str(forward_status),
            "FAKE_ENV_MARKER": str(env_marker),
            "FAKE_EXECUTOR_MARKER": str(executor_marker),
        }
    )
    subprocess.run(
        ["bash", str(pulse)],
        cwd=project,
        env=env,
        check=True,
        timeout=10,
    )
    return env_marker, executor_marker


def test_forward_pulse_missing_ultralytics_stays_yolo(tmp_path: Path) -> None:
    env_marker, executor_marker = _run_isolated_pulse(
        tmp_path,
        import_status=1,
        forward_status=0,
    )

    assert env_marker.read_text(encoding="utf-8").strip() == "production,yolo"
    assert executor_marker.exists()


def test_forward_pulse_failure_blocks_executor(tmp_path: Path) -> None:
    env_marker, executor_marker = _run_isolated_pulse(
        tmp_path,
        import_status=0,
        forward_status=17,
    )

    assert env_marker.read_text(encoding="utf-8").strip() == "production,yolo"
    assert not executor_marker.exists()

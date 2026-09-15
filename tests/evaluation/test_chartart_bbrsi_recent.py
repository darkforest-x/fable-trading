"""Synthetic fail-closed tests for the one-time recent ChartArt review runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from yoyo.evaluation import chartart_bbrsi_recent as recent


def _config() -> dict:
    return {
        "timeframes": [1, 3, 15], "end": recent.EXPECTED_END,
        "multipliers": [1, 2], "entry_margin_leverages": [1, 10],
        "initial_cash": 1000, "base_notional": 100, "cost": .002,
        "seed": 915611, "control_draws": 20, "warmup_bars": 1500, "minimum_warmup_bars": 250,
        "windows": {str(k): [{"name": "screenshot", "start": value}] for k, value in recent.EXPECTED_STARTS.items()},
        "source_directories": {str(k): "unused/%dm" % k for k in recent.EXPECTED_STARTS},
    }


def _frozen(tmp_path: Path, *, approved: bool = True, config_hash: str | None = None) -> tuple[Path, Path]:
    exp = tmp_path / "experiments/active/exp"
    exp.mkdir(parents=True)
    config = _config()
    config_path = exp / "recent_config.json"
    config_path.write_text(json.dumps(config))
    engine = {}
    dependencies = (*recent.ENGINE_FILES, *recent.RUNNER_FILES, *recent.AUXILIARY_FILES)
    for relative in dependencies:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
        if relative in recent.ENGINE_FILES:
            engine[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    auth = {"approved": approved, "authorization_id": "one", "holdout_consumption_number": 1,
            "config_sha256": config_hash or hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "engine_sha256": engine}
    (exp / "authorization_recent.json").write_text(json.dumps(auth))
    (exp / "source_receipt.json").write_text("{}")
    return tmp_path, exp


def _head(root: Path, relative: str) -> bytes:
    return (root / relative).read_bytes()


def test_unapproved_authorization_rejects_before_any_source_path_is_opened(tmp_path: Path) -> None:
    root, exp = _frozen(tmp_path, approved=False)
    with pytest.raises(ValueError, match="not approved"):
        recent.validate_authorization(root=root, experiment=exp, head_reader=_head)


def test_config_drift_rejects_before_builder_or_source_use(tmp_path: Path) -> None:
    root, exp = _frozen(tmp_path, config_hash="not-the-config")
    with pytest.raises(ValueError, match="config drift"):
        recent.validate_authorization(root=root, experiment=exp, head_reader=_head)


def test_existing_claim_rejects_a_second_exposure_without_creating_results(tmp_path: Path) -> None:
    root, exp = _frozen(tmp_path)
    claim = exp / "holdout_consumptions/one.json"
    claim.parent.mkdir()
    claim.write_text("{}")
    with pytest.raises(ValueError, match="claim already exists"):
        recent.begin_run(root=root, experiment=exp, head_reader=_head)
    assert not (exp / "recent_results").exists()


def test_begin_run_writes_claim_and_started_before_source_read(tmp_path: Path) -> None:
    root, exp = _frozen(tmp_path)
    _, auth, _, out = recent.begin_run(root=root, experiment=exp, head_reader=_head,
                                       commit_reader=lambda _: "synthetic-head")
    assert auth["authorization_id"] == "one"
    assert (out / "started.json").is_file()
    assert (exp / "holdout_consumptions/one.json").is_file()

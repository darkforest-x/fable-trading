"""Orchestration-only checks for the isolated profile-window miner driver."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from functools import partial
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from yoyo.datasets import ma_profit_miner as miner
from yoyo.datasets import ma_profit_window_miner as window


_ORIGINAL_MINER_PROFILE = miner.extract_profile
_ORIGINAL_MINER_BINDING = miner._receipt_binding
_ORIGINAL_MINER_OUTPUT_DIR = miner._source_output_dir
_ORIGINAL_MINER_INITIALIZER = miner._initialize_worker
_ORIGINAL_OUTPUT_NAME = miner.DEFAULT_OUTPUT_NAME
_ORIGINAL_WRITE_JSON = miner._write_json


def _restore_driver_globals() -> None:
    window._IMPLEMENTATION = None
    miner.extract_profile = _ORIGINAL_MINER_PROFILE
    miner._receipt_binding = _ORIGINAL_MINER_BINDING
    miner._source_output_dir = _ORIGINAL_MINER_OUTPUT_DIR
    miner._initialize_worker = _ORIGINAL_MINER_INITIALIZER
    miner.DEFAULT_OUTPUT_NAME = _ORIGINAL_OUTPUT_NAME
    miner._write_json = _ORIGINAL_WRITE_JSON


@pytest.fixture(autouse=True)
def _clean_driver_globals():
    _restore_driver_globals()
    yield
    _restore_driver_globals()


def _spec() -> dict[str, Any]:
    return {
        "source_path": "inputs/future-2m.csv", "symbol": "BTC", "venue": "binance",
        "bar_minutes": 2, "sha256": "a" * 64,
    }


def _impl() -> dict[str, str]:
    return {
        "implementation_id": window.IMPLEMENTATION_ID,
        "driver_sha256": "d" * 64,
        "adapter_sha256": "1234567890abcdef" * 4,
        "contract_sha256": "c" * 64,
        "parity_receipt_sha256": "p" * 64,
    }


def _spawn_probe(spec: dict[str, Any], output_root: str) -> dict[str, Any]:
    """Top-level so macOS ``spawn`` serializes it as a real child task."""

    binding_function = miner._receipt_binding
    destination = miner._source_output_dir(Path(output_root), spec)
    return {
        "adapter_installed": miner.extract_profile is window.extract_profile_window,
        "binding_installed": binding_function.__module__ == window.__name__,
        "implementation": window._IMPLEMENTATION,
        "output_dir": str(destination),
        "worker_plan_sha": miner._WORKER_PLAN_SHA,
        "worker_output_root": str(miner._WORKER_OUTPUT_ROOT),
    }


def test_spawn_initializer_installs_adapter_binding_and_isolated_output_dir(tmp_path: Path) -> None:
    impl = _impl()
    output = tmp_path / "source_scans"
    initializer = partial(window._initialize_worker, impl=impl)
    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=initializer,
        initargs=({"fixture": True}, {"plan": "fixture"}, "plan-sha", output),
    ) as executor:
        observed = executor.submit(_spawn_probe, _spec(), str(output)).result(timeout=30)
    assert observed["adapter_installed"] is True
    assert observed["binding_installed"] is True
    assert observed["implementation"] == impl
    assert observed["worker_plan_sha"] == "plan-sha"
    assert observed["worker_output_root"] == str(output)
    assert observed["output_dir"].endswith("__pw_1234567890ab")


def test_adapter_call_receipt_binding_preserves_original_lineage_and_changes_effective_code_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    impl = _impl()
    original_binding = {
        "miner_sha256": "m" * 64,
        "rule_dependency_sha256": {"rule.py": "r" * 64},
        "code_sha256": "base-code-sha",
    }
    calls: list[tuple[object, object]] = []

    def adapter(frame: object, row: object, **_kwargs: object) -> str:
        calls.append((frame, row))
        return "windowed-profile"

    monkeypatch.setattr(window, "extract_profile_window", adapter)
    monkeypatch.setattr(window, "_BASE_BINDING", lambda _plan, _source: dict(original_binding))
    window._install(impl)
    assert miner.extract_profile("frame", "row") == "windowed-profile"
    assert calls == [("frame", "row")]
    binding = miner._receipt_binding("plan-sha", _spec())
    assert binding["profile_implementation"] == impl
    assert binding["miner_sha256"] == original_binding["miner_sha256"]
    assert binding["rule_dependency_sha256"] == original_binding["rule_dependency_sha256"]
    assert binding["code_sha256"] != original_binding["code_sha256"]
    assert miner._source_output_dir(tmp_path, _spec()) != _ORIGINAL_MINER_OUTPUT_DIR(tmp_path, _spec())


def _build_fixture_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    plan = tmp_path / "plan.json"
    sources = tmp_path / "sources.json"
    proof = tmp_path / "parity.json"
    plan.write_text("{}\n")
    sources.write_text("{}\n")
    proof.write_text("{}\n")
    (tmp_path / window.CONTRACT_NAME).write_text(json.dumps({"parity_receipt_path": str(proof),
        "parity_builder_path": str(proof), "parity_selection_path": str(proof),
        "full_source_parity_receipt_path": str(proof)}))
    return plan, sources, proof


@pytest.mark.parametrize("failure", [False, True])
def test_build_restores_original_miner_globals_after_success_or_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    plan, sources, proof = _build_fixture_files(tmp_path)
    impl = _impl()
    monkeypatch.setattr(window, "implementation", lambda _plan: impl)
    monkeypatch.setattr(miner, "_repo_path", lambda value: Path(value))
    monkeypatch.setattr(miner, "_relative", lambda path: str(Path(path)))
    monkeypatch.setattr(window.subprocess, "check_output", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(miner, "_assert_committed", lambda _paths: "fixture-commit")

    def fake_build(_plan: Path, _sources: Path, *, workers: int) -> dict[str, Any]:
        assert miner.extract_profile is window.extract_profile_window
        assert miner._receipt_binding.__module__ == window.__name__
        isolated = miner._source_output_dir(tmp_path / "isolated", _spec())
        assert isolated.name.endswith("__pw_1234567890ab")
        if failure:
            raise RuntimeError("fixture worker failure")
        isolated.parent.mkdir(parents=True, exist_ok=True)
        master = {"attempted_sources": 1, "completed_sources": 1, "failed_sources": 0, "source_summaries": []}
        miner._write_json(isolated.parent / "master_sources.json", master)
        assert json.loads((isolated.parent / "master_sources.json").read_text())["profile_implementation"] == impl
        return master

    monkeypatch.setattr(miner, "build", fake_build)
    if failure:
        with pytest.raises(RuntimeError, match="fixture worker failure"):
            window.build(plan, sources, workers=2, output_name="isolated")
    else:
        master = window.build(plan, sources, workers=2, output_name="isolated")
        assert master["profile_implementation"] == impl
        assert (tmp_path / "isolated" / "master_sources.json").exists()
    assert window._IMPLEMENTATION is None
    assert miner.extract_profile is _ORIGINAL_MINER_PROFILE
    assert miner._receipt_binding is _ORIGINAL_MINER_BINDING
    assert miner._source_output_dir is _ORIGINAL_MINER_OUTPUT_DIR
    assert miner._initialize_worker is _ORIGINAL_MINER_INITIALIZER
    assert miner._write_json is _ORIGINAL_WRITE_JSON


def test_existing_master_is_never_overwritten(tmp_path: Path):
    target = tmp_path / "source_scans" / "master_sources.json"
    target.parent.mkdir()
    target.write_text('{"legacy":true}\n')
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        window.build(tmp_path / "plan.json", tmp_path / "sources.json")
    assert target.read_text() == '{"legacy":true}\n'


def _proof_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Any]]:
    plan = tmp_path / "plan.json"
    plan.write_text('{"plan":"fixture"}\n')
    proof = tmp_path / "parity.json"
    builder, selection = tmp_path / "parity_builder.py", tmp_path / "selection.json"
    builder.write_text("# frozen fixture builder\n")
    selection.write_text('{"fixture":true}\n')
    proof_payload = {
        "status": "passed", "adapter_sha256": miner.sha256_file(window.ADAPTER),
        "miner_sha256": miner.sha256_file(Path(miner.__file__)),
        "rule_dependency_sha256": miner._rule_dependency_hashes(),
        "exact_profiles": 20, "mismatches": 0,
        "builder_sha256": miner.sha256_file(builder), "selection_sha256": miner.sha256_file(selection),
    }
    proof.write_text(json.dumps(proof_payload))
    full = tmp_path / "full_source.json"
    full.write_text(json.dumps({"status": "passed", "artifact_checks": [{"exact": True}] * 7,
                               "profile_implementation": {"adapter_sha256": miner.sha256_file(window.ADAPTER)}}))
    contract = {
        "implementation_id": window.IMPLEMENTATION_ID,
        "change_scope": "strict_profile_array_window_only",
        "plan_sha256": miner.sha256_file(plan),
        "adapter_sha256": miner.sha256_file(window.ADAPTER),
        "driver_sha256": miner.sha256_file(Path(window.__file__)),
        "miner_sha256": miner.sha256_file(Path(miner.__file__)),
        "rule_dependency_sha256": miner._rule_dependency_hashes(),
        "parity_receipt_path": str(proof),
        "parity_receipt_sha256": miner.sha256_file(proof),
        "parity_builder_path": str(builder), "parity_builder_sha256": miner.sha256_file(builder),
        "parity_selection_path": str(selection), "parity_selection_sha256": miner.sha256_file(selection),
        "full_source_parity_receipt_path": str(full), "full_source_parity_receipt_sha256": miner.sha256_file(full),
    }
    (tmp_path / window.CONTRACT_NAME).write_text(json.dumps(contract))
    monkeypatch.setattr(miner, "_repo_path", lambda value: Path(value))
    return plan, proof, contract


@pytest.mark.parametrize("kind", ["driver", "builder", "selection"])
def test_frozen_driver_and_parity_builder_selection_cannot_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str):
    plan, _proof, contract = _proof_contract(tmp_path, monkeypatch)
    assert window.implementation(plan)["driver_sha256"] == miner.sha256_file(Path(window.__file__))
    if kind == "driver":
        contract["driver_sha256"] = "0" * 64
        (tmp_path / window.CONTRACT_NAME).write_text(json.dumps(contract))
    else:
        Path(contract[f"parity_{kind}_path"]).write_text("changed\n")
    with pytest.raises(miner.ProfitMinerError, match="drift"):
        window.implementation(plan)


def test_default_shared_root_rejects_an_independent_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(window, "implementation", lambda _plan: _impl())
    with pytest.raises(miner.ProfitMinerError, match="live queue parent lock"):
        window.build(tmp_path / "plan.json", tmp_path / "sources.json")


@pytest.mark.parametrize("kind", ["missing", "damaged", "wrong_adapter_proof"])
def test_missing_damaged_or_wrong_adapter_proof_contract_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    plan, proof, contract = _proof_contract(tmp_path, monkeypatch)
    contract_path = tmp_path / window.CONTRACT_NAME
    if kind == "missing":
        contract["parity_receipt_path"] = str(tmp_path / "missing-proof.json")
    elif kind == "damaged":
        proof.write_text('{"changed":true}\n')
    else:
        proof_payload = json.loads(proof.read_text())
        proof_payload["adapter_sha256"] = "0" * 64
        proof.write_text(json.dumps(proof_payload))
        contract["parity_receipt_sha256"] = miner.sha256_file(proof)
    contract_path.write_text(json.dumps(contract))
    with pytest.raises(miner.ProfitMinerError):
        window.implementation(plan)


def test_isolated_source_directory_differs_from_original_and_is_stable_for_recovery(tmp_path: Path) -> None:
    spec, impl = _spec(), _impl()
    original = _ORIGINAL_MINER_OUTPUT_DIR(tmp_path, spec)
    window._IMPLEMENTATION = impl
    first = window._source_output_dir(tmp_path, spec)
    second = window._source_output_dir(tmp_path, dict(spec))
    assert first == second
    assert first != original
    assert first.name == original.name + "__pw_1234567890ab"

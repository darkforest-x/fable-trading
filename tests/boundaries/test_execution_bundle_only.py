"""L4 may only run a model that was explicitly promoted into a bundle.

The executor obtains its strategy from exactly one place --
`yoyo.contracts.protocol.require_active_bundle`, which fails closed when
models/active_bundle.json is absent, because absence is not authority to trade.
Everything else that looks like a model pointer is a research authority:
models/ACTIVE, models/owner_best.json, "the newest file in runs/". Any of them
reaching the order path would let a model trade that nobody promoted.

Static checks, so they hold for code paths no test happens to execute.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXECUTION = REPO / "yoyo" / "layers" / "l4_execution"

# Research-grade pointers. None of these may appear in the execution layer.
RESEARCH_AUTHORITIES = ("models/ACTIVE", "owner_best", "ACTIVE_PREV")


def _execution_sources() -> list[Path]:
    if not EXECUTION.exists():
        return []
    return sorted(EXECUTION.rglob("*.py"))


def test_the_execution_layer_exists_to_be_checked() -> None:
    assert _execution_sources(), "no l4_execution sources found -- this suite proves nothing"


def test_execution_never_names_a_research_model_pointer() -> None:
    offenders = {}
    for path in _execution_sources():
        text = path.read_text(encoding="utf-8")
        hits = [needle for needle in RESEARCH_AUTHORITIES if needle in text]
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert not offenders, (
        f"the execution layer names research model pointers: {offenders}. "
        "Production authority is models/active_bundle.json via require_active_bundle; "
        "models/ACTIVE and owner_best are research and legacy authorities only."
    )


def test_execution_takes_its_protocol_from_the_bundle_gate() -> None:
    executor = EXECUTION / "executor.py"
    if not executor.exists():
        pytest.skip("executor not migrated yet")
    text = executor.read_text(encoding="utf-8")
    assert "require_active_bundle" in text, (
        "executor.py no longer calls require_active_bundle. That function is what "
        "makes a missing bundle a refusal instead of a default."
    )


def test_execution_does_not_load_weight_files_directly() -> None:
    """A literal .pt path in L4 is a promoted-bundle bypass with extra steps."""
    offenders = {}
    for path in _execution_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.endswith((".pt", ".txt.model", ".onnx"))
        ]
        if literals:
            offenders[str(path.relative_to(REPO))] = literals
    assert not offenders, f"execution hardcodes weight paths: {offenders}"


def test_the_check_would_notice_a_bypass(tmp_path: Path) -> None:
    """Guards the guard: prove the string check is not vacuous."""
    bad = tmp_path / "bad.py"
    bad.write_text("PATH = 'models/ACTIVE'\n", encoding="utf-8")
    assert [n for n in RESEARCH_AUTHORITIES if n in bad.read_text(encoding="utf-8")] == [
        "models/ACTIVE"
    ]

"""Nothing under experiments/ may reach the live system.

experiments/ is where new research goes now that satellite repositories are
forbidden. That only stays safe if an experiment cannot, by construction, flip
the ACTIVE pointer, append to the forward log, or call the executor -- the three
actions CLAUDE.md rules 9-11 reserve for the owner and the VPS.

The registry side of the same rule (an experiment row cannot be
production_eligible) lives in tests/contracts/test_registries.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXPERIMENTS = REPO / "experiments"

FORBIDDEN_WRITES = ("models/ACTIVE", "forward_log", "active_bundle.json", "owner_best")
FORBIDDEN_IMPORTS = (
    "yoyo.layers.l4_execution",
    "src.execution",
)


def _experiment_sources() -> list[Path]:
    if not EXPERIMENTS.exists():
        return []
    return sorted(EXPERIMENTS.rglob("*.py"))


def _imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
    return found


def test_no_experiment_imports_the_execution_layer() -> None:
    sources = _experiment_sources()
    if not sources:
        pytest.skip("no python under experiments/ yet")
    offenders = {
        str(path.relative_to(REPO)): hits
        for path in sources
        if (hits := [m for m in _imports(path) if m.startswith(FORBIDDEN_IMPORTS)])
    }
    assert not offenders, f"experiments must not import the executor: {offenders}"


def test_no_experiment_names_a_production_pointer() -> None:
    sources = _experiment_sources()
    if not sources:
        pytest.skip("no python under experiments/ yet")
    offenders = {}
    for path in sources:
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = [needle for needle in FORBIDDEN_WRITES if needle in text]
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert not offenders, (
        f"experiments reference production state: {offenders}. Promotion and the "
        "forward log are owner decisions, not experiment side effects."
    )


def test_the_experiment_tree_exists_with_the_four_outcome_directories() -> None:
    """A registry with nowhere to put results turns into a directory of TODOs."""
    for name in ("active", "accepted", "rejected", "historical"):
        assert (EXPERIMENTS / name).is_dir(), f"experiments/{name}/ is missing"


def test_the_check_would_notice_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from yoyo.layers.l4_execution import executor\n", encoding="utf-8")
    assert _imports(bad) == ["yoyo.layers.l4_execution"]

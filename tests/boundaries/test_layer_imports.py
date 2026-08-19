"""The one structural rule, enforced instead of documented.

layers/ may not import each other. They talk through contracts/ and data/.

This is not tidiness. On 2026-08-03 a single fault spanned forward_scan, frozen
and executor because one layer's fact -- which coordinate system a model was
trained in -- was being decided by another layer's fact -- whether this trade is
long or short. Nothing stopped that import, so nothing stopped the bug, and it
reached the live path. A rule written in a README would not have caught it; this
does, at the moment someone types the import.

Deliberately AST-based rather than import-based: a module that is broken or slow
to import still gets checked, and the test does not need the whole dependency
tree installed to say something useful.

Ported from yoyo-trading tests/test_layer_boundaries.py at 784766de, then
extended with the dependency directions the consolidation task book adds:
execution may not reach into training or experiments, and no business layer may
depend on the dashboard.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
YOYO = REPO / "yoyo"
LAYERS = YOYO / "layers"
# What a layer is allowed to reach for. Anything else in yoyo.* is a violation.
ALLOWED_PREFIXES = ("yoyo.contracts", "yoyo.data")

# Legacy edges that exist in the ported tree and are recorded rather than hidden.
# An entry here is a debt with a name and a reason, not a silenced failure. It is
# keyed by the file that owns the edge, so a *new* violation in the same file
# still fails.
KNOWN_LEGACY_EDGES: dict[str, tuple[str, ...]] = {
    # runtime_artifact() adapts a verified bundle to the existing L2 scorer. The
    # import is function-local and deliberate -- the alternative at the time was
    # reading the old JSON sidecar, which would have created a second authority
    # over the threshold (fault C-07). It is still contracts reaching into a
    # layer, so it is listed rather than waved through, and it disappears when
    # src/judgment finishes moving to yoyo/layers/l2_judgment.
    "yoyo/contracts/protocol.py": ("src.judgment.features", "src.judgment.frozen"),
}


def _layer_modules() -> list[tuple[str, Path]]:
    out = []
    if not LAYERS.exists():
        return out
    for layer_dir in sorted(p for p in LAYERS.iterdir() if p.is_dir()):
        for path in sorted(layer_dir.rglob("*.py")):
            out.append((layer_dir.name, path))
    return out


def _imports(path: Path, prefixes: tuple[str, ...]) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - a broken file fails elsewhere
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(prefixes):
                found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(prefixes)]
    return found


def _yoyo_imports(path: Path) -> list[str]:
    return _imports(path, ("yoyo.",))


def _allowed_legacy(path: Path) -> tuple[str, ...]:
    try:
        key = path.relative_to(REPO).as_posix()
    except ValueError:
        return ()
    return KNOWN_LEGACY_EDGES.get(key, ())


@pytest.mark.parametrize(
    ("layer", "path"),
    _layer_modules() or [("<none yet>", Path(__file__))],
    ids=lambda v: v.name if isinstance(v, Path) else str(v),
)
def test_a_layer_does_not_import_another_layer(layer: str, path: Path) -> None:
    if layer == "<none yet>":
        pytest.skip("no layer modules migrated yet")
    offenders = []
    for module in _yoyo_imports(path):
        if module.startswith(f"yoyo.layers.{layer}"):
            continue  # its own layer is fine
        if module.startswith(ALLOWED_PREFIXES):
            continue
        if module.startswith("yoyo.layers."):
            offenders.append(module)
    assert not offenders, (
        f"{path.relative_to(YOYO.parent)} reaches into another layer: {offenders}. "
        "Route it through yoyo.contracts instead -- that is the whole point of the split."
    )


def test_contracts_depend_on_no_layer() -> None:
    """A contract that imports a layer is not a contract, it is a back door."""
    contracts = YOYO / "contracts"
    if not contracts.exists():
        pytest.skip("contracts not created yet")
    bad: dict[str, list[str]] = {}
    for path in sorted(contracts.rglob("*.py")):
        allowed = _allowed_legacy(path)
        offenders = [
            module
            for module in _imports(path, ("yoyo.layers", "src."))
            if module not in allowed
        ]
        if offenders:
            bad[str(path.relative_to(YOYO.parent))] = offenders
    assert not bad, f"contracts must not import layers: {bad}"


def test_data_depends_on_no_layer() -> None:
    """data/ sits below the layers; an edge upward makes the graph a cycle."""
    data = YOYO / "data"
    if not data.exists():
        pytest.skip("data package not created yet")
    bad = {
        str(path.relative_to(YOYO.parent)): offenders
        for path in sorted(data.rglob("*.py"))
        if (offenders := _imports(path, ("yoyo.layers", "src.")))
    }
    assert not bad, f"yoyo.data must not import a layer: {bad}"


def test_execution_does_not_import_training_or_experiments() -> None:
    """L4 loads frozen, promoted bundles. It never reaches for a trainer.

    Task book section 5: an executor that can import training code can be made
    to run an unpromoted model by an import statement alone.
    """
    execution = LAYERS / "l4_execution"
    if not execution.exists():
        pytest.skip("l4_execution not migrated yet")
    forbidden = ("experiments", "yoyo.layers.l2_judgment.train", "src.judgment.train")
    bad = {
        str(path.relative_to(YOYO.parent)): offenders
        for path in sorted(execution.rglob("*.py"))
        if (offenders := _imports(path, forbidden))
    }
    assert not bad, f"execution must not import training or experiment code: {bad}"


def test_no_layer_depends_on_the_dashboard() -> None:
    """The dashboard observes the system; it is never part of it."""
    if not LAYERS.exists():
        pytest.skip("layers not migrated yet")
    forbidden = ("src.webapp", "tools.dashboard", "yoyo.webapp")
    bad = {
        str(path.relative_to(YOYO.parent)): offenders
        for _, path in _layer_modules()
        if (offenders := _imports(path, forbidden))
    }
    assert not bad, f"a business layer imports the dashboard: {bad}"


def test_the_rule_is_actually_testable(tmp_path: Path) -> None:
    """Guards the guard: prove the checker fires on a real violation.

    Without this, an empty layers/ directory would make the suite green and say
    nothing at all.
    """
    bad = tmp_path / "bad.py"
    bad.write_text("from yoyo.layers.l4_execution import executor\n", encoding="utf-8")
    assert _yoyo_imports(bad) == ["yoyo.layers.l4_execution"]


def test_every_known_legacy_edge_still_exists() -> None:
    """An exception nobody removes becomes a lie. Fail when the debt is paid.

    If src/judgment finishes moving into yoyo/layers/l2_judgment and this entry
    is left behind, the list would keep excusing an import that is no longer
    there -- and would quietly excuse a *new* one under the same key.
    """
    stale = {}
    for rel, modules in KNOWN_LEGACY_EDGES.items():
        path = REPO / rel
        if not path.exists():
            stale[rel] = "file is gone"
            continue
        present = set(_imports(path, ("yoyo.layers", "src.")))
        missing = sorted(set(modules) - present)
        if missing:
            stale[rel] = f"no longer imports {missing}"
    assert not stale, (
        f"KNOWN_LEGACY_EDGES is out of date: {stale}. Delete the entry -- the "
        "debt it recorded has been paid."
    )

"""One repository means one copy of yoyo, and this proves which copy runs.

Between 2026-08-03 and this consolidation, `yoyo` lived in a *different*
repository (~/yoyo-trading) and reached fable-trading through an editable
install. 63 files here import it. The dangerous part was not the split itself
but how it fails: setuptools' editable finder is a meta-path finder mapping the
top-level name `yoyo` to an absolute path outside this tree, and it is consulted
*after* the normal path finder. So a local yoyo/ wins for modules it contains --
and for any module it is *missing*, resolution falls straight through to the
other repository without a word.

That means a half-finished migration looks exactly like a finished one. Byte
parity, causality and boundary tests would all pass while importing code from a
repository scheduled for archiving.

Two checks, because either alone can be fooled:

  1. the imported package resolves inside this repository
  2. every `yoyo.*` module this repository imports has a file here to resolve to

The second is AST-based and imports nothing, so it also covers modules that
need torch or lightgbm to import.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
YOYO = REPO / "yoyo"
SCAN_DIRS = ("yoyo", "src", "tests", "scripts", "tools")


def test_the_imported_yoyo_lives_in_this_repository() -> None:
    import yoyo

    resolved = Path(yoyo.__file__).resolve()
    assert resolved.is_relative_to(REPO), (
        f"`import yoyo` resolved to {resolved}, outside this repository. The "
        "editable install of yoyo-trading is shadowing the local package, so "
        "every test below is measuring the wrong tree. Run pytest from the "
        "repository root (`python -m pytest tests`) or clear the editable "
        "install with `pip uninstall yoyo-trading`."
    )


def _imported_yoyo_modules() -> dict[str, list[str]]:
    """module name -> files that import it, across the whole repository."""
    found: dict[str, list[str]] = {}
    for directory in SCAN_DIRS:
        root = REPO / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            modules: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "yoyo" or node.module.startswith("yoyo."):
                        modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules += [
                        alias.name
                        for alias in node.names
                        if alias.name == "yoyo" or alias.name.startswith("yoyo.")
                    ]
            for module in modules:
                found.setdefault(module, []).append(str(path.relative_to(REPO)))
    return found


def _resolves_locally(module: str) -> bool:
    parts = module.split(".")
    assert parts[0] == "yoyo"
    base = YOYO.joinpath(*parts[1:])
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def test_every_imported_yoyo_module_has_a_file_in_this_repository() -> None:
    modules = _imported_yoyo_modules()
    assert modules, "found no yoyo imports at all -- the scan is broken, not the tree"
    missing = {
        module: importers
        for module, importers in sorted(modules.items())
        if not _resolves_locally(module)
    }
    assert not missing, (
        "these yoyo modules are imported here but have no file in ./yoyo/, so "
        "they resolve through the editable install into another repository: "
        f"{missing}"
    )


def test_the_check_would_notice_a_missing_module() -> None:
    """Guards the guard: a resolver that always says yes proves nothing."""
    assert _resolves_locally("yoyo.contracts.protocol")
    assert not _resolves_locally("yoyo.this_module_does_not_exist")


def test_no_module_here_points_readers_back_at_the_other_repository() -> None:
    """The source-repo pointer config is what the split needed; it is now wrong.

    yoyo-trading carried configs/source_repo.json pointing at an absolute
    fable-trading path for datasets, klines and weights. Porting that file back
    would make the consolidated repository reference itself through a hardcoded
    home directory.
    """
    offenders = [
        str(path.relative_to(REPO))
        for path in sorted(YOYO.rglob("*.py"))
        if "source_repo.json" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, (
        f"{offenders} still read configs/source_repo.json, the cross-repository "
        "pointer that consolidation removes"
    )


# --------------------------------------------------------------------------
# the same hazard, one directory over
# --------------------------------------------------------------------------

def test_tools_resolves_to_this_repository_even_mid_session() -> None:
    """`tools` must be a regular package, not an implicit namespace one.

    yoyo-trading also has a tools/ directory and it has an __init__.py. Under
    namespace rules the interpreter scans past a directory without one,
    collecting portions, and stops at the first regular package it finds -- so
    once any of the scripts that still insert ~/yoyo-trading onto sys.path had
    been imported, `import tools.review` resolved into the other repository and
    failed. Alone the test passed; in a full session it did not.
    """
    import tools

    assert getattr(tools, "__file__", None), (
        "tools is an implicit namespace package again. Restore tools/__init__.py: "
        "without it, resolution depends on which script ran first."
    )
    assert Path(tools.__file__).resolve().is_relative_to(REPO), (
        f"tools resolved to {tools.__file__}, outside this repository"
    )


def test_the_review_toolchain_imports() -> None:
    from tools.review import convert_labelstudio_export

    assert Path(convert_labelstudio_export.__file__).resolve().is_relative_to(REPO)

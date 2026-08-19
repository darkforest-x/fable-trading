"""No module here may put another repository ahead of this one on sys.path.

Between the 2026-08-03 split and this consolidation, 35 scripts opened with

    _YOYO = Path.home() / "yoyo-trading"
    for p in (PROJECT, _YOYO):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

which inserts PROJECT at 0 and then _YOYO at 0, leaving ~/yoyo-trading FIRST.
So those scripts imported `yoyo` from the other repository while the test suite
verified this one's copy. Harmless while the two were identical; the moment
yoyo-trading is frozen and this repository moves on, they diverge -- including
render.py, whose exact pixels the detector is bound to.

Two ways it grows back, so two checks: a literal path to a sibling repository,
and any sys.path insertion whose argument is derived from Path.home().
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("yoyo", "src", "scripts", "tools", "tests")

#: Repositories this one was split across. None may be on sys.path.
SIBLING_REPOS = ("yoyo-trading", "yolo-xx", "yoyo-eth", "darkforest-one")

#: Files whose job is to read the source repositories, with the reason. An
#: allowlist rather than a directory exemption, so a new file under the same
#: directory is still checked.
ALLOWED_TO_NAME_SOURCES = {
    "tools/consolidation/snapshot_repositories.py":
        "freezes the source repositories' state; naming them is the whole task",
    "tools/consolidation/port_asset.py":
        "copies from a source repository and records the provenance",
    "tests/parity/test_numeric_baseline_parity.py":
        "cross-checks the port against the source while it is still on disk; it "
        "APPENDS to sys.path rather than prepending, and asserts afterwards that "
        "the local yoyo still won",
    "tests/boundaries/test_no_cross_repository_bridges.py":
        "this file, which has to name them in order to look for them",
}


def _python_files() -> list[Path]:
    found: list[Path] = []
    for directory in SCAN_DIRS:
        root = REPO / directory
        if root.is_dir():
            found += sorted(root.rglob("*.py"))
    return found


def _syspath_mutations(tree: ast.AST) -> list[ast.Call]:
    """Calls of the form sys.path.insert(...) / sys.path.append(...)."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("insert", "append", "extend"):
            continue
        target = node.func.value
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        ):
            calls.append(node)
    return calls


def _mentions_home(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in ("home", "expanduser"):
            return True
    return False


def test_no_file_names_a_sibling_repository_in_a_path_expression():
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if any(repo == node.value or node.value.endswith(f"/{repo}") for repo in SIBLING_REPOS):
                hits.append(node.value)
        relative = str(path.relative_to(REPO))
        if hits and relative not in ALLOWED_TO_NAME_SOURCES:
            offenders[relative] = sorted(set(hits))
    assert not offenders, (
        f"these files build a path to a repository that is being archived: {offenders}. "
        "Everything they need is in this repository now."
    )


def test_the_allowlist_only_covers_files_that_exist():
    """An exemption for a deleted file silently covers whatever takes its name."""
    missing = [rel for rel in ALLOWED_TO_NAME_SOURCES if not (REPO / rel).is_file()]
    assert not missing, f"remove these stale allowlist entries: {missing}"


def test_no_sys_path_insertion_is_derived_from_the_home_directory():
    """A repo-relative insert is fine; one anchored at $HOME reaches outside."""
    offenders: dict[str, int] = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        count = sum(1 for call in _syspath_mutations(tree) if _mentions_home(call))
        if count:
            offenders[str(path.relative_to(REPO))] = count
    assert not offenders, (
        f"{offenders} insert a $HOME-derived path onto sys.path. That is how the "
        "cross-repository bridge came back the first time."
    )


def test_the_checks_would_notice_the_bridge_that_existed(tmp_path: Path):
    """Guards the guard, using the exact code that was removed."""
    sample = (
        "import sys\n"
        "from pathlib import Path\n"
        "PROJECT = Path(__file__).resolve().parents[1]\n"
        "_YOYO = Path.home() / 'yoyo-trading'\n"
        "for p in (PROJECT, _YOYO):\n"
        "    if str(p) not in sys.path:\n"
        "        sys.path.insert(0, str(p))\n"
    )
    tree = ast.parse(sample)
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value in SIBLING_REPOS
    ]
    assert literals == ["yoyo-trading"]
    assert any(_mentions_home(call) for call in _syspath_mutations(tree)) is False
    # the insert itself takes str(p), so the $HOME check alone would miss it --
    # which is exactly why both checks exist rather than one.
    assert _mentions_home(tree)

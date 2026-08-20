"""20 GB of mirrored artifacts must stay out of git, and its README must not.

Owner instruction, 2026-08-20: bring everything over, keep the large files
uncommitted. Both halves matter. A tree that is fully ignored is 300,000
anonymous files nobody can identify; a tree that is not ignored is a 20 GB
commit.

The rule is written `archive/consolidated/**` rather than
`archive/consolidated/`, and the difference is not stylistic. Git does not
descend into a directory excluded by the directory form, so every `!` negation
below it is dead -- the fault already recorded in
docs/learnings/directory-level-gitignore-kills-every-negation-below-it.md. This
suite re-derives that in a scratch repository rather than trusting the note,
because the note is what the previous author also believed.

Tests the rules, not the files: the mirrored tree lives in the main working
tree, and this suite has to pass from a worktree that does not contain it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GITIGNORE = REPO / ".gitignore"
PREFIX = "archive/consolidated"

KEEP = ("README.md", "MANIFEST.json")


def _rules() -> list[str]:
    return [
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_bulk_tree_is_excluded():
    assert f"{PREFIX}/**" in _rules(), (
        f"{PREFIX}/** is missing from .gitignore; a 20 GB tree is one `git add -A` "
        "from being committed"
    )


def test_the_exclusion_uses_the_form_that_keeps_negations_alive():
    rules = _rules()
    assert f"{PREFIX}/" not in rules, (
        f"'{PREFIX}/' is the directory form. Git will not descend into it, so the "
        "README and MANIFEST negations below would be silently dead. Use "
        f"'{PREFIX}/**'."
    )


@pytest.mark.parametrize("name", KEEP)
def test_the_identifying_files_are_negated_back_in(name: str):
    assert f"!{PREFIX}/{name}" in _rules(), (
        f"{name} is not negated back in. Without it the mirrored tree is "
        "300,000 files with nothing saying where they came from."
    )


def test_the_rules_behave_as_intended_in_a_real_repository(tmp_path: Path):
    """Re-derive it. The note that motivated this is what the last author believed too."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ignore_lines = [
        line
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if PREFIX in line
    ]
    (tmp_path / ".gitignore").write_text("\n".join(ignore_lines) + "\n", encoding="utf-8")

    bulk = tmp_path / PREFIX / "yolo-xx" / "datasets" / "x" / "images"
    bulk.mkdir(parents=True)
    (bulk / "frame.png").write_text("x", encoding="utf-8")
    (bulk.parent / "labels.txt").write_text("x", encoding="utf-8")
    for name in KEEP:
        (tmp_path / PREFIX / name).write_text("x", encoding="utf-8")

    out = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    )
    visible = {line[3:] for line in out.stdout.splitlines() if line.startswith("??")}
    visible.discard(".gitignore")

    assert visible == {f"{PREFIX}/{name}" for name in KEEP}, (
        f"expected only the identifying files to be visible, got {sorted(visible)}"
    )


def test_a_directory_form_exclusion_really_would_break_the_negation(tmp_path: Path):
    """Guards the guard: prove the form actually matters, rather than asserting it."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(
        f"{PREFIX}/\n!{PREFIX}/README.md\n", encoding="utf-8"
    )
    (tmp_path / PREFIX).mkdir(parents=True)
    (tmp_path / PREFIX / "README.md").write_text("x", encoding="utf-8")

    out = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    )
    visible = {line[3:] for line in out.stdout.splitlines() if line.startswith("??")}
    visible.discard(".gitignore")
    assert visible == set(), (
        "the directory form no longer kills the negation -- git's behaviour changed, "
        "and this repository's ignore rules can be simplified"
    )

"""CI must install from requirements.txt, not from a list retyped in the workflow.

PR #1 was the first time this repository's CI had ever run, and it failed with
47 collection errors -- every one of them `No module named 'cv2'`. The cause was
not the branch. The workflow installed a hand-written list of packages that had
drifted from requirements.txt: opencv-python, torch, ultralytics and Pillow were
all declared as project dependencies and none of them were installed, so every
module touching the renderer died at import. Checked against `main` in an
identical environment: 47 collection errors there too.

It is the same shape as the eleven holdout definitions and the seven file-hash
helpers -- one fact, written in two places, with nothing keeping them equal.
The fix is structural rather than a corrected list: the workflow installs
`-r requirements.txt`, so there is only one place to edit.

pytest is the one legitimate extra: it runs the tests rather than being used by
them, and does not belong in requirements.txt.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"
REQUIREMENTS = REPO / "requirements.txt"

#: Installable in CI without being a project dependency.
ALLOWED_EXTRA_INSTALLS = {"pytest", "pip"}


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), f"{WORKFLOW.relative_to(REPO)} is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def _declared_packages() -> set[str]:
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(re.split(r"[<>=!~\[]", line)[0].strip().lower())
    return names


def test_the_workflow_installs_from_requirements():
    assert "-r requirements.txt" in _workflow_text(), (
        "the CI workflow does not install from requirements.txt. A list retyped "
        "in the workflow drifts from the declared dependencies, which is how CI "
        "spent its entire existence unable to import cv2."
    )


def test_the_workflow_does_not_retype_the_dependency_list():
    """A pip install naming project packages inline has started drifting again."""
    text = _workflow_text()
    declared = _declared_packages()
    # only look at pip install lines, and only at quoted requirement specs
    offenders = set()
    for line in text.splitlines():
        if "pip install" not in line and not re.match(r'^\s+"', line):
            continue
        for quoted in re.findall(r'"([^"]+)"', line):
            name = re.split(r"[<>=!~\[]", quoted)[0].strip().lower()
            if name in declared and name not in ALLOWED_EXTRA_INSTALLS:
                offenders.add(name)
    assert not offenders, (
        f"the workflow names project dependencies inline: {sorted(offenders)}. "
        "They are already in requirements.txt; listing them twice is what drifted."
    )


def test_requirements_declares_what_the_renderer_needs():
    """The four packages whose absence produced the 47 errors."""
    declared = _declared_packages()
    for package in ("opencv-python", "torch", "ultralytics", "pillow"):
        assert package in declared, (
            f"{package} is no longer declared in requirements.txt; CI installs "
            "from that file, so removing it silently un-tests the renderer"
        )


def test_the_runner_installs_the_system_library_opencv_links_against():
    """opencv-python needs libGL, which the runner image does not guarantee."""
    text = _workflow_text()
    assert "libgl1" in text.lower(), (
        "no libgl1 install step. opencv-python imports against libGL, and without "
        "it the failure reads as a missing Python module rather than a missing "
        "system library."
    )


def test_compileall_covers_the_canonical_package():
    """yoyo/ is where new code goes; a compile check that skips it checks nothing."""
    text = _workflow_text()
    match = re.search(r"compileall[^\n]*", text)
    assert match, "no compileall step"
    assert "yoyo" in match.group(0), (
        f"compileall does not cover yoyo/: {match.group(0)!r}. That is the "
        "canonical package -- src/ is a shim layer."
    )

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


# --------------------------------------------------------------------------
# CI must test the stack that produces the numbers
# --------------------------------------------------------------------------

CONSTRAINTS = REPO / "constraints-ci.txt"


def _pins() -> dict[str, str]:
    pins = {}
    for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        pins[name.strip().lower()] = version.strip()
    return pins


def test_the_workflow_applies_the_constraints_file():
    assert "-c constraints-ci.txt" in _workflow_text(), (
        "CI does not pin versions. requirements.txt is all `>=` with no upper "
        "bounds, so CI floats to the newest of everything: on 2026-08-20 that "
        "was pandas 3.0.5 against the 2.3.3 this project runs. A green CI then "
        "says nothing about whether the numbers reproduce, and a red one may "
        "only mean a newer library changed an API."
    )


def test_every_declared_dependency_is_pinned():
    missing = sorted(_declared_packages() - set(_pins()))
    assert not missing, (
        f"{missing} are declared in requirements.txt but unpinned in "
        "constraints-ci.txt, so CI would float on them"
    )


@pytest.mark.parametrize(("package", "pinned"), sorted(_pins().items()))
def test_the_pin_matches_what_is_installed_here(package: str, pinned: str):
    """The pins are the Mac's versions; drift means CI stopped matching reality.

    This is the same contract scripts/train_on_3060.sh enforces between the Mac
    and the training box, extended to a third machine. If it fails locally, the
    venv moved and CI is now testing a stack nobody runs.
    """
    import importlib.metadata as metadata

    try:
        installed = metadata.version(package)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{package} is not installed in this environment")
    assert installed == pinned, (
        f"{package} is {installed} here but constraints-ci.txt pins {pinned}. "
        "Changing it is changing a cross-machine contract: Mac, 3060 and CI move "
        "together, and afterwards the numbers are no longer comparable with the "
        "historical curves."
    )


def test_the_python_version_matches_the_one_the_project_runs():
    match = re.search(r'python-version:\s*"([^"]+)"', _workflow_text())
    assert match, "the workflow does not pin a Python version"
    assert match.group(1) == "3.9", (
        f"CI runs Python {match.group(1)} while the Mac venv and the 3060 run "
        "3.9. A different interpreter resolves a different dependency set, which "
        "is how CI came to test pandas 3.0 against a 2.3 project."
    )

"""Every holdout boundary in the repository must name the same instant.

There are eleven of them, under six names, in code that arrived from five
repositories. They agree today. Nothing made them agree, and until this test
existed nothing would have noticed if one had been edited: they sit in
different files, under different names, and no two are read by the same test.

The failure this prevents is quiet and expensive. A boundary that is one day
early trains on holdout bars and reports a clean number; CLAUDE.md rule 1 makes
that unrecoverable, because the window is spent whether or not anyone meant to
spend it.

Deliberately parsed rather than imported. Importing src.detection.* pulls in
torch and ultralytics, and a boundary check should not depend on whether the
GPU stack installs.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from yoyo.contracts.holdout import (
    HOLDOUT_START,
    HOLDOUT_START_ISO,
    HoldoutBoundaryError,
    assert_pre_holdout,
    is_holdout,
    is_pre_holdout,
)

REPO = Path(__file__).resolve().parents[2]

#: file -> constant name. Every known definition of the boundary in Python code.
PYTHON_DEFINITIONS = {
    "yoyo/layers/l2_judgment/train.py": "HOLDOUT_START",
    "yoyo/datasets/gold_render.py": "HOLD_DEFAULT",
    "src/judgment/p1_dataset.py": "HOLDOUT_CUTOFF",
    "src/judgment/p2_protocol.py": "HOLDOUT_CUTOFF",
    "src/detection/eth3m_v2_validation.py": "HOLDOUT_START",
    "src/detection/eth3m_v2_quality_audit.py": "HOLDOUT_START",
    "src/detection/eth3m_v2_evidence.py": "HOLDOUT_START",
    "src/backtest/run.py": "ACCEPT_START",
}

#: file -> key. The same instant expressed in configuration.
CONFIG_DEFINITIONS = {
    "configs/local_signal_v2_p1.yaml": "holdout_start_exclusive",
    "configs/labelstudio/gold_annotation_v1.json": "holdout_start",
    "configs/numeric_baseline/mvp.yaml": "data_end_boundary",
}

#: 2026-05-04 written any of the ways this repository writes it.
_EXPECTED_FORMS = (
    "2026-05-04 00:00:00",
    "2026-05-04T00:00:00Z",
    "2026-05-04T00:00:00+00:00",
    "2026-05-04",
)


def _string_args(path: Path, constant: str) -> list[str]:
    """String literals in the assignment to `constant`, without importing."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if constant not in names:
            continue
        return [
            child.value
            for child in ast.walk(node.value)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        ]
    return []


@pytest.mark.parametrize(("rel", "constant"), sorted(PYTHON_DEFINITIONS.items()))
def test_each_python_definition_names_the_canonical_instant(rel: str, constant: str) -> None:
    path = REPO / rel
    assert path.is_file(), (
        f"{rel} is gone. If the constant moved, update PYTHON_DEFINITIONS -- an "
        "entry nobody maintains stops checking anything."
    )
    literals = _string_args(path, constant)
    assert literals, f"{rel} no longer assigns {constant} from a string literal"
    assert any(form in literal for literal in literals for form in _EXPECTED_FORMS), (
        f"{rel}::{constant} = {literals}, which is not the canonical holdout boundary "
        f"{HOLDOUT_START_ISO}. A boundary that disagrees with the others silently "
        "trains on holdout bars."
    )


@pytest.mark.parametrize(("rel", "key"), sorted(CONFIG_DEFINITIONS.items()))
def test_each_config_definition_names_the_canonical_instant(rel: str, key: str) -> None:
    path = REPO / rel
    assert path.is_file(), f"{rel} is gone; update CONFIG_DEFINITIONS"
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        value = json.loads(text)[key]
    else:
        match = re.search(rf'^\s*{re.escape(key)}\s*:\s*"?([^"\n#]+)"?', text, re.MULTILINE)
        assert match, f"{rel} no longer defines {key}"
        value = match.group(1).strip()
    assert any(form in str(value) for form in _EXPECTED_FORMS), (
        f"{rel}::{key} = {value!r}, not the canonical holdout boundary {HOLDOUT_START_ISO}"
    )


def test_the_scan_would_notice_a_changed_boundary(tmp_path: Path) -> None:
    """Guards the guard. A parser that finds nothing passes every file."""
    fake = tmp_path / "fake.py"
    fake.write_text('HOLDOUT_START = pd.Timestamp("2026-04-01 00:00:00", tz="UTC")\n')
    literals = _string_args(fake, "HOLDOUT_START")
    assert literals == ["2026-04-01 00:00:00", "UTC"]
    assert not any(form in lit for lit in literals for form in _EXPECTED_FORMS)


# -- the contract itself ---------------------------------------------------

def test_the_boundary_is_exclusive() -> None:
    assert is_holdout("2026-05-04T00:00:00+00:00") is True
    assert is_pre_holdout("2026-05-03T23:45:00+00:00") is True
    assert is_holdout(HOLDOUT_START) is True


def test_a_naive_time_is_refused_rather_than_assumed_utc() -> None:
    with pytest.raises(HoldoutBoundaryError, match="naive"):
        is_holdout("2026-05-03T23:45:00")


def test_a_cst_time_lands_on_the_side_its_offset_says() -> None:
    """2026-05-04 07:00 CST is 2026-05-03 23:00 UTC -- before the boundary.

    Reports in this project are written in CST and the boundary is UTC, so the
    offset is the whole question.
    """
    assert is_pre_holdout("2026-05-04T07:00:00+08:00") is True
    assert is_holdout("2026-05-04T08:00:00+08:00") is True


def test_assert_pre_holdout_says_what_it_would_cost() -> None:
    with pytest.raises(HoldoutBoundaryError, match="consumption number N"):
        assert_pre_holdout("2026-06-01T00:00:00+00:00", what="the ETH scan window")


def test_assert_pre_holdout_is_silent_on_a_legal_read() -> None:
    assert_pre_holdout("2026-01-01T00:00:00+00:00") is None

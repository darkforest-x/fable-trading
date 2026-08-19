"""The gold rows live here, not only the manifests that describe them.

C2 migrated datasets/manifests/ -- the identity documents for the frozen gold
cores -- and stopped there. The rows those manifests describe stayed in
yoyo-trading. The result passed every other check: the manifests validated, the
registry rows resolved, the annotation toolchain imported. It was only when
asked "is the data here too" that the gap showed, because nothing in this
repository actually reads gold_v1.jsonl during a test run.

That is the shape of the failure worth guarding: a dataset whose *description*
migrated and whose *contents* did not looks migrated from every angle except
opening it. The next allowed action is P0 into P1 -- Gold Dataset -- so this
repository having no gold rows would be discovered at the worst moment.

Images are deliberately NOT here (272 MB of Label Studio renders, 190 MB of
V3 gold core frames). They stay in the archived repository and are recoverable
by commit and hash; the rows, the candidate pool and the task list are text and
belong with the code that reads them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoyo.datasets.gold_schema import parse_jsonl

REPO = Path(__file__).resolve().parents[2]
ANNOTATIONS = REPO / "datasets" / "annotations"

#: file -> what it is. Named so a missing one says which capability it breaks.
GOLD_FILES = {
    "gold_v1.jsonl": "the canonical owner gold rows",
    "gold_v1_demo.jsonl": "the demo rows the annotation tests exercise",
    "gold_candidates_v1.jsonl": "the candidate pool the Label Studio tasks were built from",
    "gold_labelstudio_v1_tasks.json": "the Label Studio task list",
}


@pytest.mark.parametrize(("name", "purpose"), sorted(GOLD_FILES.items()))
def test_the_gold_file_is_present(name: str, purpose: str):
    path = ANNOTATIONS / name
    assert path.is_file(), (
        f"datasets/annotations/{name} is missing -- {purpose}. Migrating the "
        "manifests without the rows leaves a dataset that looks present from "
        "every angle except opening it."
    )
    assert path.stat().st_size > 0, f"{name} is empty"


def test_the_gold_rows_validate_against_the_canonical_schema():
    """Not just present: parseable by the schema that governs them.

    parse_jsonl runs validate_gold on every row, which refuses a core box past
    decision_bar and refuses any row reporting a holdout read.
    """
    rows = parse_jsonl((ANNOTATIONS / "gold_v1.jsonl").read_text(encoding="utf-8"))
    assert rows, "gold_v1.jsonl parsed to zero rows"
    for row in rows:
        assert row["holdout_read"] is False
        assert row["shape_label"] in ("POSITIVE", "NEGATIVE", "IGNORE")


def test_the_candidate_pool_and_the_task_list_line_up():
    candidates = [
        json.loads(line)
        for line in (ANNOTATIONS / "gold_candidates_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tasks = json.loads((ANNOTATIONS / "gold_labelstudio_v1_tasks.json").read_text(encoding="utf-8"))
    assert len(candidates) == len(tasks), (
        f"{len(candidates)} candidates but {len(tasks)} Label Studio tasks; one of "
        "the two was migrated from a different build"
    )


def test_every_gold_row_can_become_a_canonical_pattern_event():
    """The rows must survive conversion into the cross-layer contract.

    A row that parses but cannot be expressed as a PatternEvent would fail the
    first time P1 tried to use it, rather than here.
    """
    from yoyo.contracts.pattern import from_gold_row

    rows = parse_jsonl((ANNOTATIONS / "gold_v1.jsonl").read_text(encoding="utf-8"))
    for row in rows:
        event = from_gold_row(row)
        assert event.visible_end_at <= event.decision_at
        assert event.label_origin == "owner"


def test_the_images_were_deliberately_left_behind():
    """Guards the decision, so nobody 'fixes' it by copying 460 MB into git."""
    stray = [
        str(p.relative_to(REPO))
        for p in ANNOTATIONS.rglob("*")
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
    ]
    assert not stray, (
        f"{len(stray)} image files under datasets/annotations/. The rendered frames "
        "stay in the archived repository and are recoverable by commit and hash; "
        "task book section 3.6 keeps them out of git."
    )

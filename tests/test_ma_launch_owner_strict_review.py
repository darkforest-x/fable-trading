from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from yoyo.datasets.ma_launch_owner_strict_review import (
    DEFAULT_PREREG,
    resolve_strict_decision,
    validate_strict_preregistration,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments" / "active" / "exp-15m-ma-launch-ma-box-review50-v1" / "results" / "review_manifest.jsonl"


def frozen() -> tuple[dict, list[dict]]:
    prereg = json.loads(DEFAULT_PREREG.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line]
    return prereg, rows


def test_strict_shortlist_is_20_box_proposals_and_30_no_box_rows() -> None:
    prereg, rows = frozen()
    overrides, passes, rejects = validate_strict_preregistration(prereg, rows)
    decisions = [resolve_strict_decision(row, overrides, passes, rejects) for row in rows]
    counts = Counter(row["status"] for row in decisions)
    assert counts == {
        "OWNER_REJECT": 6,
        "CODEX_STRICT_REJECT": 24,
        "OWNER_DIRECTED_REBOX_PROPOSAL": 5,
        "OWNER_REFERENCE_RECROP": 3,
        "CODEX_STRICT_PASS_PENDING_OWNER": 12,
    }
    assert sum(row["core_bars"] > 0 for row in decisions) == 20
    assert sum(row["core_bars"] == 0 for row in decisions) == 30


def test_attached_grt40_moves_right_and_ltc01_is_not_misread_as_owner_directed() -> None:
    prereg, rows = frozen()
    overrides, passes, rejects = validate_strict_preregistration(prereg, rows)
    decisions = [resolve_strict_decision(row, overrides, passes, rejects) for row in rows]
    assert decisions[0]["status"] == "CODEX_STRICT_PASS_PENDING_OWNER"
    assert (decisions[0]["core_start_offset"], decisions[0]["core_end_offset"]) == (-7, -3)
    assert decisions[39]["status"] == "OWNER_DIRECTED_REBOX_PROPOSAL"
    assert (decisions[39]["core_start_offset"], decisions[39]["core_end_offset"]) == (-9, -5)


def test_owner_reference_and_four_bar_instruction_are_preserved() -> None:
    prereg, rows = frozen()
    overrides, passes, rejects = validate_strict_preregistration(prereg, rows)
    decisions = [resolve_strict_decision(row, overrides, passes, rejects) for row in rows]
    assert decisions[17]["core_bars"] == 4
    assert decisions[41]["status"] == "OWNER_REFERENCE_RECROP"
    assert decisions[43]["status"] == "OWNER_REFERENCE_RECROP"
    assert decisions[47]["status"] == "OWNER_REFERENCE_RECROP"

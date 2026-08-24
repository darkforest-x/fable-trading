from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoyo.datasets import owner_long_candidate as mod


def _candidate(sample_id: str, start: int, end: int, *, tier: str = "A_CORE") -> dict:
    start_time = mod.pd.Timestamp("2025-01-01T00:00:00Z") + mod.timedelta(
        minutes=start * mod.BAR_MINUTES
    )
    end_time = mod.pd.Timestamp("2025-01-01T00:00:00Z") + mod.timedelta(
        minutes=end * mod.BAR_MINUTES
    )
    return {
        "sample_id": sample_id,
        "symbol": "TEST",
        "win_start": start,
        "win_end": end,
        "core_global": [start + 2, end - 2],
        "rope_tier": tier,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }


def test_central_core_and_context_are_bounded() -> None:
    assert mod.central_core(100, 108) == (102, 106)
    assert mod.central_core(100, 113) == (103, 109)
    assert mod.dynamic_context(100, 113, 103, 109) == (5, 4)
    with pytest.raises(mod.OwnerLongCandidateError, match="too narrow"):
        mod.central_core(100, 102)


def test_deduplicate_preserves_aliases_and_rejects_tier_drift() -> None:
    first = _candidate("b", 10, 20)
    second = {**first, "sample_id": "a"}
    first.update(owner_preview_path="b.jpg", owner_preview_sha256="b")
    second.update(owner_preview_path="a.jpg", owner_preview_sha256="a")
    unique, summary = mod.deduplicate_targets([first, second])
    assert summary == {
        "duplicate_target_groups": 1,
        "duplicate_annotation_aliases_removed": 1,
        "unique_targets": 1,
    }
    assert unique[0]["sample_id"] == "a"
    assert unique[0]["owner_annotation_ids"] == ["a", "b"]
    with pytest.raises(mod.OwnerLongCandidateError, match="disagree on rope tier"):
        mod.deduplicate_targets([first, {**second, "rope_tier": "B_BROAD"}])


def test_assign_time_splits_keeps_dependencies_together() -> None:
    rows = [_candidate(f"s{i:02d}", i * 20, i * 20 + 10) for i in range(20)]
    summary = mod.assign_time_splits(rows, val_fraction=0.20, purge_bars=15)
    assert summary["dependency_cross_split"] == 0
    assert summary["actual_gap_bars"] >= 15
    assert {row["split"] for row in rows} == {"train", "val", "drop"}


def test_review_join_is_partial_and_never_training_eligible(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    candidates = [
        {
            **_candidate("a", 10, 20),
            "owner_annotation_ids": ["a", "a_alias"],
            "owner_filter_decision": "PENDING",
            "training_eligible": False,
            "production_eligible": False,
        },
        {
            **_candidate("b", 30, 40, tier="B_BROAD"),
            "owner_annotation_ids": ["b"],
            "owner_filter_decision": "PENDING",
            "training_eligible": False,
            "production_eligible": False,
        },
    ]
    pending.write_text("".join(json.dumps(row) + "\n" for row in candidates))
    public = tmp_path / "public.json"
    public.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "items": [
                    {"review_id": "ra", "sample_id": "a"},
                    {"review_id": "raa", "sample_id": "a_alias"},
                    {"review_id": "rb", "sample_id": "b"},
                ],
            }
        )
    )
    export = tmp_path / "answers.json"
    export.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "answers": [
                    {"review_id": "ra", "sample_id": "a", "decision": "KEEP"}
                ],
            }
        )
    )
    summary = mod.join_review_export(
        review_export=export,
        pending_manifest=pending,
        public_manifest=public,
        output_dir=tmp_path / "out",
    )
    assert summary["status_counts"] == {"KEEP": 1, "PENDING": 1}
    assert summary["complete_A_CORE"] is True
    assert summary["complete_all_long_targets"] is False
    joined = mod.read_jsonl(tmp_path / "out" / "review_joined_manifest.jsonl")
    assert all(row["training_eligible"] is False for row in joined)


def test_review_join_rejects_conflicting_duplicate_aliases(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    row = {
        **_candidate("a", 10, 20),
        "owner_annotation_ids": ["a", "a_alias"],
        "owner_filter_decision": "PENDING",
        "training_eligible": False,
        "production_eligible": False,
    }
    pending.write_text(json.dumps(row) + "\n")
    public = tmp_path / "public.json"
    public.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "items": [
                    {"review_id": "ra", "sample_id": "a"},
                    {"review_id": "raa", "sample_id": "a_alias"},
                ],
            }
        )
    )
    export = tmp_path / "answers.json"
    export.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "answers": [
                    {"review_id": "ra", "decision": "KEEP"},
                    {"review_id": "raa", "decision": "REMOVE"},
                ],
            }
        )
    )
    with pytest.raises(mod.OwnerLongCandidateError, match="conflicting decisions"):
        mod.join_review_export(
            review_export=export,
            pending_manifest=pending,
            public_manifest=public,
            output_dir=tmp_path / "out",
        )

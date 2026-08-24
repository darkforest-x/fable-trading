from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoyo.contracts.holdout import HoldoutBoundaryError
from yoyo.datasets import owner_long_candidate as mod


def _candidate(sample_id: str, start: int, end: int, *, tier: str = "A_CORE") -> dict:
    start_time = mod.pd.Timestamp("2025-01-01T00:00:00Z") + mod.timedelta(
        minutes=start * mod.BAR_MINUTES
    )
    end_time = mod.pd.Timestamp("2025-01-01T00:00:00Z") + mod.timedelta(
        minutes=end * mod.BAR_MINUTES
    )
    return {
        "event_id": f"event-{start}-{end}",
        "sample_id": sample_id,
        "symbol": "TEST",
        "source_csv": "data/test.csv",
        "source_owner_cut_time": end_time.isoformat(),
        "source_owner_global": [start + 1, end - 1],
        "source_owner_bars": end - start - 1,
        "win_start": start,
        "win_end": end,
        "core_global": [start + 2, end - 2],
        "rope_tier": tier,
        "owner_row_sha256": f"row-{sample_id}",
        "owner_original_geometry": {
            "bar_b0": 10,
            "bar_b1": 14,
            "width_bars": 5,
            "yolo_box": [0.5, 0.5, 0.1, 0.1],
            "box_index": 0,
            "n_boxes_on_image": 1,
        },
        "owner_preview_path": f"{sample_id}.jpg",
        "owner_preview_sha256": f"preview-{sample_id}",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }


def _write_public(path: Path, pairs: list[tuple[str, str]]) -> str:
    path.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "items": [
                    {"review_id": review_id, "sample_id": sample_id}
                    for review_id, sample_id in pairs
                ],
            }
        )
    )
    return mod.sha256_file(path)


def test_central_core_and_context_are_bounded() -> None:
    assert mod.central_core(100, 108) == (102, 106)
    assert mod.central_core(100, 113) == (103, 109)
    assert mod.dynamic_context(100, 113, 103, 109) == (5, 4)
    with pytest.raises(mod.OwnerLongCandidateError, match="too narrow"):
        mod.central_core(100, 102)


def test_deduplicate_preserves_alias_lineage_and_rejects_tier_drift() -> None:
    first = _candidate("b", 10, 20)
    second = {
        **first,
        "sample_id": "a",
        "owner_row_sha256": "row-a",
        "owner_preview_path": "a.jpg",
        "owner_preview_sha256": "preview-a",
    }
    unique, summary = mod.deduplicate_targets([first, second])
    assert summary == {
        "duplicate_target_groups": 1,
        "duplicate_annotation_aliases_removed": 1,
        "unique_targets": 1,
    }
    assert unique[0]["sample_id"] == "a"
    assert unique[0]["owner_annotation_ids"] == ["a", "b"]
    assert [
        row["annotation_id"] for row in unique[0]["owner_annotation_lineage"]
    ] == ["a", "b"]
    with pytest.raises(mod.OwnerLongCandidateError, match="disagree on rope tier"):
        mod.deduplicate_targets([first, {**second, "rope_tier": "B_BROAD"}])


def test_assign_time_splits_keeps_dependencies_and_events_together() -> None:
    rows = [_candidate(f"s{i:02d}", i * 20, i * 20 + 10) for i in range(20)]
    summary = mod.assign_time_splits(rows, val_fraction=0.20, purge_bars=15)
    assert summary["dependency_cross_split"] == 0
    assert summary["event_cross_split"] == 0
    assert summary["nominal_timestamp_grid_gap_bars"] >= 15
    assert summary["actual_ohlc_gap_bars"] is None
    assert summary["purge_proof_status"] == "pending_bounded_ohlc_materialization"
    assert {row["split"] for row in rows} == {"train", "val", "drop"}


def test_review_join_partial_alias_remains_pending_and_ineligible(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    public = tmp_path / "public.json"
    public_sha = _write_public(
        public,
        [("ra", "a"), ("raa", "a_alias"), ("rb", "b")],
    )
    candidates = [
        {
            **_candidate("a", 10, 20),
            "owner_annotation_ids": ["a", "a_alias"],
            "owner_filter_decision": "PENDING",
            "review_public_manifest_sha256": public_sha,
            "training_eligible": False,
            "production_eligible": False,
        },
        {
            **_candidate("b", 30, 40, tier="B_BROAD"),
            "owner_annotation_ids": ["b"],
            "owner_filter_decision": "PENDING",
            "review_public_manifest_sha256": public_sha,
            "training_eligible": False,
            "production_eligible": False,
        },
    ]
    pending.write_text("".join(json.dumps(row) + "\n" for row in candidates))
    export = tmp_path / "answers.json"
    export.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "exported_at": "2026-08-24T12:00:00+08:00",
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
        reviewer="owner",
        expected_public_rows=3,
    )
    assert summary["status_counts"] == {"PENDING": 2}
    assert summary["complete_A_CORE"] is False
    assert summary["complete_all_long_targets"] is False
    assert summary["kept_candidates"] == 0
    joined = mod.read_jsonl(tmp_path / "out" / "review_joined_manifest.jsonl")
    assert all(row["training_eligible"] is False for row in joined)
    assert joined[0]["owner_filter_review_complete"] is False


def test_review_join_resolves_target_only_when_all_aliases_agree(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    public = tmp_path / "public.json"
    public_sha = _write_public(public, [("ra", "a"), ("raa", "a_alias")])
    candidate = {
        **_candidate("a", 10, 20),
        "owner_annotation_ids": ["a", "a_alias"],
        "owner_filter_decision": "PENDING",
        "review_public_manifest_sha256": public_sha,
        "training_eligible": False,
        "production_eligible": False,
    }
    pending.write_text(json.dumps(candidate) + "\n")
    export = tmp_path / "answers.json"
    export.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "exported_at": "2026-08-24T12:00:00+08:00",
                "answers": [
                    {"review_id": "ra", "decision": "KEEP"},
                    {"review_id": "raa", "decision": "KEEP"},
                ],
            }
        )
    )
    summary = mod.join_review_export(
        review_export=export,
        pending_manifest=pending,
        public_manifest=public,
        output_dir=tmp_path / "out",
        reviewer="owner",
        expected_public_rows=2,
    )
    assert summary["status_counts"] == {"KEEP": 1}
    assert summary["complete_A_CORE"] is True
    assert summary["kept_candidates"] == 1


def test_review_join_rejects_conflicting_duplicate_aliases(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    public = tmp_path / "public.json"
    public_sha = _write_public(public, [("ra", "a"), ("raa", "a_alias")])
    row = {
        **_candidate("a", 10, 20),
        "owner_annotation_ids": ["a", "a_alias"],
        "owner_filter_decision": "PENDING",
        "review_public_manifest_sha256": public_sha,
        "training_eligible": False,
        "production_eligible": False,
    }
    pending.write_text(json.dumps(row) + "\n")
    export = tmp_path / "answers.json"
    export.write_text(
        json.dumps(
            {
                "pack_id": mod.PACK_ID,
                "exported_at": "2026-08-24T12:00:00+08:00",
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
            reviewer="owner",
            expected_public_rows=2,
        )


def test_review_public_manifest_rejects_duplicate_sample_ids(tmp_path: Path) -> None:
    public = tmp_path / "public.json"
    _write_public(public, [("r1", "same"), ("r2", "same")])
    with pytest.raises(mod.OwnerLongCandidateError, match="duplicate sample_id"):
        mod._validate_review_public_manifest(public, expected_rows=2)


def test_source_join_rejects_original_geometry_width_drift(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.touch()
    sheet = {
        "box_id": "s1",
        "owner_side": "long",
        "symbol": "TEST",
        "cut_global": "20",
        "cut_time": "2025-01-01T05:00:00+00:00",
        "bar_b0": "10",
        "bar_b1": "14",
        "width_bars": "4",
        "box_index": "0",
        "n_boxes_on_image": "1",
        "yolo_xc": "0.5",
        "yolo_yc": "0.5",
        "yolo_w": "0.1",
        "yolo_h": "0.1",
    }
    score = {
        "sample_id": "s1",
        "owner_side": "long",
        "symbol": "TEST",
        "decision_bar": 20,
        "decision_time": sheet["cut_time"],
        "resolved_source_csv": str(source),
    }
    with pytest.raises(mod.OwnerLongCandidateError, match="box width mismatch"):
        mod._validate_source_join(
            [sheet],
            [score],
            expected_direction_rows=1,
            expected_long_rows=1,
        )


def test_exact_holdout_boundary_is_not_pre_holdout() -> None:
    assert mod.is_pre_holdout(mod.HOLDOUT_START_ISO) is False
    with pytest.raises(HoldoutBoundaryError, match="at or after"):
        mod.assert_pre_holdout(mod.HOLDOUT_START_ISO, what="test row")


def test_event_id_is_stable_and_output_overwrite_is_refused(tmp_path: Path) -> None:
    kwargs = {
        "symbol": "TEST",
        "source_csv": "data/test.csv",
        "win_start": 10,
        "win_end": 20,
        "core_start": 12,
        "core_end": 17,
    }
    assert mod.stable_event_id(**kwargs) == mod.stable_event_id(**kwargs)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "receipt.json").write_text("{}")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        mod._ensure_output_is_new(occupied, role="test artifact")

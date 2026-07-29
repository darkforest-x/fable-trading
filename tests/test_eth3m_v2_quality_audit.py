from __future__ import annotations

import pandas as pd

from src.detection.eth3m_v2_quality_audit import audit_dataset_quality


def test_real_v2_semantic_audit_exposes_dependency_and_anchor_confounding() -> None:
    manifest = pd.read_csv("datasets/eth_3m_short_pilot_v2/manifest.csv")
    timing = pd.read_csv(
        "analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv",
        usecols=[
            "task_id",
            "candidate_time",
            "first_below_all_mas_lag_bars",
        ],
    )
    result = audit_dataset_quality(manifest, timing)

    counts = result["dependency_window"]["counts"]
    assert counts["all"] == {"images": 137, "blocks": 32}
    assert counts["train"] == {"images": 95, "blocks": 25}
    assert counts["val"] == {"images": 42, "blocks": 7}
    assert counts["all_positive"] == {"images": 30, "blocks": 23}
    assert counts["val_positive"] == {"images": 8, "blocks": 5}

    provenance = result["provenance_confounding"]["source_only_shortcut"]
    anchor = result["anchor_rule_confounding"]["shortcut"]
    assert (provenance["tp"], provenance["fp"], provenance["tn"], provenance["fn"]) == (
        30,
        0,
        107,
        0,
    )
    assert (anchor["tp"], anchor["fp"], anchor["tn"], anchor["fn"]) == (
        30,
        1,
        106,
        0,
    )
    assert anchor["accuracy"] == 136 / 137
    assert result["scope"]["holdout_read"] is False


def test_audit_fails_closed_if_used_metadata_reaches_holdout() -> None:
    manifest = pd.DataFrame(
        [
            {
                "sample_id": "x",
                "split": "train",
                "target": 1,
                "event_id": 1,
                "label_provenance": "owner_batch_chat_confirmed_current_T",
                "sample_kind": "confirmed_current_tip",
                "source_task_id": 1,
                "anchor_time": "2026-05-04T00:00:00Z",
                "input_start_time": "2026-05-03T14:03:00Z",
                "label_end_time": "2026-05-04T03:00:00Z",
            },
            {
                "sample_id": "y",
                "split": "val",
                "target": 0,
                "event_id": 2,
                "label_provenance": "label_studio_project_53_owner_no",
                "sample_kind": "owner_no_tip_negative",
                "source_task_id": 2,
                "anchor_time": "2026-05-05T00:00:00Z",
                "input_start_time": "2026-05-04T14:03:00Z",
                "label_end_time": "2026-05-05T03:00:00Z",
            },
        ]
    )
    timing = pd.DataFrame(
        [
            {"task_id": 1, "candidate_time": "2026-05-04T00:00:00Z", "first_below_all_mas_lag_bars": 0},
            {"task_id": 2, "candidate_time": "2026-05-05T00:00:00Z", "first_below_all_mas_lag_bars": 0},
        ]
    )

    try:
        audit_dataset_quality(manifest, timing)
    except ValueError as exc:
        assert "holdout" in str(exc)
    else:
        raise AssertionError("holdout metadata should fail closed")

import json

import pytest

from scripts.summarize_local_signal_v2_early_frontier_review import (
    auc,
    metrics,
    summarize,
)

BLOCKS = ("B02_20250915", "B03_20251115", "C05_20260215")


def write_jsonl(path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def manifest_rows() -> list[dict]:
    rows = []
    for index in range(300):
        stratum = "yes_like" if index < 150 else "similar_no_boundary"
        rows.append(
            {
                "review_id": f"S{index + 1:03d}",
                "event_id": f"event{index:04d}",
                "symbol": f"SYM{index % 40}_USDT_SWAP",
                "candidate_block": BLOCKS[index % 3],
                "retrieval_stratum_internal": stratum,
                "owner_yes_affinity_internal": index / 300,
                "nearest_owner_yes_distance_internal": 10.0 - index / 300,
                "model_confidence": 0.3 + (index % 5) / 10,
                "causal_review_actual_span_pct": 0.5 + index % 5,
                "box_start_bar": 100,
                "box_end_bar": 104 + index % 4,
                "decision_bar": 107 + index % 4,
                "visible_end_bar": 107 + index % 4,
                "window_length": 12 + index % 8,
                "future_bars": 0,
                "selection_future_used": False,
                "decision_time": "2026-02-15T01:15:00+00:00",
                "future_review_end_time": "2026-02-16T00:00:00+00:00",
                "training_eligible": False,
                "production_eligible": False,
                "holdout_read": False,
            }
        )
    return rows


def verdict_rows() -> list[dict]:
    return [
        {
            "review_id": f"S{index + 1:03d}",
            "owner_verdict": "YES" if index % 3 == 0 else "NO",
            "reviewed_at": f"2026-08-12T05:{index // 60:02d}:{index % 60:02d}+00:00",
        }
        for index in range(300)
    ]


def write_pack(tmp_path, manifest=None, verdicts=None) -> None:
    write_jsonl(tmp_path / "review_manifest.jsonl", manifest or manifest_rows())
    write_jsonl(tmp_path / "owner_verdicts.jsonl", verdicts or verdict_rows())
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "protocol": "local_signal_v2_early_frontier_review300_v1_20260812",
                "manifest_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )


def test_metrics_adds_wilson_interval() -> None:
    result = metrics([{"owner_verdict": "YES"}] * 119 + [{"owner_verdict": "NO"}] * 181)
    assert result["yes_rate_excluding_skip"] == pytest.approx(119 / 300)
    assert result["wilson95"][0] < 119 / 300 < result["wilson95"][1]


def test_auc_is_one_for_perfect_separation_and_half_for_all_ties() -> None:
    assert auc([1.0, 2.0, 3.0, 4.0], [False, False, True, True]) == 1.0
    assert auc([1.0, 1.0, 1.0, 1.0], [False, False, True, True]) == 0.5


def test_summary_unblinds_both_retrieval_strata(tmp_path) -> None:
    write_pack(tmp_path)
    result = summarize(tmp_path, permutations=50)
    assert result["overall"]["reviewed"] == 300
    assert result["overall"]["YES"] == 100
    strata = result["internal_strata_after_unblinding"]
    assert strata["yes_like"]["reviewed"] == 150
    assert strata["similar_no_boundary"]["reviewed"] == 150
    assert 0.0 <= strata["yes_rate_gap"]["two_sided_p"] <= 1.0
    assert result["automatic_training_started"] is False
    assert result["holdout_read"] is False
    assert (tmp_path / "owner_review_joined.jsonl").exists()


def test_summary_keeps_latest_verdict_for_revised_ids(tmp_path) -> None:
    verdicts = verdict_rows()
    verdicts.append(
        {
            "review_id": "S001",
            "owner_verdict": "NO",
            "reviewed_at": "2026-08-12T06:00:00+00:00",
        }
    )
    write_pack(tmp_path, verdicts=verdicts)
    result = summarize(tmp_path, permutations=10)
    assert result["data_quality"]["revised_verdicts"] == 1
    assert result["overall"]["YES"] == 99


def test_summary_rejects_incomplete_review(tmp_path) -> None:
    write_pack(tmp_path, verdicts=verdict_rows()[:299])
    with pytest.raises(ValueError, match="review incomplete"):
        summarize(tmp_path, permutations=10)


def test_summary_rejects_training_eligible_pack(tmp_path) -> None:
    manifest = manifest_rows()
    manifest[7]["training_eligible"] = True
    write_pack(tmp_path, manifest=manifest)
    with pytest.raises(ValueError, match="training_eligible"):
        summarize(tmp_path, permutations=10)


def test_summary_rejects_future_context_reaching_holdout(tmp_path) -> None:
    manifest = manifest_rows()
    manifest[11]["future_review_end_time"] = "2026-05-04T00:00:00+00:00"
    write_pack(tmp_path, manifest=manifest)
    with pytest.raises(ValueError, match="pre-holdout"):
        summarize(tmp_path, permutations=10)


def test_summary_rejects_image_that_outlives_decision_bar(tmp_path) -> None:
    manifest = manifest_rows()
    manifest[3]["visible_end_bar"] = manifest[3]["decision_bar"] + 2
    write_pack(tmp_path, manifest=manifest)
    with pytest.raises(ValueError, match="decision bar"):
        summarize(tmp_path, permutations=10)

import json

import pytest

from scripts.summarize_local_signal_v2_semantic_review import metrics, summarize


def write_jsonl(path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def manifest_rows() -> list[dict]:
    rows = []
    for index in range(200):
        source = "positive_pool" if index < 100 else "canary_candidate"
        item = {
            "review_id": f"S{index + 1:03d}",
            "source_type": source,
            "sampling_strata": {
                "volatility": ("low", "mid", "high")[index % 3],
                "confidence": ("low", "mid", "high")[index % 3],
            },
            "causal_review_actual_span_pct": 0.5 + index % 5,
        }
        if source == "canary_candidate":
            offset = index - 100
            item["canary_cohort"] = (
                "common_retained"
                if offset < 50
                else "r2_new"
                if offset < 75
                else "r1_suppressed"
            )
        rows.append(item)
    return rows


def verdict_rows() -> list[dict]:
    return [
        {
            "review_id": f"S{index + 1:03d}",
            "owner_verdict": "YES" if index < 85 or 100 <= index < 111 else "NO",
            "reviewed_at": f"2026-08-12T00:{index // 60:02d}:{index % 60:02d}+00:00",
        }
        for index in range(200)
    ]


def test_metrics_adds_wilson_interval() -> None:
    result = metrics(
        [{"owner_verdict": "YES"}] * 85 + [{"owner_verdict": "NO"}] * 15
    )
    assert result["yes_rate_excluding_skip"] == 0.85
    assert result["wilson95"][0] < 0.85 < result["wilson95"][1]


def test_summary_requires_one_exact_verdict_per_manifest_id(tmp_path) -> None:
    write_jsonl(tmp_path / "review_manifest.jsonl", manifest_rows())
    (tmp_path / "summary.json").write_text(
        json.dumps({"protocol": "test"}), encoding="utf-8"
    )
    rows = verdict_rows()
    rows[-1]["review_id"] = "S001"
    write_jsonl(tmp_path / "owner_verdicts.jsonl", rows)
    with pytest.raises(ValueError, match="duplicate IDs"):
        summarize(tmp_path)


def test_summary_writes_complete_diagnostics_without_training(tmp_path) -> None:
    write_jsonl(tmp_path / "review_manifest.jsonl", manifest_rows())
    write_jsonl(tmp_path / "owner_verdicts.jsonl", verdict_rows())
    (tmp_path / "summary.json").write_text(
        json.dumps({"protocol": "test"}), encoding="utf-8"
    )
    result = summarize(tmp_path)
    assert result["positive_pool"]["YES"] == 85
    assert result["canary_candidate"]["YES"] == 11
    assert result["data_quality"]["id_set_exact_match"] is True
    assert result["diagnosis"] == "CASE_B_POSITIVE_HIGH_CANARY_LOW"
    assert result["automatic_training_started"] is False
    assert result["holdout_read"] is False
    assert (tmp_path / "owner_review_summary.json").is_file()
    assert (tmp_path / "owner_review_diagnostics.json").is_file()
    joined = [
        json.loads(line)
        for line in (tmp_path / "owner_review_joined.jsonl").read_text().splitlines()
    ]
    assert len(joined) == 200
    assert all(row["owner_verdict"] in {"YES", "NO"} for row in joined)
    assert all(row["reviewed_at"] for row in joined)

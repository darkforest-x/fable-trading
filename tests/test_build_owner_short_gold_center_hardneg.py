from __future__ import annotations

from collections import Counter
import json

from scripts.build_owner_short_gold_center_hardneg import (
    build_audit,
    select_hard_negatives,
)


def _positive(sample_id: str, window: int) -> dict:
    return {"sample_id": sample_id, "win_len": window}


def _candidate(sample_id: str, window: int) -> dict:
    return {"sample_id": sample_id, "win_len": window}


def test_selection_keeps_exact_two_hard_per_positive_window_histogram() -> None:
    positives = [_positive("p1", 12), _positive("p2", 12), _positive("p3", 14)]
    owner_long = [_candidate("long1", 12), _candidate("long2", 14)]
    backgrounds = [
        _candidate("b1", 12),
        _candidate("b2", 12),
        _candidate("b3", 12),
        _candidate("b4", 14),
        _candidate("b5", 14),
    ]
    predictions = [
        {"sample_id": "b1", "max_confidence": 0.2, "box_count_at_floor": 1},
        {"sample_id": "b2", "max_confidence": 0.8, "box_count_at_floor": 1},
        {"sample_id": "b3", "max_confidence": 0.5, "box_count_at_floor": 1},
        {"sample_id": "b4", "max_confidence": 0.3, "box_count_at_floor": 1},
        {"sample_id": "b5", "max_confidence": 0.7, "box_count_at_floor": 1},
    ]
    selected, profile = select_hard_negatives(
        positives, owner_long, backgrounds, predictions
    )
    assert Counter(row["win_len"] for row in selected) == Counter({12: 4, 14: 2})
    assert profile["owner_long_selected"] == 2
    selected_background_ids = {
        row["sample_id"]
        for row in selected
        if row["selected_hard_kind"] == "model_ranked_background"
    }
    assert selected_background_ids == {"b1", "b2", "b3", "b5"}


def test_selection_uses_rank_not_a_score_threshold() -> None:
    positives = [_positive("p1", 12)]
    backgrounds = [_candidate("b1", 12), _candidate("b2", 12)]
    predictions = [
        {"sample_id": "b1", "max_confidence": 0.0, "box_count_at_floor": 0},
        {"sample_id": "b2", "max_confidence": 0.0, "box_count_at_floor": 0},
    ]
    selected, profile = select_hard_negatives(positives, [], backgrounds, predictions)
    assert len(selected) == 2
    assert profile["model_ranked_zero_score"] == 2


def test_audit_renders_individual_owner_and_model_cards(tmp_path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = [
        {
            "sample_id": "long1",
            "symbol": "ETH_USDT_SWAP",
            "win_len": 14,
            "win_start": 10,
            "core_start": 14,
            "core_end": 19,
            "end_time": "2026-01-01T00:00:00Z",
            "image_path": "datasets/example/long1.png",
            "selected_hard_kind": "owner_long",
        },
        {
            "sample_id": "background1",
            "symbol": "BTC_USDT_SWAP",
            "win_len": 12,
            "end_time": "2026-01-01T00:15:00Z",
            "image_path": "datasets/example/background1.png",
            "selected_hard_kind": "model_ranked_background",
            "max_confidence": 0.75,
        },
    ]
    (dataset / "hard_negative_manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    output = tmp_path / "audit.html"

    summary = build_audit(dataset, output, per_kind=1)

    page = output.read_text(encoding="utf-8")
    assert summary["owner_long_shown"] == 1
    assert summary["model_ranked_shown"] == 1
    assert page.count("<article>") == 2
    assert "Owner-long #001" in page
    assert "Model-ranked #001" in page
    assert "class=\"core\"" in page
    assert "baseline max conf 0.750" in page

import pytest

from scripts.evaluate_15m_ma_launch_owner_neg30000_val import summarize_fires


def test_summarize_fires_counts_images_boxes_classes_and_top_confidence() -> None:
    rows = [
        {
            "sample_id": "none",
            "negative_kind": "easy",
            "boxes": 0,
            "classes": [],
            "confidences": [],
        },
        {
            "sample_id": "one",
            "negative_kind": "hard",
            "boxes": 1,
            "classes": [0],
            "confidences": [0.4],
        },
        {
            "sample_id": "two",
            "negative_kind": "hard",
            "boxes": 2,
            "classes": [1, 1],
            "confidences": [0.9, 0.3],
        },
    ]
    summary = summarize_fires(rows)
    assert summary["images"] == 3
    assert summary["fired_images"] == 2
    assert summary["fire_rate"] == pytest.approx(2 / 3)
    assert summary["boxes"] == 3
    assert summary["false_boxes_per_1000_images"] == pytest.approx(1000.0)
    assert summary["class_box_counts"] == {"0": 1, "1": 2}
    assert summary["confidence"]["median"] == pytest.approx(0.4)
    assert summary["top_fired_samples"][0]["sample_id"] == "two"

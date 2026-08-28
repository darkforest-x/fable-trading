import random

from scripts.render_15m_ma_launch_t3_validation_preview import (
    row_class_id,
    row_identity,
    select_preview_rows,
    yolo_xywhn_to_xyxy,
)


def rows() -> list[dict]:
    values = []
    for class_id, class_name in ((0, "dense_long"), (1, "dense_short")):
        for index in range(10):
            values.append(
                {
                    "sample_id": f"p{class_id}-{index}",
                    "split": "val",
                    "sample_kind": "positive_weak",
                    "class_id": class_id,
                    "class_name": class_name,
                }
            )
    for index in range(20):
        values.append(
            {
                "sample_id": f"n-{index}",
                "split": "val",
                "sample_kind": "negative_easy",
                "class_id": None,
                "class_name": None,
            }
        )
    return values


def test_preview_selection_is_balanced_and_order_independent() -> None:
    source = rows()
    expected = select_preview_rows(source)
    random.Random(9).shuffle(source)
    actual = select_preview_rows(source)
    assert [row["sample_id"] for row in actual] == [row["sample_id"] for row in expected]
    assert sum(row["class_id"] == 0 for row in actual) == 4
    assert sum(row["class_id"] == 1 for row in actual) == 4
    assert sum(row["sample_kind"] == "negative_easy" for row in actual) == 8


def test_yolo_geometry_converts_and_clips() -> None:
    assert yolo_xywhn_to_xyxy((0.5, 0.5, 0.25, 0.5), 100, 80) == (38, 20, 62, 60)
    assert yolo_xywhn_to_xyxy((0.0, 0.0, 0.5, 0.5), 100, 80) == (0, 0, 25, 20)


def test_preview_selection_accepts_owner_v2_manifest_vocabulary() -> None:
    source = []
    for class_id in (0, 1):
        for index in range(8):
            source.append(
                {
                    "sample_id": f"v2-p{class_id}-{index}",
                    "split": "val",
                    "sample_kind": "positive",
                    "class_id": class_id,
                }
            )
    for index in range(12):
        source.append(
            {
                "sample_id": f"v2-n-{index}",
                "split": "val",
                "sample_kind": "negative",
                "negative_kind": "easy",
                "class_id": None,
            }
        )
    selected = select_preview_rows(source)
    assert len(selected) == 16
    assert sum(row.get("class_id") == 0 for row in selected) == 4
    assert sum(row.get("class_id") == 1 for row in selected) == 4
    assert sum(row.get("negative_kind") == "easy" for row in selected) == 8


def test_preview_selection_accepts_grade_a_direction_and_image_identity() -> None:
    source = []
    for direction in ("LONG", "SHORT"):
        for event in range(5):
            for post_bars in (2, 3):
                source.append(
                    {
                        "sample_id": f"event-{direction}-{event}",
                        "dataset_sample_id": f"image-{direction}-{event}-{post_bars}",
                        "split": "val",
                        "sample_kind": "positive",
                        "direction": direction,
                        "class_id": None,
                        "post_bars": post_bars,
                    }
                )
    for index in range(10):
        source.append(
            {
                "sample_id": f"negative-event-{index // 2}",
                "dataset_sample_id": f"negative-image-{index}",
                "split": "val",
                "sample_kind": "negative",
                "negative_kind": "easy",
                "class_id": None,
            }
        )

    selected = select_preview_rows(source)
    assert len(selected) == 16
    assert len({row_identity(row) for row in selected}) == 16
    assert sum(row_class_id(row) == 0 for row in selected) == 4
    assert sum(row_class_id(row) == 1 for row in selected) == 4
    assert sum(row.get("negative_kind") == "easy" for row in selected) == 8

import pytest

from scripts.analyze_eth3m_v10_yes_no_labels import owner_choice, parse_label_export


def _task(task_id: int, choice: str):
    return {
        "id": 18000 + task_id,
        "data": {"task_id": task_id},
        "annotations": [
            {
                "id": 12000 + task_id,
                "created_at": "2026-07-29T00:00:00Z",
                "was_cancelled": False,
                "result": [
                    {
                        "from_name": "box",
                        "origin": "prediction",
                        "value": {"rectanglelabels": ["short_start"]},
                    },
                    {
                        "from_name": "shape",
                        "origin": "manual",
                        "value": {"choices": ["invalid"]},
                    },
                    {
                        "from_name": "is_target",
                        "origin": "manual",
                        "value": {"choices": [choice]},
                    },
                ],
            }
        ],
    }


def test_parse_export_uses_only_is_target_choice():
    frame = parse_label_export([_task(2, "是"), _task(1, "不是")])
    assert frame["task_id"].tolist() == [1, 2]
    assert frame["owner_label"].tolist() == ["不是", "是"]
    assert frame["owner_is_target"].tolist() == [0, 1]


def test_owner_choice_rejects_missing_choice():
    task = _task(1, "是")
    task["annotations"][0]["result"] = []
    with pytest.raises(ValueError, match="expected one is_target"):
        owner_choice(task)


def test_owner_choice_rejects_multiple_choices():
    task = _task(1, "是")
    task["annotations"][0]["result"].append(
        {
            "from_name": "is_target",
            "origin": "manual",
            "value": {"choices": ["不是"]},
        }
    )
    with pytest.raises(ValueError, match="expected one is_target"):
        owner_choice(task)

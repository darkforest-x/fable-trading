"""Unit tests for Label Studio import-path discovery."""

from scripts.ls_auto_import import local_file_roots


def test_local_file_roots_supports_dual_image_fields() -> None:
    tasks = [
        {
            "data": {
                "causal_image": (
                    "/data/local-files/?d=eth_3m_v10_prebox200/causal_images/task_001.png"
                ),
                "review_image": (
                    "/data/local-files/?d=eth_3m_v10_prebox200/review_images/task_001.jpg"
                ),
                "task_id": 1,
            }
        }
    ]

    assert local_file_roots(tasks) == ["eth_3m_v10_prebox200"]


def test_local_file_roots_collects_mixed_pack_roots_once() -> None:
    tasks = [
        {"data": {"image": "/data/local-files/?d=dense_swap_v1/images/a.png"}},
        {"data": {"image": "/data/local-files/?d=round6_scout/images/b.png"}},
        {"data": {"image": "/data/local-files/?d=dense_swap_v1/images/c.png"}},
    ]

    assert local_file_roots(tasks) == ["dense_swap_v1", "round6_scout"]

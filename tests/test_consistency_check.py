"""Unit tests for detection consistency_check IoU matching."""
from __future__ import annotations

from src.detection.consistency_check import iou, match_greedy


def test_iou_identical_is_one() -> None:
    b = (0.5, 0.5, 0.2, 0.2)
    assert abs(iou(b, b) - 1.0) < 1e-9


def test_iou_disjoint_is_zero() -> None:
    a = (0.2, 0.2, 0.1, 0.1)
    b = (0.8, 0.8, 0.1, 0.1)
    assert iou(a, b) == 0.0


def test_match_greedy_one_to_one() -> None:
    gt = [(0.3, 0.3, 0.2, 0.2), (0.7, 0.7, 0.2, 0.2)]
    pred = [(0.31, 0.31, 0.2, 0.2), (0.1, 0.1, 0.05, 0.05)]
    m, ng, np_ = match_greedy(gt, pred, iou_thr=0.5)
    assert ng == 2 and np_ == 2
    assert m == 1

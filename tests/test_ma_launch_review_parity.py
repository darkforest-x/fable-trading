from __future__ import annotations

import pytest

from yoyo.datasets.ma_launch_review_parity import (
    ReviewParityError,
    css_box,
    parse_yolo_label,
)


def test_parse_yolo_label_and_css_box() -> None:
    class_id, cx, cy, width, height = parse_yolo_label(
        "0 0.500000 0.250000 0.200000 0.100000\n"
    )
    assert (class_id, cx, cy, width, height) == (0, 0.5, 0.25, 0.2, 0.1)
    assert css_box((cx, cy, width, height)) == (
        "left:40.000000%;top:20.000000%;width:20.000000%;height:10.000000%;"
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "0 0.5 0.5 0.2\n",
        "2 0.5 0.5 0.2 0.2\n",
        "0 0.05 0.5 0.2 0.2\n",
        "0 0.5 0.5 -0.2 0.2\n",
        "0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n",
    ],
)
def test_parse_yolo_label_rejects_invalid_rows(text: str) -> None:
    with pytest.raises((ReviewParityError, ValueError)):
        parse_yolo_label(text)


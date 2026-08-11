from scripts.build_owner_gold_center_crop_review import central_core, dynamic_context, yolo_iou


def test_central_core_is_inside_source_and_bounded() -> None:
    for width in range(4, 32):
        source_start = 100
        source_end = source_start + width - 1
        core_start, core_end = central_core(source_start, source_end)
        assert source_start <= core_start <= core_end <= source_end
        assert 4 <= core_end - core_start + 1 <= 7
        left = core_start - source_start
        right = source_end - core_end
        assert abs(left - right) <= 1


def test_dynamic_context_respects_short_delay_contract() -> None:
    core_start, core_end = central_core(100, 114)
    pre_bars, post_bars = dynamic_context(100, 114, core_start, core_end)
    assert 5 <= pre_bars <= 7
    assert 3 <= post_bars <= 5
    assert 12 <= pre_bars + (core_end - core_start + 1) + post_bars <= 19


def test_exact_yolo_box_iou() -> None:
    box = (0.5, 0.4, 0.2, 0.1)
    assert yolo_iou(box, box) == 1.0
    assert yolo_iou(box, (0.9, 0.9, 0.05, 0.05)) == 0.0

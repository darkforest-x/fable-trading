"""Pure arithmetic tests for Local Signal V2 P1 event evaluation."""
from scripts.eval_local_signal_v2_p1 import score_threshold, x_center_to_bar


def test_x_center_round_trip_at_chart_bars():
    width = 1280
    left = 12
    plot_w = width - 24
    for window_len in (24, 30, 200):
        for bar in (0, window_len // 2, window_len - 1):
            x_px = left + bar / (window_len - 1) * plot_w
            assert x_center_to_bar(x_px / width, window_len) == bar


def test_event_metrics_count_duplicates_and_unmatched_boxes_as_fp():
    rows = [
        {
            "eval_id": "p1",
            "sample_type": "positive",
            "anchor_local_bar": 20,
            "confirm_delay": 2,
        },
        {"eval_id": "p2", "sample_type": "positive", "anchor_local_bar": 20},
        {"eval_id": "n1", "sample_type": "easy_negative", "anchor_local_bar": None},
    ]
    predictions = {
        "p1": [
            {"confidence": 0.8, "center_bar": 20},
            {"confidence": 0.7, "center_bar": 21},
            {"confidence": 0.6, "center_bar": 4},
        ],
        "p2": [],
        "n1": [{"confidence": 0.9, "center_bar": 10}],
    }
    score = score_threshold(rows, predictions, 0.5)
    assert score["tp_events"] == 1
    assert score["missed_events"] == 1
    assert score["duplicate_detections"] == 1
    assert score["false_positive_boxes"] == 3
    assert score["event_precision"] == 0.25
    assert score["event_recall"] == 0.5
    assert score["fp_per_1000_bars"] == 1000.0
    assert score["mean_detection_latency_bars"] == 2.0


def test_threshold_is_inclusive_and_filters_low_scores():
    rows = [
        {
            "eval_id": "p",
            "sample_type": "positive",
            "anchor_local_bar": 5,
            "confirm_delay": 1,
        }
    ]
    predictions = {"p": [{"confidence": 0.5, "center_bar": 5}]}
    assert score_threshold(rows, predictions, 0.5)["tp_events"] == 1
    assert score_threshold(rows, predictions, 0.51)["tp_events"] == 0

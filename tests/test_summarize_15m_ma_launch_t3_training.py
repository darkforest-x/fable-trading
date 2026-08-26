from pathlib import Path

import pandas as pd
import pytest

from scripts.summarize_15m_ma_launch_t3_training import (
    EXPECTED_ARGS,
    TrainingSummaryError,
    best_metric_row,
    parse_final_results_dict,
    parse_per_class,
    validate_args,
)


def frozen_args() -> dict:
    values = dict(EXPECTED_ARGS)
    values.update(
        {
            "name": "ma_launch_t3_10000_v1_y11s_ft",
            "data": r"C:\fable\datasets\ma_launch_t3_10000_v1\data.yaml",
        }
    )
    return values


def test_validate_args_accepts_exact_windows_recipe() -> None:
    validate_args(frozen_args())


def test_validate_args_accepts_preregistered_imgsz1280_treatment() -> None:
    values = frozen_args()
    values.update(
        {
            "name": "ma_launch_t3_10000_v1_y11s_ft_imgsz1280",
            "data": r"C:\fable\datasets\ma_launch_t3_10000_v1_imgsz1280_input\data.yaml",
            "imgsz": 1280,
        }
    )
    validate_args(
        values,
        expected_imgsz=1280,
        run_name="ma_launch_t3_10000_v1_y11s_ft_imgsz1280",
        remote_dataset_name="ma_launch_t3_10000_v1_imgsz1280_input",
    )


def test_validate_args_rejects_unsafe_flip() -> None:
    values = frozen_args()
    values["fliplr"] = 0.5
    with pytest.raises(TrainingSummaryError, match="remote args drifted"):
        validate_args(values)


def test_best_metric_row_uses_map50_95() -> None:
    frame = pd.DataFrame(
        {
            "epoch": [1, 2],
            "metrics/precision(B)": [0.9, 0.8],
            "metrics/recall(B)": [0.4, 0.7],
            "metrics/mAP50(B)": [0.8, 0.75],
            "metrics/mAP50-95(B)": [0.3, 0.5],
        }
    )
    assert int(best_metric_row(frame)["epoch"]) == 2


def test_parse_per_class_takes_last_clean_or_ansi_row() -> None:
    log = """
      dense_long 822 822 0.1 0.2 0.3 0.4
    \x1b[32m      dense_long 822 822 0.71 0.72 0.73 0.74\x1b[0m
      dense_short 648 648 0.81 0.82 0.83 0.84
    """
    parsed = parse_per_class(log)
    assert parsed["dense_long"]["map50_95"] == pytest.approx(0.74)
    assert parsed["dense_short"]["instances"] == 648


def test_parse_final_results_dict_uses_reloaded_best_validation() -> None:
    log = """
    results_dict: {'metrics/precision(B)': 0.1, 'metrics/recall(B)': 0.2,
    'metrics/mAP50(B)': 0.3, 'metrics/mAP50-95(B)': 0.4, 'fitness': 0.4}
    results_dict: {'metrics/precision(B)': 0.53, 'metrics/recall(B)': 0.61, 'metrics/mAP50(B)': 0.59, 'metrics/mAP50-95(B)': 0.332, 'fitness': 0.332}
    """.replace("\n    'metrics", " 'metrics")
    parsed = parse_final_results_dict(log)
    assert parsed["precision"] == pytest.approx(0.53)
    assert parsed["map50_95"] == pytest.approx(0.332)

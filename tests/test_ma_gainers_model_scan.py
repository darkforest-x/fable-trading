"""Focused causal-window and raw-coordinate contracts for the gainer scan."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.ma_gainers_model_scan import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SUPPORT_BARS,
    build_render_task,
    closed_endpoint_indices,
    map_x_to_local_bars,
    raw_boxes,
    verify_model_names,
    install_preprocess_shape_observer,
    _coverage_streams,
)


def _frame(rows: int = SUPPORT_BARS + 28) -> pd.DataFrame:
    times = pd.date_range("2026-09-21T00:00:00Z", periods=rows, freq="15min")
    price = np.linspace(100.0, 120.0, rows)
    return pd.DataFrame({
        "ts": (times.astype("int64") // 1_000_000).astype("int64"),
        "open_time": times,
        "open": price,
        "high": price + .7,
        "low": price - .7,
        "close": price + .2,
        "volume": np.ones(rows),
    })


def test_w18_right_edge_is_core_plus_five_and_excludes_future_rows() -> None:
    frame = _frame()
    # This is the first W18 point that has exactly 1,200 support bars.
    endpoint_i = SUPPORT_BARS + 19
    task = build_render_task(frame, symbol="BTC-USDT-SWAP", minutes=15, endpoint_i=endpoint_i, n_bars=18, core_bars=4)
    assert task.image.shape == (CANVAS_HEIGHT, CANVAS_WIDTH, 3)
    assert task.metadata["core_end_i"] + 5 == endpoint_i
    assert task.metadata["window_end_i"] == endpoint_i
    assert task.metadata["support_start_i"] == 0
    assert task.metadata["decision_time_utc"] == (frame.open_time.iloc[endpoint_i] + pd.Timedelta(minutes=15)).isoformat()


def test_future_mutation_does_not_change_a_window_input() -> None:
    frame = _frame()
    endpoint_i = SUPPORT_BARS + 19
    original = build_render_task(frame, symbol="BTC-USDT-SWAP", minutes=15, endpoint_i=endpoint_i, n_bars=18, core_bars=4)
    mutated = frame.copy()
    mutated.loc[endpoint_i + 1 :, ["open", "high", "low", "close", "volume"]] *= 1_000
    replay = build_render_task(mutated, symbol="BTC-USDT-SWAP", minutes=15, endpoint_i=endpoint_i, n_bars=18, core_bars=4)
    assert original.metadata["input_png_sha256"] == replay.metadata["input_png_sha256"]


def test_x_mapping_uses_actual_plot_centers_and_clips_model_boxes() -> None:
    start, end, detail = map_x_to_local_bars(-100.0, 9_999.0, n_bars=18)
    assert (start, end) == (0, 17)
    assert detail["clipped_x0"] == 12.0
    assert detail["clipped_x1"] == 1268.0
    # W19's sixth candle center maps exactly to index 5.
    center = 12 + 5 * 1256 / 18
    assert map_x_to_local_bars(center, center, n_bars=19)[:2] == (5, 5)


def test_closed_endpoints_exclude_beijing_midnight_close_itself() -> None:
    frame = _frame(6)
    day_start = pd.Timestamp("2026-09-21T00:00:00Z")
    # The 00:00--00:15 bar closes after start and is therefore included.
    assert closed_endpoint_indices(frame, minutes=15, day_start_utc=day_start, cutoff=day_start + pd.Timedelta(hours=1)) == [0, 1, 2, 3]


def test_coverage_contract_accepts_the_frozen_top_level_list() -> None:
    streams = _coverage_streams([{"symbol": "BTC-USDT-SWAP", "minutes": 15, "status": "ok", "path": "x.csv", "sha256": "a"}])
    assert streams[0]["symbol"] == "BTC-USDT-SWAP"


def test_raw_boxes_keep_an_out_of_core_prediction_without_semantic_filter() -> None:
    frame = _frame()
    task = build_render_task(frame, symbol="BTC-USDT-SWAP", minutes=15, endpoint_i=SUPPORT_BARS + 19, n_bars=18, core_bars=4)

    class Tensor:
        def __init__(self, value): self.value = np.asarray(value)
        def cpu(self): return self
        def numpy(self): return self.value
    class Boxes:
        xyxy = Tensor([[1.0, 20.0, 1267.0, 99.0]])
        xywhn = Tensor([[.5, .08, .99, .10]])
        conf = Tensor([.25])
        cls = Tensor([1])
        def __len__(self): return 1
    class Prediction:
        boxes = Boxes()

    boxes = raw_boxes(Prediction(), task, frame, {0: "dense_launch_long", 1: "dense_launch_short"})
    assert len(boxes) == 1
    assert boxes[0]["mapped_direction"] == "short"
    assert boxes[0]["predicted_core_start_local"] == 0
    assert boxes[0]["predicted_core_end_local"] == 17


def test_model_class_identity_and_actual_preprocess_shape_are_fail_closed() -> None:
    assert verify_model_names({0: "dense_launch_long", 1: "dense_launch_short"}) == {0: "dense_launch_long", 1: "dense_launch_short"}

    class Tensor:
        shape = (8, 3, 768, 1280)
    class Predictor:
        def preprocess(self, _batch): return Tensor()
    class Model:
        callbacks = []
        def add_callback(self, event, callback):
            assert event == "on_predict_start"
            self.callbacks.append(callback)

    model = Model()
    install_preprocess_shape_observer(model)
    predictor = Predictor()
    model.callbacks[0](predictor)
    # A second predict start does not stack wrappers around preprocess.
    installed = predictor.preprocess
    model.callbacks[0](predictor)
    assert predictor.preprocess is installed
    assert predictor.preprocess([]).shape[-2:] == (768, 1280)

"""Causal renderer parity, frozen proposal geometry and serialized inference."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sys
from types import SimpleNamespace
import threading
import time

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import signals
from yoyo.monitor import yolo_detector as detector


def chart_rows(count=400, duration=3_600_000):
    candles = []
    for i in range(count):
        close = 100 + np.sin(i / 8)
        candles.append(dict(t=i * duration, o=close - .1, h=close + 1,
                            l=close - 1, c=close, v=10))
    timeframe = next(k for k, v in detector.TIMEFRAME_MS.items() if v == duration)
    return signals.analyze(candles, [], timeframe)["chart"]


def normalized_box(window, a, b):
    x0 = window.transform.x_at(a) / window.transform.width
    x1 = window.transform.x_at(b) / window.transform.width
    return [(x0 + x1) / 2, .5, x1 - x0, .1]


@pytest.mark.parametrize("timeframe,duration", list(detector.TIMEFRAME_MS.items()))
def test_native_chart_inputs_match_original_renderer_and_ignore_future(timeframe, duration):
    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart

    rows = chart_rows(duration=duration)
    endpoint = rows[389]["t"]
    windows = detector.prepare_windows(rows, timeframe, endpoint)
    raw = pd.DataFrame(rows[:390]).rename(columns={"o": "open", "h": "high",
                                                  "l": "low", "c": "close"})
    # Recompute the original renderer MAs independently from the full prefix.
    enriched = add_mas(raw)
    changed = deepcopy(rows)
    for row in changed[390:]:
        row.update(o=object(), h=float("nan"), c=-100, sma20=None)
    perturbed = detector.prepare_windows(changed, timeframe, endpoint)
    truncated = detector.prepare_windows(rows[:390], timeframe, endpoint)
    for window, future, prefix in zip(windows, perturbed, truncated):
        image, _ = render_chart(enriched.iloc[-len(window.times):], out_path=None)
        assert image.shape == (742, 1280, 3)
        assert np.array_equal(window.image, image)
        assert np.array_equal(window.image, future.image)
        assert np.array_equal(window.image, prefix.image)
        assert window.input_pixel_sha256 == hashlib.sha256(image.tobytes()).hexdigest()
        assert window.times[-1] == endpoint


@pytest.mark.parametrize("change,error", [
    (lambda r: r.pop(380), "candle_gap"),
    (lambda r: r[380].update(t=r[379]["t"]), "candle_gap"),
    (lambda r: r[399].update(confirm="0"), "unconfirmed"),
    (lambda r: r[390].update(c=float("nan")), "invalid_ohlcv"),
    (lambda r: r[390].update(sma120=None), "ma_warmup"),
    (lambda r: r[399].update(bar_close_ms=1), "close_clock"),
])
def test_invalid_visible_prefix_fails_closed(change, error):
    rows = chart_rows()
    endpoint = rows[-1]["t"]
    change(rows)
    with pytest.raises(detector.YoloDetectorError, match=error):
        detector.prepare_windows(rows, "1H", endpoint)


def test_endpoint_must_exist_and_both_windows_must_be_complete():
    rows = chart_rows()
    for candles, endpoint in [(rows[:18], rows[17]["t"]),
                              (rows, rows[-1]["t"] + 3_600_000)]:
        with pytest.raises(detector.YoloDetectorError, match="incomplete"):
            detector.prepare_windows(candles, "1H", endpoint)
    with pytest.raises(detector.YoloDetectorError, match="unsupported"):
        detector.prepare_windows(rows, "30m", rows[-1]["t"])


@pytest.mark.parametrize("core,post,expected", [(4, 2, True), (5, 9, True),
    (3, 2, False), (6, 2, False), (4, 1, False), (4, 10, False)])
def test_core_and_post_boundaries_use_nearest_candle_centers(core, post, expected):
    rows = chart_rows()
    window = detector.prepare_windows(rows, "1H", rows[-1]["t"])[1]
    b = len(window.times) - 1 - post
    a = b - core + 1
    box = normalized_box(window, a, b)
    result = detector.parse_prediction([box, box], [0, 1], [.75, .80],
                                       window, "BTC-USDT-SWAP", "1H")
    assert [p["side"] for p in result] == ["long", "short"]
    assert result[0]["detection_id"] != result[1]["detection_id"]
    for proposal in result:
        assert proposal["structural_pass"] == expected
        assert proposal["core_start_ms"] == window.times[a]
        assert proposal["core_end_ms"] == window.times[b]
        assert proposal["core_length_bars"] == core
        assert proposal["post_bars"] == post


def test_proposal_identity_is_restart_stable_and_namespaces_market_and_period():
    rows = chart_rows()
    window = detector.prepare_windows(rows, "1H", rows[-1]["t"])[0]
    # An identical image after dropping unrelated earlier history has the same ID.
    short = detector.prepare_windows(rows[-25:], "1H", rows[-1]["t"])[0]
    box = normalized_box(window, 10, 13)
    parse = lambda w, symbol="BTC-USDT-SWAP", tf="1H": detector.parse_prediction(
        [box], [0], [.75], w, symbol, tf)[0]
    original = parse(window)
    assert original == parse(short)
    assert original["detection_id"] != parse(window, "ETH-USDT-SWAP")["detection_id"]
    assert original["detection_id"] != parse(window, tf="4H")["detection_id"]
    assert detector.parse_prediction([box], [0], [.24], window, "BTC", "1H") == []
    with pytest.raises(detector.YoloDetectorError, match="invalid_detector"):
        detector.parse_prediction([box], [2], [.8], window, "BTC", "1H")


class FakeTensor:
    def __init__(self, values):
        self.values = np.array(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class FakeBoxes:
    def __init__(self):
        self.xywhn = FakeTensor([[.65, .5, .2, .1]])
        self.cls = FakeTensor([0])
        self.conf = FakeTensor([.75])

    def __len__(self):
        return 1


def test_predict_lazy_load_frozen_arguments_and_single_model_serialization():
    rows = chart_rows()
    gate = detector.YoloDetector()
    assert not gate.status()["ready"]
    calls = []
    guard = threading.Lock()
    active = maximum = 0

    class FakeModel:
        def predict(self, **kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(active, maximum)
            calls.append(kwargs)
            time.sleep(.03)
            with guard:
                active -= 1
            return [SimpleNamespace(boxes=FakeBoxes()) for _ in kwargs["source"]]

    gate._model, gate._device = FakeModel(), "cpu"
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(gate.predict, rows, "BTC-USDT-SWAP", "1H", rows[-1]["t"])
                 for _ in range(2)]
        outputs = [task.result() for task in tasks]
    assert outputs[0] == outputs[1] and len(outputs[0]) == 2
    assert maximum == 1
    for call in calls:
        assert call.keys() == detector.PREDICT_PARAMETERS.keys() | {"source", "batch", "device"}
        assert {k: call[k] for k in detector.PREDICT_PARAMETERS} == detector.PREDICT_PARAMETERS
        assert call["batch"] == 2 and call["device"] == "cpu"
        assert len(call["source"]) == 2
    assert gate.status()["windows_scored"] == 4
    assert gate.status()["last_duration_seconds"] > 0


def test_wrong_weight_is_rejected_before_model_import(tmp_path, monkeypatch):
    weight = tmp_path / "best.pt"
    weight.write_bytes(b"wrong model")
    monkeypatch.setattr(detector, "MODEL_PATH", weight)
    gate = detector.YoloDetector()
    with pytest.raises(detector.YoloDetectorError, match="sha256_mismatch"):
        gate.warmup()
    assert not gate.status()["ready"]
    assert gate.status()["error"] == "model_sha256_mismatch"


@pytest.mark.parametrize("mps_available", [True, False])
def test_load_checks_classes_and_reports_explicit_device(tmp_path, monkeypatch, mps_available):
    weight = tmp_path / "best.pt"
    weight.write_bytes(b"pinned synthetic weight")
    monkeypatch.setattr(detector, "MODEL_PATH", weight)
    monkeypatch.setattr(detector, "MODEL_SHA256", hashlib.sha256(weight.read_bytes()).hexdigest())
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(backends=SimpleNamespace(
        mps=SimpleNamespace(is_available=lambda: mps_available))))
    model = SimpleNamespace(names={0: "dense_long", 1: "dense_short"})
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: model))
    gate = detector.YoloDetector()
    status = gate.warmup()
    assert status["ready"]
    assert status["device"] == ("mps" if mps_available else "cpu")
    model.names = {0: "dense_short", 1: "dense_long"}
    with pytest.raises(detector.YoloDetectorError, match="model_classes_mismatch"):
        detector.YoloDetector().warmup()


def test_model_error_is_not_an_empty_success_or_raw_exception():
    rows = chart_rows()
    gate = detector.YoloDetector()
    gate._model = SimpleNamespace(predict=lambda **kw: (_ for _ in ()).throw(
        ValueError("private detail")))
    gate._device = "cpu"
    with pytest.raises(detector.YoloDetectorError, match="inference_failed:ValueError") as error:
        gate.predict(rows, "BTC-USDT-SWAP", "1H", rows[-1]["t"])
    assert "private detail" not in str(error.value)
    assert gate.status()["windows_scored"] == 0

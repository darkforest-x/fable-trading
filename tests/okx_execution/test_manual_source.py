"""Manual draft context preserves full input time and never promotes YOLO."""
from copy import deepcopy

import pytest

from yoyo.okx_execution.manual_source import detection_context


JOB = "a" * 32
BOX = "b" * 24
SYMBOL = "ETH-USDT-SWAP"
STEP = 300_000


class Book:
    def __init__(self):
        self.spec = dict(timeframe="5m", model_id="research", model_sha256="c"*64,
                         mode="latest", end_ms=20*STEP)
        self.value = dict(self.spec, symbol=SYMBOL, cutoff_ms=20*STEP, raw_detections=[
            dict(id=BOX, side="long", window_start_ms=0, window_end_ms=19*STEP,
                 core_start_ms=12*STEP, core_end_ms=15*STEP, detected_at_ms=20*STEP,
                 first_detected_at_ms=17*STEP, structural_pass=True)])
    def get(self, identity): return dict(spec=deepcopy(self.spec)) if identity == JOB else None
    def result(self, identity, symbol): return deepcopy(self.value) if identity == JOB and symbol == SYMBOL else None


def resolve(book):
    return detection_context(book, JOB, SYMBOL, BOX, now_ms=30*STEP)


def test_manual_context_keeps_input_endpoint_and_does_not_infer_an_order():
    result = resolve(Book())
    assert result["input_close_ms"] == 20*STEP
    assert result["detection_age_ms"] == 10*STEP
    assert result["automatic_entry_eligible"] is False
    assert not {"size", "initial_stop", "entry_price", "leverage"} & result.keys()


@pytest.mark.parametrize("change", [
    dict(symbol="BTC-USDT-SWAP"), dict(timeframe="15m"), dict(model_sha256="wrong"),
    dict(cutoff_ms=21*STEP), dict(mode="history"), dict(error="read_failed"),
])
def test_result_identity_cannot_be_swapped_into_a_selected_reference(change):
    book = Book(); book.value.update(change)
    with pytest.raises(ValueError): resolve(book)


@pytest.mark.parametrize("change", [
    dict(detected_at_ms=17*STEP), dict(window_end_ms=15*STEP), dict(core_end_ms=21*STEP),
    dict(side="unknown"), dict(core_start_ms=True), dict(window_start_ms=1),
])
def test_invalid_or_truncated_input_time_is_rejected(change):
    book = Book(); book.value["raw_detections"][0].update(change)
    with pytest.raises(ValueError): resolve(book)


def test_duplicate_detection_ids_are_not_resolved_arbitrarily():
    book = Book(); book.value["raw_detections"] *= 2
    with pytest.raises(ValueError, match="ambiguous"): resolve(book)

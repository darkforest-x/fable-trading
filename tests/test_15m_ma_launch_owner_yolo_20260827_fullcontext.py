"""Contracts for the 2026-08-27 one-event-per-document full-context review."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import send_15m_ma_launch_owner_yolo_20260827_fullcontext as sender
from scripts.render_15m_ma_launch_owner_yolo_20260827_fullcontext import (
    DEFAULT_PREREG,
    EXPECTED_CONTEXT_BARS,
    EXPECTED_EVENTS,
    FullContextError,
    inverse_x,
    inverse_y,
    load_contract,
    project_raw_box,
    x_at_float,
)
from yoyo.layers.l1_detection.render import ChartTransform


def _transform(*, bars: int, width: int, height: int, lo: float, hi: float) -> ChartTransform:
    return ChartTransform(
        n_bars=bars,
        width=width,
        height=height,
        left=12,
        top=12,
        plot_w=width - 24,
        plot_h=height - 24,
        price_min=lo,
        price_max=hi,
        candle_half_w=4,
    )


def test_preregistration_freezes_third_holdout_use_and_43_full_context_documents() -> None:
    payload = load_contract(DEFAULT_PREREG)
    assert payload["owner_authorization"]["holdout_consumption_number_for_this_configuration"] == 3
    assert payload["source_contract"]["expected_events"] == EXPECTED_EVENTS == 43
    assert payload["source_contract"]["expected_symbols_with_events"] == 19
    assert payload["visual_contract"]["documents"] == 43
    assert payload["visual_contract"]["boxes_per_document"] == 1
    assert payload["visual_contract"]["full_context_bars"] == EXPECTED_CONTEXT_BARS == 110
    assert payload["visual_contract"]["canvas_width_px"] == 1920
    assert payload["visual_contract"]["canvas_height_px"] == 1400


def test_raw_small_window_box_roundtrips_through_absolute_domain_coordinates() -> None:
    input_tf = _transform(bars=22, width=1280, height=742, lo=95.0, hi=105.0)
    context_tf = _transform(bars=110, width=1880, height=780, lo=90.0, hi=110.0)
    row = {
        "prediction_cx_norm": 0.67,
        "prediction_cy_norm": 0.54,
        "prediction_w_norm": 0.20,
        "prediction_h_norm": 0.31,
        "window_start_i": 200,
    }
    projected = project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=180,
    )
    assert projected["global_x0_bar"] < projected["global_x1_bar"]
    assert projected["price_low"] < projected["price_high"]
    assert 0 <= projected["context_x0_px"] < projected["context_x1_px"] < context_tf.width
    assert 0 <= projected["context_y0_px"] < projected["context_y1_px"] < context_tf.height

    raw_center_x = (projected["raw_x0_px"] + projected["raw_x1_px"]) / 2
    recovered_center = 200 + inverse_x(input_tf, raw_center_x)
    projected_center = (
        projected["global_x0_bar"] + projected["global_x1_bar"]
    ) / 2
    assert abs(recovered_center - projected_center) < 0.02
    raw_center_y = (projected["raw_y0_px"] + projected["raw_y1_px"]) / 2
    assert projected["price_low"] < inverse_y(input_tf, raw_center_y) < projected["price_high"]
    assert x_at_float(context_tf, recovered_center - 180) == pytest.approx(
        (projected["context_x0_px"] + projected["context_x1_px"]) / 2,
        abs=1,
    )


def test_contract_refuses_unsafe_flags(tmp_path: Path) -> None:
    payload = load_contract(DEFAULT_PREREG)
    payload["safety"]["training_or_tuning"] = True
    path = tmp_path / "unsafe.json"
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FullContextError, match="unsafe prereg safety flag"):
        load_contract(path)


def test_telegram_sender_delivers_43_documents_and_refuses_duplicate_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    documents = []
    for index in range(1, EXPECTED_EVENTS + 1):
        path = tmp_path / f"signal_{index:02d}.png"
        path.write_bytes(f"png-{index}".encode())
        documents.append(
            {
                "id": f"signal_{index:02d}",
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "caption": f"signal {index}",
            }
        )
    monkeypatch.setattr(sender, "build_contract", lambda _results: (documents, "contract"))
    texts: list[str] = []
    sent: list[tuple[Path, str]] = []
    receipt_path = tmp_path / "telegram.json"
    receipt = sender.deliver(
        results=tmp_path,
        receipt_path=receipt_path,
        sleep_seconds=0,
        send_text=lambda message: not texts.append(message),
        send_document=lambda path, caption: not sent.append((path, caption)),
    )
    assert receipt["delivery_complete"] is True
    assert len(texts) == 2
    assert len(sent) == EXPECTED_EVENTS
    assert [path.name for path, _caption in sent] == [f"signal_{index:02d}.png" for index in range(1, 44)]
    with pytest.raises(sender.FullContextDeliveryError, match="already complete"):
        sender.deliver(
            results=tmp_path,
            receipt_path=receipt_path,
            sleep_seconds=0,
            send_text=lambda _message: True,
            send_document=lambda _path, _caption: True,
        )

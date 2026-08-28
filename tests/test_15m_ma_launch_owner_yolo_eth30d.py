"""Contracts for the ETHUSDT.P 30-complete-day Owner-YOLO scan."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import send_15m_ma_launch_owner_yolo_eth30d as sender
from scripts.scan_15m_ma_launch_owner_yolo_eth30d import (
    CONTEXT_BARS,
    DEFAULT_PREREG,
    EXPECTED_DAYS,
    TARGET_END,
    TARGET_START,
    cluster_month_episodes,
    context_bounds,
    load_preregistration,
)


def _candidate(start: int, end: int, decision: int, conf: float, klass: int = 0) -> dict:
    return {
        "day": "2026-08-01T00:00:00+00:00",
        "symbol": "ETH_USDT_SWAP",
        "core_start_i": start,
        "core_end_i": end,
        "window_end_i": decision,
        "window_len": 22,
        "confidence": conf,
        "class_id": klass,
        "class_name": "dense_long" if klass == 0 else "dense_short",
    }


def test_preregistration_freezes_30_days_fifth_holdout_use_and_original_model() -> None:
    payload = load_preregistration(DEFAULT_PREREG)
    assert len(EXPECTED_DAYS) == 30
    assert EXPECTED_DAYS[0] == TARGET_START
    assert EXPECTED_DAYS[-1] + __import__("pandas").Timedelta(days=1) == TARGET_END
    assert payload["owner_authorization"]["holdout_consumption_number_for_this_configuration"] == 5
    assert payload["owner_authorization"]["new_inference_authorized"] is True
    assert payload["detector"]["confidence"] == 0.25
    assert payload["detector"]["nms_iou"] == 0.7
    assert payload["review_contract"]["full_context_bars"] == CONTEXT_BARS == 128


def test_month_episode_merge_is_cross_day_class_agnostic_and_keeps_earliest() -> None:
    candidates = [
        _candidate(100, 104, 109, 0.70, 0),
        _candidate(105, 109, 112, 0.95, 1),
        _candidate(140, 144, 149, 0.80, 0),
    ]
    annotated, episodes = cluster_month_episodes(candidates)
    assert len(annotated) == 3
    assert len(episodes) == 2
    assert episodes[0]["window_end_i"] == 109
    assert episodes[0]["confidence"] == 0.70
    assert episodes[0]["episode_candidate_count"] == 2
    assert episodes[0]["episode_long_candidates"] == 1
    assert episodes[0]["episode_short_candidates"] == 1


def test_context_bounds_prefers_detection_at_90_and_clips_at_snapshot_end() -> None:
    assert context_bounds(400, 190) == (100, 227)
    assert context_bounds(400, 390) == (272, 399)
    assert context_bounds(400, 40) == (0, 127)


def test_sender_is_resumable_and_delivers_dynamic_document_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    documents = []
    for name in ("overview.png", "signal_001.png", "signal_002.png", "all.zip", "report.html"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        documents.append(
            {
                "id": name,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "caption": name,
            }
        )
    scan = {"overlap_episodes": 2}
    monkeypatch.setattr(sender, "build_contract", lambda _results, _report: (scan, documents, "contract"))
    texts: list[str] = []
    sent: list[tuple[Path, str]] = []
    receipt_path = tmp_path / "telegram.json"
    receipt = sender.deliver(
        results=tmp_path,
        report=tmp_path / "report.html",
        receipt_path=receipt_path,
        sleep_seconds=0,
        send_text=lambda message: not texts.append(message),
        send_document=lambda path, caption: not sent.append((path, caption)),
    )
    assert receipt["delivery_complete"] is True
    assert receipt["expected_signal_charts"] == 2
    assert len(texts) == 2
    assert len(sent) == 5
    with pytest.raises(sender.Eth30dDeliveryError, match="already complete"):
        sender.deliver(
            results=tmp_path,
            report=tmp_path / "report.html",
            receipt_path=receipt_path,
            sleep_seconds=0,
            send_text=lambda _message: True,
            send_document=lambda _path, _caption: True,
        )

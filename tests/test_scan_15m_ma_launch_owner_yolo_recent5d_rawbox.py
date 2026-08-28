"""Contracts for the corrected five-day raw-box review surface."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.scan_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    DEFAULT_PREREG,
    cluster_candidates_into_episodes,
    draw_raw_prediction,
    load_preregistration,
    normalized_box_corners,
    pixel_sha256,
)
from scripts.verify_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    RawBoxVerificationError,
    resolve_repo_path,
)
from scripts.send_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    artifact_contract,
    deliver,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import IMG_HEIGHT, IMG_WIDTH, render_chart


ROOT = Path(__file__).resolve().parents[1]


class _Tensor:
    def __init__(self, values):
        self.values = np.asarray(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class _Model:
    def __init__(self, boxes):
        self.boxes = boxes

    def predict(self, **_kwargs):
        return [SimpleNamespace(boxes=self.boxes)]


class _Boxes(SimpleNamespace):
    def __len__(self):
        return len(self.conf.values)


def _frame(rows: int = 140) -> pd.DataFrame:
    times = pd.date_range("2026-08-23T00:00:00Z", periods=rows, freq="15min")
    base = np.linspace(100.0, 101.0, rows)
    return pd.DataFrame(
        {
            "open_time": times,
            "open": base,
            "high": base + 0.2,
            "low": base - 0.2,
            "close": base + 0.05,
            "volume": np.ones(rows),
        }
    )


def _candidate(**overrides):
    row = {
        "day": "2026-08-23T00:00:00+00:00",
        "rank": 1,
        "symbol": "BTC_USDT_SWAP",
        "inst_id": "BTC-USDT-SWAP",
        "daily_return": 0.1,
        "window_len": 22,
        "window_start_i": 86,
        "window_end_i": 107,
        "window_end_time": "2026-08-23T02:45:00+00:00",
        "core_start_i": 100,
        "core_end_i": 103,
        "core_length_bars": 4,
        "confirmation_bars": 4,
        "class_id": 0,
        "class_name": "dense_long",
        "confidence": 0.7,
        "prediction_cx_norm": 0.6,
        "prediction_cy_norm": 0.5,
        "prediction_w_norm": 0.2,
        "prediction_h_norm": 0.3,
        "input_width": IMG_WIDTH,
        "input_height": IMG_HEIGHT,
        "input_n_bars": 22,
    }
    row.update(overrides)
    return row


def test_preregistration_freezes_second_holdout_use_and_raw_box_contract() -> None:
    payload = load_preregistration(DEFAULT_PREREG)
    assert payload["owner_authorization"]["holdout_consumption_number_for_this_configuration"] == 2
    assert payload["detector"]["confidence"] == 0.25
    assert payload["detector"]["window_lengths"] == list(range(18, 26))
    assert payload["repair_contract"]["preserved_prediction_coordinates"] == [
        "cx",
        "cy",
        "w",
        "h",
    ]
    assert payload["repair_contract"]["maximum_boxes_per_review_panel"] == 1


def test_rawbox_receipt_paths_accept_cross_platform_relative_paths_only() -> None:
    expected = ROOT / "analysis" / "output" / "review_manifest.csv"
    assert resolve_repo_path("analysis/output/review_manifest.csv") == expected
    assert resolve_repo_path(r"analysis\output\review_manifest.csv") == expected
    for bad in ("../outside.csv", r"C:\outside.csv", "/outside.csv"):
        try:
            resolve_repo_path(bad)
        except RawBoxVerificationError:
            continue
        raise AssertionError(f"unsafe path accepted: {bad}")


def test_common_predictor_preserves_all_four_coordinates_and_input_identity() -> None:
    enriched = add_mas(_frame()).iloc[-18:].reset_index(drop=True)
    image, transform = render_chart(enriched, out_path=None)
    x0, x1 = transform.x_at(10), transform.x_at(13)
    cx = (x0 + x1) / 2 / transform.width
    width = (x1 - x0) / transform.width
    boxes = _Boxes(
        xywhn=_Tensor([[cx, 0.47, width, 0.26]]),
        cls=_Tensor([0]),
        conf=_Tensor([0.91]),
    )
    model = _Model(boxes)
    day = pd.Timestamp(enriched["open_time"].iloc[-1]).floor("D")
    stats: Counter[str] = Counter()
    hits = common._predict_batches(  # noqa: SLF001 - targeted contract test
        model,
        [
            (
                image,
                transform,
                {
                    "day": day.isoformat(),
                    "rank": 1,
                    "symbol": "BTC_USDT_SWAP",
                    "inst_id": "BTC-USDT-SWAP",
                    "daily_return": 0.1,
                    "window_len": 18,
                    "window_start_i": 0,
                    "window_end_i": 17,
                    "window_end_time": pd.Timestamp(
                        enriched["open_time"].iloc[-1]
                    ).isoformat(),
                },
            )
        ],
        batch_size=1,
        conf=0.25,
        iou=0.7,
        imgsz=960,
        device="cpu",
        day=day,
        frame=enriched,
        allowed_cores={4},
        allowed_confirmations={4},
        stats=stats,
    )
    assert len(hits) == 1
    hit = hits[0]
    assert hit["prediction_cx_norm"] == cx
    assert hit["prediction_cy_norm"] == 0.47
    assert hit["prediction_w_norm"] == width
    assert hit["prediction_h_norm"] == 0.26
    assert hit["input_pixel_sha256"] == pixel_sha256(image)
    assert hit["core_length_bars"] == 4
    assert hit["confirmation_bars"] == 4


def test_episode_clustering_merges_overlapping_decision_intervals_and_keeps_earliest() -> None:
    candidates = [
        _candidate(confidence=0.55, core_start_i=100, window_end_i=107),
        _candidate(
            confidence=0.99,
            core_start_i=104,
            core_end_i=107,
            window_start_i=90,
            window_end_i=111,
            window_end_time="2026-08-23T03:45:00+00:00",
        ),
        _candidate(
            confidence=0.88,
            core_start_i=111,
            core_end_i=114,
            window_start_i=98,
            window_end_i=119,
            window_end_time="2026-08-23T05:45:00+00:00",
        ),
        _candidate(
            confidence=0.77,
            core_start_i=130,
            core_end_i=133,
            window_start_i=116,
            window_end_i=137,
            window_end_time="2026-08-23T10:15:00+00:00",
        ),
    ]
    annotated, episodes = cluster_candidates_into_episodes(candidates)
    assert len(annotated) == 4
    assert len(episodes) == 2
    assert episodes[0]["episode_candidate_count"] == 3
    assert episodes[0]["window_end_i"] == 107
    assert episodes[0]["confidence"] == 0.55
    assert episodes[0]["episode_max_confidence"] == 0.99
    assert episodes[1]["episode_candidate_count"] == 1
    assert len({row["candidate_id"] for row in annotated}) == 4


def test_raw_overlay_uses_all_four_coordinates_and_exactly_one_rectangle() -> None:
    image = np.full((IMG_HEIGHT, IMG_WIDTH, 3), 255, dtype=np.uint8)
    row = _candidate()
    x0, y0, x1, y1 = normalized_box_corners(row)
    overlay = draw_raw_prediction(image, row)
    color = np.asarray(common.CLASS_COLORS[0], dtype=np.uint8)
    colored = np.all(overlay == color, axis=2)
    assert colored.sum() > 0
    ys, xs = np.where(colored)
    assert xs.min() <= x0 <= xs.max()
    assert xs.min() <= x1 <= xs.max()
    assert ys.min() <= y0 <= ys.max()
    assert ys.min() <= y1 <= ys.max()
    assert not np.array_equal(image, overlay)


def test_telegram_sender_validates_and_delivers_eight_hash_bound_documents(
    tmp_path: Path,
) -> None:
    _scan, _qa, artifacts = artifact_contract()
    assert [item["id"] for item in artifacts] == [
        "overview",
        "day_2026-08-23",
        "day_2026-08-24",
        "day_2026-08-25",
        "day_2026-08-26",
        "day_2026-08-27",
        "actual_inputs_and_overlays_zip",
        "html_report",
    ]
    texts: list[str] = []
    documents: list[tuple[Path, str]] = []
    receipt = deliver(
        receipt_path=tmp_path / "telegram_receipt.json",
        sleep_seconds=0,
        send_text=lambda message: not texts.append(message),
        send_document=lambda path, caption: not documents.append((path, caption)),
    )
    assert receipt["delivery_complete"] is True
    assert len(texts) == 2
    assert len(documents) == 8
    assert all(path.is_file() and caption for path, caption in documents)

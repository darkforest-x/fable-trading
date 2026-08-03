"""P1 historical detector replay using the live candidate operator.

Each unique 200-bar rendered window is inferred once, then reused to reconstruct
the live pulse schedule (tip, tip-1, tip-2 windows).  Box mapping, per-pulse
minimum-gap selection, and the final whole-series age gate call the same pure
functions as live discovery.  The module does not score LightGBM, train, read
holdout rows, or touch runtime state.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from src.detection.data import add_mas
from src.detection.render import render_chart
from src.judgment.candidates import MIN_GAP_BARS
from src.judgment.p1_dataset import CandidateObservation
from src.judgment.yolo_candidates import (
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    dedupe_indices,
    enforce_global_tip_age,
    map_box_to_signal,
)


@dataclass(frozen=True)
class LocalBoxDetection:
    source: str
    symbol: str
    window_start_i: int
    window_end_i: int
    mapped_signal_i: int
    box_x_center: float
    box_y_center: float
    box_width: float
    box_height: float
    box_confidence: float
    box_class_id: int


def _result_boxes(result: Any) -> list[tuple[float, float, float, float, float, int]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    xywhn = boxes.xywhn.cpu().numpy()
    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xywhn))
    classes = boxes.cls.cpu().numpy() if boxes.cls is not None else np.zeros(len(xywhn))
    out = []
    for coords, confidence, class_id in zip(xywhn, confs, classes):
        cx, cy, width, height = map(float, coords[:4])
        out.append((cx, cy, width, height, float(confidence), int(class_id)))
    return out


def normalized_boxes(result: Any) -> list[dict[str, Any]]:
    """Stable box evidence used by array-vs-PNG transport parity checks."""
    return [
        {
            "x": values[0],
            "y": values[1],
            "w": values[2],
            "h": values[3],
            "confidence": values[4],
            "class_id": values[5],
        }
        for values in sorted(_result_boxes(result), key=lambda item: (-item[4], *item[:4], item[5]))
    ]


def detect_historical_windows(
    *,
    frame: pd.DataFrame,
    source: str,
    symbol: str,
    model: Any,
    window_end_indices: Sequence[int],
    device: str,
    conf: float = DEFAULT_CONF,
    batch_size: int = 8,
    render_workers: int = 4,
    signal_i_lo: int | None = None,
    signal_i_hi: int | None = None,
) -> tuple[list[LocalBoxDetection], dict[str, Any]]:
    """Render/infer unique windows and retain locally valid box mappings.

    Any render or predict failure raises.  A caller may checkpoint only after
    this function returns, so a failed symbol cannot masquerade as a completed
    zero-fire symbol.
    """
    if not (0.0 < float(conf) < 1.0):
        raise ValueError("conf must be in (0, 1)")
    ends = sorted({int(value) for value in window_end_indices})
    if any(end - WINDOW + 1 < 0 or end >= len(frame) for end in ends):
        raise ValueError("window_end_indices exceed frame bounds")
    ema = add_mas(frame)
    local: list[LocalBoxDetection] = []
    rejection_counts: Counter[str] = Counter()
    predicted_boxes = 0

    def render_one(end: int):
        start = end - WINDOW + 1
        image, transform = render_chart(ema.iloc[start : end + 1])
        return end, start, image, transform

    workers = max(1, int(render_workers))
    chunk = max(1, int(batch_size))
    for offset in range(0, len(ends), chunk):
        batch_ends = ends[offset : offset + chunk]
        if workers == 1:
            rendered = [render_one(end) for end in batch_ends]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(batch_ends))) as pool:
                rendered = list(pool.map(render_one, batch_ends))
        images = [item[2] for item in rendered]
        # Ultralytics 8.4.89 on macOS/arm64 reproducibly SIGSEGVs for a
        # one-element *list* source while the identical ndarray source works.
        # Multi-image lists are safe.  This only normalizes the source wrapper.
        source_arg = images[0] if len(images) == 1 else images
        results = model.predict(source_arg, conf=conf, verbose=False, device=device)
        if len(results) != len(rendered):
            raise RuntimeError(
                f"detector result count mismatch: {len(results)} != {len(rendered)}"
            )
        for (end, start, _image, transform), result in zip(rendered, results):
            boxes = _result_boxes(result)
            predicted_boxes += len(boxes)
            for cx, cy, width, height, confidence, class_id in boxes:
                if confidence < conf:
                    rejection_counts["below_conf"] += 1
                    continue
                mapped = map_box_to_signal(
                    cx=cx,
                    w=width,
                    tf=transform,
                    window_start_i=start,
                    n_bars=WINDOW,
                    frame_length=len(frame),
                    latest_closed_i=end,
                    tip_edge_bars=TIP_EDGE_BARS,
                    apply_tip_edge=True,
                    allow_pending_entry=True,
                    signal_i_lo=signal_i_lo,
                    signal_i_hi=signal_i_hi,
                )
                if not mapped.accepted:
                    rejection_counts[mapped.rejection_reason] += 1
                    continue
                local.append(
                    LocalBoxDetection(
                        source=source,
                        symbol=symbol,
                        window_start_i=start,
                        window_end_i=end,
                        mapped_signal_i=mapped.mapped_signal_i,
                        box_x_center=cx,
                        box_y_center=cy,
                        box_width=width,
                        box_height=height,
                        box_confidence=confidence,
                        box_class_id=class_id,
                    )
                )
    return local, {
        "windows_scheduled": len(ends),
        "windows_rendered": len(ends),
        "predicted_boxes": predicted_boxes,
        "locally_accepted_boxes": len(local),
        "mapping_rejections": dict(sorted(rejection_counts.items())),
    }


def select_live_parity_observations(
    detections: Iterable[LocalBoxDetection],
    *,
    pulse_latest_indices: Sequence[int],
    max_global_tip_age_bars: int = 2,
    min_gap: int = MIN_GAP_BARS,
) -> tuple[list[CandidateObservation], dict[str, Any]]:
    """Replay live window union → min-gap → final global-age gate per pulse."""
    by_end: dict[int, list[LocalBoxDetection]] = defaultdict(list)
    for detection in detections:
        by_end[int(detection.window_end_i)].append(detection)
    selected_by_signal: dict[int, CandidateObservation] = {}
    pulse_raw_signals = 0
    pulse_gap_signals = 0
    pulse_age_signals = 0
    for latest in sorted({int(value) for value in pulse_latest_indices}):
        pulse_boxes = [
            item
            for window_end in (latest, latest - 1, latest - 2)
            for item in by_end.get(window_end, [])
        ]
        if not pulse_boxes:
            continue
        raw_signals = sorted({item.mapped_signal_i for item in pulse_boxes})
        pulse_raw_signals += len(raw_signals)
        gap_kept = dedupe_indices(raw_signals, min_gap=min_gap)
        pulse_gap_signals += len(gap_kept)
        age_kept = enforce_global_tip_age(
            gap_kept,
            latest_closed_i=latest,
            max_age_bars=max_global_tip_age_bars,
        )
        pulse_age_signals += len(age_kept)
        for signal_i in age_kept:
            evidence = [item for item in pulse_boxes if item.mapped_signal_i == signal_i]
            representative = min(
                evidence,
                key=lambda item: (
                    -item.box_confidence,
                    -item.window_end_i,
                    item.box_x_center,
                    item.box_y_center,
                    item.box_width,
                    item.box_height,
                    item.box_class_id,
                ),
            )
            observation = CandidateObservation(
                source=representative.source,
                symbol=representative.symbol,
                window_start_i=representative.window_start_i,
                window_end_i=representative.window_end_i,
                latest_closed_i=latest,
                mapped_signal_i=signal_i,
                global_tip_age_bars=latest - signal_i,
                box_x_center=representative.box_x_center,
                box_y_center=representative.box_y_center,
                box_width=representative.box_width,
                box_height=representative.box_height,
                box_confidence=representative.box_confidence,
                box_class_id=representative.box_class_id,
            )
            previous = selected_by_signal.get(signal_i)
            if previous is None or (
                observation.latest_closed_i,
                -observation.box_confidence,
                observation.window_end_i,
            ) < (
                previous.latest_closed_i,
                -previous.box_confidence,
                previous.window_end_i,
            ):
                selected_by_signal[signal_i] = observation
    observations = [selected_by_signal[key] for key in sorted(selected_by_signal)]
    return observations, {
        "pulse_raw_signal_count": pulse_raw_signals,
        "pulse_after_min_gap_count": pulse_gap_signals,
        "pulse_after_global_age_count": pulse_age_signals,
        "unique_candidate_count": len(observations),
        "global_tip_age_distribution": dict(
            sorted(Counter(item.global_tip_age_bars for item in observations).items())
        ),
    }

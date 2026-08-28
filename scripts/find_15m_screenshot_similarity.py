#!/usr/bin/env python3
"""Retrieve pre-holdout 15m OHLCV windows from a cropped chart screenshot.

Candidate ranking uses only ``open``, ``high``, ``low``, ``close`` and
``volume`` from each 14-bar matched window.  The first eight bars supply the
median true-range and volume scales; the last six bars are the higher-weight
right-edge morphology.  The optional 12 bars after the endpoint are rendered
and reported only as retrospective review context and never enter candidate
selection or distance.  Every source reader inspects the leading millisecond
timestamp before parsing OHLCV and stops at the frozen 2026-05-04 holdout
boundary, so no holdout OHLCV row is materialized.

This is completed-shape retrieval from an assumed 15m screenshot.  It is not a
signal detector, label, model score, trading rule, or production surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import matplotlib
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.patches import Rectangle
from numpy.lib.stride_tricks import sliding_window_view

matplotlib.use("Agg")


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREREG = (
    ROOT
    / "experiments/active/exp-15m-right-edge-screenshot-similarity-v1"
    / "preregistration.json"
)
HOLDOUT_START = datetime(2026, 5, 4, tzinfo=timezone.utc)
HOLDOUT_MS = int(HOLDOUT_START.timestamp() * 1000)
OHLCV = ("open", "high", "low", "close", "volume")


class ScreenshotSimilarityError(RuntimeError):
    """Fail-closed contract violation in screenshot retrieval."""


@dataclass
class Candidate:
    """One fixed-endpoint historical similarity candidate."""

    symbol: str
    start_i: int
    end_i: int
    end_ts: int
    lock_distance: float
    price_lock_distance: float
    volume_distance: float
    price_dtw_distance: float = math.inf
    distance: float = math.inf


@dataclass(frozen=True)
class SeriesData:
    """One pre-holdout symbol series plus its source audit."""

    times: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    source_path: Path
    source_audit: dict[str, Any]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a local file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    """Hash JSON data with stable key and separator rules."""

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    """Write deterministic, human-readable JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write a deterministic JSONL manifest."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def symbol_from_path(path: Path) -> str:
    """Parse the canonical OKX symbol from a deep-history filename."""

    stem = path.stem
    if not stem.startswith("okx_") or "_15m_" not in stem:
        raise ScreenshotSimilarityError(f"not a canonical 15m filename: {path}")
    return stem[len("okx_") :].split("_15m_", 1)[0]


def discover_universe(source_dir: Path, expected_symbols: int) -> list[Path]:
    """Choose the longest declared 15m snapshot for each USDT perpetual."""

    best: dict[str, tuple[int, Path]] = {}
    for path in sorted(source_dir.glob("okx_*_USDT_SWAP_15m_*.csv")):
        try:
            symbol = symbol_from_path(path)
            declared = int(path.stem.rsplit("_", 1)[-1])
        except (ScreenshotSimilarityError, ValueError):
            continue
        prior = best.get(symbol)
        if prior is None or declared > prior[0]:
            best[symbol] = (declared, path)
    paths = [item[1] for item in sorted(best.values(), key=lambda item: item[1].name)]
    if len(paths) != expected_symbols:
        raise ScreenshotSimilarityError(
            f"universe drift: expected {expected_symbols}, discovered {len(paths)}"
        )
    return paths


def read_preholdout_csv(path: Path) -> SeriesData:
    """Materialize only rows strictly before the frozen holdout boundary.

    The first CSV field is the millisecond timestamp.  It is checked before the
    remainder of a line is split, so the first boundary row terminates the read
    without parsing any holdout OHLCV value.
    """

    times: list[int] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    prefix_digest = hashlib.sha256()
    stopped_at_boundary = False
    boundary_timestamp_seen: int | None = None

    with path.open("rb") as handle:
        header_line = handle.readline()
        prefix_digest.update(header_line)
        header = header_line.decode("utf-8", "replace").strip().split(",")
        positions = {name: index for index, name in enumerate(header)}
        required = ("ts", *OHLCV)
        if any(name not in positions for name in required):
            raise ScreenshotSimilarityError(f"{path.name}: missing one of {required}")
        last_timestamp = -1
        for raw_line in handle:
            comma = raw_line.find(b",")
            if comma <= 0:
                continue
            try:
                timestamp = int(raw_line[:comma])
            except ValueError:
                continue
            if timestamp >= HOLDOUT_MS:
                stopped_at_boundary = True
                boundary_timestamp_seen = timestamp
                break
            if timestamp <= last_timestamp:
                raise ScreenshotSimilarityError(
                    f"{path.name}: timestamps are not strictly increasing pre-holdout"
                )
            fields = raw_line.rstrip(b"\r\n").split(b",")
            try:
                open_ = float(fields[positions["open"]])
                high = float(fields[positions["high"]])
                low = float(fields[positions["low"]])
                close = float(fields[positions["close"]])
                volume = float(fields[positions["volume"]])
            except (IndexError, ValueError) as exc:
                raise ScreenshotSimilarityError(
                    f"{path.name}: invalid pre-holdout OHLCV at {timestamp}"
                ) from exc
            if not (
                open_ > 0
                and close > 0
                and high >= max(open_, close)
                and low <= min(open_, close)
                and volume >= 0
            ):
                raise ScreenshotSimilarityError(
                    f"{path.name}: impossible pre-holdout OHLCV at {timestamp}"
                )
            prefix_digest.update(raw_line)
            times.append(timestamp)
            opens.append(open_)
            highs.append(high)
            lows.append(low)
            closes.append(close)
            volumes.append(volume)
            last_timestamp = timestamp

    if not times:
        raise ScreenshotSimilarityError(f"{path.name}: no pre-holdout OHLCV rows")
    if max(times) >= HOLDOUT_MS:
        raise ScreenshotSimilarityError(f"{path.name}: holdout OHLCV entered memory")
    try:
        audit_path = str(path.relative_to(ROOT))
    except ValueError:
        audit_path = str(path)
    audit = {
        "path": audit_path,
        "preholdout_rows": len(times),
        "preholdout_first_time": datetime.fromtimestamp(
            times[0] / 1000, tz=timezone.utc
        ).isoformat(),
        "preholdout_last_time": datetime.fromtimestamp(
            times[-1] / 1000, tz=timezone.utc
        ).isoformat(),
        "preholdout_prefix_sha256": prefix_digest.hexdigest(),
        "stopped_at_boundary": stopped_at_boundary,
        "boundary_timestamp_metadata_seen": (
            datetime.fromtimestamp(boundary_timestamp_seen / 1000, tz=timezone.utc).isoformat()
            if boundary_timestamp_seen is not None
            else None
        ),
        "holdout_ohlcv_rows_read": 0,
    }
    arrays = tuple(
        np.asarray(values, dtype=np.int64 if index == 0 else np.float64)
        for index, values in enumerate(
            (times, opens, highs, lows, closes, volumes)
        )
    )
    return SeriesData(*arrays, source_path=path, source_audit=audit)


def _rgb_mask(image: np.ndarray, rgb: Sequence[int]) -> np.ndarray:
    """Return an exact RGB mask for a screenshot palette color."""

    return np.all(image == np.asarray(rgb, dtype=np.uint8), axis=2).astype(np.uint8)


def extract_query_contract(reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Recover the rightmost candle and volume geometry from exact palette colors."""

    bgr = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ScreenshotSimilarityError(f"cannot decode reference: {reference_path}")
    image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    height, width = image.shape[:2]
    price_bottom = int(round(height * 0.45))
    body_min_area = int(config["body_component_min_area"])
    colors = {
        "G": tuple(config["dark_green_rgb"]),
        "R": tuple(config["dark_red_rgb"]),
    }
    volume_colors = {
        "G": tuple(config["light_green_rgb"]),
        "R": tuple(config["light_red_rgb"]),
    }
    components: list[dict[str, Any]] = []
    masks: dict[str, np.ndarray] = {}
    for direction, color in colors.items():
        mask = _rgb_mask(image[:price_bottom], color)
        masks[direction] = mask
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for x, y, component_width, component_height, area in stats[1:count]:
            if (
                4 <= int(component_width) <= 10
                and int(area) >= body_min_area
                and int(component_height) >= 2
            ):
                components.append(
                    {
                        "direction": direction,
                        "x": int(x),
                        "component_y": int(y),
                        "component_width": int(component_width),
                        "component_height": int(component_height),
                        "component_area": int(area),
                    }
                )
    components.sort(key=lambda row: row["x"])
    requested = int(config["rightmost_candles"])
    if len(components) < requested:
        raise ScreenshotSimilarityError(
            f"reference exposes {len(components)} candle components, need {requested}"
        )
    selected = components[-requested:]
    sequence = "".join(row["direction"] for row in selected)
    if sequence != str(config["expected_color_sequence"]):
        raise ScreenshotSimilarityError(
            f"reference color sequence drift: {sequence} != {config['expected_color_sequence']}"
        )

    volume_baseline = 0
    volume_masks = {
        direction: _rgb_mask(image, color) for direction, color in volume_colors.items()
    }
    for mask in volume_masks.values():
        ys = np.where(mask[int(height * 0.72) :].sum(axis=1) > 0)[0]
        if len(ys):
            volume_baseline = max(volume_baseline, int(ys.max() + height * 0.72))
    if volume_baseline <= 0:
        raise ScreenshotSimilarityError("reference has no exact-color volume bars")

    candles: list[dict[str, Any]] = []
    for component in selected:
        direction = str(component["direction"])
        x = int(component["x"])
        component_width = int(component["component_width"])
        mask = masks[direction]
        row_counts = mask[:, x : x + component_width].sum(axis=1)
        body_rows = np.where(row_counts >= 4)[0]
        global_counts = mask.sum(axis=1)
        wick_rows = np.where((row_counts > 0) & (global_counts <= 50))[0]
        if not len(body_rows) or not len(wick_rows):
            raise ScreenshotSimilarityError(f"cannot recover candle at x={x}")
        body_top = int(body_rows.min())
        body_bottom = int(body_rows.max())
        high_y = int(wick_rows.min())
        low_y = int(wick_rows.max())
        if direction == "G":
            open_y, close_y = body_bottom, body_top
        else:
            open_y, close_y = body_top, body_bottom

        volume_mask = volume_masks[direction]
        volume_rows = np.where(
            volume_mask[:, x : x + component_width].sum(axis=1) >= 3
        )[0]
        volume_pixels = (
            max(1, volume_baseline - int(volume_rows.min()) + 1)
            if len(volume_rows)
            else 1
        )
        candles.append(
            {
                **component,
                "open_y": open_y,
                "high_y": high_y,
                "low_y": low_y,
                "close_y": close_y,
                "volume_pixels": volume_pixels,
            }
        )

    ohlc = np.asarray(
        [
            [-row["open_y"], -row["high_y"], -row["low_y"], -row["close_y"]]
            for row in candles
        ],
        dtype=float,
    )
    volumes = np.asarray([row["volume_pixels"] for row in candles], dtype=float)
    tail = int(config["untrusted_volume_tail_bars"])
    volume_mask = np.ones(len(candles), dtype=bool)
    if tail:
        volume_mask[-tail:] = False
    resolved_reference = reference_path.resolve()
    try:
        reference_label = str(resolved_reference.relative_to(ROOT))
    except ValueError:
        reference_label = str(resolved_reference)
    payload = {
        "reference_path": reference_label,
        "reference_sha256": sha256_file(reference_path),
        "image_width": width,
        "image_height": height,
        "price_bottom_y": price_bottom,
        "volume_baseline_y": volume_baseline,
        "color_sequence": sequence,
        "candles": candles,
        "pseudo_ohlc": ohlc.tolist(),
        "volume_pixels": volumes.tolist(),
        "volume_trusted": volume_mask.tolist(),
    }
    payload["query_contract_sha256"] = canonical_sha256(payload)
    return payload


def true_range(ohlc: np.ndarray) -> np.ndarray:
    """Return true range, seeding the first prior close with its open."""

    prior_close = np.r_[ohlc[0, 0], ohlc[:-1, 3]]
    return np.maximum(
        ohlc[:, 1] - ohlc[:, 2],
        np.maximum(
            np.abs(ohlc[:, 1] - prior_close),
            np.abs(ohlc[:, 2] - prior_close),
        ),
    )


def normalize_window(
    ohlc: np.ndarray,
    volumes: np.ndarray,
    *,
    prelude_bars: int,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Normalize price by prelude median TR and volume by prelude median."""

    scale = max(float(np.median(true_range(ohlc[:prelude_bars]))), 1e-12)
    volume_scale = max(float(np.median(volumes[:prelude_bars])), 1e-12)
    price = (ohlc - ohlc[0, 0]) / scale
    volume = np.log(np.maximum(volumes, 1e-12) / volume_scale)
    return price, volume, scale, volume_scale


def multivariate_dtw_distance(
    candidate: np.ndarray,
    reference: np.ndarray,
    *,
    radius: int,
) -> float:
    """Exact constrained multivariate DTW with path-length normalization."""

    if candidate.ndim != 2 or reference.ndim != 2:
        raise ValueError("DTW expects two 2D arrays")
    if candidate.shape[1] != reference.shape[1]:
        raise ValueError("DTW channel counts differ")
    n, m = len(candidate), len(reference)
    if radius < abs(n - m):
        raise ValueError("DTW radius cannot connect the input lengths")
    costs = np.full((n + 1, m + 1), np.inf, dtype=float)
    steps = np.zeros((n + 1, m + 1), dtype=np.int16)
    costs[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - radius), min(m, i + radius) + 1):
            predecessors = (
                (costs[i - 1, j], steps[i - 1, j]),
                (costs[i, j - 1], steps[i, j - 1]),
                (costs[i - 1, j - 1], steps[i - 1, j - 1]),
            )
            prior_cost, prior_steps = min(predecessors, key=lambda item: item[0])
            row_cost = float(np.mean(np.square(candidate[i - 1] - reference[j - 1])))
            costs[i, j] = prior_cost + row_cost
            steps[i, j] = prior_steps + 1
    if not np.isfinite(costs[n, m]) or steps[n, m] <= 0:
        raise ValueError("DTW path is unreachable")
    return float(np.sqrt(costs[n, m] / steps[n, m]))


def score_features(
    price: np.ndarray,
    volume: np.ndarray,
    *,
    reference_price: np.ndarray,
    reference_volume: np.ndarray,
    config: dict[str, Any],
    volume_trusted: np.ndarray,
    include_dtw: bool,
) -> dict[str, float]:
    """Return frozen lockstep and optional boundary-preserving DTW distances."""

    window_bars = int(config["window_bars"])
    prelude_bars = int(config["prelude_bars"])
    edge_bars = int(config["edge_bars"])
    if price.shape != (window_bars, 4) or len(volume) != window_bars:
        raise ValueError("candidate feature shape differs from frozen window")
    channel_weights = np.asarray(
        [config["price_channel_weights"][name] for name in ("open", "high", "low", "close")],
        dtype=float,
    )
    segment_weights = np.r_[
        np.full(prelude_bars, float(config["segment_weights"]["prelude"]) / prelude_bars),
        np.full(edge_bars, float(config["segment_weights"]["edge"]) / edge_bars),
    ]
    price_delta = price - reference_price
    price_per_bar = np.sum(np.square(price_delta) * channel_weights, axis=1)
    price_lock = float(np.sqrt(np.sum(price_per_bar * segment_weights)))

    volume_weights = np.zeros(window_bars, dtype=float)
    trusted_pre = volume_trusted[:prelude_bars]
    trusted_edge = volume_trusted[prelude_bars:]
    volume_weights[:prelude_bars][trusted_pre] = (
        float(config["segment_weights"]["prelude"]) / int(trusted_pre.sum())
    )
    volume_weights[prelude_bars:][trusted_edge] = (
        float(config["segment_weights"]["edge"]) / int(trusted_edge.sum())
    )
    volume_delta = volume - reference_volume
    volume_distance = float(np.sqrt(np.sum(np.square(volume_delta) * volume_weights)))
    lock_distance = float(
        float(config["price_weight"]) * price_lock
        + float(config["volume_weight"]) * volume_distance
    )
    result = {
        "price_lock_distance": price_lock,
        "volume_distance": volume_distance,
        "lock_distance": lock_distance,
    }
    if not include_dtw:
        return result

    weighted_price = price * np.sqrt(channel_weights)[None, :]
    weighted_reference = reference_price * np.sqrt(channel_weights)[None, :]
    pre_distance = multivariate_dtw_distance(
        weighted_price[:prelude_bars],
        weighted_reference[:prelude_bars],
        radius=int(config["dtw_radius"]),
    )
    edge_distance = multivariate_dtw_distance(
        weighted_price[prelude_bars:],
        weighted_reference[prelude_bars:],
        radius=int(config["dtw_radius"]),
    )
    price_dtw = float(
        float(config["segment_weights"]["prelude"]) * pre_distance
        + float(config["segment_weights"]["edge"]) * edge_distance
    )
    dtw_blend = float(
        float(config["price_weight"]) * price_dtw
        + float(config["volume_weight"]) * volume_distance
    )
    distance = float(
        float(config["lockstep_weight"]) * lock_distance
        + float(config["dtw_weight"]) * dtw_blend
    )
    result.update(
        {
            "price_dtw_distance": price_dtw,
            "distance": distance,
        }
    )
    return result


def series_ohlc(series: SeriesData) -> np.ndarray:
    """Return a compact N x 4 OHLC matrix."""

    return np.column_stack(
        (series.opens, series.highs, series.lows, series.closes)
    )


def candidate_features(
    series: SeriesData,
    start_i: int,
    *,
    window_bars: int,
    prelude_bars: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build normalized features for one fixed window."""

    stop = start_i + window_bars
    ohlc = series_ohlc(series)[start_i:stop]
    volumes = series.volumes[start_i:stop]
    price, volume, _, _ = normalize_window(
        ohlc, volumes, prelude_bars=prelude_bars
    )
    return price, volume


def shortlist_symbol(
    symbol: str,
    series: SeriesData,
    *,
    reference_price: np.ndarray,
    reference_volume: np.ndarray,
    config: dict[str, Any],
    volume_trusted: np.ndarray,
    bar_minutes: int,
) -> list[Candidate]:
    """Vectorize lockstep scoring and keep a frozen per-symbol shortlist."""

    window_bars = int(config["window_bars"])
    prelude_bars = int(config["prelude_bars"])
    review_future_bars = int(config["review_future_bars"])
    n_rows = len(series.times)
    max_start = n_rows - window_bars - review_future_bars
    if max_start < 0:
        return []
    step_ms = bar_minutes * 60 * 1000
    full_continuity = sliding_window_view(
        np.diff(series.times) == step_ms,
        window_bars + review_future_bars - 1,
    ).all(axis=1)
    ohlc = series_ohlc(series)
    ranges = true_range(ohlc)
    pre_scales = np.median(sliding_window_view(ranges, prelude_bars), axis=1)
    offsets = np.arange(window_bars)
    score_rows: list[np.ndarray] = []
    start_rows: list[np.ndarray] = []
    chunk_size = 12000
    for chunk_start in range(0, max_start + 1, chunk_size):
        starts = np.arange(chunk_start, min(max_start + 1, chunk_start + chunk_size))
        scales = pre_scales[starts]
        valid = full_continuity[starts] & np.isfinite(scales) & (scales > 0)
        if not valid.any():
            continue
        starts = starts[valid]
        scales = scales[valid]
        indices = starts[:, None] + offsets[None, :]
        base = series.opens[starts]
        windows = np.stack(
            (
                series.opens[indices],
                series.highs[indices],
                series.lows[indices],
                series.closes[indices],
            ),
            axis=2,
        )
        prices = (windows - base[:, None, None]) / scales[:, None, None]
        window_volumes = series.volumes[indices]
        volume_scales = np.median(window_volumes[:, :prelude_bars], axis=1)
        valid_volume = volume_scales > 0
        normalized_volume = np.zeros_like(window_volumes, dtype=float)
        normalized_volume[valid_volume] = np.log(
            np.maximum(window_volumes[valid_volume], 1e-12)
            / volume_scales[valid_volume, None]
        )

        channel_weights = np.asarray(
            [
                config["price_channel_weights"][name]
                for name in ("open", "high", "low", "close")
            ],
            dtype=float,
        )
        segment_weights = np.r_[
            np.full(
                prelude_bars,
                float(config["segment_weights"]["prelude"]) / prelude_bars,
            ),
            np.full(
                int(config["edge_bars"]),
                float(config["segment_weights"]["edge"]) / int(config["edge_bars"]),
            ),
        ]
        delta = prices - reference_price[None, :, :]
        price_per_bar = np.sum(np.square(delta) * channel_weights, axis=2)
        price_distance = np.sqrt(np.sum(price_per_bar * segment_weights, axis=1))

        volume_weights = np.zeros(window_bars, dtype=float)
        pre_trusted = volume_trusted[:prelude_bars]
        edge_trusted = volume_trusted[prelude_bars:]
        volume_weights[:prelude_bars][pre_trusted] = (
            float(config["segment_weights"]["prelude"]) / int(pre_trusted.sum())
        )
        volume_weights[prelude_bars:][edge_trusted] = (
            float(config["segment_weights"]["edge"]) / int(edge_trusted.sum())
        )
        volume_delta = normalized_volume - reference_volume[None, :]
        volume_distance = np.sqrt(
            np.sum(np.square(volume_delta) * volume_weights[None, :], axis=1)
        )
        lock_distance = (
            float(config["price_weight"]) * price_distance
            + float(config["volume_weight"]) * volume_distance
        )
        lock_distance[~valid_volume] = np.inf
        score_rows.append(
            np.column_stack((lock_distance, price_distance, volume_distance))
        )
        start_rows.append(starts)

    if not score_rows:
        return []
    scores = np.vstack(score_rows)
    starts = np.concatenate(start_rows)
    finite = np.isfinite(scores[:, 0])
    scores = scores[finite]
    starts = starts[finite]
    count = min(int(config["shortlist_per_symbol"]), len(starts))
    selected = np.argpartition(scores[:, 0], count - 1)[:count]
    selected = selected[np.argsort(scores[selected, 0])]
    candidates: list[Candidate] = []
    for index in selected:
        start_i = int(starts[index])
        end_i = start_i + window_bars - 1
        candidates.append(
            Candidate(
                symbol=symbol,
                start_i=start_i,
                end_i=end_i,
                end_ts=int(series.times[end_i]),
                lock_distance=float(scores[index, 0]),
                price_lock_distance=float(scores[index, 1]),
                volume_distance=float(scores[index, 2]),
            )
        )
    return candidates


def rerank_candidates(
    candidates: list[Candidate],
    series_by_symbol: dict[str, SeriesData],
    *,
    reference_price: np.ndarray,
    reference_volume: np.ndarray,
    config: dict[str, Any],
    volume_trusted: np.ndarray,
) -> None:
    """Apply the frozen split-DTW score to every coarse shortlist row."""

    for candidate in candidates:
        price, volume = candidate_features(
            series_by_symbol[candidate.symbol],
            candidate.start_i,
            window_bars=int(config["window_bars"]),
            prelude_bars=int(config["prelude_bars"]),
        )
        score = score_features(
            price,
            volume,
            reference_price=reference_price,
            reference_volume=reference_volume,
            config=config,
            volume_trusted=volume_trusted,
            include_dtw=True,
        )
        candidate.price_dtw_distance = score["price_dtw_distance"]
        candidate.distance = score["distance"]


def dedupe_candidates(
    candidates: list[Candidate],
    *,
    same_symbol_bars: int,
    cross_symbol_minutes: int,
    diversified_top_n: int,
) -> tuple[list[Candidate], list[Candidate]]:
    """Dedupe same-symbol neighborhoods, then diversify market-time shocks."""

    ordered = sorted(
        candidates, key=lambda item: (item.distance, item.symbol, item.end_ts)
    )
    same_symbol: list[Candidate] = []
    accepted_by_symbol: dict[str, list[Candidate]] = {}
    for candidate in ordered:
        prior = accepted_by_symbol.setdefault(candidate.symbol, [])
        if any(abs(candidate.end_i - item.end_i) <= same_symbol_bars for item in prior):
            continue
        prior.append(candidate)
        same_symbol.append(candidate)

    diversified: list[Candidate] = []
    cluster_ms = cross_symbol_minutes * 60 * 1000
    for candidate in same_symbol:
        if any(abs(candidate.end_ts - item.end_ts) <= cluster_ms for item in diversified):
            continue
        diversified.append(candidate)
        if len(diversified) >= diversified_top_n:
            break
    return same_symbol, diversified


def forward_stats(candidate: Candidate, series: SeriesData, future_bars: int) -> dict[str, float]:
    """Return review-only forward descriptors, never candidate features."""

    base = float(series.closes[candidate.end_i])
    result = {
        f"forward_close_{bars}_pct": float(
            (series.closes[candidate.end_i + bars] / base - 1.0) * 100.0
        )
        for bars in (4, 8, future_bars)
    }
    future_high = series.highs[candidate.end_i + 1 : candidate.end_i + future_bars + 1]
    future_low = series.lows[candidate.end_i + 1 : candidate.end_i + future_bars + 1]
    result[f"forward_high_{future_bars}_pct"] = float(
        (future_high.max() / base - 1.0) * 100.0
    )
    result[f"forward_low_{future_bars}_pct"] = float(
        (future_low.min() / base - 1.0) * 100.0
    )
    return result


def iso_utc(timestamp_ms: int) -> str:
    """Format a millisecond timestamp in UTC."""

    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def cst_label(timestamp_ms: int) -> str:
    """Format a millisecond timestamp in Asia/Shanghai without a tz dependency."""

    return (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        + timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M")


def phase_scramble_null(
    candidates: list[Candidate],
    series_by_symbol: dict[str, SeriesData],
    *,
    reference_price: np.ndarray,
    reference_volume: np.ndarray,
    config: dict[str, Any],
    volume_trusted: np.ndarray,
    permutations: int,
    seed: int,
) -> dict[str, Any]:
    """Conditionally test whether the selected edge-row order improves alignment."""

    prelude_bars = int(config["prelude_bars"])
    window_bars = int(config["window_bars"])
    features = [
        candidate_features(
            series_by_symbol[item.symbol],
            item.start_i,
            window_bars=window_bars,
            prelude_bars=prelude_bars,
        )
        for item in candidates
    ]
    real_mean = float(np.mean([item.distance for item in candidates]))
    rng = np.random.default_rng(seed)
    null_means = np.empty(permutations, dtype=float)
    for permutation_i in range(permutations):
        distances: list[float] = []
        for price, volume in features:
            permutation = rng.permutation(window_bars - prelude_bars)
            scrambled_price = price.copy()
            scrambled_volume = volume.copy()
            scrambled_price[prelude_bars:] = price[prelude_bars:][permutation]
            scrambled_volume[prelude_bars:] = volume[prelude_bars:][permutation]
            distances.append(
                score_features(
                    scrambled_price,
                    scrambled_volume,
                    reference_price=reference_price,
                    reference_volume=reference_volume,
                    config=config,
                    volume_trusted=volume_trusted,
                    include_dtw=True,
                )["distance"]
            )
        null_means[permutation_i] = float(np.mean(distances))
    p_value = float((1 + np.sum(null_means <= real_mean)) / (permutations + 1))
    return {
        "conditional_on_frozen_diversified_top_n": len(candidates),
        "real_mean_distance": real_mean,
        "null_mean": float(null_means.mean()),
        "null_median": float(np.median(null_means)),
        "null_p05": float(np.quantile(null_means, 0.05)),
        "null_p95": float(np.quantile(null_means, 0.95)),
        "permutations": permutations,
        "seed": seed,
        "one_sided_p": p_value,
        "smaller_is_more_similar": True,
        "limitation": (
            "Conditional phase-order check on the selected Top-N; not an unconditional "
            "market-wide retrieval p-value and not a return test."
        ),
    }


def moving_averages(close: np.ndarray) -> list[tuple[np.ndarray, str, float]]:
    """Return the repository's SMA/EMA 20/60/120 review overlays."""

    series = pd.Series(close)
    return [
        (series.rolling(20, min_periods=20).mean().to_numpy(), "#111827", 1.15),
        (series.ewm(span=20, adjust=False, min_periods=20).mean().to_numpy(), "#6b7280", 1.0),
        (series.rolling(60, min_periods=60).mean().to_numpy(), "#2862ff", 1.0),
        (series.ewm(span=60, adjust=False, min_periods=60).mean().to_numpy(), "#87a8ff", 1.0),
        (series.rolling(120, min_periods=120).mean().to_numpy(), "#9d27b0", 1.0),
        (series.ewm(span=120, adjust=False, min_periods=120).mean().to_numpy(), "#ce94d7", 1.0),
    ]


def draw_candidate(
    candidate: Candidate,
    series: SeriesData,
    ax_price: Any,
    ax_volume: Any,
    *,
    rank: int,
    config: dict[str, Any],
) -> None:
    """Draw one full-context candidate with matched and review-only regions."""

    review_future_bars = int(config["review_future_bars"])
    left = max(0, candidate.end_i - 41)
    right = candidate.end_i + review_future_bars
    indices = np.arange(left, right + 1)
    x = np.arange(len(indices))
    colors = np.where(
        series.closes[indices] >= series.opens[indices], "#0b9981", "#f23645"
    )
    for local_i, source_i in enumerate(indices):
        ax_price.vlines(
            local_i,
            series.lows[source_i],
            series.highs[source_i],
            color=colors[local_i],
            linewidth=0.9,
            zorder=2,
        )
        bottom = min(series.opens[source_i], series.closes[source_i])
        height = max(
            abs(series.closes[source_i] - series.opens[source_i]),
            series.closes[source_i] * 2e-5,
        )
        ax_price.add_patch(
            Rectangle(
                (local_i - 0.34, bottom),
                0.68,
                height,
                facecolor=colors[local_i],
                edgecolor=colors[local_i],
                linewidth=0.6,
                zorder=3,
            )
        )
    for values, color, width in moving_averages(series.closes):
        ax_price.plot(x, values[indices], color=color, linewidth=width, alpha=0.95)

    matched_start = candidate.start_i - left
    endpoint = candidate.end_i - left
    for axis in (ax_price, ax_volume):
        axis.axvspan(
            matched_start - 0.5,
            endpoint + 0.5,
            color="#e8f1ff",
            alpha=0.32,
            zorder=0,
        )
        axis.axvline(endpoint + 0.5, color="#334155", linestyle="--", linewidth=1.1)
    ax_price.set_title(
        f"#{rank} {candidate.symbol.replace('_USDT_SWAP', '')} · "
        f"{cst_label(candidate.end_ts)} CST · distance {candidate.distance:.3f}",
        loc="left",
        fontsize=10,
        fontweight="bold",
    )
    ax_price.grid(axis="y", color="#e5e7eb", linewidth=0.5)
    ax_price.tick_params(labelsize=7)
    ax_price.yaxis.tick_right()
    for local_i, source_i in enumerate(indices):
        ax_volume.bar(
            local_i,
            series.volumes[source_i],
            width=0.7,
            color="#92d3cc" if colors[local_i] == "#0b9981" else "#f7aaa7",
        )
    ax_volume.set_yticks([])
    ticks = np.linspace(0, len(indices) - 1, 5, dtype=int)
    ax_volume.set_xticks(
        ticks,
        [
            datetime.fromtimestamp(
                series.times[indices[index]] / 1000, tz=timezone.utc
            ).strftime("%m-%d\n%H:%M")
            for index in ticks
        ],
        fontsize=7,
    )
    for axis in (ax_price, ax_volume):
        for spine in ("top", "left", "right"):
            axis.spines[spine].set_visible(False)


def render_candidate_chart(
    output: Path,
    candidate: Candidate,
    series: SeriesData,
    *,
    rank: int,
    config: dict[str, Any],
) -> None:
    """Write one lossless candidate chart."""

    fig = plt.figure(figsize=(12, 5.8), dpi=160, constrained_layout=True)
    grid = fig.add_gridspec(2, 1, height_ratios=[4, 1], hspace=0.03)
    ax_price = fig.add_subplot(grid[0])
    ax_volume = fig.add_subplot(grid[1], sharex=ax_price)
    draw_candidate(
        candidate, series, ax_price, ax_volume, rank=rank, config=config
    )
    plt.setp(ax_price.get_xticklabels(), visible=False)
    stats = forward_stats(candidate, series, int(config["review_future_bars"]))
    future = int(config["review_future_bars"])
    fig.suptitle(
        "Blue = 14-bar match; dashed line = review-only future | "
        f"forward close 4/8/{future}: {stats['forward_close_4_pct']:+.2f}% / "
        f"{stats['forward_close_8_pct']:+.2f}% / "
        f"{stats[f'forward_close_{future}_pct']:+.2f}%",
        fontsize=9,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def render_overview(
    output: Path,
    candidates: list[Candidate],
    series_by_symbol: dict[str, SeriesData],
    *,
    config: dict[str, Any],
) -> None:
    """Write a six-row overview of diversified market-time events."""

    shown = candidates[:6]
    fig = plt.figure(figsize=(15, 16), dpi=150, constrained_layout=True)
    outer = fig.add_gridspec(len(shown), 1)
    for rank, candidate in enumerate(shown, 1):
        inner = outer[rank - 1].subgridspec(2, 1, height_ratios=[4, 1], hspace=0.02)
        ax_price = fig.add_subplot(inner[0])
        ax_volume = fig.add_subplot(inner[1], sharex=ax_price)
        draw_candidate(
            candidate,
            series_by_symbol[candidate.symbol],
            ax_price,
            ax_volume,
            rank=rank,
            config=config,
        )
        plt.setp(ax_price.get_xticklabels(), visible=False)
    fig.suptitle(
        "Right-edge screenshot: pre-holdout 15m historical analogues\n"
        "Blue = ranked 14-bar window; dashed-line right side is review-only",
        fontsize=16,
        fontweight="bold",
    )
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def render_reference_compact(reference_path: Path, output: Path) -> None:
    """Compact the screenshot's price and volume regions without changing pixels."""

    image = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ScreenshotSimilarityError(f"cannot decode reference: {reference_path}")
    height, width = image.shape[:2]
    price = image[: int(height * 0.37)]
    volume = image[int(height * 0.80) : int(height * 0.985)]
    separator = np.full((18, width, 3), 255, dtype=np.uint8)
    compact = np.vstack((price, separator, volume))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), compact, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise OSError(f"failed to write {output}")


def candidate_row(
    rank: int,
    candidate: Candidate,
    series: SeriesData,
    *,
    config: dict[str, Any],
    all_deduped: list[Candidate],
) -> dict[str, Any]:
    """Build one transparent review-manifest row."""

    cluster_ms = int(config["cross_symbol_cluster_minutes"]) * 60 * 1000
    members = [
        item
        for item in all_deduped
        if abs(item.end_ts - candidate.end_ts) <= cluster_ms
    ]
    members.sort(key=lambda item: item.distance)
    return {
        "rank": rank,
        "symbol": candidate.symbol,
        "match_start_utc": iso_utc(series.times[candidate.start_i]),
        "match_end_utc": iso_utc(candidate.end_ts),
        "match_end_cst": cst_label(candidate.end_ts),
        "distance": candidate.distance,
        "lock_distance": candidate.lock_distance,
        "price_lock_distance": candidate.price_lock_distance,
        "price_dtw_distance": candidate.price_dtw_distance,
        "volume_distance": candidate.volume_distance,
        "market_cluster_candidate_count": len(members),
        "market_cluster_best_members": [
            {
                "symbol": item.symbol,
                "end_utc": iso_utc(item.end_ts),
                "distance": item.distance,
            }
            for item in members[:10]
        ],
        **forward_stats(candidate, series, int(config["review_future_bars"])),
        "feature_future_bars": 0,
        "review_future_bars": int(config["review_future_bars"]),
        "training_eligible": False,
        "production_eligible": False,
        "owner_verdict": "PENDING",
    }


def run(
    *,
    prereg_path: Path,
    source_dir: Path,
    reference_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Execute the frozen one-shot retrieval and write its evidence bundle."""

    if output_dir.exists() and any(output_dir.iterdir()):
        raise ScreenshotSimilarityError(f"refusing to overwrite non-empty {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    if sha256_file(reference_path) != prereg["reference"]["sha256"]:
        raise ScreenshotSimilarityError("reference SHA-256 differs from preregistration")
    if prereg["data"]["holdout_start_exclusive"] != HOLDOUT_START.isoformat():
        raise ScreenshotSimilarityError("preregistered holdout boundary drift")

    query_contract = extract_query_contract(reference_path, prereg["reference"])
    query_ohlc = np.asarray(query_contract["pseudo_ohlc"], dtype=float)
    query_volume = np.asarray(query_contract["volume_pixels"], dtype=float)
    volume_trusted = np.asarray(query_contract["volume_trusted"], dtype=bool)
    similarity = prereg["similarity"]
    reference_price, reference_volume, query_price_scale, query_volume_scale = (
        normalize_window(
            query_ohlc,
            query_volume,
            prelude_bars=int(similarity["prelude_bars"]),
        )
    )
    query_contract.update(
        {
            "normalized_price": reference_price.tolist(),
            "normalized_log_volume": reference_volume.tolist(),
            "prelude_median_true_range_pixels": query_price_scale,
            "prelude_median_volume_pixels": query_volume_scale,
        }
    )
    query_contract["query_contract_sha256"] = canonical_sha256(
        {key: value for key, value in query_contract.items() if key != "query_contract_sha256"}
    )

    paths = discover_universe(source_dir, int(prereg["data"]["expected_symbols"]))
    series_by_symbol: dict[str, SeriesData] = {}
    candidates: list[Candidate] = []
    source_audit: list[dict[str, Any]] = []
    for path_index, path in enumerate(paths, 1):
        symbol = symbol_from_path(path)
        series = read_preholdout_csv(path)
        series_by_symbol[symbol] = series
        source_audit.append(series.source_audit)
        symbol_candidates = shortlist_symbol(
            symbol,
            series,
            reference_price=reference_price,
            reference_volume=reference_volume,
            config=similarity,
            volume_trusted=volume_trusted,
            bar_minutes=int(prereg["data"]["bar_minutes"]),
        )
        candidates.extend(symbol_candidates)
        print(
            f"[{path_index:02d}/{len(paths)}] {symbol:18s} "
            f"preholdout={len(series.times):6d} "
            f"best_lock={symbol_candidates[0].lock_distance:.4f}",
            flush=True,
        )

    rerank_candidates(
        candidates,
        series_by_symbol,
        reference_price=reference_price,
        reference_volume=reference_volume,
        config=similarity,
        volume_trusted=volume_trusted,
    )
    deduped, diversified = dedupe_candidates(
        candidates,
        same_symbol_bars=int(similarity["same_symbol_dedupe_bars"]),
        cross_symbol_minutes=int(similarity["cross_symbol_cluster_minutes"]),
        diversified_top_n=int(similarity["diversified_top_n"]),
    )
    if len(diversified) != int(similarity["diversified_top_n"]):
        raise ScreenshotSimilarityError("insufficient diversified candidates")
    raw_top = deduped[: int(similarity["raw_top_n"])]
    review_rows = [
        candidate_row(
            rank,
            candidate,
            series_by_symbol[candidate.symbol],
            config=similarity,
            all_deduped=deduped,
        )
        for rank, candidate in enumerate(diversified, 1)
    ]
    raw_rows = [
        candidate_row(
            rank,
            candidate,
            series_by_symbol[candidate.symbol],
            config=similarity,
            all_deduped=deduped,
        )
        for rank, candidate in enumerate(raw_top, 1)
    ]
    null = phase_scramble_null(
        diversified,
        series_by_symbol,
        reference_price=reference_price,
        reference_volume=reference_volume,
        config=similarity,
        volume_trusted=volume_trusted,
        permutations=int(prereg["null_control"]["permutations"]),
        seed=int(prereg["null_control"]["seed"]),
    )

    charts_dir = output_dir / "charts"
    for rank, candidate in enumerate(diversified, 1):
        filename = (
            f"{rank:02d}_{candidate.symbol}_"
            f"{cst_label(candidate.end_ts).replace(' ', '_').replace(':', '')}.png"
        )
        review_rows[rank - 1]["chart_path"] = f"charts/{filename}"
        render_candidate_chart(
            charts_dir / filename,
            candidate,
            series_by_symbol[candidate.symbol],
            rank=rank,
            config=similarity,
        )
        review_rows[rank - 1]["chart_sha256"] = sha256_file(charts_dir / filename)
    render_overview(
        output_dir / "overview_top6.png",
        diversified,
        series_by_symbol,
        config=similarity,
    )
    render_reference_compact(reference_path, output_dir / "reference_compact.png")

    write_json(output_dir / "query_contract.json", query_contract)
    write_json(output_dir / "source_audit.json", source_audit)
    write_json(output_dir / "raw_top30.json", raw_rows)
    write_jsonl(output_dir / "review_manifest.jsonl", review_rows)
    write_json(output_dir / "null_control.json", null)
    summary = {
        "experiment_id": prereg["experiment_id"],
        "reference_sha256": prereg["reference"]["sha256"],
        "query_contract_sha256": query_contract["query_contract_sha256"],
        "source_symbols": len(paths),
        "preholdout_rows": int(sum(row["preholdout_rows"] for row in source_audit)),
        "holdout_ohlcv_rows_read": 0,
        "holdout_start": HOLDOUT_START.isoformat(),
        "coarse_shortlist_rows": len(candidates),
        "same_symbol_deduped_rows": len(deduped),
        "raw_top_n": len(raw_top),
        "diversified_top_n": len(diversified),
        "best_symbol": diversified[0].symbol,
        "best_match_end_utc": iso_utc(diversified[0].end_ts),
        "best_match_end_cst": cst_label(diversified[0].end_ts),
        "best_distance": diversified[0].distance,
        "null_control": null,
        "training_eligible": False,
        "production_eligible": False,
        "model_state_changes": 0,
        "trading_state_changes": 0,
    }
    write_json(output_dir / "scan_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    """Parse the reproducible CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """CLI entry point."""

    args = parse_args()
    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))
    source_dir = args.source_dir or ROOT / prereg["data"]["source_dir"]
    reference_path = args.reference or ROOT / prereg["reference"]["path"]
    summary = run(
        prereg_path=args.prereg,
        source_dir=source_dir,
        reference_path=reference_path,
        output_dir=args.out,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

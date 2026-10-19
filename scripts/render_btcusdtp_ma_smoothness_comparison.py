#!/usr/bin/env python3
"""Render a causal BTCUSDT.P moving-average visual comparison.

Source columns/window: Binance USD-M BTCUSDT 15m OHLCV, loaded only through
global row 55331 (2021-07-30 08:45 UTC).  Moving averages are computed on that
causal prefix, then displayed either over the final 160 bars for numerical
smoothness or over the exact 19-bar owner-event window for pixel styling.

Decision use: owner visual screening only.  The script performs no inference,
training, label mutation, threshold selection, holdout read, or production
change.  Every panel within a sheet shares the same chart transform so apparent
smoothness cannot be created by a different y-axis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.window_render import enrich, load_prefix, window_slice
from yoyo.layers.l1_detection import render as render_module
from yoyo.layers.l1_detection.data import ALL_MA_COLS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT
    / "data/kline_preholdout_binance_um15m/series"
    / "binance_um_BTCUSDT_15m_221952.csv"
)
DEFAULT_OUTPUT_DIR = ROOT / "analysis/output/p1_btcusdtp_ma_visual_comparison_20260903"
DEFAULT_PREREGISTRATION = DEFAULT_OUTPUT_DIR / "preregistration.json"
BASELINE_DATASET_IMAGE = (
    ROOT
    / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/images/train"
    / "A02629_BTC_USDT_SWAP_SHORT_d119de99b7c6cb6fc4149a1f.png"
)
EXPECTED_PREREGISTRATION_SHA256 = (
    "a424498023fa0dc5c06a85c9d7afd954fe746692f40563f991557b253dff4518"
)
WINDOW_START = 55313
WINDOW_END = 55331
CONTEXT_BARS = 160
CONTEXT_START = WINDOW_END - CONTEXT_BARS + 1
CORE_START = 55325
CORE_END = 55329
EXPECTED_WINDOW_START = pd.Timestamp("2021-07-30T04:15:00Z")
EXPECTED_WINDOW_END = pd.Timestamp("2021-07-30T08:45:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
FAMILY_PERIODS = (20, 40, 60, 80, 100, 120)
PALETTE = tuple(render_module.MA_COLORS[column] for column in ALL_MA_COLS)
MONO_COLOR = (126, 120, 154)
CHARTPRIME_UP = (151, 162, 23)  # RGB #17a297 converted to BGR.
CHARTPRIME_DOWN = (0, 165, 255)  # Pine color.orange converted to BGR.

NUMERIC_CONDITIONS = OrderedDict(
    [
        ("01_current_close", "Current: close SMA/EMA 20/60/120"),
        ("02_current_hl2", "Current family on hl2"),
        ("03_hl2_sma6", "hl2: 6 x SMA"),
        ("04_hl2_ema6", "hl2: 6 x EMA"),
        ("05_hl2_rma6", "hl2: 6 x RMA"),
        ("06_hl2_wma6", "hl2: 6 x WMA"),
        ("07_hl2_vwma6", "hl2: 6 x VWMA"),
        ("08_hl2_hma6", "hl2: 6 x HMA (extra)"),
        ("09_chartprime_default", "ChartPrime default: one SMA40 + glow"),
    ]
)

STYLE_CONDITIONS = OrderedDict(
    [
        ("01_current_1px_multicolor", "Current: 1px, six colors"),
        ("02_2px_multicolor", "2px, six colors"),
        ("03_3px_multicolor", "3px, six colors"),
        ("04_2px_multicolor_glow", "2px + 7px glow, six colors"),
        ("05_1px_monochrome", "1px, one MA color"),
        ("06_2px_monochrome", "2px, one MA color"),
        ("07_2px_monochrome_glow", "2px + 7px glow, one MA color"),
        ("08_sma2_ema1_multicolor", "SMA 2px / EMA 1px, six colors"),
    ]
)


class VisualComparisonError(RuntimeError):
    """Raised when the frozen visual-comparison contract drifts."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sma(source: pd.Series, length: int) -> pd.Series:
    return source.rolling(length, min_periods=length).mean()


def ema(source: pd.Series, length: int) -> pd.Series:
    return source.ewm(span=length, adjust=False).mean()


def rma(source: pd.Series, length: int) -> pd.Series:
    values = source.to_numpy(dtype=float)
    output = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return pd.Series(output, index=source.index)
    seed = values[:length]
    if np.all(np.isfinite(seed)):
        output[length - 1] = float(np.mean(seed))
    alpha = 1.0 / length
    for index in range(length, len(values)):
        value, previous = values[index], output[index - 1]
        if np.isfinite(value) and np.isfinite(previous):
            output[index] = alpha * value + (1.0 - alpha) * previous
    return pd.Series(output, index=source.index)


def wma(source: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(weights.sum())
    return source.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / denominator), raw=True
    )


def vwma(source: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    numerator = (source * volume).rolling(length, min_periods=length).sum()
    denominator = volume.rolling(length, min_periods=length).sum().replace(0, np.nan)
    return numerator / denominator


def hma(source: pd.Series, length: int) -> pd.Series:
    half = max(1, length // 2)
    root = max(1, round(math.sqrt(length)))
    return wma(2.0 * wma(source, half) - wma(source, length), root)


def family_lines(
    source: pd.Series,
    volume: pd.Series,
    family: str,
) -> OrderedDict[str, pd.Series]:
    functions = {
        "sma": lambda period: sma(source, period),
        "ema": lambda period: ema(source, period),
        "rma": lambda period: rma(source, period),
        "wma": lambda period: wma(source, period),
        "vwma": lambda period: vwma(source, volume, period),
        "hma": lambda period: hma(source, period),
    }
    if family not in functions:
        raise VisualComparisonError(f"unknown MA family: {family}")
    return OrderedDict(
        (f"{family.upper()}{period}", functions[family](period))
        for period in FAMILY_PERIODS
    )


def current_lines(source: pd.Series) -> OrderedDict[str, pd.Series]:
    lines: OrderedDict[str, pd.Series] = OrderedDict()
    for period in (20, 60, 120):
        lines[f"SMA{period}"] = sma(source, period)
    for period in (20, 60, 120):
        lines[f"EMA{period}"] = ema(source, period)
    return lines


def slice_lines(
    lines: Mapping[str, pd.Series], start: int, end: int
) -> OrderedDict[str, np.ndarray]:
    return OrderedDict(
        (label, series.iloc[start : end + 1].to_numpy(dtype=float))
        for label, series in lines.items()
    )


def draw_candles(
    image: np.ndarray,
    frame: pd.DataFrame,
    transform: render_module.ChartTransform,
) -> None:
    for index, (_, candle) in enumerate(frame.iterrows()):
        x = transform.x_at(index)
        y_high = transform.y_at(candle["high"])
        y_low = transform.y_at(candle["low"])
        y_open = transform.y_at(candle["open"])
        y_close = transform.y_at(candle["close"])
        color = (
            render_module.CANDLE_GREEN
            if float(candle["close"]) >= float(candle["open"])
            else render_module.CANDLE_RED
        )
        cv2.line(
            image,
            (x, y_high),
            (x, y_low),
            render_module.WICK,
            1,
            cv2.LINE_AA,
        )
        y1, y2 = min(y_open, y_close), max(y_open, y_close)
        if y2 - y1 < 2:
            y2 = y1 + 2
        cv2.rectangle(
            image,
            (x - transform.candle_half_w, y1),
            (x + transform.candle_half_w, y2),
            color,
            -1,
            cv2.LINE_AA,
        )


def line_points(
    values: np.ndarray, transform: render_module.ChartTransform
) -> np.ndarray:
    return np.asarray(
        [
            (transform.x_at(index), transform.y_at(float(value)))
            for index, value in enumerate(values)
            if np.isfinite(value)
        ],
        dtype=np.int32,
    )


def draw_standard_lines(
    image: np.ndarray,
    lines: Mapping[str, np.ndarray],
    transform: render_module.ChartTransform,
    *,
    widths: Mapping[str, int] | int,
    monochrome: bool,
    glow_width: int | None,
    glow_alpha: float = 0.20,
) -> None:
    labels = list(lines)
    colors = [MONO_COLOR] * len(labels) if monochrome else list(PALETTE[: len(labels)])
    if glow_width is not None:
        overlay = image.copy()
        for label, color in zip(labels, colors):
            points = line_points(lines[label], transform)
            if len(points) >= 2:
                cv2.polylines(
                    overlay, [points], False, color, glow_width, cv2.LINE_AA
                )
        cv2.addWeighted(overlay, glow_alpha, image, 1.0 - glow_alpha, 0, dst=image)
    for label, color in zip(labels, colors):
        points = line_points(lines[label], transform)
        width = int(widths[label]) if isinstance(widths, Mapping) else int(widths)
        if len(points) >= 2:
            cv2.polylines(image, [points], False, color, width, cv2.LINE_AA)


def draw_chartprime_line(
    image: np.ndarray,
    line: np.ndarray,
    source: np.ndarray,
    transform: render_module.ChartTransform,
) -> None:
    def draw_segments(target: np.ndarray, width: int) -> None:
        for index in range(1, len(line)):
            if not all(np.isfinite([line[index - 1], line[index], source[index]])):
                continue
            color = CHARTPRIME_UP if source[index] >= line[index] else CHARTPRIME_DOWN
            cv2.line(
                target,
                (transform.x_at(index - 1), transform.y_at(float(line[index - 1]))),
                (transform.x_at(index), transform.y_at(float(line[index]))),
                color,
                width,
                cv2.LINE_AA,
            )

    overlay = image.copy()
    draw_segments(overlay, 7)
    cv2.addWeighted(overlay, 0.20, image, 0.80, 0, dst=image)
    draw_segments(image, 2)


def render_panel(
    frame: pd.DataFrame,
    lines: Mapping[str, np.ndarray],
    transform: render_module.ChartTransform,
    *,
    widths: Mapping[str, int] | int = 2,
    monochrome: bool = False,
    glow_width: int | None = None,
    chartprime_source: np.ndarray | None = None,
) -> np.ndarray:
    image = np.full(
        (transform.height, transform.width, 3),
        render_module.BG,
        dtype=np.uint8,
    )
    draw_candles(image, frame.reset_index(drop=True), transform)
    if chartprime_source is not None:
        if len(lines) != 1:
            raise VisualComparisonError("ChartPrime reference must contain one line")
        draw_chartprime_line(
            image, next(iter(lines.values())), chartprime_source, transform
        )
    else:
        draw_standard_lines(
            image,
            lines,
            transform,
            widths=widths,
            monochrome=monochrome,
            glow_width=glow_width,
        )
    return image


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise VisualComparisonError(f"failed to write PNG: {path}")


def make_contact_sheet(
    images: Mapping[str, np.ndarray],
    labels: Mapping[str, str],
    output: Path,
    *,
    columns: int,
    cell_width: int,
) -> None:
    first = next(iter(images.values()))
    cell_height = round(first.shape[0] * cell_width / first.shape[1])
    header_height = 40
    cells: list[np.ndarray] = []
    for key, image in images.items():
        thumbnail = cv2.resize(
            image, (cell_width, cell_height), interpolation=cv2.INTER_AREA
        )
        header = np.full((header_height, cell_width, 3), 245, dtype=np.uint8)
        cv2.putText(
            header,
            labels[key],
            (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (25, 25, 25),
            1,
            cv2.LINE_AA,
        )
        cells.append(np.vstack([header, thumbnail]))
    while len(cells) % columns:
        cells.append(np.full_like(cells[0], 255))
    rows = [
        np.hstack(cells[index : index + columns])
        for index in range(0, len(cells), columns)
    ]
    write_png(output, np.vstack(rows))


def numeric_metrics(
    lines: Mapping[str, np.ndarray], transform: render_module.ChartTransform
) -> dict[str, float]:
    slopes: list[float] = []
    curvatures: list[float] = []
    pixel_lines: list[np.ndarray] = []
    for values in lines.values():
        pixels = np.asarray(
            [transform.y_at(float(value)) if np.isfinite(value) else np.nan for value in values],
            dtype=float,
        )
        pixels = pixels[np.isfinite(pixels)]
        if len(pixels) >= 2:
            slopes.append(float(np.mean(np.abs(np.diff(pixels)))))
        if len(pixels) >= 3:
            curvatures.append(float(np.mean(np.abs(np.diff(pixels, n=2)))))
        pixel_lines.append(pixels)
    width = float("nan")
    if pixel_lines and len({len(values) for values in pixel_lines}) == 1:
        matrix = np.vstack(pixel_lines)
        width = float(np.median(np.max(matrix, axis=0) - np.min(matrix, axis=0)))
    return {
        "mean_abs_slope_px_per_bar": float(np.mean(slopes)) if slopes else float("nan"),
        "mean_abs_curvature_px_per_bar2": (
            float(np.mean(curvatures)) if curvatures else float("nan")
        ),
        "median_bundle_width_px": width,
    }


def prefix_content_sha256(frame: pd.DataFrame) -> str:
    columns = ["open_time", "open", "high", "low", "close", "volume"]
    hashed = pd.util.hash_pandas_object(frame[columns], index=True).to_numpy(dtype=np.uint64)
    return hashlib.sha256(hashed.tobytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--preregistration", type=Path, default=DEFAULT_PREREGISTRATION)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_path = args.source.resolve()
    output_dir = args.output_dir.resolve()
    preregistration = args.preregistration.resolve()
    results_path = output_dir / "render_receipt.json"
    numeric_sheet_path = output_dir / "numeric_families_contact_sheet.png"
    style_sheet_path = output_dir / "pixel_styles_contact_sheet.png"
    for path in (results_path, numeric_sheet_path, style_sheet_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite visual artifact: {path}")
    if (
        not preregistration.is_file()
        or sha256_file(preregistration) != EXPECTED_PREREGISTRATION_SHA256
    ):
        raise VisualComparisonError("preregistration identity drifted")

    raw = load_prefix(source_path, WINDOW_END)
    if len(raw) != WINDOW_END + 1:
        raise VisualComparisonError(f"causal prefix length drifted: {len(raw)}")
    if raw.iloc[WINDOW_START]["open_time"] != EXPECTED_WINDOW_START:
        raise VisualComparisonError("window start timestamp drifted")
    if raw.iloc[WINDOW_END]["open_time"] != EXPECTED_WINDOW_END:
        raise VisualComparisonError("window end timestamp drifted")
    if raw["open_time"].max() >= HOLDOUT_START:
        raise VisualComparisonError("holdout boundary violated")

    current = enrich(raw)
    close = raw["close"]
    hl2 = (raw["high"] + raw["low"]) / 2.0
    volume = raw["volume"]
    current_close = current_lines(close)
    current_hl2 = current_lines(hl2)
    numeric_full: OrderedDict[str, OrderedDict[str, pd.Series]] = OrderedDict(
        [
            ("01_current_close", current_close),
            ("02_current_hl2", current_hl2),
            ("03_hl2_sma6", family_lines(hl2, volume, "sma")),
            ("04_hl2_ema6", family_lines(hl2, volume, "ema")),
            ("05_hl2_rma6", family_lines(hl2, volume, "rma")),
            ("06_hl2_wma6", family_lines(hl2, volume, "wma")),
            ("07_hl2_vwma6", family_lines(hl2, volume, "vwma")),
            ("08_hl2_hma6", family_lines(hl2, volume, "hma")),
            ("09_chartprime_default", OrderedDict([("SMA40", sma(hl2, 40))])),
        ]
    )
    if tuple(numeric_full) != tuple(NUMERIC_CONDITIONS):
        raise VisualComparisonError("numeric condition order drifted")

    context = window_slice(current, CONTEXT_START, WINDOW_END)
    yolo_window = window_slice(current, WINDOW_START, WINDOW_END)
    context_transform = render_module.make_chart_transform(context)
    yolo_transform = render_module.make_chart_transform(yolo_window)

    numeric_images: OrderedDict[str, np.ndarray] = OrderedDict()
    numeric_line_values: OrderedDict[str, OrderedDict[str, np.ndarray]] = OrderedDict()
    for key, lines in numeric_full.items():
        sliced = slice_lines(lines, CONTEXT_START, WINDOW_END)
        numeric_line_values[key] = sliced
        chartprime_source = (
            hl2.iloc[CONTEXT_START : WINDOW_END + 1].to_numpy(dtype=float)
            if key == "09_chartprime_default"
            else None
        )
        numeric_images[key] = render_panel(
            context,
            sliced,
            context_transform,
            widths=2,
            chartprime_source=chartprime_source,
        )

    style_lines = slice_lines(current_close, WINDOW_START, WINDOW_END)
    style_images: OrderedDict[str, np.ndarray] = OrderedDict(
        [
            (
                "01_current_1px_multicolor",
                render_panel(yolo_window, style_lines, yolo_transform, widths=1),
            ),
            (
                "02_2px_multicolor",
                render_panel(yolo_window, style_lines, yolo_transform, widths=2),
            ),
            (
                "03_3px_multicolor",
                render_panel(yolo_window, style_lines, yolo_transform, widths=3),
            ),
            (
                "04_2px_multicolor_glow",
                render_panel(
                    yolo_window,
                    style_lines,
                    yolo_transform,
                    widths=2,
                    glow_width=7,
                ),
            ),
            (
                "05_1px_monochrome",
                render_panel(
                    yolo_window,
                    style_lines,
                    yolo_transform,
                    widths=1,
                    monochrome=True,
                ),
            ),
            (
                "06_2px_monochrome",
                render_panel(
                    yolo_window,
                    style_lines,
                    yolo_transform,
                    widths=2,
                    monochrome=True,
                ),
            ),
            (
                "07_2px_monochrome_glow",
                render_panel(
                    yolo_window,
                    style_lines,
                    yolo_transform,
                    widths=2,
                    monochrome=True,
                    glow_width=7,
                ),
            ),
            (
                "08_sma2_ema1_multicolor",
                render_panel(
                    yolo_window,
                    style_lines,
                    yolo_transform,
                    widths={
                        label: (2 if label.startswith("SMA") else 1)
                        for label in style_lines
                    },
                ),
            ),
        ]
    )
    if tuple(style_images) != tuple(STYLE_CONDITIONS):
        raise VisualComparisonError("style condition order drifted")
    original_baseline = cv2.imread(str(BASELINE_DATASET_IMAGE), cv2.IMREAD_COLOR)
    if original_baseline is None or not np.array_equal(
        original_baseline, style_images["01_current_1px_multicolor"]
    ):
        raise VisualComparisonError("current 19-bar baseline pixel parity failed")

    output_dir.mkdir(parents=True, exist_ok=True)
    numeric_dir = output_dir / "numeric_full_resolution"
    style_dir = output_dir / "style_full_resolution"
    for key, image in numeric_images.items():
        write_png(numeric_dir / f"{key}.png", image)
    for key, image in style_images.items():
        write_png(style_dir / f"{key}.png", image)
    make_contact_sheet(
        numeric_images,
        NUMERIC_CONDITIONS,
        numeric_sheet_path,
        columns=3,
        cell_width=500,
    )
    make_contact_sheet(
        style_images,
        STYLE_CONDITIONS,
        style_sheet_path,
        columns=4,
        cell_width=480,
    )

    all_outputs = [numeric_sheet_path, style_sheet_path]
    all_outputs.extend(sorted(numeric_dir.glob("*.png")))
    all_outputs.extend(sorted(style_dir.glob("*.png")))
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "p1-btcusdtp-ma-visual-comparison-20260903",
        "classification": "read_only_preholdout_visual_screening",
        "generator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "preregistration": {
            "path": str(preregistration.relative_to(ROOT)),
            "sha256": sha256_file(preregistration),
        },
        "source": {
            "path": str(source_path.relative_to(ROOT)),
            "symbol": "BTCUSDT.P",
            "venue": "Binance USD-M",
            "timeframe": "15m",
            "loaded_prefix_rows": len(raw),
            "loaded_prefix_content_sha256": prefix_content_sha256(raw),
            "maximum_loaded_time": raw["open_time"].max().isoformat(),
            "holdout_rows_read": 0,
        },
        "event": {
            "event_id": "d2fdfcb2fc81a9d80439",
            "direction": "SHORT",
            "window_start_i": WINDOW_START,
            "window_end_i": WINDOW_END,
            "window_start_time": raw.iloc[WINDOW_START]["open_time"].isoformat(),
            "window_end_time": raw.iloc[WINDOW_END]["open_time"].isoformat(),
            "core_start_i": CORE_START,
            "core_end_i": CORE_END,
            "baseline_dataset_image": str(BASELINE_DATASET_IMAGE.relative_to(ROOT)),
            "baseline_decoded_pixel_match": True,
        },
        "shared_axes": {
            "numeric_context": {
                "bars": CONTEXT_BARS,
                "price_min": context_transform.price_min,
                "price_max": context_transform.price_max,
            },
            "yolo_window_style": {
                "bars": len(yolo_window),
                "price_min": yolo_transform.price_min,
                "price_max": yolo_transform.price_max,
            },
        },
        "numeric_conditions": {
            key: {
                "label": NUMERIC_CONDITIONS[key],
                "metrics": numeric_metrics(lines, context_transform),
            }
            for key, lines in numeric_line_values.items()
        },
        "style_conditions": {
            key: {"label": STYLE_CONDITIONS[key]} for key in style_images
        },
        "outputs": {
            str(path.relative_to(ROOT)): {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "width": int(cv2.imread(str(path)).shape[1]),
                "height": int(cv2.imread(str(path)).shape[0]),
            }
            for path in all_outputs
        },
        "interpretation": {
            "visual_only": True,
            "model_inference_performed": False,
            "training_performed": False,
            "holdout_consumed": False,
            "threshold_tuned": False,
            "dataset_mutated": False,
            "active_or_frozen_changed": False,
            "production_eligible": False,
        },
    }
    results_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "receipt": str(results_path),
                "numeric_sheet": str(numeric_sheet_path),
                "style_sheet": str(style_sheet_path),
                "numeric_metrics": {
                    key: value["metrics"]
                    for key, value in receipt["numeric_conditions"].items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

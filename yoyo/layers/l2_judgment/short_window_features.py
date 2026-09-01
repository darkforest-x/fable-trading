"""Box-aware L2 features from the exact 18/19-bar L1 visual window.

Inputs are limited to the OHLC values and the six SMA/EMA 20/60/120 values
drawn inside one already-renderable L1 window, plus the raw L1 prediction box
and confidence available for that same window.  The moving-average values may
depend on closes before the visible window by definition, but no earlier raw
bar, rolling statistic, future bar, volume value, symbol identity, or outcome
is exposed to the model.

Each price/MA value is expressed in the renderer's normalized vertical
coordinate system.  Windows are right-aligned to 19 positions, so the decision
bar is always ``t00`` and an 18-bar input has one explicit missing ``t18``
position.  This preserves time direction without resizing or inventing a bar.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


MAX_WINDOW_BARS = 19
ALLOWED_WINDOW_BARS = (18, 19)
VISIBLE_PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "sma20",
    "sma60",
    "sma120",
    "ema20",
    "ema60",
    "ema120",
)
BOX_FEATURE_COLUMNS = (
    "l1_confidence",
    "prediction_cx_norm",
    "prediction_cy_norm",
    "prediction_w_norm",
    "prediction_h_norm",
    "window_len",
    "core_start_local_norm",
    "core_end_local_norm",
    "core_length_bars",
    "confirmation_bars",
)


def _slot_prefix(offset: int) -> str:
    return f"t{offset:02d}"


SEQUENCE_FEATURE_COLUMNS = tuple(
    feature
    for offset in range(MAX_WINDOW_BARS - 1, -1, -1)
    for feature in (
        f"{_slot_prefix(offset)}_valid",
        f"{_slot_prefix(offset)}_in_core",
        *(f"{_slot_prefix(offset)}_{column}_y" for column in VISIBLE_PRICE_COLUMNS),
    )
)
SHORT_WINDOW_FEATURE_COLUMNS = BOX_FEATURE_COLUMNS + SEQUENCE_FEATURE_COLUMNS


def _finite_unit(value: object, name: str) -> float:
    number = float(value)
    if not np.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite in [0, 1], got {value!r}")
    return number


def _price_y(value: object, *, price_min: float, price_max: float) -> float:
    number = float(value)
    if not np.isfinite(number):
        return float("nan")
    span = float(price_max) - float(price_min)
    if not np.isfinite(span) or span <= 0:
        raise ValueError("renderer price bounds must define a positive finite span")
    return (float(price_max) - number) / span


def extract_short_window_features(
    window: pd.DataFrame,
    detection: Mapping[str, object],
    *,
    price_min: float,
    price_max: float,
) -> dict[str, float]:
    """Return the fixed short-window feature vector for one L1 proposal.

    ``window`` must contain exactly 18 or 19 chronological rows ending at the
    L1 decision bar.  ``detection`` supplies only the current raw box geometry,
    its confidence, and mapped core indices.  No row outside ``window`` is read.
    """

    frame = window.reset_index(drop=True)
    n_bars = len(frame)
    if n_bars not in ALLOWED_WINDOW_BARS:
        raise ValueError(f"window must contain 18 or 19 bars, got {n_bars}")
    missing = sorted(set(VISIBLE_PRICE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"window missing visible columns: {missing}")

    declared_window = int(detection["window_len"])
    if declared_window != n_bars:
        raise ValueError(f"declared window_len {declared_window} != rows {n_bars}")
    window_start_i = int(detection["window_start_i"])
    core_start_local = int(detection["core_start_i"]) - window_start_i
    core_end_local = int(detection["core_end_i"]) - window_start_i
    if not 0 <= core_start_local <= core_end_local < n_bars:
        raise ValueError(
            f"mapped core [{core_start_local}, {core_end_local}] escapes {n_bars}-bar window"
        )
    core_length = core_end_local - core_start_local + 1
    confirmation = n_bars - core_end_local - 1
    if int(detection["confirmation_bars"]) != confirmation:
        raise ValueError("confirmation_bars differs from mapped core geometry")

    features: dict[str, float] = {
        "l1_confidence": _finite_unit(detection["l1_confidence"], "l1_confidence"),
        "prediction_cx_norm": _finite_unit(
            detection["prediction_cx_norm"], "prediction_cx_norm"
        ),
        "prediction_cy_norm": _finite_unit(
            detection["prediction_cy_norm"], "prediction_cy_norm"
        ),
        "prediction_w_norm": _finite_unit(
            detection["prediction_w_norm"], "prediction_w_norm"
        ),
        "prediction_h_norm": _finite_unit(
            detection["prediction_h_norm"], "prediction_h_norm"
        ),
        "window_len": float(n_bars),
        "core_start_local_norm": core_start_local / max(n_bars - 1, 1),
        "core_end_local_norm": core_end_local / max(n_bars - 1, 1),
        "core_length_bars": float(core_length),
        "confirmation_bars": float(confirmation),
    }

    # Right alignment: a 19-bar window fills t18..t00; an 18-bar window fills
    # t17..t00 and leaves the oldest t18 slot explicitly missing.
    first_offset = n_bars - 1
    for offset in range(MAX_WINDOW_BARS - 1, -1, -1):
        prefix = _slot_prefix(offset)
        local_index = first_offset - offset
        if local_index < 0:
            features[f"{prefix}_valid"] = 0.0
            features[f"{prefix}_in_core"] = 0.0
            for column in VISIBLE_PRICE_COLUMNS:
                features[f"{prefix}_{column}_y"] = float("nan")
            continue
        features[f"{prefix}_valid"] = 1.0
        features[f"{prefix}_in_core"] = float(
            core_start_local <= local_index <= core_end_local
        )
        row = frame.iloc[local_index]
        for column in VISIBLE_PRICE_COLUMNS:
            features[f"{prefix}_{column}_y"] = _price_y(
                row[column], price_min=price_min, price_max=price_max
            )

    if tuple(features) != SHORT_WINDOW_FEATURE_COLUMNS:
        raise AssertionError("short-window feature order drifted")
    return features

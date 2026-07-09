"""Higher-timeframe trend flags for judgment-layer dense-breakout signals."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import numpy as np
import pandas as pd

from src.data.loader import list_series, load_series

SeriesKey = tuple[str, str]
SIGNAL_BAR: Final = pd.Timedelta(minutes=15)
H1_BAR: Final = pd.Timedelta(hours=1)
H1_EMA_MIN_BARS: Final = 55
H9_FLAG_COLUMNS: Final = ("h1_up_slope", "h1_above_ma", "h1_ok")


def hourly_state(frame: pd.DataFrame) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    close = frame.set_index("open_time")["close"].resample("1h").last().dropna()
    ema55 = close.ewm(span=55, adjust=False).mean()
    ema144 = close.ewm(span=144, adjust=False).mean()
    up_slope = (ema55.diff(12) > 0).to_numpy()
    above_ma = (close > ema144).to_numpy()
    return close.index, up_slope, above_ma


def add_h9_flags(
    rows: pd.DataFrame,
    *,
    series_frames: Mapping[SeriesKey, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    out = rows.copy()
    for column in H9_FLAG_COLUMNS:
        out[column] = False
    if out.empty:
        return out

    groups = list_series() if series_frames is None else None
    for (source, symbol), group in out.groupby(["source", "symbol"]):
        key = (str(source), str(symbol))
        if series_frames is not None:
            frame = series_frames.get(key)
            if frame is None:
                continue
        else:
            assert groups is not None
            paths = groups.get(key)
            if paths is None:
                continue
            frame = load_series(paths)
        idx, up_slope, above_ma = hourly_state(frame)
        if len(idx) == 0:
            continue
        cutoff = (group["signal_time"] + SIGNAL_BAR - H1_BAR).to_numpy()
        pos = idx.searchsorted(cutoff, side="right") - 1
        valid = pos >= H1_EMA_MIN_BARS
        row_locs = out.index.get_indexer(group.index)
        clipped = np.clip(pos, 0, len(idx) - 1)
        out.iloc[row_locs, out.columns.get_loc("h1_up_slope")] = np.where(valid, up_slope[clipped], False)
        out.iloc[row_locs, out.columns.get_loc("h1_above_ma")] = np.where(valid, above_ma[clipped], False)
        out.iloc[row_locs, out.columns.get_loc("h1_ok")] = valid
    return out

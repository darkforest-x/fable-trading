"""Pure event-clock alignment of externally validated five-minute taker flow.

Sources: Binance USD-M kline schema (binance-public-data#futures) and pandas
2.3.3 read_csv/DatetimeIndex contracts. Uses only open_time, earliest_available_at,
optional observed_at, quote_volume, taker buy/sell quote amounts, quote delta,
and trade_count within caller-declared [window_start, window_end) intervals.
No OHLC, future labels, rolling tuning, IO, threshold, or trading decision.

Each complete source bar must be available by decision_time. Historical close
boundaries are NOT observed exchange/network delivery. Missing bars, unavailable
bars and known zero activity stay distinct. A missing window is never aggregated
as if complete. Imbalance is sum(delta)/sum(quote), not mean(bar imbalance).
This module cannot establish profitability or permission to read a data source.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

STEP = pd.Timedelta(minutes=5)
AMOUNTS = ["quote_volume", "taker_buy_quote_volume", "taker_sell_quote_volume",
           "delta_quote_volume", "trade_count"]
WINDOW_COLUMNS = ["window_id", "event_id", "window_kind", "window_start",
                  "window_end", "decision_time", "direction"]
RESULT_COLUMNS = ["expected_bars", "observed_bars", "available_bars", "missing_bars",
                  "unavailable_bars", "zero_volume_bars", "status", "quote_volume_sum",
                  "delta_quote_volume_sum", "imbalance", "directional_imbalance",
                  "flow_defined", "max_source_available_at"]


def clocks(values: pd.Series, name: str, *, missing: bool = False,
           grid: bool = True) -> pd.Series:
    """Require explicit zoned clocks, not guessed epoch units or local times."""
    if pd.api.types.is_numeric_dtype(values.dtype) and len(values):
        raise ValueError(name + " must be explicitly zoned timestamps")
    if not isinstance(values.dtype, pd.DatetimeTZDtype):
        for value in values:
            if pd.isna(value) and missing:
                continue
            if isinstance(value, (int, float, np.number)) or pd.isna(value):
                raise ValueError(name + " invalid clock")
            if pd.Timestamp(value).tzinfo is None:
                raise ValueError(name + " requires explicit timezone")
    result = pd.to_datetime(values, utc=True, format="mixed").dt.as_unit("ns")
    if not missing and result.isna().any():
        raise ValueError(name + " missing clock")
    if grid and (result.dropna().astype("int64") % STEP.value != 0).any():
        raise ValueError(name + " must be on five-minute grid")
    return result


def _check_amounts(frame: pd.DataFrame) -> None:
    """Validate only available in-window amounts; future values cannot veto past."""
    values = frame[AMOUNTS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("non-finite available flow")
    total, buy, sell, delta, count = values.T
    if (np.minimum.reduce([total, buy, sell, count]) < 0).any():
        raise ValueError("negative available amount/count")
    if (count != np.floor(count)).any() or (count >= 2**63).any():
        raise ValueError("trade count must be int64-valued")
    if ((total == 0) != (count == 0)).any():
        raise ValueError("zero trading/count mismatch")
    if ((total == 0) & ((buy != 0) | (sell != 0) | (delta != 0))).any():
        raise ValueError("zero volume has nonzero flow")
    eps = 8 * np.finfo(float).eps
    # Cancellation error scales with operands, not the tiny resulting delta.
    if (np.abs(buy + sell - total) > eps * (buy + sell + total)).any():
        raise ValueError("buy/sell conservation mismatch")
    if (np.abs(buy - sell - delta) > eps * (buy + sell + np.abs(delta))).any():
        raise ValueError("delta conservation mismatch")


def align_windows(flow: pd.DataFrame, windows: pd.DataFrame, *,
                  availability_mode: str = "historical_boundary") -> pd.DataFrame:
    """Align each independently declared window without changing either input.

    ``observed_delivery`` requires caller-supplied observed_at for every bar;
    absent/NaT/late arrival remains unavailable. Even an early delivery stamp
    cannot make an unclosed bar available. Window and source identities must
    be unique; gaps are reported, never forward-filled or silently deduplicated.
    Returned metadata belongs to its stated decision clock, not earlier K1 time.
    max_source_available_at is the maximum of the AVAILABLE subset only; it
    cannot measure lateness of missing/unavailable bars, whose counts stay explicit.
    """
    if availability_mode not in {"historical_boundary", "observed_delivery"}:
        raise ValueError("unknown availability mode")
    if not set(WINDOW_COLUMNS).issubset(windows) or not set(
        ["open_time", "earliest_available_at"] + AMOUNTS
    ).issubset(flow):
        raise ValueError("missing required columns")
    if set(RESULT_COLUMNS).intersection(windows):
        raise ValueError("result columns collide with input")
    w = windows.copy(deep=True).reset_index(drop=True)
    f = flow.copy(deep=True)
    for col in ["window_id", "event_id", "window_kind"]:
        if w[col].isna().any() or w[col].astype(str).str.strip().eq("").any():
            raise ValueError("missing window identity")
    if w["window_id"].duplicated().any():
        raise ValueError("duplicate window identity")
    if not w["direction"].isin([-1, 1]).all() or w["direction"].map(
        lambda x: isinstance(x, (bool, np.bool_))
    ).any():
        raise ValueError("direction must be -1 or +1")
    for col in ["window_start", "window_end", "decision_time"]:
        w[col] = clocks(w[col], col)
    if ((w.window_start >= w.window_end) | (w.window_end > w.decision_time)).any():
        raise ValueError("invalid window ordering or future window")
    for col in ["open_time", "earliest_available_at"]:
        f[col] = clocks(f[col], col)
    if f.open_time.duplicated().any():
        raise ValueError("duplicate source bar")
    if not (f.earliest_available_at == f.open_time + STEP).all():
        raise ValueError("earliest availability must equal complete-bar boundary")
    if availability_mode == "observed_delivery":
        if "observed_at" not in f:
            f["observed_at"] = pd.Series(pd.NaT, index=f.index, dtype="datetime64[ns, UTC]")
        f["observed_at"] = clocks(f.observed_at, "observed_at", missing=True, grid=False)
    f = f.sort_values("open_time").reset_index(drop=True)
    index = pd.DatetimeIndex(f.open_time)
    records = []
    for row in w.itertuples(index=False):
        expected = int((row.window_end - row.window_start) / STEP)
        lo, hi = index.searchsorted([row.window_start, row.window_end], side="left")
        selected = f.iloc[lo:hi]
        available_at = selected.earliest_available_at
        if availability_mode == "observed_delivery":
            available_at = available_at.where(available_at >= selected.observed_at,
                                                selected.observed_at)
            available_at = available_at.mask(selected.observed_at.isna())
        available = selected.loc[available_at.notna() & available_at.le(row.decision_time)]
        _check_amounts(available)
        missing = expected - len(selected)
        unavailable = len(selected) - len(available)
        zero = int(available.quote_volume.eq(0).sum())
        status = "missing_bars" if missing else "unavailable_bars" if unavailable else "complete"
        total = delta = imbalance = directional = float("nan")
        defined = False
        if not missing and not unavailable:
            total = math.fsum(available.quote_volume)
            delta = math.fsum(available.delta_quote_volume)
            if not math.isfinite(total) or not math.isfinite(delta):
                raise ValueError("window sum overflow")
            if total == 0:
                status = "zero_volume"
            else:
                imbalance = delta / total
                directional = row.direction * imbalance
                defined = True
        records.append(dict(expected_bars=expected, observed_bars=len(selected),
            available_bars=len(available), missing_bars=missing, unavailable_bars=unavailable,
            zero_volume_bars=zero, status=status, quote_volume_sum=total,
            delta_quote_volume_sum=delta, imbalance=imbalance,
            directional_imbalance=directional, flow_defined=defined,
            max_source_available_at=available_at.loc[available.index].max()))
    out = pd.concat([w, pd.DataFrame(records, columns=RESULT_COLUMNS)], axis=1)
    out["max_source_available_at"] = pd.to_datetime(out.max_source_available_at, utc=True)
    return out

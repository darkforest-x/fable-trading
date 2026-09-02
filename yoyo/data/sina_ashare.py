"""Parse and forward-adjust Sina A-share 60-minute source rows.

The minute source is the JSONP endpoint used by AKShare's
``stock_zh_a_minute`` implementation.  Input columns are the provider's
close-labelled ``day``, OHLC, volume and amount.  Rows are retained only when
their close label is at or before the explicit timezone-aware cutoff.  No
missing market interval is filled.

QFQ uses Sina's sparse corporate-action factor file.  For each trading date,
the most recent factor dated at or before that session is selected, then OHLC
is divided by that factor and rounded to cents, matching the direction and
rounding in AKShare's pinned daily QFQ implementation.  Volume and amount are
not altered.  Factor alignment depends only on the bar's own date or an earlier
date; it never selects a later factor row.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

SINA_HOURLY_CLOSE_SLOTS: tuple[str, ...] = (
    "10:30",
    "11:30",
    "14:00",
    "15:00",
)


class SinaAShareDataError(ValueError):
    """Raised when Sina JSONP, factor, or temporal semantics fail closed."""


def _unwrap_jsonp(text: str) -> Any:
    """Decode a JSON or ``...=(JSON);`` payload without evaluating JavaScript."""

    source = str(text).strip()
    if not source:
        raise SinaAShareDataError("empty Sina response")
    candidates = [source]
    marker = source.find("=(")
    if marker >= 0:
        end = source.rfind(");")
        if end <= marker + 2:
            raise SinaAShareDataError("malformed Sina JSONP wrapper")
        candidates.insert(0, source[marker + 2 : end])
    equal = source.find("=")
    if equal >= 0:
        raw_body = source[equal + 1 :].strip()
        first_line = raw_body.splitlines()[0].strip().rstrip(";").strip()
        if first_line:
            candidates.append(first_line)
        body = raw_body.rstrip(";").strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
        candidates.append(body)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise SinaAShareDataError("Sina response is not strict JSON/JSONP")


def parse_sina_hourly_jsonp(
    text: str,
    *,
    symbol: str,
    cutoff_close: object,
) -> pd.DataFrame:
    """Parse completed Sina 60m bars into repository open-time semantics."""

    cutoff = pd.Timestamp(cutoff_close)
    if cutoff.tzinfo is None:
        raise SinaAShareDataError("cutoff_close must be timezone-aware")
    cutoff = cutoff.tz_convert("Asia/Shanghai")
    payload = _unwrap_jsonp(text)
    rows: object = payload.get("data") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise SinaAShareDataError("Sina minute payload is not a row sequence")
    parsed: list[list[Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            close_time = pd.Timestamp(str(raw["day"]))
            values = [
                float(raw["open"]),
                float(raw["high"]),
                float(raw["low"]),
                float(raw["close"]),
                float(raw["volume"]),
                float(raw["amount"]),
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SinaAShareDataError(f"invalid Sina minute row:{symbol}") from exc
        if close_time.tzinfo is None:
            close_time = close_time.tz_localize("Asia/Shanghai")
        else:
            close_time = close_time.tz_convert("Asia/Shanghai")
        if close_time > cutoff:
            continue
        parsed.append(
            [
                close_time,
                (close_time - pd.Timedelta(hours=1)).tz_convert("UTC"),
                *values,
            ]
        )
    frame = pd.DataFrame(
        parsed,
        columns=[
            "raw_close_time",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ],
    )
    if frame.empty:
        raise SinaAShareDataError(f"no retained Sina 60m bars:{symbol}")
    frame.sort_values("raw_close_time", inplace=True, ignore_index=True)
    if frame["raw_close_time"].duplicated().any():
        raise SinaAShareDataError(f"duplicate Sina close-label:{symbol}")
    slots = set(frame["raw_close_time"].dt.strftime("%H:%M"))
    unexpected = sorted(slots - set(SINA_HOURLY_CLOSE_SLOTS))
    if unexpected:
        raise SinaAShareDataError(
            f"unexpected Sina 60m close labels {unexpected}:{symbol}"
        )
    numeric = frame[["open", "high", "low", "close", "volume", "amount"]].to_numpy(
        dtype=float
    )
    if not np.isfinite(numeric).all():
        raise SinaAShareDataError(f"non-finite Sina OHLCVA:{symbol}")
    if bool((frame[["open", "high", "low", "close"]] <= 0).any().any()):
        raise SinaAShareDataError(f"non-positive Sina OHLC:{symbol}")
    body_high = frame[["open", "close"]].max(axis=1)
    body_low = frame[["open", "close"]].min(axis=1)
    if bool((frame["high"] < body_high).any()) or bool(
        (frame["low"] > body_low).any()
    ):
        raise SinaAShareDataError(f"invalid Sina candle bounds:{symbol}")
    if bool((frame[["volume", "amount"]] < 0).any().any()):
        raise SinaAShareDataError(f"negative Sina volume/amount:{symbol}")
    frame["sina_symbol"] = symbol
    frame["adjustment"] = "none"
    return frame


def parse_sina_qfq_factor_js(text: str, *, symbol: str) -> pd.DataFrame:
    """Parse Sina's sparse QFQ factor file as strict data, never via ``eval``."""

    payload = _unwrap_jsonp(text)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise SinaAShareDataError(f"invalid Sina QFQ factor payload:{symbol}")
    parsed: list[tuple[pd.Timestamp, float]] = []
    for raw in payload["data"]:
        if isinstance(raw, Mapping):
            if "d" not in raw or "f" not in raw:
                raise SinaAShareDataError(f"invalid Sina QFQ factor row:{symbol}")
            raw_date, raw_factor = raw["d"], raw["f"]
        elif (
            isinstance(raw, Sequence)
            and not isinstance(raw, (str, bytes))
            and len(raw) >= 2
        ):
            raw_date, raw_factor = raw[0], raw[1]
        else:
            raise SinaAShareDataError(f"invalid Sina QFQ factor row:{symbol}")
        try:
            factor_date = pd.Timestamp(str(raw_date)).normalize()
            factor = float(raw_factor)
        except (TypeError, ValueError) as exc:
            raise SinaAShareDataError(f"invalid Sina QFQ factor row:{symbol}") from exc
        if not np.isfinite(factor) or factor <= 0:
            raise SinaAShareDataError(f"non-positive Sina QFQ factor:{symbol}")
        parsed.append((factor_date, factor))
    if not parsed:
        raise SinaAShareDataError(f"empty Sina QFQ factors:{symbol}")
    factors = pd.DataFrame(parsed, columns=["factor_date", "qfq_factor"])
    factors.sort_values("factor_date", inplace=True, ignore_index=True)
    factors.drop_duplicates("factor_date", keep="last", inplace=True, ignore_index=True)
    return factors


def apply_sina_qfq(
    hourly: pd.DataFrame,
    factors: pd.DataFrame,
    *,
    symbol: str,
) -> pd.DataFrame:
    """Apply date-causal Sina QFQ factors to raw hourly OHLC rows."""

    required_hourly = {
        "raw_close_time",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "sina_symbol",
    }
    if not required_hourly.issubset(hourly.columns):
        raise SinaAShareDataError(f"hourly columns missing for QFQ:{symbol}")
    if not {"factor_date", "qfq_factor"}.issubset(factors.columns):
        raise SinaAShareDataError(f"factor columns missing for QFQ:{symbol}")
    result = hourly.copy()
    closes = pd.to_datetime(result["raw_close_time"], utc=True).dt.tz_convert(
        "Asia/Shanghai"
    )
    result["factor_date"] = closes.dt.tz_localize(None).dt.normalize()
    factor_rows = factors[["factor_date", "qfq_factor"]].copy()
    factor_rows["factor_date"] = pd.to_datetime(factor_rows["factor_date"]).dt.normalize()
    factor_rows.sort_values("factor_date", inplace=True)
    result.sort_values("factor_date", inplace=True)
    result = pd.merge_asof(
        result,
        factor_rows,
        on="factor_date",
        direction="backward",
        allow_exact_matches=True,
    )
    if result["qfq_factor"].isna().any():
        first_missing = result.loc[result["qfq_factor"].isna(), "factor_date"].iloc[0]
        raise SinaAShareDataError(
            f"Sina QFQ factor does not cover {first_missing.date()}:{symbol}"
        )
    for column in ("open", "high", "low", "close"):
        result[f"raw_{column}"] = result[column].astype(float)
        result[column] = (result[column].astype(float) / result["qfq_factor"]).round(2)
    result.drop(columns=["factor_date"], inplace=True)
    result["adjustment"] = "qfq"
    result.sort_values("raw_close_time", inplace=True, ignore_index=True)
    body_high = result[["open", "close"]].max(axis=1)
    body_low = result[["open", "close"]].min(axis=1)
    if bool((result["high"] < body_high).any()) or bool(
        (result["low"] > body_low).any()
    ):
        raise SinaAShareDataError(f"QFQ rounding invalidated candle bounds:{symbol}")
    return result

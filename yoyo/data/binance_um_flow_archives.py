"""Pure parser for checksum-verified Binance USD-M kline flow columns.

Sources (USD-M, not COIN-M, OKX, open interest, or order-book imbalance):
https://github.com/binance/binance-public-data#futures
https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/market-data/rest-api/Kline-Candlestick-Data

Uses only each supplied bar's open/close timestamps, OHLC, total base/quote
volume, trade count, and taker-buy base/quote volume. There is no rolling
window, network/file IO, gap filling, resampling, fitting, or economic rule.
Sell volume = total - buy; signed delta = buy - sell, independently for base
and quote units. This is executed taker flow, not candle-direction-signed
whole-bar volume: a falling candle can have positive taker delta.

The existing parse_month_zip must first validate checksum, ZIP membership,
OHLCV, epoch unit, month, and close boundary. Its seven OHLCV output columns
are preserved unchanged. Decimal comparisons precede float64 conversion;
invalid values are rejected, never clipped. trade_count is an int64. Decimal
subtractions are performed with enough precision to retain the input digits.

close_time preserves the source inclusive close normalized to milliseconds
by the existing parser: open_time + interval - 1 ms. It is NOT an availability
clock. earliest_available_at = open_time + interval is only the theoretical
earliest complete-bar boundary, NOT actual exchange/network/archive delivery.
Monthly archives are retrospective; use in a live pipeline requires a separate
observed delivery clock. No approval to read real prices is granted here.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import numpy as np
import pandas as pd

from yoyo.data.binance_um_archives import (
    KLINE_COLUMNS,
    BinanceArchiveError,
    interval_delta,
    parse_month_zip,
)


SOURCE_EXCHANGE = "binance_usdm"
FLOW_COLUMNS = (
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "taker_sell_base_volume",
    "taker_sell_quote_volume",
    "delta_base_volume",
    "delta_quote_volume",
)
_HEADER_ALIASES = {"count": "trade_count", "taker_buy_volume": "taker_buy_base_volume"}
_INT64_MAX = 2**63 - 1


def _decimal(token: str, *, field: str, row: int) -> Decimal:
    try:
        value = Decimal(token.strip())
    except (InvalidOperation, ValueError) as exc:
        raise BinanceArchiveError(f"invalid {field} at flow row {row}") from exc
    if not value.is_finite() or value < 0:
        raise BinanceArchiveError(f"non-finite or negative {field} at flow row {row}")
    # Canonical zero prevents an arbitrary textual zero exponent from inflating
    # the exact-subtraction precision budget; it does not alter any quantity.
    return Decimal(0) if value == 0 else value


def _float64(value: Decimal, *, field: str, row: int) -> float:
    converted = float(value)
    if not math.isfinite(converted) or (value != 0 and converted == 0):
        raise BinanceArchiveError(f"float64 overflow/underflow in {field} at flow row {row}")
    return converted


def parse_flow_month_zip(
    payload: bytes,
    *,
    symbol: str,
    month: str,
    expected_sha256: str,
    interval: str = "5m",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Preserve validated OHLCV and add same-bar taker-flow fields and clocks.

    Accept an unheaded 12-column archive or explicitly recognized headers in
    that same order. ``count`` and ``taker_buy_volume`` are accepted spelling
    aliases, not additional fields. Positive trading requires positive base
    and quote totals and an integral positive count; zero trading requires all
    four volume inputs and count to be zero. Counts must fit signed int64.
    """

    output, audit = parse_month_zip(
        payload,
        symbol=symbol,
        month=month,
        expected_sha256=expected_sha256,
        interval=interval,
    )
    duration = interval_delta(interval)
    if duration <= pd.Timedelta(0):
        raise BinanceArchiveError("flow interval must be positive")
    # The base parser has already checked the digest and exact CSV member.
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        csv_payload = archive.read(f"{symbol}-{interval}-{month}.csv")
    try:
        rows = list(csv.reader(io.StringIO(csv_payload.decode("utf-8")), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BinanceArchiveError("invalid flow CSV text") from exc
    if rows and rows[0] and rows[0][0].strip().lower() in {"open_time", "open time"}:
        header = tuple(
            _HEADER_ALIASES.get(value.strip().lower().replace(" ", "_"), value.strip().lower().replace(" ", "_"))
            for value in rows.pop(0)
        )
        if header != KLINE_COLUMNS:
            raise BinanceArchiveError("flow header column drift")
    if len(rows) != len(output) or not rows:
        raise BinanceArchiveError("flow row count differs from validated OHLCV")

    values: dict[str, list[Any]] = {column: [] for column in FLOW_COLUMNS}
    close_milliseconds: list[int] = []
    divisor = 1000 if audit["epoch_unit"] == "microseconds" else 1
    for index, row in enumerate(rows):
        if len(row) != len(KLINE_COLUMNS):
            raise BinanceArchiveError(f"flow column drift at row {index}")
        fields = dict(zip(KLINE_COLUMNS, row))
        # Exact integer checks also reject fractional epochs silently truncated
        # by a numeric conversion; the inherited epoch normalization is retained.
        for clock in ("open_time", "close_time"):
            stamp = _decimal(fields[clock], field=clock, row=index)
            if stamp != stamp.to_integral_value() or stamp > _INT64_MAX:
                raise BinanceArchiveError(f"non-int64 {clock} at flow row {index}")
            normalized = int(stamp) // divisor
            if clock == "open_time" and (
                int(stamp) % divisor != 0
                or normalized % int(duration.value // 1_000_000) != 0
            ):
                raise BinanceArchiveError(f"unaligned open_time at flow row {index}")
            expected = int(output.iloc[index]["ts"])
            if clock == "close_time":
                expected += int(duration.value // 1_000_000) - 1
                close_milliseconds.append(normalized)
            if normalized != expected:
                raise BinanceArchiveError(f"flow {clock} differs from validated OHLCV")

        amounts = {
            key: _decimal(fields[key], field=key, row=index)
            for key in ("volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume")
        }
        total_base, total_quote = amounts["volume"], amounts["quote_volume"]
        buy_base, buy_quote = amounts["taker_buy_base_volume"], amounts["taker_buy_quote_volume"]
        if buy_base > total_base or buy_quote > total_quote:
            raise BinanceArchiveError(f"taker buy exceeds total at flow row {index}")
        if (buy_base == 0) != (buy_quote == 0):
            raise BinanceArchiveError(f"inconsistent zero taker-buy base/quote at flow row {index}")
        if (buy_base == total_base) != (buy_quote == total_quote):
            raise BinanceArchiveError(f"inconsistent zero taker-sell base/quote at flow row {index}")
        count = _decimal(fields["trade_count"], field="trade_count", row=index)
        if count != count.to_integral_value() or count > _INT64_MAX:
            raise BinanceArchiveError(f"non-int64 trade_count at flow row {index}")
        if total_base == 0:
            if total_quote != 0 or buy_base != 0 or buy_quote != 0 or count != 0:
                raise BinanceArchiveError(f"inconsistent zero-volume flow row {index}")
        elif total_quote <= 0 or count <= 0:
            raise BinanceArchiveError(f"positive volume needs quote volume and trades at flow row {index}")
        # Check representability before arithmetic. Float equality is never used
        # to decide whether buy <= total, so sub-ULP violations are still errors.
        for key, value in amounts.items():
            _float64(value, field=key, row=index)
        with localcontext() as context:
            context.prec = max(value.adjusted() for value in amounts.values()) - min(
                value.as_tuple().exponent for value in amounts.values()
            ) + 3
            sell_base, sell_quote = total_base - buy_base, total_quote - buy_quote
            derived = {
                "quote_volume": total_quote,
                "taker_buy_base_volume": buy_base,
                "taker_buy_quote_volume": buy_quote,
                "taker_sell_base_volume": sell_base,
                "taker_sell_quote_volume": sell_quote,
                "delta_base_volume": buy_base - sell_base,
                "delta_quote_volume": buy_quote - sell_quote,
            }
        for key, value in derived.items():
            values[key].append(_float64(value, field=key, row=index))
        values["trade_count"].append(int(count))

    for column in FLOW_COLUMNS:
        output[column] = pd.Series(values[column], dtype="int64" if column == "trade_count" else "float64")
    if not np.isfinite(output[list(FLOW_COLUMNS)].to_numpy(dtype=float)).all():
        raise BinanceArchiveError("non-finite output flow dtype conversion")
    output["close_time"] = pd.to_datetime(close_milliseconds, unit="ms", utc=True)
    output["earliest_available_at"] = output["open_time"] + duration
    output["source_exchange"] = SOURCE_EXCHANGE
    output["symbol"] = symbol
    audit = {
        **audit,
        "source_exchange": SOURCE_EXCHANGE,
        "interval": interval,
        "flow_schema": "binance_usdm_kline_taker_flow_v1",
        "flow_columns": list(FLOW_COLUMNS),
        "availability_semantics": "theoretical_complete_bar_boundary_not_observed_delivery",
    }
    return output, audit

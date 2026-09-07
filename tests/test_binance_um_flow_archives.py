"""Synthetic ZIP contract tests; no market data, files, or network are read."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pandas as pd
import pytest

from yoyo.data.binance_um_archives import KLINE_COLUMNS, BinanceArchiveError, interval_delta, parse_month_zip
from yoyo.data.binance_um_flow_archives import FLOW_COLUMNS, SOURCE_EXCHANGE, parse_flow_month_zip


def _row(*, interval: str = "5m", offset: int = 0, microseconds: bool = False) -> list[str]:
    start = int(pd.Timestamp("2024-01-01T00:00:00Z").value // 1_000_000)
    span = int(interval_delta(interval).value // 1_000_000)
    opened = start + offset * span
    closed = opened + span - 1
    if microseconds:
        opened, closed = opened * 1000, closed * 1000 + 999
    return [str(opened), "101", "102", "99", "100", "10", str(closed), "1000", "7", "7", "700", "0"]


def _zip(rows: list[list[str]], *, interval: str = "5m", member: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member or f"BTCUSDT-{interval}-2024-01.csv", "\n".join(",".join(row) for row in rows) + "\n")
    return buffer.getvalue()


def _parse(rows: list[list[str]], *, interval: str = "5m"):
    payload = _zip(rows, interval=interval)
    return parse_flow_month_zip(payload, symbol="BTCUSDT", month="2024-01", expected_sha256=hashlib.sha256(payload).hexdigest(), interval=interval)


@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1h", "4h", "1d"])
@pytest.mark.parametrize("microseconds", [False, True])
def test_ohlcv_parity_flow_and_distinct_clocks(interval, microseconds):
    rows = [_row(interval=interval, microseconds=microseconds), _row(interval=interval, offset=1, microseconds=microseconds)]
    payload = _zip(rows, interval=interval)
    original = bytes(payload)
    arguments = dict(symbol="BTCUSDT", month="2024-01", expected_sha256=hashlib.sha256(payload).hexdigest(), interval=interval)
    base, base_audit = parse_month_zip(payload, **arguments)
    flow, audit = parse_flow_month_zip(payload, **arguments)
    pd.testing.assert_frame_equal(flow[list(base.columns)], base, check_exact=True)
    assert payload == original
    assert all(audit[key] == value for key, value in base_audit.items())
    assert flow["source_exchange"].tolist() == ["binance_usdm"] * 2
    assert SOURCE_EXCHANGE == audit["source_exchange"]
    assert flow["symbol"].tolist() == ["BTCUSDT"] * 2
    assert (flow["earliest_available_at"] == flow["open_time"] + interval_delta(interval)).all()
    assert ((flow["earliest_available_at"] - flow["close_time"]) == pd.Timedelta(milliseconds=1)).all()
    assert str(flow["earliest_available_at"].dtype) == "datetime64[ns, UTC]"
    assert str(flow["close_time"].dtype) == "datetime64[ns, UTC]"
    assert str(flow["trade_count"].dtype) == "int64"
    assert all(str(flow[key].dtype) == "float64" for key in FLOW_COLUMNS if key != "trade_count")
    assert flow.iloc[0]["taker_sell_base_volume"] == 3
    assert flow.iloc[0]["taker_sell_quote_volume"] == 300
    assert flow.iloc[0]["delta_base_volume"] == 4
    assert flow.iloc[0]["delta_quote_volume"] == 400
    # This falling candle has positive aggressive-buy delta, whereas the old
    # candle-colour proxy would assign the whole 10 units to negative flow.
    assert flow.iloc[0]["close"] < flow.iloc[0]["open"]
    assert flow.iloc[0]["delta_base_volume"] > 0


@pytest.mark.parametrize("aliases", [False, True])
def test_recognized_header(aliases):
    header = list(KLINE_COLUMNS)
    if aliases:
        header[8:10] = ["count", "taker_buy_volume"]
    flow, _ = _parse([header, _row()])
    assert len(flow) == 1


def test_header_drift_is_not_silently_positional():
    header = list(KLINE_COLUMNS)
    header[9], header[10] = header[10], header[9]
    with pytest.raises(BinanceArchiveError, match="header column drift"):
        _parse([header, _row()])


def test_zero_trades_and_zero_volumes_are_valid():
    row = _row()
    for index in (5, 7, 8, 9, 10):
        row[index] = "0"
    flow, _ = _parse([row])
    assert flow[list(FLOW_COLUMNS)].eq(0).all().all()


@pytest.mark.parametrize("buy_base,buy_quote", [("0", "1"), ("1", "0"), ("10", "999"), ("9", "1000")])
def test_base_and_quote_zero_participation_must_agree(buy_base, buy_quote):
    row = _row()
    row[9], row[10] = buy_base, buy_quote
    with pytest.raises(BinanceArchiveError, match="inconsistent zero taker"):
        _parse([row])


@pytest.mark.parametrize("buy_base,buy_quote,delta", [("0", "0", -10), ("10", "1000", 10)])
def test_all_sell_or_all_buy_is_valid(buy_base, buy_quote, delta):
    row = _row()
    row[9], row[10] = buy_base, buy_quote
    frame, _ = _parse([row])
    assert frame["delta_base_volume"].iloc[0] == delta
    assert frame["delta_quote_volume"].iloc[0] == delta * 100


def test_same_candle_with_reversed_flow_changes_delta_not_ohlcv():
    buying, selling = _row(), _row()
    selling[9], selling[10] = "3", "300"
    buy_frame, _ = _parse([buying])
    sell_frame, _ = _parse([selling])
    ohlcv_columns = ["ts", "open", "high", "low", "close", "volume", "open_time"]
    pd.testing.assert_frame_equal(buy_frame[ohlcv_columns], sell_frame[ohlcv_columns], check_exact=True)
    assert buy_frame["delta_base_volume"].iloc[0] == 4
    assert sell_frame["delta_base_volume"].iloc[0] == -4
    assert buy_frame["delta_quote_volume"].iloc[0] == 400
    assert sell_frame["delta_quote_volume"].iloc[0] == -400


@pytest.mark.parametrize("field,value", [
    (7, "-1"), (9, "-1"), (10, "-1"), (8, "-1"),
    (7, "NaN"), (9, "NaN"), (10, "NaN"), (8, "NaN"),
    (7, "Infinity"), (9, "Infinity"), (10, "-Infinity"), (8, "Infinity"),
    (7, ""), (9, ""), (10, ""), (8, ""),
    (7, "true"), (9, "false"), (10, "false"), (8, "true"),
    (8, "1.5"), (8, str(2**63)), (9, "10.000000000000000000000000000000001"),
    (10, "1000.000000000000000000000000000000001"),
    (7, "1e309"), (9, "1e-999"),
    (7, "0"), (8, "0"),
])
def test_invalid_flow_fields_fail_closed(field, value):
    row = _row()
    row[field] = value
    with pytest.raises(BinanceArchiveError):
        _parse([row])


@pytest.mark.parametrize("field", [7, 8, 9, 10])
def test_zero_base_rejects_any_nonzero_flow_or_count(field):
    row = _row()
    for index in (5, 7, 8, 9, 10):
        row[index] = "0"
    row[field] = "1"
    with pytest.raises(BinanceArchiveError):
        _parse([row])


@pytest.mark.parametrize("count", ["7.0", "7e0", str(2**63 - 1)])
def test_exact_integral_counts_are_not_float_rounded(count):
    row = _row()
    row[8] = count
    frame, _ = _parse([row])
    assert int(frame["trade_count"].iloc[0]) == (2**63 - 1 if count == str(2**63 - 1) else 7)


def test_decimal_subtraction_preserves_tiny_remainder():
    row = _row()
    row[9] = "9.999999999999999999999999999999999"
    flow, _ = _parse([row])
    assert flow.iloc[0]["taker_sell_base_volume"] == 1e-33
    assert flow.iloc[0]["taker_buy_base_volume"] == 10.0


@pytest.mark.parametrize("header", [False, True])
def test_missing_or_extra_columns_rejected(header):
    rows = [list(KLINE_COLUMNS)] if header else []
    with pytest.raises((BinanceArchiveError, ValueError)):
        _parse(rows + [_row()[:-2]])
    with pytest.raises((BinanceArchiveError, ValueError, pd.errors.ParserError)):
        _parse(rows + [_row() + ["extra"]])


def test_bad_checksum_and_member_are_inherited_before_flow():
    row = _row()
    row[9] = "not a number"
    payload = _zip([row])
    with pytest.raises(BinanceArchiveError, match="checksum mismatch"):
        parse_flow_month_zip(payload, symbol="BTCUSDT", month="2024-01", expected_sha256="0" * 64)
    payload = _zip([row], member="ETHUSDT-5m-2024-01.csv")
    with pytest.raises(BinanceArchiveError, match="member drift"):
        parse_flow_month_zip(payload, symbol="BTCUSDT", month="2024-01", expected_sha256=hashlib.sha256(payload).hexdigest())


@pytest.mark.parametrize("change", ["geometry", "duplicate", "close_boundary", "outside_month", "fractional_clock"])
def test_existing_ohlcv_and_clock_guards(change):
    row = _row()
    rows = [row]
    if change == "geometry":
        row[2] = "99"
    elif change == "duplicate":
        rows.append(list(row))
    elif change == "close_boundary":
        row[6] = str(int(row[6]) + 1)
    elif change == "outside_month":
        row[0] = str(int(row[0]) - 300_000)
        row[6] = str(int(row[6]) - 300_000)
    else:
        row[0] += ".1"
    with pytest.raises((BinanceArchiveError, ValueError)):
        _parse(rows)


def test_gaps_are_reported_not_filled_or_resampled():
    frame, audit = _parse([_row(), _row(offset=2)])
    assert len(frame) == 2
    assert audit["non_bar_gaps"] == 1
    assert frame["open_time"].diff().iloc[1] == pd.Timedelta(minutes=10)


def test_correct_duration_with_off_grid_open_is_rejected():
    row = _row()
    row[0], row[6] = str(int(row[0]) + 60_000), str(int(row[6]) + 60_000)
    with pytest.raises(BinanceArchiveError, match="unaligned open_time"):
        _parse([row])


def test_microsecond_open_cannot_hide_sub_millisecond_offset():
    row = _row(microseconds=True)
    row[0] = str(int(row[0]) + 1)
    with pytest.raises(BinanceArchiveError, match="unaligned open_time"):
        _parse([row])

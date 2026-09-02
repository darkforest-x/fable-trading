import json

import pandas as pd
import pytest

from yoyo.data.sina_ashare import (
    SinaAShareDataError,
    apply_sina_qfq,
    parse_sina_hourly_jsonp,
    parse_sina_qfq_factor_js,
)


def _minute_text(rows):
    return "foo=(" + json.dumps(rows) + ");"


def test_hourly_jsonp_uses_close_labels_and_frozen_cutoff():
    rows = [
        {
            "day": "2026-09-02 10:30:00",
            "open": "10.0",
            "high": "10.4",
            "low": "9.9",
            "close": "10.2",
            "volume": "100",
            "amount": "1000",
        },
        {
            "day": "2026-09-02 15:00:00",
            "open": "10.2",
            "high": "10.5",
            "low": "10.1",
            "close": "10.4",
            "volume": "200",
            "amount": "2000",
        },
    ]
    frame = parse_sina_hourly_jsonp(
        _minute_text(rows),
        symbol="sh600000",
        cutoff_close="2026-09-02T14:00:00+08:00",
    )
    assert len(frame) == 1
    assert frame.iloc[0]["raw_close_time"] == pd.Timestamp(
        "2026-09-02T10:30:00+08:00"
    )
    assert frame.iloc[0]["open_time"] == pd.Timestamp(
        "2026-09-02T09:30:00+08:00"
    ).tz_convert("UTC")


def test_hourly_jsonp_rejects_a_wall_clock_bucket_that_crosses_lunch():
    rows = [
        {
            "day": "2026-09-02 12:30:00",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 1,
            "amount": 1,
        }
    ]
    with pytest.raises(SinaAShareDataError, match="unexpected Sina 60m"):
        parse_sina_hourly_jsonp(
            _minute_text(rows),
            symbol="sh600000",
            cutoff_close="2026-09-02T15:00:00+08:00",
        )


def test_qfq_factor_parser_never_needs_javascript_eval():
    text = 'var KKE_sh600000qfq={"data":[["2026-01-01","2"],["2026-06-01","4"]]};'
    factors = parse_sina_qfq_factor_js(text, symbol="sh600000")
    assert factors["qfq_factor"].tolist() == [2.0, 4.0]


def test_qfq_factor_parser_accepts_observed_mapping_rows_and_trailing_comment():
    text = (
        'var sh600000qfq={"total":2,"data":'
        '[{"d":"2026-07-16","f":"1.0"},'
        '{"d":"2025-07-16","f":"1.0472440944882"}]}\n'
        "/* opaque provider comment */"
    )
    factors = parse_sina_qfq_factor_js(text, symbol="sh600000")
    assert factors["factor_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-07-16",
        "2026-07-16",
    ]
    assert factors["qfq_factor"].tolist() == [1.0472440944882, 1.0]


def test_qfq_factor_alignment_uses_same_or_earlier_date_only():
    closes = pd.to_datetime(
        ["2026-05-29 15:00", "2026-06-01 15:00"],
    ).tz_localize("Asia/Shanghai")
    hourly = pd.DataFrame(
        {
            "raw_close_time": closes,
            "open_time": (closes - pd.Timedelta(hours=1)).tz_convert("UTC"),
            "open": [20.0, 40.0],
            "high": [22.0, 44.0],
            "low": [18.0, 36.0],
            "close": [21.0, 42.0],
            "volume": [100.0, 200.0],
            "amount": [1000.0, 2000.0],
            "sina_symbol": ["sh600000", "sh600000"],
            "adjustment": ["none", "none"],
        }
    )
    factors = pd.DataFrame(
        {
            "factor_date": pd.to_datetime(["2026-01-01", "2026-06-01"]),
            "qfq_factor": [2.0, 4.0],
        }
    )
    adjusted = apply_sina_qfq(hourly, factors, symbol="sh600000")
    assert adjusted["open"].tolist() == [10.0, 10.0]
    assert adjusted["close"].tolist() == [10.5, 10.5]
    assert adjusted["qfq_factor"].tolist() == [2.0, 4.0]
    assert adjusted["raw_open"].tolist() == [20.0, 40.0]
    assert adjusted["volume"].tolist() == [100.0, 200.0]


def test_qfq_rejects_bars_earlier_than_first_known_factor():
    close = pd.DatetimeIndex([pd.Timestamp("2025-12-31T15:00:00+08:00")])
    hourly = pd.DataFrame(
        {
            "raw_close_time": close,
            "open_time": (close - pd.Timedelta(hours=1)).tz_convert("UTC"),
            "open": [20.0],
            "high": [22.0],
            "low": [18.0],
            "close": [21.0],
            "volume": [100.0],
            "amount": [1000.0],
            "sina_symbol": ["sh600000"],
            "adjustment": ["none"],
        }
    )
    factors = pd.DataFrame(
        {
            "factor_date": pd.to_datetime(["2026-01-01"]),
            "qfq_factor": [2.0],
        }
    )
    with pytest.raises(SinaAShareDataError, match="does not cover"):
        apply_sina_qfq(hourly, factors, symbol="sh600000")

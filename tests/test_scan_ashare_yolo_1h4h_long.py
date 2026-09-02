import json

import pandas as pd
import pytest

from scripts.scan_ashare_yolo_1h4h_long import (
    FOUR_HOUR_CUTOFF_CST,
    HOURLY_CLOSE_SLOTS,
    ONE_HOUR_CUTOFF_CST,
    AShareMultiTimeframeError,
    _parse_hourly_payload,
    aggregate_session_four_hour,
    load_standard_retail_universe,
    select_delivery_events,
    select_scan_endpoints,
    validate_four_hour_schedule,
    validate_one_hour_schedule,
    PREREG,
)


def _hourly_frame(dates: pd.DatetimeIndex, *, include_partial: bool = True) -> pd.DataFrame:
    rows = []
    value = 10.0
    for day in dates:
        for slot in HOURLY_CLOSE_SLOTS:
            close_time = pd.Timestamp(f"{day.date()} {slot}", tz="Asia/Shanghai")
            rows.append(
                {
                    "raw_close_time": close_time,
                    "open_time": (close_time - pd.Timedelta(hours=1)).tz_convert("UTC"),
                    "open": value,
                    "high": value + 0.4,
                    "low": value - 0.3,
                    "close": value + 0.1,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "secid": "1.600000",
                    "adjustment": "qfq",
                }
            )
            value += 0.01
    if include_partial:
        for slot in HOURLY_CLOSE_SLOTS[:3]:
            close_time = pd.Timestamp(f"2026-09-02 {slot}", tz="Asia/Shanghai")
            rows.append(
                {
                    "raw_close_time": close_time,
                    "open_time": (close_time - pd.Timedelta(hours=1)).tz_convert("UTC"),
                    "open": value,
                    "high": value + 0.4,
                    "low": value - 0.3,
                    "close": value + 0.1,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "secid": "1.600000",
                    "adjustment": "qfq",
                }
            )
            value += 0.01
    return pd.DataFrame(rows)


def test_hourly_parser_drops_the_not_yet_completed_1500_bar():
    payload = {
        "data": {
            "klines": [
                "2026-09-02 10:30,10,10.1,10.3,9.9,100,1000",
                "2026-09-02 11:30,10.1,10.2,10.4,10,100,1000",
                "2026-09-02 14:00,10.2,10.3,10.5,10.1,100,1000",
                "2026-09-02 15:00,10.3,10.4,10.6,10.2,100,1000",
            ]
        }
    }
    frame = _parse_hourly_payload(payload, secid="1.600000", adjustment="qfq")
    assert frame["raw_close_time"].tolist()[-1] == ONE_HOUR_CUTOFF_CST
    assert frame["raw_close_time"].dt.strftime("%H:%M").tolist() == [
        "10:30",
        "11:30",
        "14:00",
    ]
    assert frame.iloc[-1]["open_time"] == pd.Timestamp(
        "2026-09-02T13:00:00+08:00"
    ).tz_convert("UTC")


def test_hourly_parser_fails_closed_on_an_unknown_close_slot():
    payload = {
        "data": {
            "klines": ["2026-09-01 12:30,10,10.1,10.3,9.9,100,1000"]
        }
    }
    with pytest.raises(AShareMultiTimeframeError, match="unexpected 60m"):
        _parse_hourly_payload(payload, secid="1.600000", adjustment="qfq")


def test_session_four_hour_uses_exact_four_slots_and_never_bridges_partial_today():
    frame = _hourly_frame(pd.DatetimeIndex([pd.Timestamp("2026-09-01")]))
    result = aggregate_session_four_hour(frame)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["raw_close_time"] == FOUR_HOUR_CUTOFF_CST
    assert pd.Timestamp(row["open_time"]).tz_convert("Asia/Shanghai") == pd.Timestamp(
        "2026-09-01T09:30:00+08:00"
    )
    assert row["open"] == frame.iloc[0]["open"]
    assert row["close"] == frame.iloc[3]["close"]
    assert row["high"] == frame.iloc[:4]["high"].max()
    assert row["low"] == frame.iloc[:4]["low"].min()
    assert row["volume"] == 400.0
    assert row["amount"] == 4000.0
    assert row["source_rows"] == 4


def test_both_schedule_gates_accept_only_exact_reference_histories():
    dates = pd.bdate_range(end="2026-09-01", periods=160)
    frame = _hourly_frame(dates)
    validate_one_hour_schedule(frame, frame, secid="1.600000")
    four_hour = aggregate_session_four_hour(frame)
    validate_four_hour_schedule(four_hour, four_hour, secid="1.600000")
    shifted = frame.drop(index=frame.index[-8]).reset_index(drop=True)
    with pytest.raises(AShareMultiTimeframeError, match="1h_schedule_mismatch"):
        validate_one_hour_schedule(shifted, frame, secid="1.600000")


def test_endpoint_contract_is_one_latest_hour_and_five_complete_sessions():
    frame = pd.DataFrame({"close": range(200)})
    assert select_scan_endpoints(frame, "1h") == [199]
    assert select_scan_endpoints(frame, "4h") == [195, 196, 197, 198, 199]


def test_delivery_keeps_only_long_and_requires_a_venue_qualified_search_key():
    rows = [
        {
            "direction": "LONG",
            "board": "SH_MAIN",
            "name": "浦发银行",
            "market": 1,
            "code": "600000",
            "search_key": "SH600000",
            "timeframe": "1h",
        },
        {
            "direction": "SHORT",
            "board": "SZ_MAIN",
            "name": "平安银行",
            "market": 0,
            "code": "000001",
            "search_key": "SZ000001",
            "timeframe": "4h",
        },
    ]
    delivered = select_delivery_events(rows)
    assert [row["search_key"] for row in delivered] == ["SH600000"]
    assert delivered[0]["event_id"].endswith("SH600000")


def test_frozen_standard_retail_universe_excludes_permission_and_name_restrictions():
    universe = load_standard_retail_universe()
    assert len(universe) == 3111
    assert universe["board"].value_counts().to_dict() == {
        "SH_MAIN": 1666,
        "SZ_MAIN": 1445,
    }
    assert not universe["name"].str.upper().str.replace(" ", "").str.startswith(
        ("PT", "ST", "*ST")
    ).any()
    assert not universe["name"].str.contains("退").any()


def test_preregistration_freezes_two_separate_holdout_consumptions():
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    configs = payload["configuration_consumptions"]
    assert [row["holdout_consumption_number_for_checkpoint"] for row in configs] == [
        9,
        10,
    ]
    assert [row["latest_endpoints_per_symbol"] for row in configs] == [1, 5]
    assert payload["delivery_contract"]["direction"] == "LONG only"
    assert payload["safety"]["telegram_send"] is False

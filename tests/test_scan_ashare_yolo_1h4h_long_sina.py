import json
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import scan_ashare_yolo_1h4h_long_sina as scanner
from scripts.scan_ashare_yolo_1h4h_long_sina import (
    CUTOFF_CST,
    PREREG,
    AShareSinaScanError,
    _authorized_cached_eastmoney_overlap,
    _endpoints,
    _parity_stats,
    _parse_eastmoney_overlap,
    verify_frozen_contract,
)


def _parity_frame(values: list[float]) -> pd.DataFrame:
    closes = pd.date_range(
        "2026-08-01 10:30",
        periods=len(values),
        freq="h",
        tz="Asia/Shanghai",
    )
    return pd.DataFrame(
        {
            "raw_close_time": closes,
            "open": values,
            "high": values,
            "low": values,
            "close": values,
        }
    )


def test_preregistration_freezes_authorized_replacement_consumptions():
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    configurations = payload["configuration_consumptions"]
    assert [
        row["holdout_consumption_number_for_checkpoint"]
        for row in configurations
    ] == [11, 12]
    assert [row["latest_endpoints_per_symbol"] for row in configurations] == [1, 5]
    assert {row["cutoff_close_cst"] for row in configurations} == {
        CUTOFF_CST.isoformat()
    }
    assert payload["source_contract"]["minute_bars"]["params"]["datalen"] == "1970"
    assert payload["delivery_contract"]["direction"] == "LONG only"
    assert payload["safety"]["telegram_send"] is False
    assert payload["safety"]["order_action"] is False


def test_eastmoney_overlap_parser_uses_exact_completed_close_labels():
    payload = {
        "data": {
            "klines": [
                "2026-09-02 10:30,10,10.1,10.3,9.9,100,1000",
                "2026-09-02 15:00,10.1,10.2,10.4,10.0,200,2000",
                "2026-09-03 10:30,10.2,10.3,10.5,10.1,300,3000",
            ]
        }
    }
    frame = _parse_eastmoney_overlap(
        payload, secid="1.600000", adjustment="qfq"
    )
    assert frame["raw_close_time"].tolist() == [
        pd.Timestamp("2026-09-02T10:30:00+08:00"),
        CUTOFF_CST,
    ]
    assert frame.iloc[-1]["open_time"] == pd.Timestamp(
        "2026-09-02T14:00:00+08:00"
    ).tz_convert("UTC")


def test_source_parity_gate_reports_and_fails_under_frozen_limits():
    left = _parity_frame([100.0, 101.0, 102.0])
    right = _parity_frame([100.0, 101.0, 102.0])
    result = _parity_stats(
        left,
        right,
        columns=("open", "high", "low", "close"),
        minimum_shared=3,
        median_max=0.001,
        p99_max=0.005,
        label="synthetic",
    )
    assert result["passed"] is True
    assert result["shared_rows"] == 3

    bad = right.copy()
    bad.loc[2, ["open", "high", "low", "close"]] = 50.0
    with pytest.raises(AShareSinaScanError, match="parity failed"):
        _parity_stats(
            left,
            bad,
            columns=("open", "high", "low", "close"),
            minimum_shared=3,
            median_max=0.001,
            p99_max=0.005,
            label="synthetic",
        )


def test_endpoint_contract_is_latest_1h_and_recent_five_sessions():
    frame = pd.DataFrame({"close": range(200)})
    assert _endpoints(frame, "1h") == [199]
    assert _endpoints(frame, "4h") == [195, 196, 197, 198, 199]


def test_eastmoney_transport_recovery_preserves_same_endpoint(monkeypatch):
    payload = {
        "data": {
            "klines": [
                "2026-09-02 10:30,10,10.1,10.3,9.9,100,1000",
                "2026-09-02 15:00,10.1,10.2,10.4,10.0,200,2000",
            ]
        }
    }
    commands = []

    def fail_primary(*args, **kwargs):
        raise ConnectionError("fake-IP route closed")

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "dig":
            return SimpleNamespace(
                returncode=0,
                stdout="push2hisipv6.trafficmanager.cn.\n2001:4860:4860::8888\n",
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(scanner.base, "request_json", fail_primary)
    monkeypatch.setattr(scanner.subprocess, "run", fake_run)
    frame = scanner.fetch_eastmoney_overlap(
        "1.600000", fqt="1", adjustment="qfq"
    )
    transport = frame.attrs["transport_receipt"]
    assert transport["mode"] == "public_ipv6_same_https_endpoint"
    assert transport["hostname"] == "push2his.eastmoney.com"
    assert "fake-IP route closed" in transport["primary_transport_error"]
    curl_command = next(command for command in commands if command[0] == "curl")
    assert scanner.base.KLINE_URL in curl_command
    assert "push2his.eastmoney.com:443:[2001:4860:4860::8888]" in curl_command
    assert "fqt=1" in curl_command
    assert "secid=1.600000" in curl_command


def test_owner_authorized_cached_parity_inputs_are_hash_pinned_and_long_enough():
    reference = _authorized_cached_eastmoney_overlap(
        "1.000001", fqt="0", adjustment="none"
    )
    shanghai = _authorized_cached_eastmoney_overlap(
        "1.600000", fqt="1", adjustment="qfq"
    )
    shenzhen = _authorized_cached_eastmoney_overlap(
        "0.000001", fqt="1", adjustment="qfq"
    )
    assert len(reference) == 127
    assert len(shanghai) == len(shenzhen) == 126
    assert set(shanghai["raw_close_time"].dt.strftime("%H:%M")) == {
        "10:30",
        "11:30",
        "14:00",
        "15:00",
    }
    assert set(shenzhen["raw_close_time"].dt.strftime("%H:%M")) == {
        "10:30",
        "11:30",
        "14:00",
        "15:00",
    }
    assert reference.attrs["transport_receipt"]["derivation"] == (
        "direct_frozen_60m_bytes"
    )
    assert shanghai.attrs["transport_receipt"]["derivation"] == (
        "exact_four_by_15m_to_60m"
    )


def test_all_frozen_local_inputs_match_preregistration_without_network():
    prereg, gates = verify_frozen_contract()
    assert prereg["experiment_id"].endswith("sina-20260902-v2")
    assert gates

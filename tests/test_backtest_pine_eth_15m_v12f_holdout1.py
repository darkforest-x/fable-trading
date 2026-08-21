"""The owner-approved V12F holdout runner must stay frozen and bounded."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import scripts.backtest_pine_eth_15m_v12f_holdout1 as holdout_runner
from scripts.backtest_pine_eth_15m_v12f_holdout1 import (
    BAR_DURATION,
    CONTROL_ASSIGNMENT_SEEDS,
    FROZEN_CONFIG_SHA256,
    HOLDOUT_START,
    MIN_WARMUP_BARS,
    OWNER_APPROVAL,
    PERIODS,
    REQUESTED_END,
    REQUESTED_START,
    _canonical_sha256,
    _create_ledger_exclusive,
    _fetch_approved_tail_memory,
    _read_local_prefix_before,
    approved_arms,
    frozen_config_contract,
    load_approved_bounded_frame,
    materialize_period_v12f_signals,
)


def _continuous_fixture(end_inclusive: pd.Timestamp) -> pd.DataFrame:
    times = pd.date_range(
        REQUESTED_START - MIN_WARMUP_BARS * BAR_DURATION,
        end_inclusive,
        freq="15min",
    )
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0] * len(times),
            "high": [101.0] * len(times),
            "low": [99.0] * len(times),
            "close": [100.0] * len(times),
            "volume": [1.0] * len(times),
        }
    )


def test_approval_and_config_identity_are_exact() -> None:
    assert OWNER_APPROVAL["owner_reply"] == "批准"
    assert OWNER_APPROVAL["consumption_number"] == 1
    assert REQUESTED_START.isoformat() == "2026-02-21T00:00:00+00:00"
    assert HOLDOUT_START.isoformat() == "2026-05-04T00:00:00+00:00"
    assert REQUESTED_END.isoformat() == "2026-08-21T00:00:00+00:00"
    assert [period.name for period in PERIODS] == [
        "requested_recent_6m",
        "protected_holdout_fresh_start",
    ]
    assert CONTROL_ASSIGNMENT_SEEDS == 32
    assert _canonical_sha256(frozen_config_contract()) == FROZEN_CONFIG_SHA256


def test_only_v9_and_v12f_are_reachable() -> None:
    arms = approved_arms()
    assert [arm.name for arm in arms] == [
        "v9_frozen_baseline",
        "v12f_ma6_w8_full_gate",
    ]
    assert all(arm.execution.take_profit_percent is None for arm in arms)
    serialized = json.dumps(frozen_config_contract(), ensure_ascii=False).lower()
    assert "parameter search" in serialized
    assert "training" in serialized
    assert "v12e" in serialized and "forbidden" in frozen_config_contract()


def test_bounded_loader_never_opens_a_row_at_or_after_end(tmp_path: Path) -> None:
    frame = _continuous_fixture(
        REQUESTED_END + 3 * BAR_DURATION,
    )
    path = tmp_path / "fixture.csv"
    frame.to_csv(path, index=False)
    loaded, quality = load_approved_bounded_frame(path)
    assert loaded["open_time"].max() == REQUESTED_END - BAR_DURATION
    assert len(loaded) == len(
        pd.date_range(
            REQUESTED_START - MIN_WARMUP_BARS * BAR_DURATION,
            REQUESTED_END - BAR_DURATION,
            freq="15min",
        )
    )
    assert quality["local_boundary_timestamp_rows_inspected"] == 1
    assert quality["local_ohlcv_rows_at_or_after_approved_end_materialized"] == 0
    assert quality["api_tail"]["requests"] == 0
    assert quality["last_bar"] == "2026-08-20T23:45:00+00:00"


def test_stale_local_prefix_is_filled_from_strictly_bounded_memory_tail(
    tmp_path: Path,
) -> None:
    local = _continuous_fixture(
        pd.Timestamp("2026-08-20T23:00:00Z"),
    )
    path = tmp_path / "stale.csv"
    local.to_csv(path, index=False)

    def bounded_response(url: str) -> dict:
        assert f"after={int(REQUESTED_END.timestamp() * 1_000)}" in url
        timestamps = pd.DatetimeIndex(
            [
                "2026-08-20T23:45:00Z",
                "2026-08-20T23:30:00Z",
                "2026-08-20T23:15:00Z",
                "2026-08-20T23:00:00Z",
            ]
        )
        return {
            "code": "0",
            "data": [
                [
                    str(int(timestamp.timestamp() * 1_000)),
                    "101",
                    "102",
                    "100",
                    "101.5",
                    "1",
                    "0",
                    "0",
                    "1",
                ]
                for timestamp in timestamps
            ],
        }

    loaded, quality = load_approved_bounded_frame(
        path,
        request_fn=bounded_response,
    )
    assert loaded["open_time"].iloc[-5:].tolist() == list(
        pd.date_range("2026-08-20T22:45:00Z", periods=5, freq="15min")
    )
    assert quality["api_tail"]["requests"] == 1
    assert quality["api_tail"]["rows_materialized_after_local_prefix"] == 3
    assert quality["api_tail"]["response_rows_at_or_after_approved_end"] == 0
    assert quality["api_tail"]["unconfirmed_rows_inside_approved_tail"] == 0
    assert quality["api_tail"]["raw_kline_rows_written_to_disk"] == 0


def test_loader_rejects_any_end_other_than_the_owner_approved_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fixture.csv"
    pd.DataFrame(
        {
            "open_time": ["2026-02-20T00:00:00Z"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1.0],
        }
    ).to_csv(path, index=False)
    with pytest.raises(RuntimeError, match="frozen to 2026-08-21"):
        load_approved_bounded_frame(
            path,
            end_exclusive=REQUESTED_END + BAR_DURATION,
        )


def test_local_reader_preserves_duplicates_for_the_quality_gate(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    duplicate_time = "2026-02-20T00:00:00Z"
    pd.DataFrame(
        {
            "open_time": [duplicate_time, duplicate_time],
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [1.0, 1.0],
        }
    ).to_csv(path, index=False)
    loaded, _ = _read_local_prefix_before(path, end_exclusive=REQUESTED_END)
    assert loaded["open_time"].duplicated().sum() == 1


def test_api_tail_rejects_unconfirmed_rows_inside_approved_window() -> None:
    timestamp = pd.Timestamp("2026-08-20T23:15:00Z")

    def unconfirmed_response(_: str) -> dict:
        return {
            "code": "0",
            "data": [
                [
                    str(int(timestamp.timestamp() * 1_000)),
                    "100",
                    "101",
                    "99",
                    "100",
                    "1",
                    "0",
                    "0",
                    "0",
                ]
            ],
        }

    with pytest.raises(RuntimeError, match="unconfirmed candle"):
        _fetch_approved_tail_memory(
            start_after=pd.Timestamp("2026-08-20T23:00:00Z"),
            end_exclusive=REQUESTED_END,
            request_fn=unconfirmed_response,
        )


def test_api_tail_pages_backward_without_off_by_one() -> None:
    pages = [
        ["2026-08-20T23:45:00Z", "2026-08-20T23:30:00Z"],
        ["2026-08-20T23:15:00Z", "2026-08-20T23:00:00Z"],
    ]
    cursors: list[int] = []

    def response(url: str) -> dict:
        cursor = int(url.rsplit("after=", 1)[1])
        cursors.append(cursor)
        timestamps = pd.DatetimeIndex(pages[len(cursors) - 1])
        return {
            "code": "0",
            "data": [
                [
                    str(int(timestamp.timestamp() * 1_000)),
                    "100",
                    "101",
                    "99",
                    "100",
                    "1",
                    "0",
                    "0",
                    "1",
                ]
                for timestamp in timestamps
            ],
        }

    tail, audit = _fetch_approved_tail_memory(
        start_after=pd.Timestamp("2026-08-20T23:00:00Z"),
        end_exclusive=REQUESTED_END,
        request_fn=response,
    )
    assert cursors == [
        int(REQUESTED_END.timestamp() * 1_000),
        int(pd.Timestamp("2026-08-20T23:30:00Z").timestamp() * 1_000),
    ]
    assert tail["open_time"].tolist() == list(
        pd.DatetimeIndex(
            [
                "2026-08-20T23:15:00Z",
                "2026-08-20T23:30:00Z",
                "2026-08-20T23:45:00Z",
            ]
        )
    )
    assert audit["requests"] == 2
    assert audit["duplicate_response_timestamps"] == 0


def test_api_tail_rejects_duplicate_and_end_boundary_timestamps() -> None:
    duplicate = pd.Timestamp("2026-08-20T23:45:00Z")

    def duplicate_response(_: str) -> dict:
        row = [
            str(int(duplicate.timestamp() * 1_000)),
            "100",
            "101",
            "99",
            "100",
            "1",
            "0",
            "0",
            "1",
        ]
        return {"code": "0", "data": [row, row]}

    with pytest.raises(RuntimeError, match="duplicate timestamps"):
        _fetch_approved_tail_memory(
            start_after=pd.Timestamp("2026-08-20T23:00:00Z"),
            end_exclusive=REQUESTED_END,
            request_fn=duplicate_response,
        )

    def boundary_response(_: str) -> dict:
        return {
            "code": "0",
            "data": [
                [
                    str(int(REQUESTED_END.timestamp() * 1_000)),
                    "100",
                    "101",
                    "99",
                    "100",
                    "1",
                    "0",
                    "0",
                    "1",
                ]
            ],
        }

    with pytest.raises(RuntimeError, match="at/after"):
        _fetch_approved_tail_memory(
            start_after=pd.Timestamp("2026-08-20T23:00:00Z"),
            end_exclusive=REQUESTED_END,
            request_fn=boundary_response,
        )


def test_consumption_ledger_claim_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "consumption.json"
    monkeypatch.setattr(holdout_runner, "RESULTS", tmp_path)
    monkeypatch.setattr(holdout_runner, "LEDGER_OUTPUT", ledger)
    value = {"status": "started", "approval": OWNER_APPROVAL}
    _create_ledger_exclusive(value)
    with pytest.raises(FileExistsError):
        _create_ledger_exclusive(value)


def test_period_gate_matches_pine_date_and_guard_semantics() -> None:
    period = PERIODS[0]
    times = pd.DatetimeIndex(
        [
            period.start,
            period.start + BAR_DURATION,
            period.end - 2 * BAR_DURATION,
            period.end - BAR_DURATION,
        ]
    )
    frame = pd.DataFrame(
        {
            "open_time": times,
            "entry_allowed": [True, False, True, True],
            "v9_long": [True, True, True, True],
            "v9_short": [False, False, False, False],
            "ma6_w8_long_pass": [False, False, True, False],
            "ma6_w8_short_pass": [False, False, False, False],
        }
    )
    out = materialize_period_v12f_signals(frame, period)
    # Eligible W8 rejection suppresses the full state transition.
    assert not out.loc[0, "v12f_period_long"]
    # Outside calendar/volatility guards the raw signal remains visible for cooldown.
    assert out.loc[1, "v12f_period_long"]
    assert out.loc[2, "v12f_period_long"]
    # Pine time_close < end is false on the terminal bar, so the raw signal is ungated.
    assert out.loc[3, "v12f_period_long"]
    assert not out.loc[3, "v12f_period_gate_candidate"]

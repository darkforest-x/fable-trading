from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features
from src.judgment.p1_dataset import (
    CandidateObservation,
    DATASET_COLUMNS,
    HOLDOUT_CUTOFF,
    PROTOCOL_VERSION,
    P1DatasetContractError,
    assign_event_groups,
    build_candidate_row,
    file_sha256,
    load_immutable_dataset,
    load_preholdout_candles,
    schema_sha256,
    write_dataset_csv,
)


def _frame(n: int = 420) -> pd.DataFrame:
    i = np.arange(n, dtype=float)
    close = 100.0 + 0.003 * i + 0.3 * np.sin(i / 9.0)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": 1000.0 + (i % 17),
        }
    )


def _observation(signal_i: int = 320) -> CandidateObservation:
    return CandidateObservation(
        source="okx",
        symbol="FIXTURE_USDT_SWAP",
        window_start_i=signal_i - 199,
        window_end_i=signal_i,
        latest_closed_i=signal_i,
        mapped_signal_i=signal_i,
        global_tip_age_bars=0,
        box_x_center=0.98,
        box_y_center=0.5,
        box_width=0.02,
        box_height=0.1,
        box_confidence=0.9,
        box_class_id=0,
    )


def test_schema_contains_exact_28_features_in_order():
    start = DATASET_COLUMNS.index(FEATURE_COLUMNS[0])
    assert DATASET_COLUMNS[start : start + 28] == FEATURE_COLUMNS
    assert len(FEATURE_COLUMNS) == 28
    assert len(schema_sha256()) == 64


def test_preholdout_loader_stops_before_boundary_ohlcv(tmp_path: Path):
    path = tmp_path / "candles.csv"
    path.write_text(
        "open_time,open,high,low,close,volume\n"
        "2026-05-03T23:45:00Z,1,2,0.5,1.5,10\n"
        "2026-05-04T00:00:00Z,NOT_READ,NOT_READ,NOT_READ,NOT_READ,NOT_READ\n"
        "2026-05-04T00:15:00Z,3,4,2,3.5,10\n",
        encoding="utf-8",
    )
    frame, stats = load_preholdout_candles(path)
    assert len(frame) == 1
    assert stats["post_cutoff_ohlcv_rows_materialized"] == 0
    assert stats["boundary_timestamp_checked"] is True
    assert frame["open_time"].max() < HOLDOUT_CUTOFF


def test_candidate_row_uses_linear_short_and_cost_once():
    frame = _frame()
    signal_i = 320
    entry_i = signal_i + 1
    # Force ATR=1 and a same-bar TP/SL collision: conservative short SL=entry+2.
    featured = add_features(add_indicators(frame))
    featured.loc[signal_i, "atr14"] = 1.0
    featured.loc[signal_i, "atr_pct"] = 0.01
    entry = float(frame.loc[entry_i, "open"])
    frame.loc[entry_i, "low"] = entry - 6.0
    frame.loc[entry_i, "high"] = entry + 3.0

    row, rejected = build_candidate_row(
        frame=frame,
        featured=featured,
        observation=_observation(signal_i),
        build_id="fixture",
        detector_path="models/fixture.pt",
        detector_sha256="a" * 64,
    )
    assert rejected is None and row is not None
    assert row["exit_reason"] == "sl_ambiguous"
    assert row["exit_price_research"] == pytest.approx(entry + 2.0)
    assert row["gross_ret"] == pytest.approx(1.0 - row["exit_price_research"] / entry)
    assert row["fee_swap_taker"] == pytest.approx(0.001)
    assert row["net_ret_swap_taker"] == pytest.approx(row["gross_ret"] - 0.001)
    assert row["feature_source_max_i"] == signal_i
    assert pd.Timestamp(row["entry_time_research"]) > pd.Timestamp(row["signal_time"])


def test_event_groups_connect_overlapping_intervals():
    base = {column: "" for column in DATASET_COLUMNS}
    rows = []
    for candidate, start, end in (
        ("a", "2026-01-01T00:15:00Z", "2026-01-01T01:00:00Z"),
        ("b", "2026-01-01T00:45:00Z", "2026-01-01T01:30:00Z"),
        ("c", "2026-01-01T02:00:00Z", "2026-01-01T02:15:00Z"),
    ):
        row = dict(base)
        row.update(
            source="okx",
            symbol="BTC_USDT_SWAP",
            signal_time=start,
            candidate_id=candidate,
            interval_start=start,
            interval_end=end,
        )
        rows.append(row)
    grouped = assign_event_groups(rows)
    by_id = {row["candidate_id"]: row["event_group_id"] for row in grouped}
    assert by_id["a"] == by_id["b"]
    assert by_id["c"] != by_id["a"]


def test_manifest_loader_fails_closed_on_hash_or_protocol(tmp_path: Path):
    dataset = tmp_path / "dataset.csv"
    empty_row = {column: "" for column in DATASET_COLUMNS}
    empty_row.update(
        build_id="fixture",
        protocol_version=PROTOCOL_VERSION,
        source="okx",
        symbol="BTC_USDT_SWAP",
        signal_time="2026-01-01T00:00:00+00:00",
    )
    digest = write_dataset_csv([empty_row], dataset)
    manifest = tmp_path / "manifest.json"
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "schema_sha256": schema_sha256(),
        "training_eligible": True,
        "dataset_path": str(dataset),
        "dataset_sha256": digest,
        "row_count": 1,
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_immutable_dataset(manifest)
    assert len(loaded) == 1

    payload["dataset_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(P1DatasetContractError, match="bytes hash"):
        load_immutable_dataset(manifest)

    payload["dataset_sha256"] = file_sha256(dataset)
    payload["protocol_version"] = "wrong"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(P1DatasetContractError, match="protocol"):
        load_immutable_dataset(manifest)

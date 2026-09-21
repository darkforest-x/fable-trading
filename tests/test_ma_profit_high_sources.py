"""Regression tests for canonical Profit3R high-timeframe archive inputs."""
from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd

import yoyo.data.ma_profit_high_sources as high


def _archive(path: Path, rows: list[tuple[str, float]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write("time,open,high,low,close,volume,quote_volume\n")
        for stamp, price in rows:
            handle.write(f"{stamp},{price},{price + 1},{price - 1},{price + .5},2,3\n")


def test_single_archive_read_derives_complete_utc_buckets_and_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(high, "ROOT", tmp_path)
    archive = tmp_path / "BTCUSDT_30m.csv.gz"
    times = pd.date_range("2025-01-01T00:00:00Z", periods=8, freq="30min")
    _archive(archive, [(stamp.isoformat(), float(index + 10)) for index, stamp in enumerate(times)])
    result = high.build_archive_source(
        archive, venue="binance", cutoff_ms=int(pd.Timestamp("2025-01-01T04:00:00Z").value // 1_000_000), output_dir=tmp_path / "out"
    )
    assert result["status"] == "materialized"
    by_minutes = {row["bar_minutes"]: row for row in result["sources"]}
    assert [by_minutes[minutes]["rows"] for minutes in (30, 60, 240)] == [8, 4, 1]
    assert by_minutes[240]["first_open_utc"] == "2025-01-01T00:00:00+00:00"
    assert by_minutes[240]["last_close_utc"] == "2025-01-01T04:00:00+00:00"
    assert by_minutes[30]["original_sha256"]
    assert by_minutes[30]["normalized_30m_sha256"]
    emitted = pd.read_csv(tmp_path / by_minutes[60]["source_path"])
    assert emitted.ts.tolist() == [int(times[0].value // 1_000_000), int(times[2].value // 1_000_000), int(times[4].value // 1_000_000), int(times[6].value // 1_000_000)]


def test_incomplete_utc_bucket_is_dropped_and_gap_is_reported(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(high, "ROOT", tmp_path)
    archive = tmp_path / "ETHUSDT_30m.csv.gz"
    times = pd.date_range("2025-01-01T00:00:00Z", periods=8, freq="30min").delete(1)
    _archive(archive, [(stamp.isoformat(), float(index + 10)) for index, stamp in enumerate(times)])
    result = high.build_archive_source(
        archive, venue="binance", cutoff_ms=int(pd.Timestamp("2025-01-01T04:00:00Z").value // 1_000_000), output_dir=tmp_path / "out"
    )
    by_minutes = {row["bar_minutes"]: row for row in result["sources"]}
    assert by_minutes[30]["gap_count"] == 1
    assert by_minutes[60]["rows"] == 3
    assert pd.read_csv(tmp_path / by_minutes[60]["source_path"]).ts.tolist()[0] == int(times[1].value // 1_000_000)


def test_malformed_or_empty_archives_are_explicit_statuses_not_zero_signal_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(high, "ROOT", tmp_path)
    malformed = tmp_path / "0GUSDT_30m.csv.gz"
    _archive(malformed, [("not-a-time", 1.0)])
    failed = high._worker(str(malformed), "binance", 2_000_000_000_000, str(tmp_path / "out"))
    assert failed["status"] == "failed"
    assert failed["sources"] == []
    assert failed["error_type"]
    assert failed["base"]["original_sha256"]

    empty = tmp_path / "EMPTYUSDT_30m.csv.gz"
    _archive(empty, [])
    result = high.build_archive_source(empty, venue="binance", cutoff_ms=2_000_000_000_000, output_dir=tmp_path / "out")
    assert result["status"] == "empty"
    assert result["sources"] == []

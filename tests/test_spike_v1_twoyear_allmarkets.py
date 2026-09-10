import json

import pandas as pd
import pytest

import yoyo.evaluation.spike_v1_twoyear_allmarkets as runner
from yoyo.evaluation.spike_v1_twoyear_allmarkets import END, START, WARMUP_START, aggregate, normalize


def test_frozen_two_year_window_ends_after_latest_closed_utc_day():
    assert START == pd.Timestamp("2024-09-10T00:00:00Z")
    assert END == pd.Timestamp("2026-09-10T00:00:00Z")
    assert WARMUP_START <= pd.Timestamp("2023-08-30T00:00:00Z")


def test_complete_utc_aggregation_discards_partial_group():
    index = pd.date_range("2025-01-01", periods=9, freq="30min", tz="UTC")
    frame = pd.DataFrame({"open":range(1,10), "high":range(2,11), "low":range(1,10), "close":range(1,10), "volume":1., "quote_volume":1.}, index=index)
    out = aggregate(frame, 240)
    assert len(out) == 1 and out.iloc[0].open == 1 and out.iloc[0].close == 8


def test_okx_unconfirmed_bar_is_excluded():
    left = pd.Timestamp("2025-01-01T00:00:00Z"); right = left + pd.Timedelta(hours=1)
    payload = {"data": [[str(left.value // 10**6), "1", "2", "1", "2", "3", "4", "5", "0"], [str((left + pd.Timedelta(minutes=30)).value // 10**6), "2", "3", "2", "3", "4", "5", "6", "1"]]}
    frame = normalize("okx", payload, left, right)
    assert len(frame) == 1 and frame.index[0] == left + pd.Timedelta(minutes=30)


def test_aggregate_rejects_a_frozen_source_without_ohlcv_schema():
    frame = pd.DataFrame({"price": [1.]}, index=pd.DatetimeIndex(["2025-01-01T00:00:00Z"]))
    with pytest.raises(ValueError, match="source missing OHLCV columns"):
        aggregate(frame, 30)


def _write_catalog(data, venue="binance", symbol="BADUSDT"):
    data.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"venue": venue, "symbol": symbol, "asset": "BAD", "eligible": True, "tick": .01}]).to_json(
        data / "catalog.json", orient="records"
    )


def test_evaluate_covered_records_complete_but_malformed_source_as_traceable_error(tmp_path, monkeypatch):
    data, results = tmp_path / "data", tmp_path / "results"
    _write_catalog(data)
    source = data / "normalized" / "binance" / "BADUSDT_30m.csv.gz"
    source.parent.mkdir(parents=True)
    pd.DataFrame({"price": [1.]}, index=pd.DatetimeIndex(["2025-01-01T00:00:00Z"])).to_csv(source, compression="gzip")
    receipt = data / "market_receipts" / "binance" / "BADUSDT.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"venue": "binance", "symbol": "BADUSDT", "asset": "BAD", "tick": .01,
                                   "status": "complete", "path": str(source)}), encoding="utf-8")
    monkeypatch.setattr(runner, "DATA", data)
    monkeypatch.setattr(runner, "RESULTS", results)

    runner.evaluate_covered()

    coverage = pd.read_csv(results / "coverage_limited.csv")
    assert set(coverage.status) == {"source_error"}
    assert coverage.detail.str.contains("receipt=").all()
    assert coverage.detail.str.contains("source missing OHLCV columns").all()


def test_fetch_preserves_terminal_error_receipt_without_refetching(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _write_catalog(data)
    receipt = data / "market_receipts" / "binance" / "BADUSDT.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"venue": "binance", "symbol": "BADUSDT", "asset": "BAD", "status": "error",
                                   "path": str(data / "missing.csv.gz"), "error": "prior HTTP error"}), encoding="utf-8")
    monkeypatch.setattr(runner, "DATA", data)

    class ClientMustNotRun:
        def __init__(self, venue):
            raise AssertionError("terminal receipt must not refetch")

    monkeypatch.setattr(runner, "Client", ClientMustNotRun)
    runner.fetch(venue="binance")
    ledger = pd.read_csv(data / "error_ledger.csv")
    assert ledger.loc[0, "status"] == "error"


def test_gate_fetch_preserves_terminal_error_receipts_without_refetching(tmp_path, monkeypatch):
    data = tmp_path / "data"
    _write_catalog(data, venue="gate", symbol="BAD_USDT")
    receipts = data / "gate_timeframe_receipts"
    receipts.mkdir(parents=True)
    for minutes in runner.CONFIG["timeframes"]:
        (receipts / f"BAD_USDT_{minutes}m.json").write_text(json.dumps({"venue": "gate", "symbol": "BAD_USDT", "asset": "BAD",
                                                                          "minutes": minutes, "status": "error", "error": "prior HTTP error",
                                                                          "path": str(data / "missing.csv.gz")}), encoding="utf-8")
    monkeypatch.setattr(runner, "DATA", data)

    class ClientMustNotRun:
        def __init__(self, venue):
            raise AssertionError("terminal receipt must not refetch")

    monkeypatch.setattr(runner, "Client", ClientMustNotRun)
    runner.fetch_gate_timeframes()
    coverage = pd.read_csv(data / "gate_timeframe_coverage.csv")
    assert set(coverage.status) == {"error"}

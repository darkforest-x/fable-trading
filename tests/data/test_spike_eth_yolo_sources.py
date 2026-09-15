"""Synthetic contract checks for the isolated ETH V9 OKX source builder."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.data import spike_eth_yolo_sources as sources


def config(**overrides):
    value = {
        "experiment_id": "exp-spike-eth-v9-test",
        "start_utc": "2026-07-14T16:00:00Z",
        "end_utc": "2026-07-14T16:05:00Z",
        "warmup_bars": 2,
        "timeframes": {"1m": 1},
    }
    value.update(overrides)
    return value


def row(stamp, *, confirm="1", close="101", volume="7", vol_ccy="70", quote="7000"):
    return [str(stamp), "100", "102", "98", close, volume, vol_ccy, quote, confirm]


class Response:
    def __init__(self, payload, status_code=200):
        self.content = json.dumps(payload, separators=(",", ":")).encode()
        self.status_code = status_code
        self.url = "https://www.okx.com/fake"
        self.headers = {"Date": "Mon, 01 Jan 2026 00:00:00 GMT"}


class Session:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if url.endswith(sources.INSTRUMENT_PATH):
            return Response({"code": "0", "data": [{
                "instId": sources.INSTRUMENT, "instType": "SWAP", "state": "live", "baseCcy": "",
                "quoteCcy": "", "instFamily": "ETH-USDT", "uly": "ETH-USDT", "settleCcy": "USDT", "ctType": "linear", "ctVal": "0.1",
                "ctValCcy": "ETH", "ctMult": "1", "tickSz": "0.01", "lotSz": "1", "minSz": "1", "listTime": "1",
            }]})
        after = int(params["after"])
        return Response({"code": "0", "data": [item for item in self.candles if int(item[0]) < after][:int(params["limit"])]})


@pytest.fixture(autouse=True)
def no_rate_sleep(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)


def test_normalize_config_expands_warmup_and_rejects_bad_native_mapping():
    normalized = sources.normalize_config(config())
    spec = normalized["timeframes"]["1m"]
    assert spec["expected"] == 7
    assert spec["source_start_ms"] == spec["start_ms"] - 2 * 60_000
    assert sources.normalize_config(config(
        end_utc="2026-07-16T00:00:00Z", timeframes={"1Dutc": {"minutes": 1440, "sourcebar": "1Dutc"}},
    ))["timeframes"]["1Dutc"]["sourcebar"] == "1Dutc"
    with pytest.raises(ValueError, match="native bar"):
        sources.normalize_config(config(timeframes={"1m": {"minutes": 1, "sourcebar": "3m"}}))


def test_parse_rows_excludes_unconfirmed_and_rejects_geometry_or_conflicting_duplicates():
    start, period = 1_020_000, 60_000
    kept, audit = sources.parse_confirmed_rows([row(start), row(start + period, confirm="0")], period_ms=period, source_start_ms=start, end_ms=start + 3 * period)
    assert list(kept) == [start]
    assert audit["unconfirmed"] == 1
    malformed = row(start + period)
    malformed[2] = "99"
    with pytest.raises(sources.SourceError, match="geometry"):
        sources.parse_confirmed_rows([malformed], period_ms=period, source_start_ms=start, end_ms=start + 3 * period)
    changed = row(start, close="99")
    with pytest.raises(sources.SourceError, match="conflicting duplicate"):
        sources.parse_confirmed_rows([row(start), changed], period_ms=period, source_start_ms=start, end_ms=start + 3 * period)


def test_swap_metadata_accepts_empty_spot_fields_but_requires_derivative_identity():
    metadata = sources._instrument_metadata({"data": [{
        "instId": sources.INSTRUMENT, "instType": "SWAP", "instFamily": "ETH-USDT", "uly": "ETH-USDT",
        "baseCcy": "", "quoteCcy": "", "settleCcy": "USDT", "ctType": "linear", "ctVal": "0.1",
        "ctValCcy": "ETH", "tickSz": "0.01", "lotSz": "0.01",
    }]})
    assert metadata["derived_base_asset"] == "ETH"
    assert metadata["baseCcy"] == metadata["quoteCcy"] == ""
    wrong = dict(metadata)
    wrong.pop("derived_base_asset")
    wrong.pop("derived_base_asset_basis")
    wrong["uly"] = "BTC-USDT"
    with pytest.raises(sources.SourceError, match="uly"):
        sources._instrument_metadata({"data": [wrong]})


def test_run_config_freezes_native_volume_and_receipts_and_reuses_identical_config(tmp_path):
    normalized = sources.normalize_config(config())
    spec = normalized["timeframes"]["1m"]
    candles = [row(stamp) for stamp in range(spec["end_ms"] - spec["period_ms"], spec["source_start_ms"] - spec["period_ms"], -spec["period_ms"])]
    session = Session(candles)
    summary = sources.run_config(config(), tmp_path, session=session)
    item = summary["timeframes"]["1m"]
    assert item["expected"] == item["confirmed"] == 7
    assert item["gaps"] == [] and item["revised"] == 0
    assert item["excluded"] == {"unconfirmed": 0, "outside_window": 0, "exact_duplicates": 0}
    saved = pd.read_csv(tmp_path / "1m.csv")
    assert list(saved.columns) == list(sources.CANONICAL_COLUMNS)
    assert saved.loc[0, "volume"] == 7
    assert saved.loc[0, "volCcy"] == 70
    assert saved.loc[0, "quote_volume"] == 7000
    assert summary["tick_size"] == "0.01"
    assert (tmp_path / "raw").exists()
    history_requests = [params for url, params in session.calls if url.endswith(sources.HISTORY_PATH)]
    assert history_requests and int(history_requests[0]["before"]) == spec["source_start_ms"] - 1
    calls = len(session.calls)
    assert sources.run_config(config(), tmp_path, session=session) == summary
    assert len(session.calls) == calls


def test_gap_fails_loudly_but_keeps_raw_receipt_for_inspection(tmp_path):
    normalized = sources.normalize_config(config())
    spec = normalized["timeframes"]["1m"]
    missing = spec["source_start_ms"] + 3 * spec["period_ms"]
    candles = [row(stamp) for stamp in range(spec["end_ms"] - spec["period_ms"], spec["source_start_ms"] - spec["period_ms"], -spec["period_ms"]) if stamp != missing]
    with pytest.raises(sources.SourceError, match="grid has 1 gaps"):
        sources.run_config(config(), tmp_path, session=Session(candles))
    assert list((tmp_path / "raw").rglob("*.receipt.json"))
    assert not (tmp_path / "summary.json").exists()


def test_resume_rejects_different_config_before_network(tmp_path):
    sources._prepare_output(tmp_path, sources.normalize_config(config()))
    with pytest.raises(sources.SourceError, match="different acquisition config"):
        sources.run_config(config(warmup_bars=3), tmp_path, session=Session([]))


def test_builder_commit_gate_is_explicit_and_independent_of_acquisition(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    module = Path(sources.__file__).read_bytes()

    def fake_git(command, **kwargs):
        if command[1] == "rev-parse":
            return "deadbeef\n"
        assert command[:2] == ["git", "show"]
        return module

    monkeypatch.setattr(sources.subprocess, "check_output", fake_git)
    result = sources.assert_builder_committed(root)
    assert result["builder_commit"] == "deadbeef"
    assert result["builder_sha256"] == sources._sha256(module)

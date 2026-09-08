"""Synthetic public-feed tests; never access network or production caches."""

import json

import pytest
import requests

from yoyo.data.okx_altcoin_snapshot import (
    PERIOD_MS, PublicClient, SnapshotError, collect_stream, load_symbols,
    normalize_pages, run, utc_ms,
)


SYMBOL = "SOPH-USDT-SWAP"
START = utc_ms("2026-07-01T00:00:00Z")
HOUR = PERIOD_MS["1H"]


def page(data, name="p1"):
    return {"fetched_at": "2026-09-09T00:00:00Z", "source_page": name,
            "response": {"code": "0", "data": data}}


def oi(t, n="10"):
    return [str(t), n, "1000", "100"]


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.text = "response"

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, duration):
        self.value += duration


def client(tmp_path, responses):
    session = Session(responses)
    clock = Clock()
    return PublicClient(tmp_path, session=session, clock=clock.now, sleep=clock.sleep), session, clock


def test_sort_dedup_scope_and_nominal_availability():
    data = [oi(START + 2 * HOUR), oi(START), oi(START - HOUR), oi(START + HOUR), oi(START + 3 * HOUR)]
    pages = [page(data), page([oi(START + HOUR, "10.0")], "p2")]
    rows, coverage = normalize_pages("oi", pages, SYMBOL, "1H", START, START + 3 * HOUR)
    assert [r["event_time"] for r in rows] == [START, START + HOUR, START + 2 * HOUR]
    assert rows[0]["available_at_nominal"] == START + HOUR
    assert rows[0]["oi_base"] == "1000"
    assert coverage["duplicate_count"] == 1
    assert coverage["excluded_before_start"] == 1
    assert coverage["excluded_at_or_after_end_or_incomplete"] == 1
    assert coverage["full_period_coverage"]


def test_unclosed_bucket_is_excluded_even_if_timestamp_precedes_end():
    rows, result = normalize_pages("oi", [page([oi(START), oi(START + HOUR)])], SYMBOL,
                                   "1H", START, START + HOUR + 1)
    assert len(rows) == 1
    assert result["excluded_at_or_after_end_or_incomplete"] == 1


def test_conflicting_duplicates_reject_instead_of_last_write_wins():
    with pytest.raises(SnapshotError, match="Conflicting duplicate"):
        normalize_pages("oi", [page([oi(START)]), page([oi(START, "11")])],
                        SYMBOL, "1H", START, START + HOUR)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-1", "", None, True, "word"])
def test_invalid_oi_values_are_rejected(invalid):
    with pytest.raises(SnapshotError):
        normalize_pages("oi", [page([oi(START, invalid)])], SYMBOL, "1H", START, START + HOUR)


def test_bad_shape_and_off_grid_are_rejected():
    with pytest.raises(SnapshotError, match="exactly"):
        normalize_pages("taker", [page([[str(START), "2"]])], SYMBOL, "1H", START, START + HOUR)
    with pytest.raises(SnapshotError, match="Off-grid"):
        normalize_pages("oi", [page([oi(START + 1)])], SYMBOL, "1H", START, START + 2 * HOUR)


def test_taker_order_is_sell_then_buy_and_funding_never_substitutes_prediction():
    rows, _ = normalize_pages("taker", [page([[str(START), "3", "7"]])], SYMBOL, "1H", START, START + HOUR)
    assert (rows[0]["sell_base"], rows[0]["buy_base"]) == ("3", "7")
    data = [{"instId": SYMBOL, "fundingTime": str(START), "fundingRate": "-0.001", "realizedRate": ""},
            {"instId": SYMBOL, "fundingTime": str(START + HOUR), "fundingRate": "0.001", "realizedRate": "0.0009"}]
    rows, _ = normalize_pages("funding", [page(data)], SYMBOL, "1H", START, START + 2 * HOUR)
    assert rows[0]["realized_rate"] is None
    assert rows[0]["predicted_rate"] == "-0.001"
    assert rows[0]["rate_status"] == "predicted_only"
    assert rows[1]["realized_rate"] == "0.0009"


def test_missing_funding_rates_and_wrong_instrument_reject():
    for data in [{"instId": SYMBOL, "fundingTime": str(START)},
                 {"instId": "WRONG-USDT-SWAP", "fundingTime": str(START), "fundingRate": "0"}]:
        with pytest.raises(SnapshotError):
            normalize_pages("funding", [page([data])], SYMBOL, "1H", START, START + HOUR)


def test_paginate_unordered_boundary_duplicate_and_replay_frozen_pages(tmp_path):
    responses = [Response({"code": "0", "data": [oi(START + HOUR), oi(START + 2 * HOUR)]}),
                 Response({"code": "0", "data": [oi(START + HOUR), oi(START)]})]
    api, session, clock = client(tmp_path, responses)
    rows, report = collect_stream(api, "oi", SYMBOL, "1H", START, START + 3 * HOUR)
    assert len(rows) == 3 and report["stop_reason"] == "reached_start"
    assert session.calls[1][1]["params"]["end"] == str(START)
    assert clock.value >= 0.5
    original = {p: p.read_bytes() for p in tmp_path.glob("raw/**/*.json")}
    again, second = collect_stream(api, "oi", SYMBOL, "1H", START, START + 3 * HOUR)
    assert again == rows and second == report and len(session.calls) == 2
    assert all(p.read_bytes() == body for p, body in original.items())


@pytest.mark.parametrize("failure", [Response({"code": "50011", "msg": "limited", "data": []}),
                                     Response({"code": "0", "data": []}, status=429),
                                     requests.Timeout("timeout")])
def test_errors_are_bounded_and_receipted(tmp_path, failure):
    api, session, _ = client(tmp_path, [failure, failure, failure])
    with pytest.raises(SnapshotError, match="after 3 attempts"):
        api.page("oi", {"instId": SYMBOL, "period": "1H"})
    assert len(session.calls) == 3
    assert len(list(tmp_path.glob("errors/*.json"))) == 3
    assert not list(tmp_path.glob("raw/**/*.json"))


def test_failed_missing_page_can_resume_without_overwriting_source(tmp_path):
    first = Response({"code": "0", "data": [oi(START + HOUR)]})
    timeout = requests.Timeout("timeout")
    api, _, _ = client(tmp_path, [first, timeout, timeout, timeout])
    _, report = collect_stream(api, "oi", SYMBOL, "1H", START, START + 2 * HOUR)
    assert not report["acquisition_complete"] and report["error"]
    frozen_path = next(tmp_path.glob("raw/**/*.json"))
    frozen = frozen_path.read_bytes()
    resumed, session, _ = client(tmp_path, [Response({"code": "0", "data": [oi(START)]})])
    rows, report = collect_stream(resumed, "oi", SYMBOL, "1H", START, START + 2 * HOUR)
    assert len(rows) == 2 and report["acquisition_complete"]
    assert len(session.calls) == 1 and frozen_path.read_bytes() == frozen


def test_empty_history_reports_missing_coverage_without_fake_values(tmp_path):
    api, _, _ = client(tmp_path, [Response({"code": "0", "data": []})])
    rows, report = collect_stream(api, "oi", SYMBOL, "1H", START, START + 5 * HOUR)
    assert rows == [] and report["missing_periods"] == 5
    assert report["acquisition_complete"] and not report["full_period_coverage"]


def test_malformed_successful_page_rejects_whole_normalized_stream(tmp_path):
    api, _, _ = client(tmp_path, [Response({"code": "0", "data": [oi(START), oi(START, "11")]})])
    rows, report = collect_stream(api, "oi", SYMBOL, "1H", START, START + HOUR)
    assert rows == [] and report["normalization_rejected"]
    assert not report["acquisition_complete"] and "Conflicting duplicate" in report["error"]
    assert len(list(tmp_path.glob("raw/**/*.json"))) == 1


def test_instrument_snapshot_is_metadata_not_dated_history(tmp_path):
    data = {"instId": SYMBOL, "listTime": str(START - 100 * HOUR), "ctVal": "100", "ctMult": "1", "ctValCcy": "SOPH", "state": "live"}
    api, _, _ = client(tmp_path, [Response({"code": "0", "data": [data]})])
    rows, report = collect_stream(api, "instruments", SYMBOL, "1H", START, START + HOUR)
    assert rows[0]["ct_val"] == "100"
    assert "event_time" not in rows[0]
    assert report["historical_specifications"] is False
    raw = json.loads(next(tmp_path.glob("raw/**/*.json")).read_text())
    assert raw["requested_at"] <= raw["fetched_at"]


def test_default_client_never_loads_netrc_credentials(tmp_path):
    api = PublicClient(tmp_path)
    assert api.session.trust_env is False


def test_manifest_rejects_changed_range_and_writes_csv(tmp_path):
    api, _, _ = client(tmp_path, [Response({"code": "0", "data": [oi(START)]})])
    result = run([SYMBOL], tmp_path, START, START + HOUR, "1H", ["oi"], client=api)
    assert result["errors"] == 0
    assert (tmp_path / "normalized" / (SYMBOL + "_1H_oi.csv")).read_text().startswith("inst_id,event_time,available_at_nominal")
    with pytest.raises(SnapshotError, match="Frozen manifest differs"):
        run([SYMBOL], tmp_path, START, START + 2 * HOUR, "1H", ["oi"], client=api)


def test_symbols_and_utc_inputs_are_strict(tmp_path):
    source = tmp_path / "symbols.json"
    source.write_text(json.dumps({"symbols": [SYMBOL]}))
    assert load_symbols(source) == [SYMBOL]
    source.write_text(json.dumps([SYMBOL, SYMBOL]))
    with pytest.raises(SnapshotError, match="Duplicate"):
        load_symbols(source)
    with pytest.raises(SnapshotError, match="explicitly"):
        utc_ms("2026-07-01T00:00:00")
    with pytest.raises(SnapshotError, match="explicitly"):
        utc_ms("2026-07-01T00:00:00+08:00")


def test_inclusive_retention_edge_advances_below_last_row(tmp_path):
    responses = [Response({"code": "0", "data": [oi(START + HOUR)]}),
                 Response({"code": "0", "data": []})]
    api, session, _ = client(tmp_path, responses)
    rows, report = collect_stream(api, "oi", SYMBOL, "1H", START, START + 3 * HOUR)
    assert len(rows) == 1
    assert session.calls[1][1]["params"]["end"] == str(START)
    assert report["stop_reason"] == "empty_page"
    assert report["acquisition_complete"] and report["missing_periods"] == 2

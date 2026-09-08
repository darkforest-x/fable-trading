"""Injected current-derivatives responses only: no HTTP, secrets or market reads."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

import yoyo.rotation.derivatives as derivatives


NOW = datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
EVENT_MS = int((NOW - timedelta(seconds=1)).timestamp() * 1000)


@pytest.fixture(autouse=True)
def no_network_and_known_receipt_time(monkeypatch):
    monkeypatch.setattr(derivatives, "_now", lambda: NOW)

    def refused(*args, **kwargs):
        raise AssertionError("tests cannot use real HTTP")

    monkeypatch.setattr(derivatives, "build_opener", refused)


def responses(symbol="BTCUSDT"):
    return {
        "/fapi/v1/premiumIndex": {"symbol": symbol, "markPrice": "50000", "lastFundingRate": "-0.0001", "time": EVENT_MS,
                                  "nextFundingTime": EVENT_MS + 4 * 60 * 60 * 1000},
        "/fapi/v1/openInterest": {"symbol": symbol, "openInterest": "100.5", "time": EVENT_MS},
        "/fapi/v1/fundingInfo": [{"symbol": symbol, "fundingIntervalHours": 4}],
    }


def run(payloads=None, *, symbol="BTCUSDT", guard=None):
    fixture = responses(symbol) if payloads is None else payloads
    calls = []

    def transport(url):
        calls.append(url)
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "fapi.binance.com"
        assert parsed.path in derivatives.ALLOWED_ENDPOINTS
        if parsed.path == "/fapi/v1/fundingInfo":
            assert not parsed.query
        else:
            assert parse_qs(parsed.query) == {"symbol": [symbol]}
        value = fixture[parsed.path]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    result = derivatives.fetch_context(symbol, guard=guard or (lambda: None), transport=transport)
    json.dumps(result, allow_nan=False)
    return result, calls


def test_complete_context_keeps_units_provenance_and_does_not_infer_direction():
    result, calls = run()
    assert len(calls) == 3
    assert result["status"] == "ok"
    assert result["funding_rate"] == -.0001
    assert result["funding_kind"] == "last_reported_rate"
    assert result["funding_settlement_time"] is None
    assert result["interval_hours"] == 4
    assert result["open_interest"] == 100.5
    assert result["open_interest_unit"] == "base_asset_units_as_reported"
    assert result["open_interest_usd"] == 5_025_000
    assert result["open_interest_quote"] == result["open_interest_usd"]
    assert result["quote_currency"] == "USDT"
    assert result["open_interest_usd_is_approximate"] is True
    assert result["open_interest_usd_kind"].startswith("derived_")
    assert result["observed_at"] == NOW.isoformat()
    assert result["event_time"] == {"premium_index": (NOW - timedelta(seconds=1)).isoformat(),
                                    "open_interest": (NOW - timedelta(seconds=1)).isoformat(),
                                    "funding_info": None}
    assert result["synchronized"] is False
    assert result["source"] == "Binance USD-M, not spot flow"
    assert "direction" not in result and "inflow" not in result
    assert all(source["method"] == "GET" and source["raw_persisted"] is False for source in result["sources"])


def test_guard_precedes_every_io_and_guard_failure_is_not_downgraded():
    order = []

    def guard():
        order.append("guard")

    def transport(url):
        order.append("request")
        return responses()[urlsplit(url).path]

    derivatives.fetch_context("BTCUSDT", guard=guard, transport=transport)
    assert order == ["guard", "request"] * 3
    order.clear()

    def denied():
        order.append("denied")
        raise PermissionError("test authorization revoked")

    with pytest.raises(PermissionError):
        derivatives.fetch_context("BTCUSDT", guard=denied, transport=transport)
    assert order == ["denied"]


def test_permission_revoked_between_endpoints_blocks_second_io():
    requests = []

    def guard():
        if requests:
            raise PermissionError("revoked after first endpoint")

    def transport(url):
        requests.append(url)
        return responses()[urlsplit(url).path]

    with pytest.raises(PermissionError):
        derivatives.fetch_context("BTCUSDT", guard=guard, transport=transport)
    assert len(requests) == 1


@pytest.mark.parametrize("bad", [None, "", "nan", "Infinity", float("nan"), float("inf"), True, {}])
def test_missing_or_invalid_rate_remains_none_without_backfill(bad):
    fixture = responses()
    fixture["/fapi/v1/premiumIndex"]["lastFundingRate"] = bad
    fixture["/fapi/v1/premiumIndex"]["interestRate"] = "0.123"
    fixture["/fapi/v1/fundingInfo"][0]["adjustedFundingRateCap"] = "0.25"
    result, _ = run(fixture)
    assert result["status"] == "partial"
    assert result["funding_rate"] is None
    assert result["open_interest"] == 100.5
    assert any("lastFundingRate" in reason for reason in result["reasons"])


def test_absent_rate_field_is_not_replaced_by_interest_or_cap():
    fixture = responses()
    del fixture["/fapi/v1/premiumIndex"]["lastFundingRate"]
    result, _ = run(fixture)
    assert result["funding_rate"] is None


@pytest.mark.parametrize("payload", [[], [{"symbol": "OTHERUSDT", "fundingIntervalHours": 8}],
                                     [{"symbol": "BTCUSDT"}], [{"symbol": "BTCUSDT", "fundingIntervalHours": 0}],
                                     [{"symbol": "BTCUSDT", "fundingIntervalHours": "NaN"}]])
def test_unreported_or_invalid_interval_is_unknown_never_eight_hour_default(payload):
    fixture = responses()
    fixture["/fapi/v1/fundingInfo"] = payload
    result, _ = run(fixture)
    assert result["interval_hours"] is None
    assert result["status"] == "partial"
    assert result["funding_rate"] == -.0001


@pytest.mark.parametrize("bad", [None, -1, "NaN", float("inf"), True])
def test_invalid_oi_is_not_zero_and_cannot_produce_usd_notional(bad):
    fixture = responses()
    fixture["/fapi/v1/openInterest"]["openInterest"] = bad
    result, _ = run(fixture)
    assert result["open_interest"] is None
    assert result["open_interest_usd"] is None
    assert result["status"] == "partial"


def test_actual_zero_oi_and_zero_funding_are_valid_observations():
    fixture = responses()
    fixture["/fapi/v1/openInterest"]["openInterest"] = "0"
    fixture["/fapi/v1/premiumIndex"]["lastFundingRate"] = "0"
    result, _ = run(fixture)
    assert result["status"] == "ok"
    assert result["open_interest"] == result["open_interest_usd"] == result["funding_rate"] == 0


def test_missing_mark_price_does_not_remove_oi_or_invent_a_notional():
    fixture = responses()
    del fixture["/fapi/v1/premiumIndex"]["markPrice"]
    result, _ = run(fixture)
    assert result["open_interest"] == 100.5
    assert result["open_interest_usd"] is None
    assert result["status"] == "partial"


@pytest.mark.parametrize("bad", [None, "NaN", True, 1.5, 10**30])
def test_unknown_source_time_masks_values_instead_of_using_receipt_time(bad):
    fixture = responses()
    fixture["/fapi/v1/premiumIndex"]["time"] = bad
    result, _ = run(fixture)
    assert result["funding_rate"] is None and result["mark_price"] is None
    assert result["open_interest_usd"] is None
    assert result["event_time"]["premium_index"] is None
    assert result["status"] == "partial"


@pytest.mark.parametrize("age", [-1, 301])
def test_future_or_stale_source_masks_values_and_retains_reported_time(age):
    fixture = responses()
    stamp = NOW - timedelta(seconds=age)
    fixture["/fapi/v1/openInterest"]["time"] = int(stamp.timestamp() * 1000)
    result, _ = run(fixture)
    assert result["open_interest"] is None
    assert result["open_interest_usd"] is None
    assert result["event_time"]["open_interest"] == stamp.isoformat()
    assert result["funding_rate"] is not None
    assert result["status"] == "partial"


def test_different_event_times_are_explicit_and_large_skew_blocks_notional():
    fixture = responses()
    fixture["/fapi/v1/openInterest"]["time"] -= 61_000
    result, _ = run(fixture)
    assert result["funding_rate"] is not None and result["open_interest"] == 100.5
    assert result["open_interest_usd"] is None
    assert result["event_time_skew_seconds"] == 61
    assert result["status"] == "partial" and result["synchronized"] is False
    fixture["/fapi/v1/openInterest"]["time"] += 60_000
    near, _ = run(fixture)
    assert near["event_time_skew_seconds"] == 1
    assert near["open_interest_usd"] is not None
    assert near["status"] == "partial" and near["synchronized"] is False


@pytest.mark.parametrize("error", [HTTPError("https://fapi.binance.com", 429, "limit", {}, None),
                                   {"code": -1003, "msg": "too many requests"}], ids=["http429", "api1003"])
def test_rate_limit_stops_remaining_requests_without_retry(error):
    fixture = responses()
    fixture["/fapi/v1/premiumIndex"] = error
    result, calls = run(fixture)
    assert len(calls) == 1
    assert result["status"] == "unavailable"
    assert any("rate_limited" in reason for reason in result["reasons"])


def test_source_failure_is_exposed_without_silent_venue_fallback():
    fixture = responses()
    fixture["/fapi/v1/premiumIndex"] = OSError("synthetic network failure")
    result, calls = run(fixture)
    assert len(calls) == 3
    assert result["funding_rate"] is None and result["open_interest"] == 100.5
    assert result["status"] == "partial"
    assert result["sources"][0]["status"] == "unavailable"
    assert all(url.startswith("https://fapi.binance.com/") for url in calls)


def test_spot_only_symbol_has_no_perpetual_context_and_stops_requests():
    fixture = responses("SPOTONLYUSDT")
    fixture["/fapi/v1/premiumIndex"] = {"code": -1121, "msg": "Invalid symbol."}
    result, calls = run(fixture, symbol="SPOTONLYUSDT")
    assert len(calls) == 1
    assert result["status"] == "unavailable"
    assert result["open_interest"] is None
    assert any("no_matching_usdm_perpetual_symbol" in reason for reason in result["reasons"])


def test_thousand_prefixed_base_is_not_silently_rescaled_to_another_asset():
    result, _ = run(symbol="1000SHIBUSDT")
    assert result["base_asset"] == "1000SHIB"
    assert result["open_interest"] == 100.5


@pytest.mark.parametrize("kind,payload", [
    ("/fapi/v1/premiumIndex", []),
    ("/fapi/v1/premiumIndex", {"symbol": "OTHERUSDT", "lastFundingRate": ".1", "markPrice": "1", "time": EVENT_MS}),
    ("/fapi/v1/openInterest", {"symbol": "OTHERUSDT", "openInterest": "1", "time": EVENT_MS}),
    ("/fapi/v1/fundingInfo", {}),
    ("/fapi/v1/fundingInfo", ["wrong"]),
    ("/fapi/v1/fundingInfo", [{"symbol": "BTCUSDT", "fundingIntervalHours": 4}] * 2),
])
def test_schema_and_symbol_mismatch_stays_visible_as_partial(kind, payload):
    fixture = responses()
    fixture[kind] = payload
    result, _ = run(fixture)
    assert result["status"] == "partial"
    assert any(source["status"] == "partial" for source in result["sources"])


@pytest.mark.parametrize("symbol", ["btcusdt", "BTCUSDC", "BTCUSDT_260925", "", "BTCUSDT&symbol=ETHUSDT",
                                    "https://example.com/BTCUSDT", "../BTCUSDT"])
def test_input_cannot_add_a_symbol_alias_endpoint_or_host(symbol):
    seen = []
    with pytest.raises(ValueError):
        derivatives.fetch_context(symbol, guard=lambda: seen.append("guard"), transport=lambda url: seen.append(url))
    assert seen == []


@pytest.mark.parametrize("url", ["http://fapi.binance.com/fapi/v1/fundingInfo",
                                 "https://evil.example/fapi/v1/fundingInfo",
                                 "https://fapi.binance.com/fapi/v1/order?symbol=BTCUSDT",
                                 "https://fapi.binance.com/fapi/v1/fundingInfo?symbol=BTCUSDT",
                                 "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT&symbol=ETHUSDT"])
def test_internal_http_allowlist_is_enforced_before_network(url):
    with pytest.raises(ValueError):
        derivatives._http_get(url)


def test_redirect_handler_refuses_extra_io_even_to_same_host():
    request = derivatives.Request("https://fapi.binance.com/fapi/v1/fundingInfo", method="GET")
    with pytest.raises(HTTPError):
        derivatives._NoRedirect().redirect_request(request, None, 302, "moved", {}, "https://example.com")

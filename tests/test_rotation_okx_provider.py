"""Offline OKX public-provider tests; no network, account or holdout reads.

Fixtures use pre-holdout timestamps. Deliberately malformed JSON values and
pagination boundaries verify fail-closed handling before numeric conversion.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from yoyo.contracts.holdout import assert_pre_holdout
from yoyo.contracts.rotation import INTERVAL_SECONDS, RotationError, utc
import yoyo.rotation.okx_provider as module
from yoyo.rotation.okx_provider import OKXProvider, _RejectRedirects


AS_OF = utc("2026-04-30T00:00:00Z")


def candle(stamp, **changes):
    values = [str(stamp), "10", "12", "9", "11", "2", "21", "21", "1"]
    fields = ("stamp", "open", "high", "low", "close", "volume", "volume_ccy", "quote_volume", "confirm")
    for key, value in changes.items():
        values[fields.index(key)] = value
    return values


def candles(count, *, interval="1d", as_of=AS_OF):
    duration = INTERVAL_SECONDS[interval] * 1000
    boundary = int(as_of.timestamp() * 1000) // duration * duration
    return [candle(boundary - duration * (index + 1)) for index in range(count)]


def payload(data, code="0", msg=""):
    return {"code": code, "msg": msg, "data": data}


def client_for(responses, guard=lambda: None):
    calls = []
    queue = list(responses)

    def transport(url):
        calls.append(url)
        assert queue, "Unexpected extra public request"
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    return OKXProvider(guard=guard, transport=transport, request_spacing=0), calls


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("This provider test must not construct a real HTTP opener")
    monkeypatch.setattr(module, "build_opener", forbidden)


@pytest.mark.parametrize("interval,bar", [("1d", "1Dutc"), ("4h", "4H"), ("15m", "15m")])
def test_closed_utc_boundaries_and_quote_units(interval, bar):
    as_of = utc("2026-04-30T17:41:27+00:00")
    rows = candles(3, interval=interval, as_of=as_of)
    # Use distinct volumes to prove that field 7, not base volume or a
    # close-times-volume approximation, supplies quote_volume.
    rows[0][6] = "20.5"
    provider, calls = client_for([payload(rows)])
    frame = provider.candles("BTCUSDT", interval, as_of=as_of, limit=95)
    query = parse_qs(urlparse(calls[0]).query)
    duration = INTERVAL_SECONDS[interval] * 1000
    boundary = int(as_of.timestamp() * 1000) // duration * duration
    assert query == {"instId": ["BTC-USDT"], "bar": [bar], "after": [str(boundary)], "limit": ["95"]}
    assert urlparse(calls[0]).scheme == "https" and urlparse(calls[0]).netloc == "www.okx.com"
    assert frame.index.tz == timezone.utc
    assert frame.index.is_monotonic_increasing
    assert frame.index[-1].value // 1_000_000 == boundary - duration
    assert frame.iloc[-1]["volume"] == 2 and frame.iloc[-1]["quote_volume"] == 21
    assert frame["confirmed"].all()
    assert list(frame.columns) == ["open", "high", "low", "close", "volume", "quote_volume", "confirmed"]
    assert len(frame.attrs["input_digest"]) == 64
    assert frame.attrs["source"] == provider.name


@pytest.mark.parametrize("limit,requests", [(1, 1), (95, 1), (100, 1), (101, 2), (299, 3), (500, 5)])
def test_pagination_is_exclusive_bounded_and_authorized_per_request(limit, requests):
    rows = candles(limit)
    calls, guards = [], []

    def transport(url):
        calls.append(url)
        assert len(guards) == len(calls), "Each page needs an immediately preceding guard"
        params = parse_qs(urlparse(url).query)
        count = int(params["limit"][0])
        boundary = int(params["after"][0])
        assert count <= 100
        return payload([row for row in rows if int(row[0]) < boundary][:count])

    provider = OKXProvider(guard=lambda: guards.append("checked"), transport=transport, request_spacing=0)
    frame = provider.candles("ETHUSDT", "1d", as_of=AS_OF, limit=limit)
    assert len(frame) == limit and len(calls) == requests
    assert len(provider.receipts) == requests
    for i in range(1, len(calls)):
        cursor = int(parse_qs(urlparse(calls[i]).query)["after"][0])
        assert cursor == int(rows[i * 100 - 1][0])


def test_input_identity_is_independent_of_payload_order():
    rows = candles(3)
    first, _ = client_for([payload(rows)])
    second, _ = client_for([payload(rows[::-1])])
    one = first.candles("BTCUSDT", "1d", as_of=AS_OF, limit=3)
    two = second.candles("BTCUSDT", "1d", as_of=AS_OF, limit=3)
    pd.testing.assert_frame_equal(one, two)
    assert one.attrs["input_digest"] == two.attrs["input_digest"]


def test_short_recent_history_is_returned_without_fabrication():
    provider, calls = client_for([payload(candles(2))])
    frame = provider.candles("NEWUSDT", "1d", as_of=AS_OF, limit=100)
    assert len(frame) == 2 and len(calls) == 1


def test_empty_older_page_ends_available_history():
    provider, calls = client_for([payload(candles(100)), payload([])])
    frame = provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=150)
    assert len(frame) == 100 and len(calls) == 2


def test_holdout_guard_fails_before_any_http():
    def guard():
        assert_pre_holdout(utc("2026-05-04T00:00:00Z"), what="offline guard test")
    provider, calls = client_for([], guard=guard)
    with pytest.raises(ValueError, match="holdout"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=2)
    assert calls == [] and provider.receipts == []


def test_guard_runs_after_rate_limit_wait_and_cannot_expire_in_queue(monkeypatch):
    actions = []
    expired = False

    def sleep(seconds):
        nonlocal expired
        actions.append(("wait", seconds))
        expired = True

    def guard():
        actions.append("guard")
        if expired:
            raise RotationError("authorization expired during wait")

    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(module.time, "sleep", sleep)
    provider = OKXProvider(guard=guard, transport=lambda url: pytest.fail("expired transport called"), request_spacing=.35)
    provider._last = 10.0
    with pytest.raises(RotationError, match="expired"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=1)
    assert actions == [("wait", .35), "guard"]


def test_second_page_authorization_revocation_propagates():
    count = 0

    def guard():
        nonlocal count
        count += 1
        if count == 2:
            raise PermissionError("approval revoked")

    provider, calls = client_for([payload(candles(100))], guard=guard)
    with pytest.raises(PermissionError, match="revoked"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=101)
    assert len(calls) == 1


def test_entire_page_times_are_validated_before_bad_prices():
    rows = candles(2)
    rows[0][1] = "bad-price"
    rows[1][0] = str(int(AS_OF.timestamp() * 1000))
    provider, _ = client_for([payload(rows)])
    with pytest.raises(RotationError, match="beyond the closed cutoff"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=2)


def test_all_page_times_precede_price_conversion():
    first = candles(100)
    first[0][1] = "bad-price"
    second = [candle(int(AS_OF.timestamp() * 1000))]
    provider, calls = client_for([payload(first), payload(second)])
    with pytest.raises(RotationError, match="beyond the closed cutoff"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=101)
    assert len(calls) == 2


@pytest.mark.parametrize("problem,match", [
    ("duplicate", "duplicate"), ("gap", "gap"), ("stale", "latest closed"),
    ("unaligned", "unaligned"), ("unconfirmed", "unconfirmed"),
    ("missing-field", "nine fields"), ("extra-field", "nine fields"),
    ("negative", "geometry"), ("zero-price", "geometry"), ("high-below-close", "geometry"),
    ("low-above-open", "geometry"), ("nan", "nonfinite"), ("infinite", "nonfinite"),
    ("non-numeric", "numeric"), ("bool-value", "numeric"),
])
def test_invalid_candle_payload_is_never_silently_repaired(problem, match):
    rows = candles(3)
    if problem == "duplicate": rows[1] = rows[0][:]
    elif problem == "gap": rows.pop(1)
    elif problem == "stale": rows.pop(0)
    elif problem == "unaligned": rows[0][0] = str(int(rows[0][0]) + 1)
    elif problem == "unconfirmed": rows[0][8] = "0"
    elif problem == "missing-field": rows[0].pop()
    elif problem == "extra-field": rows[0].append("extra")
    elif problem == "negative": rows[0][7] = "-1"
    elif problem == "zero-price": rows[0][3] = "0"
    elif problem == "high-below-close": rows[0][2] = "10.5"
    elif problem == "low-above-open": rows[0][3] = "10.5"
    elif problem == "nan": rows[0][1] = "NaN"
    elif problem == "infinite": rows[0][5] = float("inf")
    elif problem == "non-numeric": rows[0][1] = "error"
    elif problem == "bool-value": rows[0][5] = True
    provider, _ = client_for([payload(rows)])
    with pytest.raises(RotationError, match=match):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=3)


@pytest.mark.parametrize("bad_stamp", [True, None, 1.1, "1.0", "-1", "nan", "", {}])
def test_timestamp_type_is_strict(bad_stamp):
    rows = candles(1)
    rows[0][0] = bad_stamp
    provider, _ = client_for([payload(rows)])
    with pytest.raises(RotationError, match="timestamp"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=1)


def test_duplicate_across_pages_fails():
    first = candles(100)
    provider, _ = client_for([payload(first), payload([first[-1]])])
    with pytest.raises(RotationError, match="duplicate"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=101)


def test_server_cannot_ignore_older_page_bound():
    first = candles(100)
    # Current-cutoff-safe but not older than the previous pagination cursor.
    new = candle(int(first[-1][0]) + 86400000 * 100)
    provider, _ = client_for([payload(first), payload([new])])
    with pytest.raises(RotationError, match="beyond|exclusive"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=101)


@pytest.mark.parametrize("bad", [payload([]), {}, [], payload("wrong"), payload(candles(1), code=0),
                                payload([], code="50011", msg="rate limit reached"),
                                HTTPError("https://www.okx.com", 429, "Too Many Requests", {}, None)],
                         ids=["empty", "envelope-missing", "envelope-array", "data-type", "code-type", "rate-limit", "http-429"])
def test_api_errors_do_not_trigger_retry_or_fallback(bad):
    provider, calls = client_for([bad])
    with pytest.raises(RotationError):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=1)
    assert len(calls) == 1 and "www.okx.com" in calls[0]


def test_response_larger_than_requested_count_is_rejected():
    provider, _ = client_for([payload(candles(2))])
    with pytest.raises(RotationError, match="exceeded"):
        provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=1)


@pytest.mark.parametrize("override", [
    {"limit": 0}, {"limit": 501}, {"limit": True}, {"limit": 1.5},
    {"interval": "1h"}, {"symbol": "BTC-USDT"}, {"symbol": "BTCUSDT&foo=bar"},
    {"symbol": "BTCUSD"}, {"symbol": "btcusdt"},
    {"as_of": datetime(2026, 4, 30)},
])
def test_invalid_arguments_fail_before_network(override):
    provider, calls = client_for([])
    args = {"symbol": "BTCUSDT", "interval": "1d", "as_of": AS_OF, "limit": 95, **override}
    with pytest.raises(RotationError):
        provider.candles(**args)
    assert not calls


def member(base, **changes):
    return {"instId": base + "-USDT", "baseCcy": base, "quoteCcy": "USDT", "instType": "SPOT", "state": "live", **changes}


def ticker(base, volume, **changes):
    return {"instId": base + "-USDT", "instType": "SPOT", "volCcy24h": volume, "vol24h": "999999999", **changes}


def test_discovery_uses_quote_turnover_preserves_benchmarks_and_explicit_exclusions():
    names = ["BTC", "ETH", "SOL", "JUP", "LINK", "BTCUP", "USDC", "INTW", "MISSING", "SUSPENDED"]
    members = [member(base, **({"state": "suspend"} if base == "SUSPENDED" else {})) for base in names]
    tickers = [ticker(base, str(index + 1)) for index, base in enumerate(names) if base != "MISSING"]
    provider, calls = client_for([payload(members), payload(tickers)])
    symbols, exclusions, selection_at = provider.discover(maximum=4)
    assert symbols == ["BTCUSDT", "ETHUSDT", "LINKUSDT", "JUPUSDT"]
    reasons = {entry["symbol"]: entry["reason"] for entry in exclusions}
    assert reasons["BTCUPUSDT"] == reasons["USDCUSDT"] == reasons["INTWUSDT"] == "non_crypto_or_leveraged_asset"
    assert reasons["MISSINGUSDT"] == "missing_or_invalid_quote_volume"
    assert reasons["SUSPENDEDUSDT"] == "not_active_spot"
    assert reasons["SOLUSDT"] == "outside_frozen_volume_budget"
    assert utc(selection_at).tzinfo == timezone.utc
    assert [urlparse(url).path for url in calls] == ["/api/v5/public/instruments", "/api/v5/market/tickers"]
    assert all(parse_qs(urlparse(url).query) == {"instType": ["SPOT"]} for url in calls)


@pytest.mark.parametrize("bad_volume", [None, "", "NaN", "inf", "-1", "0", True])
def test_discovery_does_not_substitute_base_volume_for_missing_quote_turnover(bad_volume):
    provider, _ = client_for([payload([member("BTC")]), payload([ticker("BTC", bad_volume)])])
    with pytest.raises(RotationError, match="no eligible"):
        provider.discover(maximum=2)


def test_discovery_requires_matching_spot_membership_and_ticker_units():
    members = [member("BTC"), member("ETH"), member("SOL", instType="SWAP"), member("LINK", quoteCcy="USDC")]
    tickers = [ticker("BTC", "100"), ticker("ETH", "200", instType="SWAP"), ticker("SOL", "300"), ticker("LINK", "400")]
    provider, _ = client_for([payload(members), payload(tickers)])
    symbols, excluded, _ = provider.discover(maximum=2)
    assert symbols == ["BTCUSDT"]
    assert {item["symbol"] for item in excluded} == {"ETHUSDT", "SOLUSDT"}


@pytest.mark.parametrize("members,tickers,match", [
    ([], [ticker("BTC", "1")], "empty"),
    ([member("BTC")], [], "empty"),
    ([member("BTC"), member("BTC")], [ticker("BTC", "1")], "duplicate"),
    ([member("BTC")], [ticker("BTC", "1"), ticker("BTC", "1")], "duplicate"),
    ([member("BTC", instId="ETH-USDT")], [ticker("BTC", "1")], "inconsistent"),
    ([None], [ticker("BTC", "1")], "schema"),
    ([member("BTC")], [None], "schema"),
])
def test_discovery_rejects_ambiguous_or_changed_source_schema(members, tickers, match):
    provider, _ = client_for([payload(members), payload(tickers)])
    with pytest.raises(RotationError, match=match):
        provider.discover(maximum=2)


@pytest.mark.parametrize("maximum", [0, 1, 201, True, 2.5])
def test_invalid_discovery_budget_never_reads_current_membership(maximum):
    provider, calls = client_for([])
    with pytest.raises(RotationError, match="maximum"):
        provider.discover(maximum=maximum)
    assert not calls


def test_each_discovery_read_is_guarded():
    count = 0

    def guard():
        nonlocal count
        count += 1
        if count == 2:
            raise RotationError("current discovery is no longer permitted")

    provider, calls = client_for([payload([member("BTC")])], guard=guard)
    with pytest.raises(RotationError, match="no longer permitted"):
        provider.discover(maximum=2)
    assert len(calls) == 1


def test_real_http_path_uses_only_public_get_and_hashes_original_bytes(monkeypatch):
    raw = json.dumps(payload(candles(1)), indent=2).encode()
    captured = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, bound):
            assert bound == 8_000_001
            return raw

    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 7
            return Response()

    def opener(handler):
        assert isinstance(handler, _RejectRedirects)
        return Opener()

    monkeypatch.setattr(module, "build_opener", opener)
    provider = OKXProvider(guard=lambda: None, timeout=7, request_spacing=0)
    provider.candles("BTCUSDT", "1d", as_of=AS_OF, limit=1)
    request = captured[0]
    assert request.get_method() == "GET" and request.data is None
    assert set(key.lower() for key, _ in request.header_items()) == {"accept", "user-agent"}
    receipt = provider.receipts[0]
    assert receipt["response_sha256"] == receipt["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["hash_basis"] == "raw_response_bytes" and receipt["raw_persisted"] is False
    assert receipt["endpoint"] == "/api/v5/market/history-candles"
    assert receipt["params"]["bar"] == "1Dutc" and receipt["url"] == request.full_url
    assert utc(receipt["requested_at"]) <= utc(receipt["received_at"])


def test_redirect_and_nonpublic_route_are_rejected():
    with pytest.raises(RotationError, match="redirected"):
        _RejectRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere.example")
    provider, calls = client_for([])
    with pytest.raises(RotationError, match="allowlist"):
        provider._get("/api/v5/account/balance", {})
    assert calls == []

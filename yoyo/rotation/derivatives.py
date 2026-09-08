"""Optional current Binance USD-M diagnostics; no orders or historical joins.

Verified official schema (2026-09-09):
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
Sections: Mark Price, Open Interest, Get Funding Rate Info. premiumIndex uses
``lastFundingRate`` (latest reported rate, not a realized settlement ledger),
``markPrice`` and ``time``; openInterest uses ``openInterest`` and ``time``.
fundingInfo lists symbols with adjusted funding settings; an absent symbol
does not establish an eight-hour interval. It supplies no event timestamp.

Only three fixed GET paths at https://fapi.binance.com are allowed. The caller
owns live-mode authorization and must pass its guard; this module never turns
history into a current request. Guard failures propagate BEFORE each IO.

Clock-quality policy, not execution freshness policy: future event times and
events older than 300 seconds at receipt have their source values masked. A
notional estimate is allowed only when mark/OI events differ by at most 60
seconds. Endpoint observations remain explicitly independent, even then.
OI is the reported base quantity (1000-prefixed bases are not rescaled).
OI * markPrice is USDT quote notional, exposed as an approximate USD diagnostic
with its assumption attached; no USDT/USD exchange rate is obtained. A single
OI snapshot establishes neither its change, direction, nor spot capital flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = "https://fapi.binance.com"
ALLOWED_ENDPOINTS = frozenset({"/fapi/v1/premiumIndex", "/fapi/v1/openInterest", "/fapi/v1/fundingInfo"})
SOURCE_NAME = "Binance USD-M, not spot flow"
DOC_URL = "https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data"
MAX_SOURCE_AGE_SECONDS = 300
MAX_NOTIONAL_EVENT_SKEW_SECONDS = 60
MAX_RESPONSE_BYTES = 1_000_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class _NoRedirect(HTTPRedirectHandler):
    """A redirect cannot expand the endpoint allowlist or trigger hidden IO."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "derivatives redirects are refused", headers, fp)


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "fapi.binance.com"
            or parsed.path not in ALLOWED_ENDPOINTS or parsed.fragment):
        raise ValueError("derivatives request is outside the fixed endpoint allowlist")
    params = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
    if parsed.path == "/fapi/v1/fundingInfo":
        if params:
            raise ValueError("fundingInfo does not accept caller query parameters")
    elif (set(params) != {"symbol"} or len(params["symbol"]) != 1
          or re.fullmatch(r"[A-Z0-9]{1,26}USDT", params["symbol"][0]) is None):
        raise ValueError("derivatives request requires one exact USDT symbol")


def _http_get(url: str) -> object:
    """GET a bounded public JSON response; no redirects, retries or credentials."""
    _validate_url(url)
    request = Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": "fable-rotation-research/1",
    })
    with build_opener(_NoRedirect()).open(request, timeout=8) as response:
        if response.geturl() != url:
            raise ValueError("derivatives response URL differs from the allowed request")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("derivatives response exceeds the byte budget")
        return json.loads(raw)


def _numeric(value, *, nonnegative=False, positive=False) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (nonnegative and result < 0) or (positive and result <= 0):
        return None
    return result


def _integer(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _event_time(value, *, received_at: datetime, kind: str, reasons: list) -> tuple[datetime | None, bool]:
    milliseconds = _integer(value)
    try:
        event = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc) if milliseconds is not None and milliseconds >= 0 else None
    except (OverflowError, OSError, ValueError):
        event = None
    if event is None:
        reasons.append(f"{kind}:missing_or_invalid_event_time")
        return None, False
    age = (received_at - event).total_seconds()
    if age < 0:
        reasons.append(f"{kind}:future_event_time_values_masked")
        return event, False
    if age > MAX_SOURCE_AGE_SECONDS:
        reasons.append(f"{kind}:event_older_than_300_seconds_values_masked")
        return event, False
    return event, True


def fetch_context(symbol: str, *, guard: Callable, transport=None) -> dict:
    """Fetch optional CURRENT diagnostics after caller authorization checks.

    ``transport(url) -> decoded JSON`` is injectable; tests need no HTTP. Each
    request invokes ``guard()`` directly before transport. Guard exceptions
    propagate unchanged; network/JSON/API failures become explicit data gaps.
    Rate limiting and an invalid perpetual symbol stop remaining requests.

    Returned rates are ratios, OI is reported base quantity, prices and quote
    notionals are USDT. ``event_time`` is a per-source dict, never a fabricated
    common timestamp. ``observed_at`` is actual final receipt/attempt time,
    while each sources entry carries its own receipt time and quality status.
    The caller must not pass these current snapshots into a historical score.
    """
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{1,26}USDT", symbol) is None:
        raise ValueError("current derivatives diagnostic requires an exact uppercase USDT symbol")
    if not callable(guard) or (transport is not None and not callable(transport)):
        raise ValueError("guard and optional transport must be callable")
    result = {
        "symbol": symbol, "status": "unavailable", "funding_rate": None,
        "interval_hours": None, "open_interest": None, "mark_price": None,
        "open_interest_usd": None, "open_interest_quote": None,
        "open_interest_unit": "base_asset_units_as_reported",
        "base_asset": symbol[:-4], "quote_currency": "USDT",
        "open_interest_usd_kind": "derived_base_times_mark_price_USDT_approx_USD",
        "open_interest_usd_is_approximate": True,
        "observed_at": None,
        "event_time": {"premium_index": None, "open_interest": None, "funding_info": None},
        "event_time_skew_seconds": None, "synchronized": False,
        "funding_kind": "last_reported_rate", "funding_settlement_time": None,
        "source": SOURCE_NAME, "sources": [],
        "reasons": ["independent_current_endpoint_observations_not_a_synchronized_snapshot",
                    "single_open_interest_snapshot_cannot_infer_change_direction_or_spot_flow",
                    "latest_reported_funding_rate_is_not_a_realized_settlement_record"],
    }
    payloads = {}
    problems = []
    request_fn = _http_get if transport is None else transport
    for kind, endpoint in (("premium_index", "/fapi/v1/premiumIndex"),
                           ("open_interest", "/fapi/v1/openInterest"),
                           ("funding_info", "/fapi/v1/fundingInfo")):
        url = BASE_URL + endpoint
        if kind != "funding_info":
            url += "?" + urlencode({"symbol": symbol})
        _validate_url(url)
        # Keep this outside all provider-error handlers: authorization is not
        # a missing-market-data condition and must abort before any further IO.
        guard()
        error = None
        stop = False
        try:
            payload = request_fn(url)
            if isinstance(payload, dict) and "code" in payload and payload["code"] not in (0, "0"):
                code = _integer(str(payload["code"]).lstrip("-"))
                code = -code if code is not None and str(payload["code"]).startswith("-") else code
                error = f"api_error:{payload['code']}"
                if code == -1121:
                    error = "no_matching_usdm_perpetual_symbol"
                    stop = True
                elif code in {-1003, 418, 429}:
                    error = "rate_limited_no_retry"
                    stop = True
            if error is None:
                payloads[kind] = payload
        except HTTPError as exc:
            error = f"http_{exc.code}"
            if exc.code in (418, 429):
                error += "_rate_limited_no_retry"
                stop = True
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            error = f"request_failed:{type(exc).__name__}"
        received_at = _now()
        source = {"name": SOURCE_NAME, "kind": kind, "url": url,
                  "documentation": DOC_URL, "method": "GET",
                  "observed_at": _iso(received_at), "event_time": None,
                  "status": "unavailable" if error else "received", "raw_persisted": False}
        result["sources"].append(source)
        result["observed_at"] = source["observed_at"]
        if error:
            problems.append(f"{kind}:{error}")
        else:
            source["_received_at"] = received_at
        if stop:
            problems.append("remaining_derivatives_requests_skipped")
            break
    times = {}
    for source in result["sources"]:
        kind = source["kind"]
        received_at = source.pop("_received_at", None)
        if received_at is None:
            continue
        payload = payloads[kind]
        before = len(problems)
        if kind == "funding_info":
            result["reasons"].append("funding_info_has_no_exchange_event_timestamp")
            if not isinstance(payload, list) or any(not isinstance(row, dict) or not isinstance(row.get("symbol"), str) for row in payload):
                problems.append("funding_info:schema_invalid")
            else:
                matches = [row for row in payload if row["symbol"] == symbol]
                if not matches:
                    problems.append("funding_info:interval_not_reported_no_eight_hour_assumption")
                elif len(matches) != 1:
                    problems.append("funding_info:duplicate_symbol_records")
                else:
                    interval = _integer(matches[0].get("fundingIntervalHours"))
                    if interval is None or interval <= 0:
                        problems.append("funding_info:missing_or_invalid_interval_hours")
                    else:
                        result["interval_hours"] = interval
        elif not isinstance(payload, dict) or payload.get("symbol") != symbol:
            problems.append(f"{kind}:schema_or_symbol_mismatch")
        else:
            event, fresh = _event_time(payload.get("time"), received_at=received_at, kind=kind, reasons=problems)
            result["event_time"][kind] = source["event_time"] = _iso(event) if event else None
            times[kind] = event if fresh else None
            if fresh:
                fields = (("funding_rate", "lastFundingRate", {}), ("mark_price", "markPrice", {"positive": True})) if kind == "premium_index" else (("open_interest", "openInterest", {"nonnegative": True}),)
                for target, field, options in fields:
                    number = _numeric(payload.get(field), **options)
                    result[target] = number
                    if number is None:
                        problems.append(f"{kind}:missing_or_invalid_{field}")
        source["status"] = "ok" if len(problems) == before else "partial"
    premium_time, oi_time = times.get("premium_index"), times.get("open_interest")
    if premium_time is not None and oi_time is not None:
        skew = abs((premium_time - oi_time).total_seconds())
        result["event_time_skew_seconds"] = skew
        if skew:
            problems.append("mark_and_open_interest_have_different_exchange_event_times")
        if skew > MAX_NOTIONAL_EVENT_SKEW_SECONDS:
            problems.append("derived_notional_omitted_event_time_skew_exceeds_60_seconds")
        elif result["mark_price"] is not None and result["open_interest"] is not None:
            quote = _numeric(result["mark_price"] * result["open_interest"], nonnegative=True)
            result["open_interest_quote"] = quote
            result["open_interest_usd"] = quote
            if quote is None:
                problems.append("derived_notional_nonfinite")
            else:
                result["reasons"].append("derived_quote_notional_uses_USDT_approximately_one_USD_not_a_verified_FX_rate")
    present = result["funding_rate"] is not None or result["open_interest"] is not None
    result["status"] = ("partial" if problems else "ok") if present else "unavailable"
    result["reasons"] = list(dict.fromkeys(result["reasons"] + problems))
    return result

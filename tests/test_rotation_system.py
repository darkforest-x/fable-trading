"""End-to-end synthetic observer tests; no real market or holdout is read."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from yoyo.contracts.rotation import (EXPERIMENT_ID, RotationConfig, RotationError,
                                    authorize, digest, iso, safe_output, source_identity, utc)
from yoyo.rotation.journal import Journal
from yoyo.rotation.metadata import asset_context
from yoyo.rotation.pipeline import risk_example, run_scan
from yoyo.rotation.providers import BinanceProvider, SyntheticProvider, parse_klines
from yoyo.rotation.server import Observer, create_app

CONFIG = RotationConfig(mode="synthetic_demo")
CATALOG = {"schema_version": 1, "assets": [], "events": [], "reviews": []}


@pytest.fixture
def snapshot():
    return run_scan(CONFIG, catalog=CATALOG)


def test_default_synthetic_pipeline_has_all_three_timeframes(snapshot):
    assert snapshot["status"] == "ok"
    assert snapshot["mode"] == "synthetic_demo"
    assert snapshot["errors"] == []
    assert snapshot["universe"]["observed"] == len(CONFIG.symbols)
    assert len(snapshot["input_hashes"]) == 3 * len(CONFIG.symbols)
    assert all(c["trigger"]["state"] != "insufficient_data" for c in snapshot["candidates"])
    assert all(c["event_risk"] == "unknown" and c["risk"]["status"] == "blocked" for c in snapshot["candidates"])
    assert snapshot["financial_validation"]["net_return"] is None
    assert snapshot["policy"]["execution_eligible"] is False
    json.dumps(snapshot, allow_nan=False)


def test_identical_scan_content_is_idempotent_despite_wall_time(snapshot):
    again = run_scan(CONFIG, catalog=CATALOG)
    assert snapshot["scan_id"] == again["scan_id"]
    assert snapshot["candidates"] == again["candidates"]


def test_live_gate_fails_before_injected_provider_io():
    class Never:
        def candles(self, *args, **kwargs):
            pytest.fail("unauthorized provider was called")
    with pytest.raises(RotationError, match="explicit owner"):
        run_scan(replace(CONFIG, mode="live_observation", as_of="live"), provider=Never())


def test_preholdout_override_is_rejected_before_io():
    with pytest.raises(ValueError, match="holdout"):
        run_scan(CONFIG, as_of="2026-05-04T00:00:00Z")


def test_real_mode_cannot_substitute_synthetic():
    with pytest.raises(RotationError, match="impersonate"):
        run_scan(replace(CONFIG, mode="historical_research"), provider=SyntheticProvider(guard=lambda: None))


def test_current_listing_cannot_select_historical_universe():
    with pytest.raises(RotationError, match="historical universe"):
        replace(CONFIG, universe_mode="exchange_spot")


def test_approval_is_bound_to_config_source_scope_and_time():
    live = replace(CONFIG, mode="live_observation", as_of="live")
    now = utc("2026-09-09T01:00:00Z")
    receipt = {"scope": "bounded_live_observation", "experiment_id": EXPERIMENT_ID,
               "config_hash": live.config_hash, "source_hash": "source",
               "owner_quote": "synthetic test approval only", "reference": "test-fixture-only",
               "approved_at": "2026-09-09T00:00:00Z", "valid_until": "2026-09-09T02:00:00Z",
               "consumption_number": 1}
    policy = authorize(live, now, source_hash="source", receipt=receipt, now=now)
    assert policy["holdout_consumed"] and not policy["execution_eligible"]
    for key, bad in (("source_hash", "other"), ("config_hash", "other"), ("scope", "backtest"),
                     ("owner_quote", ""), ("consumption_number", True), ("valid_until", "2026-09-08T00:00:00Z")):
        with pytest.raises(RotationError):
            authorize(live, now, source_hash="source", receipt={**receipt, key: bad}, now=now)
    with pytest.raises(RotationError, match="historical replay"):
        authorize(live, "2026-09-08T00:00:00Z", source_hash="source", receipt=receipt, now=now)
    with pytest.raises(RotationError, match="expired"):
        authorize(live, "2026-09-09T01:59:59Z", source_hash="source", receipt=receipt,
                  now="2026-09-09T02:03:00Z")


def test_permission_rechecked_after_rate_limit_wait():
    checks, requests = [], []
    def guard():
        checks.append(1)
        if len(checks) == 2:
            raise RotationError("expired while paced")
    provider = BinanceProvider(guard=guard, transport=lambda url: requests.append(url), request_spacing=0)
    with pytest.raises(RotationError, match="expired while paced"):
        provider.candles("BTCUSDT", "1d", as_of=utc(CONFIG.as_of), limit=95)
    assert len(checks) == 2 and requests == []


def test_unsupported_venue_and_insufficient_anchor_warmup_are_rejected():
    with pytest.raises(RotationError, match="venue"):
        replace(CONFIG, venue="silent_fallback")
    with pytest.raises(RotationError, match="coverage"):
        replace(CONFIG, setup_bars=44)


@pytest.mark.parametrize("path", ["data/kline_fetched/rotation", "models/rotation", "forward_log.csv", "output/rotation"])
def test_outputs_cannot_write_other_namespaces(path):
    with pytest.raises(RotationError, match="inside"):
        safe_output(Path(path))


def test_provider_guard_precedes_http_and_requests_closed_end():
    calls = []
    def denied():
        raise RotationError("blocked")
    client = BinanceProvider(guard=denied, transport=lambda url: calls.append(url), request_spacing=0)
    with pytest.raises(RotationError, match="blocked"):
        client.candles("BTCUSDT", "1d", as_of=utc(CONFIG.as_of), limit=95)
    assert calls == []
    stamp = int(utc("2026-04-29T00:00:00Z").timestamp() * 1000)
    payload = [[stamp, "10", "11", "9", "10.5", "20", stamp + 86400000 - 1, "210"]]
    def transport(url):
        calls.append(url)
        return payload
    client = BinanceProvider(guard=lambda: None, transport=transport, request_spacing=0)
    frame = client.candles("BTCUSDT", "1d", as_of=utc(CONFIG.as_of), limit=95)
    assert "endTime=" + str(stamp + 86400000 - 1) in calls[0]
    assert frame.iloc[0].quote_volume == 210 and frame.iloc[0].volume == 20
    assert client.receipts[0]["raw_persisted"] is False


def test_provider_rejects_future_before_parsing_bad_prices():
    stamp = int(utc(CONFIG.as_of).timestamp() * 1000)
    bad = [[stamp, "not numeric", "", "", "", "", stamp + 86400000 - 1, ""]]
    with pytest.raises(RotationError, match="beyond"):
        parse_klines(bad, interval="1d", as_of=utc(CONFIG.as_of))


def test_metadata_is_time_ordered_not_array_order():
    old = {"symbol": "SOLUSDT", "published_at": "2026-01-01T00:00:00Z", "asset_type": "crypto"}
    new = {"symbol": "SOLUSDT", "published_at": "2026-04-01T00:00:00Z", "asset_type": "non_crypto"}
    for rows in ([old, new], [new, old]):
        result = asset_context("SOLUSDT", {**CATALOG, "assets": rows}, as_of=CONFIG.as_of)
        assert result["asset_type"] == "non_crypto" and result["event_risk"] == "block"
    future = {**new, "published_at": "2026-05-01T00:00:00Z"}
    assert asset_context("SOLUSDT", {**CATALOG, "assets": [old, future]}, as_of=CONFIG.as_of)["asset_type"] == "crypto"
    with pytest.raises(RotationError, match="conflicting"):
        asset_context("SOLUSDT", {**CATALOG, "assets": [old, {**old, "asset_type": "non_crypto"}]}, as_of=CONFIG.as_of)


def test_latest_revoked_or_expired_review_cannot_reuse_old_clear():
    old = {"symbol": "SOLUSDT", "reviewed_at": "2026-01-01T00:00:00Z", "valid_until": "2026-05-01T00:00:00Z",
           "reviewer": "synthetic", "sources": ["test://only"], "status": "reviewed"}
    revoked = {**old, "reviewed_at": "2026-04-01T00:00:00Z", "status": "revoked"}
    for rows in ([old, revoked], [revoked, old]):
        assert asset_context("SOLUSDT", {**CATALOG, "reviews": rows}, as_of=CONFIG.as_of)["event_risk"] == "unknown"


def test_future_announcements_are_not_retroactive_and_empty_is_unknown():
    event = {"symbol": "SOLUSDT", "published_at": "2026-05-01T00:00:00Z", "effective_at": "2026-04-01T00:00:00Z",
             "source_url": "https://example.org/test", "title": "test", "severity": "block"}
    result = asset_context("SOLUSDT", {**CATALOG, "events": [event]}, as_of=CONFIG.as_of)
    assert result["events"] == [] and result["event_risk"] == "unknown"


def test_journal_duplicate_old_and_same_cutoff_revision(tmp_path, snapshot):
    journal = Journal(tmp_path, enforce_path=False)
    assert journal.save(snapshot)["inserted"]
    assert journal.save(snapshot) == {"inserted": False, "events_added": 0}
    revised = deepcopy(snapshot)
    revised["scan_id"] = "revised"
    revised["candidates"][0]["status"] = "breakout"
    with pytest.raises(RotationError, match="same-cutoff"):
        journal.save(revised)
    revised["as_of"] = "2026-04-29T00:00:00Z"
    with pytest.raises(RotationError, match="older"):
        journal.save(revised)
    assert journal.events()["total"] == len(snapshot["candidates"])


def test_changed_catalog_does_not_serve_previous_clear_snapshot(tmp_path, snapshot):
    journal = Journal(tmp_path, enforce_path=False)
    journal.save(snapshot)
    observer = Observer(CONFIG, journal=journal, catalog={**CATALOG, "reviews": [{"new": "unreviewed"}]})
    with pytest.raises(RotationError, match="no saved"):
        observer.latest()


def test_risk_examples_account_for_cost_caps_and_unknown_portfolio():
    candidate = {"setup": {"state": "breakout", "entry_reference": 100, "stop_reference": 92},
                 "trigger": {"state": "watch"}, "event_risk": "clear", "liquidity_eligible": True}
    risk = risk_example(candidate, CONFIG, {"state": "supportive"})
    assert risk["status"] == "illustration"
    assert risk["notional_per_10000"] < 625
    assert risk["max_loss_per_10000"] <= 50.01
    assert risk["portfolio_checked"] is False and risk["execution_eligible"] is False
    candidate["event_risk"] = "unknown"
    assert risk_example(candidate, CONFIG, {"state": "supportive"})["status"] == "blocked"


def test_new_event_invalidates_an_older_still_unexpired_review():
    catalog = deepcopy(CATALOG)
    catalog["reviews"] = [{"symbol": "SOLUSDT", "reviewed_at": "2026-04-01T00:00:00Z",
                           "valid_until": "2026-05-01T00:00:00Z", "reviewer": "fixture",
                           "sources": ["https://example.test/review"], "status": "reviewed"}]
    assert asset_context("SOLUSDT", catalog, as_of=CONFIG.as_of)["event_risk"] == "clear"
    catalog["events"] = [{"symbol": "SOLUSDT", "title": "fixture unlock", "severity": "warning",
                           "published_at": "2026-04-29T00:00:00Z", "source_url": "https://example.test/event"}]
    result = asset_context("SOLUSDT", catalog, as_of=CONFIG.as_of)
    assert result["event_risk"] == "unknown"
    assert result["risk_coverage_reason"] == "new_event_after_review_requires_new_review"


def test_discovery_does_not_confuse_jup_with_a_leveraged_token():
    bases = ["BTC", "ETH", "JUP", "BTCUP"]
    info = {"symbols": [{"symbol": b + "USDT", "baseAsset": b, "quoteAsset": "USDT",
                         "status": "TRADING", "isSpotTradingAllowed": True} for b in bases]}
    tickers = [{"symbol": b + "USDT", "quoteVolume": "1000000"} for b in bases]
    provider = BinanceProvider(guard=lambda: None, request_spacing=0,
        transport=lambda url: info if "exchangeInfo" in url else tickers)
    symbols, excluded, _ = provider.discover(maximum=20)
    assert "JUPUSDT" in symbols and "BTCUPUSDT" not in symbols
    assert any(r["symbol"] == "BTCUPUSDT" for r in excluded)


def test_running_observer_separates_updated_catalog_and_rejects_mid_scan_change(tmp_path):
    catalog_path = tmp_path / "events.json"
    catalog_path.write_text(json.dumps(CATALOG))
    observer = Observer(CONFIG, journal=Journal(tmp_path / "runtime", enforce_path=False),
                        catalog=CATALOG, catalog_path=catalog_path)
    first = observer.scan()
    revised = {**CATALOG, "notes": "new evidence fixture"}
    catalog_path.write_text(json.dumps(revised))
    with pytest.raises(RotationError, match="no saved"):
        observer.latest()
    second = observer.scan()
    assert first["catalog_hash"] != second["catalog_hash"]
    def changing(configuration, **kwargs):
        result = run_scan(configuration, **kwargs)
        catalog_path.write_text(json.dumps({**revised, "notes": "changed mid scan"}))
        return result
    observer.scan_function = changing
    with pytest.raises(RotationError, match="changed during"):
        observer.scan()


def test_explicit_okx_factory_is_used_without_fallback(monkeypatch):
    import yoyo.rotation.okx_provider as module
    calls = []
    class Fixture:
        receipts = []
        def __init__(self, *, guard):
            self.inner = SyntheticProvider(guard=guard)
        def candles(self, symbol, interval, **kwargs):
            calls.append((symbol, interval))
            return self.inner.candles(symbol, interval, **kwargs)
    monkeypatch.setattr(module, "OKXProvider", Fixture)
    value = run_scan(replace(CONFIG, mode="historical_research", venue="okx"))
    assert value["venue"] == "okx" and len(calls) == len(CONFIG.symbols) * 3
    assert all(c["derivatives"]["status"] == "not_collected" for c in value["candidates"])


def test_running_process_cannot_claim_a_new_disk_source_identity(monkeypatch):
    import yoyo.rotation.pipeline as module
    monkeypatch.setattr(module, "source_identity", lambda: {"source_hash": "new_disk_version"})
    with pytest.raises(RotationError, match="restart required"):
        run_scan(CONFIG)


def test_auxiliary_derivatives_never_change_candidate_scores(monkeypatch):
    import yoyo.rotation.pipeline as module
    calls = []
    def context(symbol, *, guard):
        guard(); calls.append(symbol)
        return {"status": "ok", "funding_rate": 0.9, "open_interest": 1e20}
    monkeypatch.setattr(module, "fetch_context", context)
    class Fixture:
        receipts = []
        def candles(self, symbol, interval, **kwargs):
            return SyntheticProvider(guard=lambda: None).candles(symbol, interval, **kwargs)
    fixed = utc("2026-09-01T00:00:00Z")
    def observe(limit):
        conf = replace(CONFIG, mode="live_observation", as_of="live", derivative_context_limit=limit)
        receipt = {"scope": "bounded_live_observation", "experiment_id": EXPERIMENT_ID,
                   "config_hash": conf.config_hash, "source_hash": source_identity()["source_hash"],
                   "owner_quote": "synthetic test only", "reference": "test fixture, no real data",
                   "approved_at": "2026-08-31T23:00:00Z", "valid_until": "2026-09-01T01:00:00Z",
                   "consumption_number": 1}
        return run_scan(conf, receipt=receipt, provider=Fixture(), wall_clock=lambda: fixed)
    plain, auxiliary = observe(0), observe(6)
    assert len(calls) == 6
    assert [(c["symbol"], c["score"]) for c in plain["candidates"]] == [(c["symbol"], c["score"]) for c in auxiliary["candidates"]]
    assert all(c["derivatives"]["use"] == "auxiliary_only_not_used_in_score" for c in auxiliary["candidates"])


def test_api_empty_scan_export_failure_retains_snapshot_and_csrf(tmp_path):
    observer = Observer(CONFIG, journal=Journal(tmp_path, enforce_path=False), catalog=CATALOG)
    client = TestClient(create_app(observer))
    assert client.get("/api/snapshot").status_code == 404
    assert client.get("/api/journal").json()["events"] == []
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/status").json()["policy"]["scan_allowed"]
    assert client.post("/api/scan", json={"mode": "live_observation"}).status_code == 422
    assert client.post("/api/scan", json={}, headers={"Origin": "https://example.org"}).status_code == 403
    first = client.post("/api/scan", json={})
    assert first.status_code == 200, first.text
    assert client.get("/api/export.csv").status_code == 200
    event_count = client.get("/api/journal").json()["total"]
    assert client.post("/api/scan", json={}).status_code == 200
    assert client.get("/api/journal").json()["total"] == event_count
    def broken(*args, **kwargs):
        raise RotationError("synthetic provider failure")
    observer.scan_function = broken
    assert client.post("/api/scan", json={}).status_code == 422
    old = client.get("/api/snapshot").json()
    assert old["scan_id"] == first.json()["scan_id"]
    assert old["last_scan_error"] == "synthetic provider failure"
    assert client.get("/api/status").json()["busy"] is False


def test_api_concurrent_scan_is_rejected(tmp_path):
    observer = Observer(CONFIG, journal=Journal(tmp_path, enforce_path=False), catalog=CATALOG)
    observer.lock.acquire()
    try:
        response = TestClient(create_app(observer)).post("/api/scan", json={})
        assert response.status_code == 409
    finally:
        observer.lock.release()


def test_failed_sources_are_partial_not_silent_zero():
    class PartialProvider:
        receipts = []
        def __init__(self):
            self.synthetic = SyntheticProvider(guard=lambda: None)
        def candles(self, symbol, interval, **kwargs):
            if symbol == "SOLUSDT":
                raise RotationError("fixture deliberate gap")
            return self.synthetic.candles(symbol, interval, **kwargs)
    # Injected test adapter, explicitly no actual HTTP, into historical policy.
    result = run_scan(replace(CONFIG, mode="historical_research"), provider=PartialProvider(), catalog=CATALOG)
    assert result["status"] == "partial" and len(result["errors"]) == 3
    sol = next(c for c in result["candidates"] if c["symbol"] == "SOLUSDT")
    assert sol["daily"]["state"] == "insufficient_data" and sol["review_status"] == "blocked"

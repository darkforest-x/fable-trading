"""Once-per-event API charging, queue lifecycle and automatic snapshot evidence."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.automatic import AutomaticReviews
from yoyo.vision_research.server import create_app
from yoyo.vision_research.source import SourceError, chart_sha256
from yoyo.vision_research.store import ResearchStore
from yoyo.vision_research.zhipu import ZhipuClient

NOW = 108_000_100
KEY = "test-only-automatic-key.not-a-real-key"


def signal(identifier="one", offset=0):
    return {"id": identifier, "symbol": "TEST-USDT-SWAP", "timeframe": "15m",
            "timeframe_min": 15, "bar_close_ms": 108_000_000 + offset,
            "side": "long", "price": 101}


class Source:
    def __init__(self):
        self.now = NOW
        self.items = [signal()]
        self.clock = lambda: self.now
        self.warning = ""

    def list_signals(self):
        return {"items": copy.deepcopy(self.items), "warning": self.warning}

    def status(self):
        return {"available": True, "count": len(self.items)}

    def live_chart(self, signal_id, post_signal_bars):
        assert post_signal_bars == 12
        s = next(item for item in self.items if item["id"] == signal_id)
        rows = [{"t": i * 900_000, "o": 100, "h": 102, "l": 99, "c": 101, "is_closed": i < 120,
                 **{f"{ma}{n}": 100.5 for ma in ("sma", "ema") for n in (20, 60, 120)}}
                for i in range(1, 121)]
        self.chart = {"candles": rows, "snapshot_id": "a" * 32, "chart_sha256": chart_sha256(rows),
                      "provenance": {**s, "signal_bar_close_ms": s["bar_close_ms"], "observed_at_ms": self.now,
                                     "time_boundary": "live_observation", "visible_end_ms": self.now,
                                     "bar_count": 120, "last_bar_closed": False,
                                     "recognition_eligible": True, "source_stale": False}}
        return copy.deepcopy(self.chart)

    def chart_snapshot(self, signal_id, snapshot_id):
        assert signal_id == self.chart["provenance"]["id"] and snapshot_id == self.chart["snapshot_id"]
        return copy.deepcopy(self.chart)


def fixture_queue(tmp_path, process=None):
    store, source, calls = ResearchStore(tmp_path), Source(), []
    def run(s, run_id):
        calls.append(s["id"])
        record = {"id": run_id, "created_at": "2026-09-23", "status": "completed",
                  "analysis_scope": "current_right_edge", "decision": {"verdict": "uncertain"},
                  "provenance": {"id": s["id"], "time_boundary": "live_observation", "observed_at_ms": source.now}}
        store.save(record)
        return record
    queue = AutomaticReviews(store, source, process or run, threading.Lock(), lambda: True,
                             enabled_default=True, clock=source.clock)
    return queue, store, source, calls


def test_refresh_and_reordered_candidates_charge_once_per_signal(tmp_path):
    queue, store, source, calls = fixture_queue(tmp_path)
    source.items = [signal(), signal("two", -900_000)]
    queue.discover_once()
    queue.discover_once()
    source.items.reverse()
    queue.discover_once()
    assert queue.snapshot()["counts"]["queued"] == 2
    assert queue.process_once() and queue.process_once()
    assert not queue.process_once()
    queue.discover_once()
    assert not queue.process_once()
    assert sorted(calls) == ["one", "two"]
    assert queue.snapshot()["counts"]["completed"] == 2


def test_expired_future_and_queue_expiry_never_call_provider(tmp_path):
    queue, _, source, calls = fixture_queue(tmp_path)
    source.items = [signal(), signal("expired", -12 * 900_000), signal("future", 900_000)]
    queue.discover_once()
    assert queue.snapshot()["counts"]["queued"] == 1
    assert queue.snapshot()["counts"]["skipped"] == 1
    source.now += 12 * 900_000
    queue.process_once()
    assert not calls
    assert queue.snapshot()["counts"]["skipped"] == 2
    assert all(job["run_id"] is None for job in queue.snapshot()["items"])


def test_pause_persists_and_leaves_pending_jobs_unclaimed(tmp_path):
    queue, store, source, calls = fixture_queue(tmp_path)
    queue.discover_once()
    queue.set_enabled(False)
    assert not queue.process_once()
    other = AutomaticReviews(store, source, queue.process, threading.Lock(), lambda: True,
                             enabled_default=True, clock=source.clock)
    assert not other.snapshot()["enabled"] and not calls
    other.set_enabled(True)
    assert other.process_once() and len(calls) == 1


def test_inference_mutex_and_concurrent_workers_allow_one_claim(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def process(s, rid):
        calls.append(s["id"])
        entered.set()
        assert release.wait(3)
        return {"id": rid, "status": "failed", "error": "test failure"}
    queue, _, _, _ = fixture_queue(tmp_path, process)
    queue.discover_once()
    with queue.inference_lock:
        assert not queue.process_once()
        assert queue.snapshot()["counts"]["queued"] == 1
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(queue.process_once)
        assert entered.wait(3)
        assert not pool.submit(queue.process_once).result(timeout=3)
        queue.set_enabled(False)
        release.set()
        assert first.result(timeout=3)
    assert calls == ["one"]
    assert queue.snapshot()["counts"]["failed"] == 1


def test_restart_interrupted_call_is_not_retried(tmp_path):
    queue, store, source, calls = fixture_queue(tmp_path)
    queue.discover_once()
    job = queue.snapshot()["items"][0]
    job.update(status="running", run_id="unknown")
    with store.connect() as db:
        db.execute("UPDATE automatic_reviews SET status='running',record=?", (json.dumps(job),))
    other = AutomaticReviews(store, source, queue.process, threading.Lock(), lambda: True, clock=source.clock)
    other.discover_once()
    assert other.snapshot()["counts"]["interrupted"] == 1
    assert not other.process_once() and not calls


def test_restart_reconciles_finished_run_without_calling_again(tmp_path):
    queue, store, source, calls = fixture_queue(tmp_path)
    queue.discover_once()
    run = queue.process(signal(), "saved-result")
    job = queue.snapshot()["items"][0]
    job.update(status="running", run_id=run["id"])
    with store.connect() as db:
        db.execute("UPDATE automatic_reviews SET status='running',record=?", (json.dumps(job),))
    other = AutomaticReviews(store, source, queue.process, threading.Lock(), lambda: True, clock=source.clock)
    assert other.snapshot()["counts"]["completed"] == 1
    assert not other.process_once() and calls == ["one"]


def test_existing_manual_current_review_is_reused(tmp_path):
    queue, _, _, calls = fixture_queue(tmp_path)
    run = queue.process(signal(), "manual-run")
    queue.discover_once()
    job = queue.snapshot()["items"][0]
    assert job["run_id"] == run["id"] and job["reused_existing"]
    assert not queue.process_once() and calls == ["one"]


@pytest.mark.parametrize('code', ['payment_required', 'quota_exceeded', 'authentication_failed', 'rate_limited'])
def test_account_or_quota_error_pauses_without_retries(tmp_path, code):
    def process(s, rid):
        return {"id": rid, "status": "failed", "error": "provider failure", "error_details": {"code": code}}
    queue, _, _, _ = fixture_queue(tmp_path, process)
    queue.discover_once()
    queue.process_once()
    assert not queue.snapshot()["enabled"]
    queue.set_enabled(True)
    queue.discover_once()
    assert not queue.process_once()


def test_source_error_has_no_fake_run_and_missing_credentials_do_not_enqueue(tmp_path):
    def process(s, rid):
        raise SourceError("行情过期")
    queue, _, _, _ = fixture_queue(tmp_path, process)
    queue.configured = lambda: False
    queue.discover_once()
    assert not queue.snapshot()["items"] and "API Key" in queue.snapshot()["error"]
    queue.configured = lambda: True
    queue.discover_once()
    queue.process_once()
    assert queue.snapshot()["items"][0]["run_id"] is None
    assert queue.snapshot()["items"][0]["error"] == "行情过期"


def test_automatic_api_uses_frozen_live_image_references_and_raw_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", KEY)
    sent = []
    decision = {"verdict": "uncertain", "side": "unknown", "summary": "仍在密集", "evidence": [],
                "risks": [], "box_2d": None, "assessment_scope": "current_right_edge", "current_state": "converging"}
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "mock-automatic-response", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(decision)}}]})
    app = create_app(tmp_path, source=Source(), seed_defaults=True,
                     provider_factory=lambda api_key, model: ZhipuClient(api_key, model, transport=httpx.MockTransport(respond)))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post('/api/automatic', json={"enabled": True}).status_code == 200
        assert app.state.automatic.process_once()
        job = client.get('/api/automatic').json()["items"][0]
        assert job["status"] == 'completed' and job["decision"] == decision
        assert client.get('/api/signals').json()['items'][0]['ai_review'] == job
        run = client.get('/api/runs/' + job['run_id']).json()
        assert run['automatic'] and run['source'] == 'spike_automatic'
        assert run['analysis_scope'] == 'current_right_edge' and run['provenance']['last_bar_closed'] is False
        assert run['provenance']['pixel_origin'] == 'server_render'
        assert run['provenance']['observed_at_ms'] == NOW
        assert run['reference_revision'] == 1 and len(run['references']) == 5
        assert run['provenance']['chart_sha256'] == chart_sha256(run['provenance']['chart_candles'])
        assert run['training_eligible'] is False and run['production_eligible'] is False
        assert client.get(run['image_url']).headers['content-type'] == 'image/png'
        trace = client.get('/api/exchanges/' + run['api_exchange_id']).json()
        assert len(sent) == 1 and sent[0]['reasoning_effort'] == 'max'
        assert json.loads(trace['request']['body_text']) == sent[0]
        assert KEY not in json.dumps(trace)
        assert client.post('/api/automatic', json={"enabled": 1}).status_code == 400
        assert client.post('/api/automatic', json={"enabled": False}, headers={'Origin':'https://example.com'}).status_code == 403
        assert client.post('/api/automatic', json={"enabled": False}).json()['enabled'] is False


def test_server_lifespan_reviews_without_browser_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", KEY)
    called = threading.Event()
    sent = []

    class Client:
        model = "glm-5.3-flash"

        def analyze(self, **kwargs):
            sent.append(kwargs)
            called.set()
            return {"decision": {"verdict": "uncertain"}, "usage": {}}

        def close(self):
            pass

    app = create_app(tmp_path, source=Source(), automatic_worker=True,
                     provider_factory=lambda **kwargs: Client())
    with TestClient(app):
        assert called.wait(3), "The server must discover and review without any browser requests"
        assert len(sent) == 1
    assert app.state.automatic.snapshot()["counts"]["completed"] == 1
    assert all(not thread.is_alive() for thread in app.state.automatic.threads)


@pytest.mark.parametrize("defect", ["wrong_signal", "stale", "expired_snapshot"])
def test_invalid_automatic_snapshot_never_reaches_provider(tmp_path, monkeypatch, defect):
    monkeypatch.setenv("ZHIPU_API_KEY", KEY)

    class BadSource(Source):
        def live_chart(self, *args):
            chart = super().live_chart(*args)
            if defect == "wrong_signal":
                chart["provenance"]["id"] = "other"
            elif defect == "stale":
                chart["provenance"]["source_stale"] = True
            return chart

        def chart_snapshot(self, *args):
            if defect == "expired_snapshot":
                raise SourceError("快照已过期")
            return super().chart_snapshot(*args)

    def provider(**kwargs):
        pytest.fail("Invalid snapshots must be rejected before creating a model client")

    app = create_app(tmp_path, source=BadSource(), provider_factory=provider)
    queue = app.state.automatic
    queue.set_enabled(True)
    queue.discover_once()
    assert queue.process_once()
    job = queue.snapshot()["items"][0]
    assert job["status"] == "failed" and job["run_id"] is None

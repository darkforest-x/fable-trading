"""Offline API lifecycle, credential, image and review behavior tests."""
import base64
import hashlib
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
import pytest

from yoyo.vision_research.gemini import GeminiError
from yoyo.vision_research.images import image_from_bytes, image_from_data_url
from yoyo.vision_research.server import create_app
from yoyo.vision_research.store import ResearchStore

KEY = "test-secret-never-persist-this-key"


def image_url(color="navy"):
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "camera-owner")
    Image.new("RGB", (80, 60), color).save(output, "PNG", pnginfo=metadata)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


class EmptySource:
    def list_signals(self):
        return {"items": [], "warning": "offline test"}

    def status(self):
        return {"available": False, "count": 0, "warning": "offline test"}


class FakeProvider:
    instances = []
    failure = False

    def __init__(self, api_key, model):
        assert api_key == KEY
        self.model, self.closed = model, False
        self.instances.append(self)

    def close(self):
        self.closed = True

    def check_connection(self):
        return {"ok": True, "model": self.model, "message": "metadata only"}

    def analyze(self, image, references, criteria):
        if self.failure:
            raise GeminiError("quota", "额度暂不可用")
        assert hashlib.sha256(image.data).hexdigest() == image.sha256
        return {"decision": {"verdict": "uncertain", "side": "unknown", "summary": "测试结果",
                             "evidence": ["画面不清"], "risks": [], "box_2d": None},
                "usage": {"total_tokens": 12}, "model": self.model,
                "response_id": "mock-1", "latency_ms": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    FakeProvider.instances, FakeProvider.failure = [], False
    app = create_app(tmp_path, source=EmptySource(), provider_factory=FakeProvider)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def configure(client):
    response = client.post("/api/config", json={"api_key": KEY})
    assert response.status_code == 200
    assert KEY not in response.text


def test_key_required_and_not_stored_or_returned(client, tmp_path):
    assert client.get("/api/status").json()["api_key_configured"] is False
    assert client.post("/api/connection-test", json={}).status_code == 503
    assert not FakeProvider.instances
    configure(client)
    assert client.post("/api/connection-test", json={}).json()["ok"] is True
    assert FakeProvider.instances[-1].closed
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()
    fresh = create_app(tmp_path, source=EmptySource(), provider_factory=FakeProvider)
    with TestClient(fresh, base_url="http://127.0.0.1") as other:
        assert other.get("/api/status").json()["api_key_configured"] is False


def test_completed_run_review_and_export_preserve_original_decision(client):
    configure(client)
    result = client.post("/api/analyze", json={"image_data_url": image_url(), "image_name": "chart.png"})
    assert result.status_code == 200
    run = result.json()
    assert run["status"] == "completed" and FakeProvider.instances[-1].closed
    assert run["provenance"]["time_boundary"] == "unverified_upload"
    assert not run["production_eligible"] and not run["training_eligible"]
    pixels = client.get(run["image_url"]).content
    assert hashlib.sha256(pixels).hexdigest() == run["image_sha256"]
    assert "private" not in Image.open(io.BytesIO(pixels)).info
    for verdict in ("rejected", "accepted"):
        response = client.post(f"/api/runs/{run['id']}/review", json={"verdict": verdict, "note": "人工意见"})
        assert response.status_code == 200
    exported = client.get(f"/api/runs/{run['id']}/export")
    saved = exported.json()
    assert saved["decision"] == run["decision"]
    assert len(saved["review_history"]) == 2
    assert KEY not in exported.text
    assert len(client.get("/api/runs").json()["items"]) == 1


def test_failed_provider_is_recorded_without_retry(client):
    configure(client)
    FakeProvider.failure = True
    run = client.post("/api/analyze", json={"image_data_url": image_url()}).json()
    assert run["status"] == "failed" and run["decision"] is None
    assert len(FakeProvider.instances) == 1 and FakeProvider.instances[0].closed
    assert client.post(f"/api/runs/{run['id']}/review", json={"verdict": "accepted"}).status_code == 409


def test_invalid_or_ambiguous_inputs_never_reach_provider(client):
    configure(client)
    cases = [
        {}, {"image_data_url": image_url(), "signal_id": "other"},
        {"image_data_url": "data:image/png;base64,bm90LWltYWdl"},
        {"image_data_url": image_url(), "references": [{"name": "same", "data_url": image_url()}]},
    ]
    for payload in cases:
        assert client.post("/api/analyze", json=payload).status_code == 400
    assert client.get("/api/runs").json()["items"] == []
    assert all(instance.closed for instance in FakeProvider.instances)


def test_cross_origin_host_and_request_formats_are_rejected(client):
    assert client.post("/api/config", json={"api_key": KEY}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/config", content="bad", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/config", content="x" * 5000, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.get("/").headers["x-frame-options"] == "DENY"


def test_restart_marks_in_flight_result_unknown(tmp_path):
    store = ResearchStore(tmp_path)
    store.save({"id": "test", "created_at": "2026-09-23", "status": "running"})
    restarted = ResearchStore(tmp_path)
    assert restarted.get("test")["status"] == "interrupted"


def test_actual_image_validation_and_metadata_removal():
    image = image_from_data_url(image_url(), "../../secret.png")
    assert image.name == ".._.._secret.png"
    assert not Image.open(io.BytesIO(image.data)).info
    for raw in (b"invalid", b""):
        with pytest.raises(ValueError):
            image_from_bytes(raw)
    with pytest.raises(ValueError):
        image_from_data_url("data:image/svg+xml;base64,PHN2Zz4=")


def test_changed_preview_is_rejected_before_inference(client):
    configure(client)
    response = client.post("/api/analyze", json={"image_data_url": image_url(), "expected_image_sha256": "a" * 64})
    assert response.status_code == 400 and "图表已变化" in response.json()["detail"]
    assert client.get("/api/runs").json()["items"] == []


def test_overlapping_requests_do_not_create_duplicate_paid_calls(client, monkeypatch):
    configure(client)
    entered, release = threading.Event(), threading.Event()
    original = FakeProvider.analyze
    calls = []

    def slow(self, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return original(self, **kwargs)

    monkeypatch.setattr(FakeProvider, "analyze", slow)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, "/api/analyze", json={"image_data_url": image_url()})
        try:
            assert entered.wait(5)
            other = client.post("/api/analyze", json={"image_data_url": image_url()})
            assert other.status_code == 409
        finally:
            release.set()
        assert pending.result().json()["status"] == "completed"
    assert len(calls) == 1 and all(instance.closed for instance in FakeProvider.instances)

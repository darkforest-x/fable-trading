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
from yoyo.vision_research.source import SourceError
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


class CaptureSource(EmptySource):
    chart = {
        "candles": [{"t": 900000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "sma20": None,
                     "ema20": 1.2, "sma60": None, "ema60": None, "sma120": None, "ema120": None}],
        "provenance": {"id": "signal-1", "symbol": "TEST-USDT", "timeframe": "15m",
                       "bar_close_ms": 1800000, "visible_start_ms": 900000,
                       "visible_end_ms": 1800000, "time_boundary": "signal_close", "overlay": "none"},
        "colors": {"candles": {"up": "#3db6a0", "down": "#df6d79"}},
        "chart_sha256": "a" * 64,
    }

    def signal_chart(self, signal_id):
        if signal_id != "signal-1":
            raise SourceError("candidate missing")
        return self.chart

    def signal_image(self, signal_id):
        if signal_id != "signal-1":
            raise SourceError("candidate missing")
        return image_from_data_url(image_url()), self.chart["provenance"]


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
        self.received_references = [(item.name, item.sha256) for item in references]
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


def test_key_is_private_persisted_and_never_returned_or_in_ledger(client, tmp_path):
    assert client.get("/api/status").json()["api_key_configured"] is False
    assert client.post("/api/connection-test", json={}).status_code == 503
    assert not FakeProvider.instances
    configure(client)
    assert client.post("/api/connection-test", json={}).json()["ok"] is True
    assert FakeProvider.instances[-1].closed
    for path in tmp_path.rglob("*"):
        if path.is_file():
            if path == tmp_path / "private" / "settings.json":
                assert path.stat().st_mode & 0o777 == 0o600
                assert json.loads(path.read_text())["api_key"] == KEY
            else:
                assert KEY.encode() not in path.read_bytes()
    fresh = create_app(tmp_path, source=EmptySource(), provider_factory=FakeProvider)
    with TestClient(fresh, base_url="http://127.0.0.1") as other:
        assert other.get("/api/status").json()["api_key_configured"] is True
        assert other.get("/api/status").json()["credential_source"] == "local_config"
        assert other.post("/api/connection-test", json={}).json()["ok"] is True
        assert other.get("/private/settings.json").status_code == 404
        assert KEY not in other.get("/api/runs").text


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


def test_safe_provider_diagnostics_survive_record_and_export(client, monkeypatch):
    configure(client)

    def payment_error(*args, **kwargs):
        raise GeminiError("payment_required", "预付款余额不足（HTTP 402 · payment_required）",
                          http_status=402, provider_code="payment_required")

    monkeypatch.setattr(FakeProvider, "analyze", payment_error)
    monkeypatch.setattr(FakeProvider, "check_connection", payment_error)
    diagnostic = {"code": "payment_required", "http_status": 402, "provider_code": "payment_required"}
    run = client.post("/api/analyze", json={"image_data_url": image_url()}).json()
    assert run["status"] == "failed"
    assert run["error_details"] == diagnostic
    assert run["latency_ms"] is not None and run["latency_ms"] >= 0
    saved = client.get(f"/api/runs/{run['id']}/export")
    assert saved.json()["error_details"] == diagnostic and KEY not in saved.text
    probe = client.post("/api/connection-test", json={})
    assert probe.status_code == 502
    assert probe.json()["error_details"] == diagnostic
    assert "HTTP 402" in probe.json()["message"] and KEY not in probe.text


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


def test_global_references_persist_and_each_run_keeps_its_reference_snapshot(client, tmp_path):
    configure(client)
    assert client.get("/api/references").json() == {"items": [], "revision": 0}
    saved = client.put("/api/references", json={
        "references": [{"name": "setup A", "data_url": image_url("teal")}],
        "expected_revision": 0,
    })
    assert saved.status_code == 200
    snapshot = saved.json()
    assert snapshot["revision"] == 1 and snapshot["items"][0]["name"] == "setup A"
    assert client.get(snapshot["items"][0]["image_url"]).status_code == 200

    fresh = create_app(tmp_path, source=EmptySource(), provider_factory=FakeProvider)
    with TestClient(fresh, base_url="http://127.0.0.1") as restarted:
        assert restarted.get("/api/references").json() == snapshot

    run = client.post("/api/analyze", json={
        "image_data_url": image_url("white"), "reference_revision": 1,
    })
    assert run.status_code == 200
    run = run.json()
    assert run["reference_source"] == "global" and run["reference_revision"] == 1
    assert run["references"] == snapshot["items"]
    assert FakeProvider.instances[-1].received_references == [("setup A", snapshot["items"][0]["sha256"])]

    explicit_none = client.post("/api/analyze", json={
        "image_data_url": image_url("black"), "references": [], "reference_revision": None,
    })
    assert explicit_none.status_code == 200
    assert explicit_none.json()["reference_source"] == "request"
    assert explicit_none.json()["references"] == []
    assert FakeProvider.instances[-1].received_references == []

    cleared = client.put("/api/references", json={"references": [], "expected_revision": 1})
    assert cleared.status_code == 200 and cleared.json() == {"items": [], "revision": 2}
    assert client.get(f"/api/runs/{run['id']}").json()["references"] == snapshot["items"]
    assert client.put("/api/references", json={"references": [], "expected_revision": 1}).status_code == 409


def test_reference_duplicates_and_stale_analyze_revision_are_rejected(client):
    configure(client)
    duplicate = image_url("purple")
    response = client.put("/api/references", json={"references": [
        {"name": "same", "data_url": duplicate}, {"name": "same", "data_url": image_url("orange")},
    ]})
    assert response.status_code == 400
    assert client.get("/api/references").json() == {"items": [], "revision": 0}
    assert client.put("/api/references", json={"references": [
        {"name": "one", "data_url": duplicate}, {"name": "two", "data_url": duplicate},
    ]}).status_code == 400
    assert client.put("/api/references", json={
        "references": [{"name": "one", "data_url": duplicate}], "expected_revision": 0,
    }).status_code == 200

    stale = client.post("/api/analyze", json={
        "image_data_url": image_url(), "reference_revision": 0,
    })
    assert stale.status_code == 409
    assert client.get("/api/runs").json()["items"] == []


def test_more_than_four_global_references_are_retained_and_used(client):
    configure(client)
    colors = ["red", "green", "blue", "yellow", "purple", "orange"]
    saved = client.put("/api/references", json={"references": [
        {"name": color, "data_url": image_url(color)} for color in colors
    ], "expected_revision": 0})
    assert saved.status_code == 200
    run = client.post("/api/analyze", json={"image_data_url": image_url("white"),
                                           "reference_revision": saved.json()["revision"]})
    assert run.status_code == 200
    assert [item["name"] for item in run.json()["references"]] == colors
    assert len(FakeProvider.instances[-1].received_references) == 6


def test_failed_private_settings_write_preserves_the_previous_key(client, monkeypatch, tmp_path):
    from yoyo.vision_research.settings import LocalSettings
    configure(client)
    before = (tmp_path / "private" / "settings.json").read_bytes()
    def fail_save(self, api_key, model):
        raise RuntimeError("本机模型配置未能保存，请检查目录权限")
    monkeypatch.setattr(LocalSettings, "save", fail_save)
    response = client.post("/api/config", json={"api_key": "replacement-secret-value-123456", "model": "gemini-test"})
    assert response.status_code == 500
    assert "replacement-secret" not in response.text
    assert (tmp_path / "private" / "settings.json").read_bytes() == before
    assert client.post("/api/connection-test", json={}).json()["ok"] is True


def test_browser_chart_capture_is_tied_to_the_causal_chart_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    source = CaptureSource()
    app = create_app(tmp_path, source=source, provider_factory=FakeProvider)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        configure(client)
        chart = client.get("/api/signals/signal-1/chart")
        assert chart.status_code == 200 and chart.json() == source.chart
        response = client.post("/api/analyze", json={
            "signal_id": "signal-1", "chart_capture_data_url": image_url("gold"),
            "expected_chart_sha256": "a" * 64,
        })
        assert response.status_code == 200
        run = response.json()
        assert run["source"] == "spike_capture"
        assert run["provenance"]["render_version"] == "tradingview-lightweight-charts-4.2.0"
        assert run["provenance"]["pixel_origin"] == "browser_capture"
        assert run["provenance"]["pixel_attestation"] == "unverified"
        assert run["provenance"]["capture_pixels_attested_to_ohlc"] is False
        assert run["provenance"]["chart_sha256"] == "a" * 64
        assert run["provenance"]["chart_candles"] == source.chart["candles"]
        clients_before_stale = len(FakeProvider.instances)
        assert client.post("/api/analyze", json={
            "signal_id": "signal-1", "chart_capture_data_url": image_url("gold"),
            "expected_chart_sha256": "b" * 64,
        }).status_code == 409
        assert len(FakeProvider.instances) == clients_before_stale
        assert len(client.get("/api/runs").json()["items"]) == 1


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

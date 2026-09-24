"""Actual HTTP bodies survive parsing failures, without leaking credentials."""

import base64
import io
import json

import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from yoyo.vision_research.server import create_app
from yoyo.vision_research.store import ResearchStore
from yoyo.vision_research.zhipu import ZhipuClient

KEY = "exchange-test-key.private-secret"


class EmptySource:
    def status(self):
        return {"available": False, "count": 0}


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (80, 60), "navy").save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def make_client(tmp_path, handler, seed=False):
    app = create_app(tmp_path, source=EmptySource(), seed_defaults=seed,
                     provider_factory=lambda api_key, model: ZhipuClient(
                         api_key, model, transport=httpx.MockTransport(handler)))
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/config", json={"api_key": KEY}).status_code == 200
    return client


def raw_response():
    decision = {"verdict": "uncertain", "side": "unknown", "summary": "可见证据不足",
                "evidence": [], "risks": [], "box_2d": None,
                "assessment_scope": "current_right_edge", "current_state": "unclear"}
    return json.dumps({"id": "original-response-id", "model": "glm-5.3-flash",
                       "choices": [{"finish_reason": "stop", "message": {
                           "role": "assistant", "content": json.dumps(decision, ensure_ascii=False),
                           "reasoning_content": "provider-supplied-reasoning", "extra": "original-field",
                       }}], "usage": {"prompt_tokens": 100, "completion_tokens": 9,
                                      "total_tokens": 109, "prompt_tokens_details": {"cached_tokens": 12}}},
                      ensure_ascii=False, indent=3) + "\n"


def test_request_and_response_are_exact_and_only_detail_loads_images(tmp_path):
    seen = []
    response_text = raw_response()
    def respond(request):
        seen.append(request)
        return httpx.Response(200, text=response_text)
    with make_client(tmp_path, respond, seed=True) as client:
        run = client.post("/api/analyze", json={"image_data_url": picture()}).json()
        assert run["status"] == "completed"
        exchange_id = run["api_exchange_id"]
        detail = client.get(f"/api/exchanges/{exchange_id}").json()
        assert detail["request"]["body_text"] == seen[0].content.decode()
        assert detail["response"]["body_text"] == response_text
        assert detail["http_status"] == 200 and detail["status"] == "completed"
        assert detail["image_count"] == 6 and detail["run_id"] == run["id"]
        assert detail["kind"] == "recognition" and not detail["redacted"]
        assert len(seen) == 1
        assert KEY not in json.dumps(detail) and "headers" not in detail["request"]
        listing = client.get("/api/exchanges").json()["items"]
        assert len(listing) == 1 and listing[0]["id"] == exchange_id
        assert "request" not in listing[0] and "base64" not in json.dumps(listing)
        assert "base64" not in client.get("/api/runs").text
        exported = client.get(f"/api/exchanges/{exchange_id}/export")
        assert exported.json() == detail and "attachment" in exported.headers["content-disposition"]
        assert client.get("/api/exchanges/unknown").status_code == 404
        before_run = client.get(f"/api/runs/{run['id']}/export").json()
    reopened = ResearchStore(tmp_path)
    assert reopened.get_exchange(exchange_id) == detail
    assert reopened.get(run["id"]) == before_run


@pytest.mark.parametrize(("status", "body", "expected"), [
    (402, '{"error":{"code":"1113","message":"original billing detail"}}', "payment_required"),
    (200, '{not JSON\n', "invalid_response"),
    (200, '{"id":"test","choices":[{"finish_reason":"stop","message":{"content":"bad decision"}}]}', "invalid_json"),
])
def test_failed_requests_keep_the_original_response_before_validation(tmp_path, status, body, expected):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(status, text=body)
    with make_client(tmp_path, respond) as client:
        run = client.post("/api/analyze", json={"image_data_url": picture()}).json()
        assert run["status"] == "failed" and run["error_details"]["code"] == expected
        detail = client.get(f"/api/exchanges/{run['api_exchange_id']}").json()
        assert detail["response"]["body_text"] == body
        assert detail["http_status"] == status and detail["status"] == "failed"
        assert len(seen) == 1


def test_connection_test_redacts_echoed_credentials_but_keeps_error_body(tmp_path):
    response_body = '{"error":{"code":"1000","message":"echo ' + KEY + '"}}'
    with make_client(tmp_path, lambda request: httpx.Response(401, text=response_body)) as client:
        result = client.post("/api/connection-test", json={})
        assert result.status_code == 502
        detail = client.get(f"/api/exchanges/{result.json()['api_exchange_id']}").json()
        assert detail["kind"] == "connection_test" and detail["run_id"] is None
        assert detail["image_count"] == 0 and detail["redacted"]
        assert json.loads(detail["request"]["body_text"])["messages"][0]["content"] == "请只回复 OK。"
        assert detail["response"]["body_text"] == response_body.replace(KEY, "[API_KEY_REDACTED]")
        assert KEY not in json.dumps(detail)


def test_timeout_has_input_and_no_invented_response(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("secret transport diagnostic " + KEY)
    with make_client(tmp_path, timeout) as client:
        result = client.post("/api/connection-test", json={}).json()
        detail = client.get(f"/api/exchanges/{result['api_exchange_id']}").json()
        assert detail["request"]["body_text"]
        assert detail["response"] is None and detail["http_status"] is None
        assert detail["status"] == "failed" and detail["error"]
        assert KEY not in json.dumps(detail)


def test_recognition_timeout_keeps_phase_and_sent_input_after_restart(tmp_path):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret transport diagnostic " + KEY)

    with make_client(tmp_path, timeout) as client:
        run = client.post("/api/analyze", json={"image_data_url": picture()}).json()
        assert run["status"] == "failed"
        assert run["error_details"] == {
            "code": "timeout", "http_status": None, "provider_code": None, "timeout_phase": "read",
        }
        assert "连续 300 秒未收到数据" in run["error"]
        assert len(calls) == 1

    reopened = ResearchStore(tmp_path)
    assert reopened.get(run["id"]) == run
    detail = reopened.get_exchange(run["api_exchange_id"])
    assert detail["status"] == "failed" and detail["error"] == run["error"]
    assert detail["request"]["body_text"] == calls[0].content.decode()
    assert detail["response"] is None
    assert KEY not in json.dumps(detail) and KEY not in json.dumps(run)


def test_reasoning_truncation_diagnostics_and_original_response_survive_restart(tmp_path):
    payload = json.loads(raw_response())
    payload["choices"][0].update(finish_reason="length")
    payload["choices"][0]["message"]["content"] = ""
    payload["usage"] = {"completion_tokens": 8192, "completion_tokens_details": {"reasoning_tokens": 8190}}
    raw = json.dumps(payload)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, text=raw)

    with make_client(tmp_path, respond) as client:
        run = client.post("/api/analyze", json={"image_data_url": picture()}).json()
        assert run["status"] == "failed" and run["decision"] is None
        assert run["error_details"]["output"]["reasoning_tokens"] == 8190
        assert "尚未输出最终 JSON" in run["error"]
    reopened = ResearchStore(tmp_path)
    assert reopened.get(run["id"])["error_details"] == run["error_details"]
    assert reopened.get_exchange(run["api_exchange_id"])["response"]["body_text"] == raw
    assert len(calls) == 1


def test_restart_keeps_legacy_runs_untouched_and_marks_pending_trace(tmp_path):
    store = ResearchStore(tmp_path)
    legacy = {"id": "legacy", "created_at": "2026-09-23", "status": "completed", "decision": {"summary": "old"}}
    store.save(legacy)
    trace = {"id": "a" * 32, "created_at": "2026-09-23", "status": "running",
             "request": {"body_text": "original body"}, "response": None}
    store.save_exchange(trace)
    reopened = ResearchStore(tmp_path)
    assert reopened.get("legacy") == legacy
    detail = reopened.get_exchange("a" * 32)
    assert detail["status"] == "interrupted" and detail["request"] == trace["request"]
    assert reopened.list_exchanges()[0]["status"] == "interrupted"

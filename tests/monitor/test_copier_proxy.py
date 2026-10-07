"""The workbench forwards a fixed set of copier routes and never lets the public
gateway turn trading back on."""
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from yoyo.monitor.copier_proxy import check_route, install

LOCAL = {"Origin": "http://127.0.0.1:8766", "Sec-Fetch-Site": "same-origin"}
PUBLIC = {**LOCAL, "X-Fable-Gateway": "public"}


@pytest.fixture
def proxy():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    app = FastAPI()
    install(app, transport=httpx.MockTransport(handler))
    client = TestClient(app, base_url="http://127.0.0.1:8766")
    client.seen = seen
    return client


def test_reads_are_forwarded_to_the_copier_api(proxy):
    response = proxy.get("/api/copier/dashboard/orders-card")
    assert response.status_code == 200
    assert proxy.seen[-1].url == httpx.URL("http://127.0.0.1:8080/api/dashboard/orders-card")


def test_query_string_is_kept(proxy):
    proxy.get("/api/copier/messages?intent=open&per_page=8")
    assert proxy.seen[-1].url.params["intent"] == "open"


@pytest.mark.parametrize("path", ["messages/ingest", "control/anything", "orders"])
def test_unlisted_writes_are_not_proxied(proxy, path):
    assert proxy.post(f"/api/copier/{path}", json={}, headers=LOCAL).status_code == 404
    assert not proxy.seen


@pytest.mark.parametrize("method, path", [("GET", "../health"), ("POST", "settings/../control/dry-run"),
                                          ("GET", "dashboard//stats"), ("GET", "ingest%2F..")])
def test_route_check_rejects_raw_traversal(method, path):
    # HTTP clients normalise dot segments before sending; check the guard itself.
    with pytest.raises(HTTPException) as caught:
        check_route(method, path)
    assert caught.value.status_code == 404


def test_signal_injection_is_never_proxied_even_locally(proxy):
    response = proxy.post("/api/copier/messages/ingest", json={"content": "BTC long", "external_id": "x"}, headers=LOCAL)
    assert response.status_code == 404
    assert not proxy.seen


def test_foreign_origin_cannot_write(proxy):
    response = proxy.post("/api/copier/control/dry-run", json={"enabled": False},
                          headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert not proxy.seen


def test_local_write_is_forwarded_as_the_copier_origin(proxy):
    response = proxy.post("/api/copier/control/dry-run", json={"enabled": False}, headers=LOCAL)
    assert response.status_code == 200
    sent = proxy.seen[-1]
    assert sent.headers["origin"] == "http://127.0.0.1:8080"
    assert json.loads(sent.content) == {"enabled": False}


@pytest.mark.parametrize("path, body", [("control/dry-run", {"enabled": True}), ("control/kill-switch", {"enabled": True})])
def test_public_gateway_may_stop_trading(proxy, path, body):
    assert proxy.post(f"/api/copier/{path}", json=body, headers=PUBLIC).status_code == 200


@pytest.mark.parametrize("method, path, body", [
    ("POST", "control/dry-run", {"enabled": False}),
    ("POST", "control/kill-switch", {"enabled": False}),
    ("PUT", "settings", {"risk": {"position_pct": 1}}),
    ("PUT", "monitor/switches", {"features": {"okx_execute": True}}),
])
def test_public_gateway_cannot_resume_or_reconfigure(proxy, method, path, body):
    response = proxy.request(method, f"/api/copier/{path}", json=body, headers=PUBLIC)
    assert response.status_code == 403
    assert not proxy.seen


def test_public_gateway_may_read(proxy):
    assert proxy.get("/api/copier/settings", headers={"X-Fable-Gateway": "public"}).status_code == 200


def test_service_down_is_reported_as_503():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    app = FastAPI()
    install(app, transport=httpx.MockTransport(handler))
    response = TestClient(app, base_url="http://127.0.0.1:8766").get("/api/copier/health")
    assert response.status_code == 503

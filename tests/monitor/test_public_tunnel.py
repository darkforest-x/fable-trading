"""Exercise the actual Caddy gate against an isolated HTTP upstream, no tunnel."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import threading
import time

import httpx
import pytest

from yoyo.monitor.public_tunnel import binary, caddy_config


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    try:
        caddy = binary("caddy")
    except RuntimeError:
        pytest.skip("Install Caddy to run the real gateway integration tests")
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            seen.append(dict(method=self.command, path=self.path, headers=dict(self.headers)))
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{upstream.server_port}/static/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(seen[-1]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.do_GET()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    runtime = tmp_path_factory.mktemp("public-gateway")
    config = runtime / "Caddyfile"
    config.write_text(caddy_config(port=port, upstream_port=upstream.server_port))
    with (runtime / "log").open("wb") as log:
        process = subprocess.Popen([caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
                                   stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5,
                              headers={"Host": "test.trycloudflare.com", "X-Forwarded-Proto": "https"}) as client:
                for _ in range(60):
                    try:
                        if client.get("/").status_code == 200:
                            break
                    except httpx.ConnectError:
                        time.sleep(0.05)
                else:
                    pytest.fail("Anonymous gateway failed to start")
                yield client, seen, upstream.server_port
        finally:
            process.terminate()
            process.wait(timeout=10)
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=2)


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/api/status", "/api/research/manual",
                                  "/api/research/datasets/example/file?path=private.csv",
                                  "/api/vision/exchanges/example/export"])
def test_workspace_surfaces_are_available_without_login(gateway, path):
    client, seen, _ = gateway
    before = len(seen)
    response = client.get(path)
    assert response.status_code == 200
    assert len(seen) == before + 1


def test_reads_ignore_stale_auth_and_never_forward_credentials(gateway):
    client, _, port = gateway
    response = client.get("/api/vision/status?x=1", headers={
        # This is an expired Basic credential. It must not prompt for login or reach the app.
        "Authorization": "Basic c3Bpa2U6ZXhwaXJlZA==",
        "Cookie": "private=secret", "Forwarded": "host=evil.invalid;proto=https",
        "X-Forwarded-Host": "evil.invalid", "X-Forwarded-For": "192.0.2.1"})
    assert response.status_code == 200
    assert "www-authenticate" not in {key.lower() for key in response.headers}
    headers = {k.lower(): v for k, v in response.json()["headers"].items()}
    assert headers["host"] == f"127.0.0.1:{port}"
    assert headers["origin"] == f"http://127.0.0.1:{port}"
    assert not any(key.startswith("x-forwarded-") for key in headers)
    assert not {"authorization", "proxy-authorization", "cookie", "forwarded"} & headers.keys()
    assert "192.0.2.1" not in headers.get("x-forwarded-for", "")
    assert response.json()["path"] == "/api/vision/status?x=1"
    redirected = client.get("/redirect")
    assert redirected.headers["location"] == "https://test.trycloudflare.com/static/"


@pytest.mark.parametrize("headers", [{}, {"Origin": "null"}, {"Origin": "https://evil.invalid"},
    {"Origin": "https://test.trycloudflare.com", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://test.trycloudflare.com"},
    {"Origin": "https://evil.invalid", "X-Forwarded-Host": "evil.invalid"}])
def test_foreign_mutation_is_rejected_before_origin_rewriting(gateway, headers):
    client, seen, _ = gateway
    before = len(seen)
    assert client.post("/api/research/manual/principles", headers=headers, json={}).status_code == 403
    assert len(seen) == before


def test_anonymous_same_origin_writes_keep_existing_api_contract(gateway):
    client, _, port = gateway
    response = client.post("/api/research/manual/principles",
                           headers={"Origin": "https://test.trycloudflare.com", "Sec-Fetch-Site": "same-origin"},
                           json={"test": True})
    assert response.status_code == 200
    assert response.json()["method"] == "POST"
    headers = {k.lower(): v for k, v in response.json()["headers"].items()}
    assert headers["origin"] == f"http://127.0.0.1:{port}"
    assert headers["sec-fetch-site"] == "same-origin"


def test_plain_http_invalid_host_and_desktop_actions_are_blocked(gateway):
    client, seen, _ = gateway
    before = len(seen)
    assert client.get("/", headers={"X-Forwarded-Proto": "http"}).status_code == 400
    assert client.get("/", headers={"Host": "evil.invalid"}).status_code == 421
    for path in ("/api/tradingview/open", "/api/tradingview/open/"):
        assert client.post(path, headers={"Origin": "https://test.trycloudflare.com"}).status_code == 403
    assert len(seen) == before

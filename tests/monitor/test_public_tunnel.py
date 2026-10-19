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


@pytest.fixture(scope="module", params=["test.trycloudflare.com", "paper.ngrok-free.dev", "workspace.example.com"])
def gateway(tmp_path_factory, request):
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
    public_url = None if request.param.endswith(".trycloudflare.com") else "https://" + request.param
    config.write_text(caddy_config(port=port, upstream_port=upstream.server_port, public_url=public_url))
    with (runtime / "log").open("wb") as log:
        process = subprocess.Popen([caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
                                   stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5,
                              headers={"Host": request.param, "X-Forwarded-Proto": "https"}) as client:
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
    assert redirected.headers["location"] == f"https://{client.headers['host']}/static/"


@pytest.mark.parametrize("headers", [{}, {"Origin": "null"}, {"Origin": "https://evil.invalid"},
    {"Origin": "https://test.trycloudflare.com", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://test.trycloudflare.com"},
    {"Origin": "https://evil.invalid", "X-Forwarded-Host": "evil.invalid"}])
def test_foreign_mutation_is_rejected_before_origin_rewriting(gateway, headers):
    client, seen, _ = gateway
    headers = {key: value.replace("test.trycloudflare.com", client.headers["host"]) for key, value in headers.items()}
    before = len(seen)
    assert client.post("/api/research/manual/principles", headers=headers, json={}).status_code == 403
    assert len(seen) == before


def test_anonymous_same_origin_writes_keep_existing_api_contract(gateway):
    client, _, port = gateway
    response = client.post("/api/research/manual/principles",
                           headers={"Origin": f"https://{client.headers['host']}", "Sec-Fetch-Site": "same-origin"},
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
    assert client.get("/", headers={"Host": "other.ngrok-free.dev"}).status_code == 421
    for path in ("/api/tradingview/open", "/api/tradingview/open/"):
        assert client.post(path, headers={"Origin": f"https://{client.headers['host']}"}).status_code == 403
    assert len(seen) == before


@pytest.mark.parametrize("url", ["http://paper.ngrok-free.dev", "https://*.ngrok-free.dev",
    "https://paper.ngrok-free.dev/", "https://paper.ngrok-free.dev:443", "https://a@paper.ngrok-free.dev",
    "https://paper.ngrok-free.dev\nrespond 200", "https://-paper.ngrok-free.dev"])
def test_fixed_host_rejects_unsafe_config_values(url):
    with pytest.raises(ValueError):
        caddy_config(public_url=url)


@pytest.mark.parametrize("reachable", [False, True])
def test_fixed_migration_retires_old_endpoint_only_after_public_health_check(monkeypatch, tmp_path, reachable):
    from yoyo.monitor import public_tunnel as tunnel
    removed = []
    monkeypatch.setattr(tunnel, "RUNTIME", tmp_path)
    monkeypatch.setattr(tunnel, "binary", lambda _: "/fake/ngrok")
    monkeypatch.setattr(tunnel, "start_gateway", lambda **_: None)
    monkeypatch.setattr(tunnel, "start_job", lambda *_: None)
    monkeypatch.setattr(tunnel, "public_ready", lambda _: reachable)
    monkeypatch.setattr(tunnel.time, "sleep", lambda _: None)
    monkeypatch.setattr(tunnel, "remove_job", removed.append)
    monkeypatch.setattr(tunnel, "status", lambda: {})
    if reachable:
        tunnel.start_fixed("https://paper.ngrok-free.dev")
        assert tunnel.saved_fixed_url() == "https://paper.ngrok-free.dev"
        assert removed == [tunnel.LABELS[1]]
    else:
        with pytest.raises(RuntimeError, match="previous tunnel retained"):
            tunnel.start_fixed("https://paper.ngrok-free.dev")
        assert tunnel.saved_fixed_url() is None
        assert not removed


@pytest.mark.parametrize("reachable", [False, True])
def test_named_tunnel_waits_for_public_dns_before_retiring_fallback(monkeypatch, tmp_path, reachable):
    from yoyo.monitor import named_tunnel, public_tunnel as tunnel
    tunnel_id = "f2c1b564-afd7-4e38-9081-54764ec40714"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"TunnelID": tunnel_id, "AccountTag": "test-account", "TunnelSecret": "test-secret"}))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    old = {"provider": "ngrok", "url": "https://old.ngrok-free.dev"}
    (runtime / "fixed-endpoint.json").write_text(json.dumps(old))
    (runtime / "access.txt").write_text("working fallback")
    removed, jobs, gates = [], [], []
    monkeypatch.setattr(tunnel, "RUNTIME", runtime)
    monkeypatch.setattr(tunnel, "binary", lambda _: "/fake/cloudflared")
    monkeypatch.setattr(tunnel, "start_gateway", lambda **kw: gates.append(kw))
    monkeypatch.setattr(tunnel, "start_job", lambda label, args: jobs.append((label, args)))
    monkeypatch.setattr(tunnel, "public_ready", lambda _: reachable)
    monkeypatch.setattr(tunnel, "installed", lambda _: True)
    monkeypatch.setattr(tunnel, "gateway_ready", lambda: True)
    monkeypatch.setattr(tunnel, "remove_job", removed.append)
    monkeypatch.setattr(named_tunnel.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    result = named_tunnel.start("https://workspace.example.com", tunnel_id, source)
    assert gates[0]["additional_public_urls"] == (old["url"],)
    assert jobs[0][0] == tunnel.NAMED_LABEL
    assert "test-secret" not in " ".join(jobs[0][1])
    assert (runtime / f"cloudflare-{tunnel_id}.json").stat().st_mode & 0o777 == 0o600
    assert result["public_ready"] is reachable
    assert result["pending_activation"] is not reachable
    if reachable:
        assert removed == [tunnel.LABELS[1], tunnel.FIXED_LABEL]
        assert tunnel.saved_fixed_url() == "https://workspace.example.com"
        assert "working fallback" not in (runtime / "access.txt").read_text()
    else:
        assert not removed
        assert json.loads((runtime / "fixed-endpoint.json").read_text()) == old
        assert (runtime / "access.txt").read_text() == "working fallback"
    # A normal restart uses the saved named provider, never accidentally ngrok.
    removed.clear()
    assert tunnel.start()["provider"] == "cloudflare_named"
    assert tunnel.status()["url"] == "https://workspace.example.com"


def test_named_tunnel_rejects_credentials_for_another_tunnel_before_mutation(monkeypatch, tmp_path):
    from yoyo.monitor import named_tunnel, public_tunnel as tunnel
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"TunnelID": "f2c1b564-afd7-4e38-9081-54764ec40714",
                                  "AccountTag": "test", "TunnelSecret": "test"}))
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(tunnel, "RUNTIME", runtime)
    with pytest.raises(ValueError, match="do not match"):
        named_tunnel.start("https://workspace.example.com", "7a3754ce-7a91-49fd-a189-29091626c535", source)
    assert not runtime.exists()


def test_named_ingress_matches_only_the_configured_host(tmp_path):
    from yoyo.monitor import named_tunnel
    try:
        cloudflared = binary("cloudflared")
    except RuntimeError:
        pytest.skip("Install cloudflared for actual ingress validation")
    cfg = tmp_path / "named.yml"
    cfg.write_text(named_tunnel.config("https://workspace.example.com",
                   "f2c1b564-afd7-4e38-9081-54764ec40714", tmp_path / "credentials.json"))
    base = [cloudflared, "tunnel", "--config", str(cfg), "ingress"]
    subprocess.run(base + ["validate"], check=True, capture_output=True, timeout=10)
    matched = subprocess.run(base + ["rule", "https://workspace.example.com/api/status"],
                             check=True, capture_output=True, text=True, timeout=10)
    rejected = subprocess.run(base + ["rule", "https://other.example.com/api/status"],
                              check=True, capture_output=True, text=True, timeout=10)
    assert "http://127.0.0.1:8780" in matched.stdout + matched.stderr
    assert "http_status:404" in rejected.stdout + rejected.stderr

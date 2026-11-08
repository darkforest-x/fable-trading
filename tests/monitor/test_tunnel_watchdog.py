"""Exercise outage classification and bounded recovery without production writes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import threading

import pytest

from yoyo.monitor import tunnel_watchdog as guard


@pytest.fixture
def origin():
    replies = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            code, body, cloudflare = replies[-1]
            self.send_response_only(code)
            self.send_header("Server", "cloudflare" if cloudflare else "test")
            if cloudflare:
                self.send_header("CF-Ray", "test-ray")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/status", replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("code,body,cf,expected", [
    (200, b'{"service":"spike Impulse Monitor"}', False, "healthy"),
    (200, b'{"service":"different"}', False, "http_200"),
    (200, b'[]', False, "http_200"),
    (530, b'error code: 1033', True, "cloudflare_1033"),
    (530, b'<title>Error 1033</title>', True, "cloudflare_1033"),
    (530, b'<span class="cf-error-code">1033</span>', True, "cloudflare_1033"),
    (530, b'error code: 1033', False, "http_530"),
    (530, b'error code: 1036', True, "http_530"),
    (403, b'Error 1033', True, "http_403"),
    (502, b'Bad Gateway', True, "http_502"),
])
def test_real_http_response_classification(origin, code, body, cf, expected):
    url, replies = origin
    replies.append((code, body, cf))
    assert guard.probe(url) == expected


def tick(previous=None, now=10000, **changes):
    args = dict(previous=previous or {}, now=now, identity="test-endpoint",
                local="healthy", edge="healthy", public="cloudflare_1033", installed=True)
    args.update(changes)
    return guard.decide(**args)


def test_three_consecutive_failures_and_public_recovery():
    first = tick()
    second = tick(first, now=10060)
    third = tick(second, now=10120)
    assert first["action"] == second["action"] == "observe"
    assert third["action"] == "restart_connector"
    assert third["attempts"] == [10120]
    restored = tick(third, now=10180, public="healthy")
    assert restored["failures"] == 0 and restored["action"] == "healthy"
    assert restored["attempts"] == [10120]


@pytest.mark.parametrize("change", [
    {"local": "unreachable"}, {"edge": "http_502"}, {"public": "unreachable"},
    {"public": "http_403"}, {"public": "http_502"}, {"installed": False},
])
def test_unrelated_or_disabled_failures_never_restart(change):
    state = tick(tick(), now=10060)
    state = tick(state, now=10120, **change)
    assert state["action"] != "restart_connector" and state["failures"] == 0


@pytest.mark.parametrize("change", [{"now": 10500}, {"now": 9000}, {"identity": "other"}])
def test_sleep_clock_or_endpoint_change_cannot_complete_old_streak(change):
    state = tick(tick(), now=10060)
    args = {"now": 10120, **change}
    assert tick(state, **args)["failures"] == 1


def test_cooldown_and_hourly_limit():
    state = tick(tick(), now=10060)
    state["attempts"] = [9900]
    assert tick(state, now=10120)["action"] == "cooldown"
    state["attempts"] = [7000, 8000, 9000]
    assert tick(state, now=10120)["action"] == "hourly_limit"
    state["attempts"] = [6000, 8000, 9000]
    assert tick(state, now=10120)["action"] == "restart_connector"


def test_backwards_clock_keeps_restart_cooldown():
    state = tick(tick(), now=10060)
    state["attempts"] = [11000]
    after = tick(state, now=10120)
    assert after["action"] == "cooldown" and after["attempts"] == [11000]


def test_restart_only_existing_exact_connector(monkeypatch):
    commands = []
    monkeypatch.setattr(guard.gateway, "installed", lambda label: label == guard.gateway.NAMED_LABEL)
    monkeypatch.setattr(guard.subprocess, "run", lambda cmd, **kw: (
        commands.append(cmd) or subprocess.CompletedProcess(cmd, 0)))
    assert guard.restart_connector() == "requested"
    assert commands == [["launchctl", "kickstart", "-k", guard.gateway.target(guard.gateway.NAMED_LABEL)]]
    monkeypatch.setattr(guard.gateway, "installed", lambda _: False)
    assert guard.restart_connector() == "connector_disabled"
    assert len(commands) == 1


def test_attempt_is_durable_before_failed_restart(monkeypatch, tmp_path):
    from yoyo.monitor import named_tunnel

    monkeypatch.setattr(guard.gateway, "RUNTIME", tmp_path)
    (tmp_path / "named-endpoint.json").write_text("{}")
    monkeypatch.setattr(named_tunnel, "saved", lambda: {
        "url": "https://workspace.example.com", "tunnel_id": "example"})
    monkeypatch.setattr(guard.gateway, "installed", lambda _: True)
    monkeypatch.setattr(guard, "probe", lambda url, *args:
                        "cloudflare_1033" if url.startswith("https") else "healthy")
    clock = [10000]
    monkeypatch.setattr(guard.time, "time", lambda: clock[0])
    attempts = []

    def restart():
        state = json.loads((tmp_path / "tunnel-health.json").read_text())
        assert state["attempts"][-1] == clock[0]
        attempts.append(clock[0])
        return "failed"

    monkeypatch.setattr(guard, "restart_connector", restart)
    for _ in range(3):
        state = guard.check()
        clock[0] += 60
    assert state["restart_result"] == "failed" and attempts == [10120]
    for _ in range(3):
        state = guard.check()
        clock[0] += 60
    assert state["action"] == "cooldown" and attempts == [10120]


# Discipline app tunnel (owner 2026-11-08 “app报错了 今天好几次了 能稳定一点吗”).

def test_discipline_health_and_json_1033_classification(origin):
    url, replies = origin
    replies.append((200, b'{"service":"spike-discipline","mode":"okx_live"}', False))
    assert guard.probe(url, service=guard.DISCIPLINE_SERVICE) == "healthy"
    assert guard.probe(url) == "http_200"  # the monitor's name does not match
    replies.append((530, b'{"title":"Tunnel unavailable","error_code":1033,"detail":"The host is configured as a Cloudflare Tunnel"}', True))
    assert guard.probe(url, service=guard.DISCIPLINE_SERVICE) == "cloudflare_1033"


def discipline_runtime(tmp_path, monkeypatch, local, public, enabled=True):
    (tmp_path / "cloudflared.json").write_text(json.dumps({"tunnel": "test-tunnel"}))
    flag = tmp_path / "enabled"
    if enabled:
        flag.write_text("owner-approved\n")
    clock = iter(range(20000, 30000, 60))
    restarts = []
    monkeypatch.setattr(guard.time, "time", lambda: next(clock))
    monkeypatch.setattr(guard.gateway, "installed", lambda label: True)
    monkeypatch.setattr(guard, "probe", lambda url, headers=None, service=guard.SERVICE: local if "127.0.0.1" in url else public)
    monkeypatch.setattr(guard, "restart_connector", lambda label=None: restarts.append(label) or "requested")
    return flag, restarts


def test_discipline_check_is_inert_until_enabled(tmp_path, monkeypatch):
    flag, restarts = discipline_runtime(tmp_path, monkeypatch, "healthy", "cloudflare_1033", enabled=False)
    for _ in range(5):
        assert guard.check_discipline(tmp_path, flag)["action"] == "discipline_not_enabled"
    assert restarts == [] and not (tmp_path / "tunnel-health.json").exists()


def test_discipline_restarts_only_its_connector_after_three_confirmed_1033(tmp_path, monkeypatch):
    flag, restarts = discipline_runtime(tmp_path, monkeypatch, "healthy", "cloudflare_1033")
    actions = [guard.check_discipline(tmp_path, flag)["action"] for _ in range(3)]
    assert actions == ["observe", "observe", "restart_connector"]
    assert restarts == [guard.DISCIPLINE_LABEL]
    state = json.loads((tmp_path / "tunnel-health.json").read_text())
    assert state["restart_result"] == "requested" and state["identity"].endswith("/test-tunnel")
    # Inside the 10-minute cooldown nothing else is restarted.
    assert [guard.check_discipline(tmp_path, flag)["action"] for _ in range(3)][-1] == "cooldown"
    assert restarts == [guard.DISCIPLINE_LABEL]


def test_discipline_never_restarts_when_the_service_itself_is_down(tmp_path, monkeypatch):
    flag, restarts = discipline_runtime(tmp_path, monkeypatch, "unreachable", "cloudflare_1033")
    for _ in range(5):
        assert guard.check_discipline(tmp_path, flag)["action"] == "observe"
    assert restarts == []

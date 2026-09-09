"""Real loopback HTTP checks for the read-only Windows gateway; no desktop UI."""
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from yoyo.monitor import desktop_client as client
from yoyo.monitor.tradingview import DesktopOpenError


@contextmanager
def running(server):
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.fixture
def gateway():
    state = SimpleNamespace(requests=[], opens=[], status=200, body=b'{"ok":true}',
                            headers={}, delay=0, opener_error=None)

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state.requests.append((self.command, self.path, dict(self.headers)))
            if state.delay:
                time.sleep(state.delay)
            try:
                self.send_response(state.status)
                for name, value in state.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(state.body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_POST = do_GET

    def opener(symbol, timeframe):
        state.opens.append((symbol, timeframe))
        if state.opener_error:
            raise state.opener_error
        return {"requested": True, "symbol": symbol, "timeframe": timeframe, "target": "windows"}

    with running(ThreadingHTTPServer(("127.0.0.1", 0), Upstream)) as upstream:
        with running(client.create_server(0, upstream.server_port, opener)) as server:
            state.server = server
            state.upstream = upstream
            yield state


def request(server, method="GET", path="/api/status", headers=None, body=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.headers), response.read()
    finally:
        connection.close()


def action_headers(server, hostname="127.0.0.1"):
    host = f"{hostname}:{server.server_port}"
    return {"Host": host, "Origin": f"http://{host}", "X-Spike-Action": "open-tradingview",
            "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def post(gateway, payload=None, headers=None, path=client.ACTION_PATH):
    if payload is None:
        payload = {"symbol": "BTC-USDT-SWAP", "timeframe": "1H"}
    return request(gateway.server, "POST", path,
                   action_headers(gateway.server) if headers is None else headers,
                   payload if isinstance(payload, bytes) else json.dumps(payload))


@pytest.mark.parametrize("path", sorted(client.READ_ROUTES) + ["/static/app.js", "/static/icons/spike.svg"])
def test_allowlisted_get_is_byte_exact_and_only_fixed_headers_reach_mac(gateway, path):
    gateway.body = b"\x00\xff<script>unchanged()</script>\r\n"
    gateway.headers = {"Content-Type": "text/javascript; charset=utf-8", "Set-Cookie": "private=secret",
                       "Access-Control-Allow-Origin": "*", "Location": "https://private.invalid"}
    target = path + "?symbol=BTC-USDT-SWAP&timeframe=1Dutc&x=%2F%20"
    status, headers, body = request(gateway.server, path=target,
        headers={"Cookie": "private=credential", "Authorization": "Bearer private",
                 "Origin": "http://elsewhere.invalid", "X-Forwarded-Host": "evil.invalid"})
    assert status == 200 and body == gateway.body
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert {name.lower() for name in headers}.isdisjoint({"set-cookie", "access-control-allow-origin", "location"})
    assert gateway.requests == [("GET", target, {"Host": "127.0.0.1:8766",
                                                "Accept-Encoding": "identity", "Connection": "close"})]
    assert gateway.opens == []


@pytest.mark.parametrize("path", ["/api/config", "/api/model", "/api/scan", "/api/unknown", "/static/../secret",
    "/static/./app.js", "/static//app.js", "/static/%2e%2e/secret", "/static/%2E%2E%2Fsecret",
    "/static/%252e%252e%252fsecret", "/static/..%5csecret", "/static/..\\secret", "/static/%00app.js",
    "/static/", "//static/app.js", "http://127.0.0.1:8767/api/status", "/api/status#fragment",
    "/api/%73tatus", "/api/status/", "/static/app.js#fragment"])
def test_unlisted_encoded_traversal_and_absolute_targets_do_not_reach_upstream(gateway, path):
    assert request(gateway.server, path=path, headers={"Host": f"127.0.0.1:{gateway.server.server_port}"})[0] == 404
    assert not gateway.requests and not gateway.opens


def test_loopback_binding_and_local_identity_need_no_mac_connection(gateway):
    assert gateway.server.server_address[0] == "127.0.0.1"
    status, headers, body = request(gateway.server, path="/api/desktop")
    assert status == 200 and json.loads(body) == client.DESKTOP_IDENTITY
    assert not gateway.requests and not gateway.opens


def test_non_loopback_peer_is_rejected_even_with_valid_local_headers(gateway, monkeypatch):
    get_request = gateway.server.get_request

    def remote_peer():
        connection, peer = get_request()
        return connection, ("192.0.2.1", peer[1])

    monkeypatch.setattr(gateway.server, "get_request", remote_peer)
    assert request(gateway.server)[0] == 403
    assert post(gateway)[0] == 403
    assert not gateway.requests and not gateway.opens


def test_missing_host_is_rejected(gateway):
    connection = HTTPConnection("127.0.0.1", gateway.server.server_port, timeout=3)
    try:
        connection.putrequest("GET", "/api/status", skip_host=True)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()
    assert not gateway.requests and not gateway.opens


def test_pythonw_without_stderr_can_serve_success_and_method_errors(gateway, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    assert request(gateway.server)[0] == 200
    assert request(gateway.server, "UNKNOWN")[0] == 405
    assert len(gateway.requests) == 1 and not gateway.opens


@pytest.mark.parametrize("symbol", ["BTC-USDT-SWAP", "ETH-USD-SWAP", "BTC-USDC-SWAP"])
@pytest.mark.parametrize("timeframe", ["15m", "30m", "1H", "4H", "1Dutc"])
def test_every_action_identity_reaches_only_local_opener(gateway, symbol, timeframe):
    status, _, body = post(gateway, {"symbol": symbol, "timeframe": timeframe})
    assert status == 200
    assert json.loads(body) == {"requested": True, "symbol": symbol, "timeframe": timeframe, "target": "windows"}
    assert gateway.opens == [(symbol, timeframe)]
    assert gateway.requests == []


def test_localhost_and_absent_fetch_site_are_accepted_for_exact_same_origin(gateway):
    headers = action_headers(gateway.server, "localhost")
    del headers["Sec-Fetch-Site"]
    assert post(gateway, headers=headers)[0] == 200
    assert gateway.requests == [] and len(gateway.opens) == 1


@pytest.mark.parametrize("host", ["evil.invalid", "evil.invalid:{port}", "localhost.evil.invalid:{port}",
    "127.0.0.1.evil.invalid:{port}", "127.0.0.1", "localhost", "127.0.0.1:1", "localhost:1",
    "127.1:{port}", "127.0.0.2:{port}", "[::1]:{port}", "LOCALHOST:{port}", "localhost.:{port}"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_host_and_dns_rebinding_gate(gateway, host, method):
    headers = action_headers(gateway.server)
    headers["Host"] = host.format(port=gateway.server.server_port)
    headers["Origin"] = "http://" + headers["Host"]
    status = (post(gateway, headers=headers)[0] if method == "POST"
              else request(gateway.server, headers=headers)[0])
    assert status == 403
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("name,value", [("Origin", "https://evil.invalid"), ("Origin", "null"),
    ("Origin", ""), ("Origin", "http://localhost:{port}"), ("Origin", "https://127.0.0.1:{port}"),
    ("Origin", "http://127.0.0.1:{port}/"), ("X-Spike-Action", ""), ("X-Spike-Action", "open"),
    ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Site", "same-site"), ("Sec-Fetch-Site", "none")])
def test_cross_origin_action_never_reaches_any_opener_or_upstream(gateway, name, value):
    headers = action_headers(gateway.server)
    headers[name] = value.format(port=gateway.server.server_port)
    status, response_headers, _ = post(gateway, headers=headers)
    assert status == 403
    assert "Access-Control-Allow-Origin" not in response_headers
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("name", ["Origin", "X-Spike-Action"])
def test_missing_action_gate_header_is_rejected(gateway, name):
    headers = action_headers(gateway.server)
    del headers[name]
    assert post(gateway, headers=headers)[0] == 403
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("path", [client.ACTION_PATH + "?", client.ACTION_PATH + "?symbol=ETH-USDT-SWAP",
    client.ACTION_PATH + "/", "/api/tradingview/%6fpen", "//api/tradingview/open", "/api/status", "/"])
def test_post_path_query_variants_are_never_proxied_or_opened(gateway, path):
    assert post(gateway, path=path)[0] == 405
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE", "TRACE", "CONNECT", "UNKNOWN"])
def test_non_post_action_method_has_no_side_effect_and_no_cors(gateway, method):
    status, headers, body = request(gateway.server, method, client.ACTION_PATH)
    assert status == 405
    assert headers["Allow"] == "POST"
    assert "Access-Control-Allow-Origin" not in headers
    if method == "HEAD":
        assert body == b""
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("payload", [[], None, "symbol", {"symbol": "BTC-USDT-SWAP"},
    {"symbol": "BTC-USDT-SWAP", "timeframe": "1H", "url": "https://evil.invalid"},
    {"symbol": 12, "timeframe": "1H"}, {"symbol": "BTC-USDT-SWAP", "timeframe": False},
    {"symbol": "https://evil.invalid", "timeframe": "1H"},
    {"symbol": "BTC-USDT-SWAP&symbol=BAD", "timeframe": "1H"},
    {"symbol": "BTC-USDT-SWAP", "timeframe": "5m"},
    {"symbol": "BTC-USDT-SWAP", "timeframe": "1D"},
    {"symbol": "btc-usdt-swap", "timeframe": "1H"}])
def test_invalid_payload_or_identity_never_calls_injected_opener(gateway, payload):
    assert post(gateway, payload=json.dumps(payload).encode())[0] == 400
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("body", [b'{"symbol":"BTC-USDT-SWAP","symbol":"ETH-USDT-SWAP","timeframe":"1H"}',
    b'{"symbol":"BTC-USDT-SWAP","timeframe":"1H","timeframe":"4H"}', b"{", b"\xff", b""])
def test_malformed_and_ambiguous_json_is_rejected(gateway, body):
    assert post(gateway, body)[0] == 400
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "", "application/jsonp"])
def test_action_requires_json_content_type(gateway, content_type):
    headers = action_headers(gateway.server)
    headers["Content-Type"] = content_type
    assert post(gateway, headers=headers)[0] == 415
    assert not gateway.requests and not gateway.opens


def test_action_body_limit_is_checked_before_reading(gateway):
    headers = action_headers(gateway.server)
    headers["Content-Length"] = "513"
    assert post(gateway, b"", headers)[0] == 413
    assert post(gateway, b"x" * 513)[0] == 413
    assert not gateway.requests and not gateway.opens


def test_exact_action_body_limit_is_allowed(gateway):
    body = b'{"symbol":"BTC-USDT-SWAP","timeframe":"1H"}'
    assert post(gateway, body + b" " * (512 - len(body)))[0] == 200
    assert gateway.requests == [] and gateway.opens == [("BTC-USDT-SWAP", "1H")]


@pytest.mark.parametrize("extra_headers", [{"Content-Length": "-1"}, {"Content-Length": "NaN"},
    {"Transfer-Encoding": "chunked"}, {"Content-Encoding": "gzip"}])
def test_unsupported_body_framing_and_encoding_are_rejected(gateway, extra_headers):
    headers = action_headers(gateway.server)
    headers.update(extra_headers)
    assert post(gateway, headers=headers)[0] in {400, 415}
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("name", ["Host", "Origin", "X-Spike-Action", "Sec-Fetch-Site", "Content-Length", "Content-Type"])
def test_duplicate_sensitive_headers_are_rejected(gateway, name):
    headers = action_headers(gateway.server)
    body = b'{"symbol":"BTC-USDT-SWAP","timeframe":"1H"}'
    headers["Content-Length"] = str(len(body))
    connection = HTTPConnection("127.0.0.1", gateway.server.server_port, timeout=3)
    try:
        connection.putrequest("POST", client.ACTION_PATH, skip_host=True, skip_accept_encoding=True)
        for key, value in headers.items():
            connection.putheader(key, value)
        connection.putheader(name, headers[name])
        connection.endheaders(body)
        response = connection.getresponse()
        assert response.status in {400, 403, 415}
        response.read()
    finally:
        connection.close()
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 404, 500])
def test_upstream_errors_and_redirects_are_sanitized_and_never_followed(gateway, status):
    gateway.status = status
    gateway.headers["Location"] = f"http://127.0.0.1:{gateway.upstream.server_port}/private-target"
    gateway.body = b"private upstream exception"
    result_status, headers, body = request(gateway.server)
    assert result_status == 502 and json.loads(body) == {"detail": client.UPSTREAM_ERROR}
    assert "private" not in body.decode() and "Location" not in headers
    assert len(gateway.requests) == 1 and gateway.requests[0][1] == "/api/status"


def test_upstream_unavailable_is_sanitized_and_desktop_identity_stays_available(monkeypatch):
    monkeypatch.setattr(client, "TIMEOUT_SECONDS", 0.05)
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        # A bound but non-listening socket reserves an unavailable upstream port;
        # platform TCP behavior may reject immediately or reach the short timeout.
        with running(client.create_server(0, port, lambda *args: pytest.fail("unexpected opener"))) as server:
            assert request(server)[0] == 502
            status, _, body = request(server, path="/api/desktop")
            assert status == 200 and json.loads(body) == client.DESKTOP_IDENTITY


def test_upstream_read_timeout_is_sanitized(gateway, monkeypatch):
    monkeypatch.setattr(client, "TIMEOUT_SECONDS", 0.05)
    gateway.delay = 0.2
    status, _, body = request(gateway.server)
    assert status == 502 and json.loads(body) == {"detail": client.UPSTREAM_ERROR}


@pytest.mark.parametrize("advertised", [False, True])
def test_upstream_body_is_bounded_even_without_length_header(gateway, monkeypatch, advertised):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 64)
    gateway.body = b"x" * 65
    if advertised:
        gateway.headers["Content-Length"] = "65"
    assert request(gateway.server)[0] == 502


def test_truncated_upstream_body_is_never_served_as_success(gateway):
    gateway.headers["Content-Length"] = "1000"
    gateway.body = b"incomplete"
    assert request(gateway.server)[0] == 502


@pytest.mark.parametrize("error,status,detail", [(DesktopOpenError("busy", 409), 409, "busy"),
    (DesktopOpenError("not ready", 503), 503, "not ready"),
    (RuntimeError("private native window title"), 503, "未能打开 Windows TradingView"),
    (TimeoutError("private native timeout"), 503, "调用 Windows TradingView 超时")])
def test_local_open_failure_contract_never_falls_back_to_mac(gateway, error, status, detail):
    gateway.opener_error = error
    response_status, _, body = post(gateway)
    assert response_status == status and detail in json.loads(body)["detail"]
    assert "private" not in body.decode()
    assert gateway.requests == [] and gateway.opens == [("BTC-USDT-SWAP", "1H")]


def test_request_body_timeout_is_explicit_and_has_no_action(gateway, monkeypatch):
    monkeypatch.setattr(client, "TIMEOUT_SECONDS", 0.05)
    headers = action_headers(gateway.server)
    headers["Content-Length"] = "50"
    assert post(gateway, b"{", headers)[0] == 408
    assert not gateway.requests and not gateway.opens


@pytest.mark.parametrize("port,upstream_port", [(8766, 8766), (-1, 8767), (65536, 8767),
    (8766, 0), (8766, 65536), (True, 8767), (8766, "8767")])
def test_invalid_port_configuration_never_binds(port, upstream_port):
    with pytest.raises(ValueError):
        client.create_server(port, upstream_port)

"""Windows loopback viewer for the existing Mac Spike monitor, using stdlib only.

The SSH reverse tunnel exposes Mac 127.0.0.1:8766 at Windows 127.0.0.1:8767.
This gateway serves only an explicit read-only route list, preserving response
bytes. TradingView activation is a separate, same-origin local POST and can
never reach the Mac. There is no scanner, model, configuration, credential
forwarding, arbitrary proxy destination, or browser launch in this client.
"""
from __future__ import annotations

import argparse
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket

from yoyo.monitor.tradingview import DesktopOpenError, chart_url

LOOPBACK = "127.0.0.1"
UPSTREAM_HOST_HEADER = "127.0.0.1:8766"
TIMEOUT_SECONDS = 12
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_ACTION_BYTES = 512
READ_ROUTES = frozenset({"/", "/api/status", "/api/health", "/healthz",
                         "/api/signals", "/api/candidates", "/api/markets", "/api/chart"})
ACTION_PATH = "/api/tradingview/open"
DESKTOP_IDENTITY = {"target": "windows", "source": "mac", "transport": "ssh-loopback"}
UPSTREAM_ERROR = "Mac 数据连接不可用或响应超时，请检查 SSH 隧道后重试。"
LOG = logging.getLogger("spike.desktop_client")


def _open_windows_chart(symbol: str, timeframe: str) -> dict:
    # The native bridge is loaded only after a valid, explicit local action.
    from yoyo.monitor.windows_tradingview import open_chart
    return open_chart(symbol, timeframe)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_path(target: str) -> str | None:
    """Accept origin-form paths only; query text is forwarded without rewriting.

    Static asset names use a deliberately narrow plain-ASCII alphabet. Rejecting
    percent escapes in the path also excludes nested/encoded traversal, while
    query values may retain the percent escapes used for chart identities.
    """
    if (not target.startswith("/") or target.startswith("//") or "#" in target
            or any(ord(char) <= 32 or ord(char) == 127 for char in target)):
        return None
    path = target.partition("?")[0]
    if path in READ_ROUTES or path in {"/api/desktop", ACTION_PATH}:
        return path
    if not re.fullmatch(r"/static/[A-Za-z0-9_./-]+", path):
        return None
    if any(segment in {"", ".", ".."} for segment in path.split("/")[2:]):
        return None
    return path


class DesktopClientHandler(BaseHTTPRequestHandler):
    server_version = "SpikeDesktop"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(TIMEOUT_SECONDS)

    def log_message(self, format, *args):
        # Request targets can include private chart identities; do not log them.
        pass

    def _one_header(self, name):
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else None

    def _trusted(self):
        host = self._one_header("Host")
        expected = {f"127.0.0.1:{self.server.server_port}",
                    f"localhost:{self.server.server_port}"}
        try:
            local_client = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            local_client = False
        if host not in expected or not local_client:
            self._json(403, {"detail": "仅允许从本机 Spike 页面访问。"})
            return False
        return True

    def _target(self):
        # BaseHTTPRequestHandler collapses an initial //; validate the raw
        # request target so that normalization cannot expand the route list.
        return self.requestline.split()[1]

    def _send(self, status, body, content_type="application/json; charset=utf-8", encoding=None,
              allow=None):
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload, allow=None):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), allow=allow)

    def send_error(self, code, message=None, explain=None):
        if code == 501:
            self._method_not_allowed()
        else:
            # Never reflect parser errors containing raw headers/request text.
            self._json(code, {"detail": "HTTP 请求无效。"})

    def _method_not_allowed(self):
        if self._trusted():
            allowed = "POST" if self.path == ACTION_PATH else "GET"
            self._json(405, {"detail": "不支持此请求方法。"}, allow=allowed)

    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed

    def do_GET(self):
        if not self._trusted():
            return
        target = self._target()
        path = _read_path(target)
        if path is None:
            self._json(404, {"detail": "页面不存在。"})
        elif path == ACTION_PATH:
            self._json(405, {"detail": "打开图表需要本机页面操作。"}, allow="POST")
        elif path == "/api/desktop":
            self._json(200, DESKTOP_IDENTITY)
        else:
            self._proxy_get(target)

    def _proxy_get(self, target):
        connection = HTTPConnection(LOOPBACK, self.server.upstream_port, timeout=TIMEOUT_SECONDS)
        try:
            # HTTPConnection never follows redirects or uses environment proxies.
            # No incoming header, cookie or credential is forwarded.
            connection.request("GET", target, headers={"Host": UPSTREAM_HOST_HEADER,
                               "Accept-Encoding": "identity", "Connection": "close"})
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise ValueError("upstream status")
            lengths = response.headers.get_all("Content-Length", [])
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit()
                            or int(lengths[0]) > MAX_RESPONSE_BYTES):
                raise ValueError("upstream length")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("upstream body limit")
            if lengths and len(body) != int(lengths[0]):
                raise ValueError("upstream truncated body")
            content_type = response.getheader("Content-Type", "application/octet-stream")
            if len(content_type) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in content_type):
                raise ValueError("upstream content type")
            encoding = response.getheader("Content-Encoding")
            if encoding not in {None, "identity", "gzip", "deflate", "br"}:
                raise ValueError("upstream encoding")
        except (HTTPException, OSError, ValueError):
            # Includes connect/read socket timeouts; never expose remote bodies,
            # redirect locations, socket errors or private host information.
            self._json(502, {"detail": UPSTREAM_ERROR})
            return
        finally:
            connection.close()
        self._send(response.status, body, content_type, encoding)

    def do_POST(self):
        if not self._trusted():
            return
        # Query strings, slash variants and encodings are not action aliases.
        if self._target() != ACTION_PATH:
            self._json(405, {"detail": "不支持此请求方法。"}, allow="GET")
            return
        origin = f"http://{self._one_header('Host')}"
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        if (self._one_header("Origin") != origin
                or self._one_header("X-Spike-Action") != "open-tradingview"
                or (sites and sites != ["same-origin"])):
            self._json(403, {"detail": "请从本机 Spike 页面点击查看结构。"})
            return
        content_type = self._one_header("Content-Type")
        if (content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json"
                or self.headers.get_all("Content-Encoding")):
            self._json(415, {"detail": "请求必须使用 JSON。"})
            return
        length = self._one_header("Content-Length")
        if self.headers.get_all("Transfer-Encoding") or length is None or not re.fullmatch(r"[0-9]+", length):
            self._json(400, {"detail": "请求长度无效。"})
            return
        if len(length) > 3 or int(length) > MAX_ACTION_BYTES:
            self._json(413, {"detail": "请求内容过大。"})
            return
        try:
            body = self.rfile.read(int(length))
            if len(body) != int(length):
                raise ValueError("incomplete body")
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or set(payload) != {"symbol", "timeframe"}
                    or not all(isinstance(value, str) for value in payload.values())):
                raise ValueError("invalid action fields")
        except (socket.timeout, TimeoutError):
            self._json(408, {"detail": "读取请求超时，请重试。"})
            return
        except (ValueError, UnicodeError):
            self._json(400, {"detail": "请求只接受合约和周期两个字符串字段。"})
            return
        try:
            symbol, timeframe = payload["symbol"], payload["timeframe"]
            chart_url(symbol, timeframe)  # Validate before even an injected opener is called.
            receipt = self.server.chart_opener(symbol, timeframe)
            self._json(200, receipt)
        except DesktopOpenError as error:
            status = error.status_code if 400 <= error.status_code <= 599 else 503
            self._json(status, {"detail": str(error)})
        except (socket.timeout, TimeoutError):
            self._json(503, {"detail": "调用 Windows TradingView 超时，请查看应用后重试。"})
        except Exception:
            self._json(503, {"detail": "未能打开 Windows TradingView，请确认应用已安装并登录。"})


class DesktopClientServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # pythonw.exe has no stderr; also avoid logging headers or chart data.
        LOG.warning("Desktop client request failed.")


def create_server(port=8766, upstream_port=8767, opener=None):
    """Create a fixed-loopback server; port=0 is supported for isolated tests."""
    for value, minimum in ((port, 0), (upstream_port, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 65535:
            raise ValueError("port must be an integer in the allowed TCP range")
    if port == upstream_port:
        raise ValueError("gateway and upstream ports must differ")
    server = DesktopClientServer((LOOPBACK, port), DesktopClientHandler)
    server.upstream_port = upstream_port
    server.chart_opener = _open_windows_chart if opener is None else opener
    return server


def main():
    parser = argparse.ArgumentParser(description="Windows local viewer for the Mac Spike monitor")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--upstream-port", type=int, default=8767)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.upstream_port <= 65535:
        parser.error("ports must be between 1 and 65535")
    if args.port == args.upstream_port:
        parser.error("gateway and upstream ports must differ")
    if os.name == "nt" or os.environ.get("LOCALAPPDATA"):
        log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Spike"
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(log_dir / "client.log"), level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = create_server(args.port, args.upstream_port)
    except OSError as error:
        LOG.error("Desktop client could not bind loopback port %s (errno=%s).", args.port, error.errno)
        return 1
    LOG.info("Desktop client listening on 127.0.0.1:%s; upstream loopback port %s.", args.port, args.upstream_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())

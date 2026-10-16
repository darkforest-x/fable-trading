"""Small OKX V5 REST client with no hidden mutation retries.

Source: https://www.okx.com/docs-v5/en/ (authentication and Trade, 2026-10-16).
The exact encoded path/body are signed. HTTP errors, transport ambiguity and
per-item sCode failures are distinct from an empty successful account response.
Only the explicit execution CLI constructs a write-enabled client.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


class APIError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(f"OKX error {self.code}")


class TransportError(RuntimeError):
    """A POST may have succeeded. Its original client ID must be reconciled."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TransportError("redirect refused")


def signature(secret: str, timestamp: str, method: str, path: str, body: str) -> str:
    message = (timestamp + method + path + body).encode()
    return base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()


class Client:
    def __init__(self, config, credentials=None, *, writes=False, opener=None):
        config.validate()
        self.config, self.credentials, self.writes = config, credentials, writes
        if credentials and credentials.get("demo") is not config.demo:
            raise ValueError("credential environment differs from configuration")
        self.opener = opener or build_opener(NoRedirect())
        self.last_request = 0.0

    def request(self, method, path, *, query=None, body=None, private=True):
        if method not in {"GET", "POST"} or not path.startswith("/api/v5/"):
            raise ValueError("unsupported request")
        if method == "POST" and not self.writes:
            raise PermissionError("read-only client")
        if private and not self.credentials:
            raise ValueError("local credentials are not configured")
        path += "?" + urlencode(sorted(query.items())) if query else ""
        encoded = json.dumps(body, separators=(",", ":"), allow_nan=False) if body is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": "Fable-Task59/1"}
        if self.config.demo:
            headers["x-simulated-trading"] = "1"
        if private:
            stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            headers.update({"OK-ACCESS-KEY": self.credentials["api_key"],
                            "OK-ACCESS-PASSPHRASE": self.credentials["passphrase"],
                            "OK-ACCESS-TIMESTAMP": stamp,
                            "OK-ACCESS-SIGN": signature(self.credentials["secret"], stamp, method, path, encoded)})
        # Conservative single-client pacing, not a signal/portfolio limit.
        time.sleep(max(0, .21 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = Request(self.config.host + path, data=encoded.encode() if encoded else None,
                          headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=10) as response:
                payload = json.loads(response.read(8 * 1024 * 1024))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            # Do not render request headers, credentials, raw URLs or server messages.
            raise TransportError(f"{method} response unavailable; reconcile before any retry") from None
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise APIError(payload.get("code", "malformed") if isinstance(payload, dict) else "malformed")
        rows = payload.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TransportError("invalid response shape")
        for row in rows:
            if "sCode" in row and str(row["sCode"]) != "0":
                raise APIError(row["sCode"])
        return rows

    def get(self, path, **query):
        return self.request("GET", path, query=query, private=not path.startswith(("/api/v5/public/", "/api/v5/market/")))

    def post(self, path, body):
        return self.request("POST", path, body=body)

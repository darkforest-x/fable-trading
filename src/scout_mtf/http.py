"""Shared HTTP helpers (IPv4 preference + browser UA for OKX WAF)."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

BASE = "https://www.okx.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _prefer_ipv4() -> None:
    if getattr(socket, "_fable_ipv4_patched", False):
        return
    _orig = socket.getaddrinfo

    def _wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        res = _orig(*args, **kwargs)
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 or res

    socket.getaddrinfo = _wrapped  # type: ignore[assignment]
    socket._fable_ipv4_patched = True  # type: ignore[attr-defined]


_prefer_ipv4()


def get_json(path: str, *, retries: int = 4, timeout: float = 20.0) -> dict[str, Any]:
    url = BASE + path if path.startswith("/") else path
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")

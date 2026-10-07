"""Same-origin proxy from the workbench to the loopback Discord copier (127.0.0.1:8080).

The copier keeps its own process and venv (yoyo/copier, requirements-copier.txt);
the workbench only forwards a fixed set of routes, never a caller-chosen URL --
the same pattern as the 8771 vision proxy in yoyo.research_workspace.api.

Write policy:
* Signal ingestion (``messages/ingest``) is not proxied. Signals enter only from
  the Chrome extension straight to 8080, so the workbench -- and its public tunnel
  -- cannot be used to inject a trade.
* Requests arriving through the public gateway (Caddy sets ``X-Fable-Gateway:
  public``) may read, and may only make changes that stop trading: kill switch on,
  simulation on. Turning trading back on or editing risk settings is local-only.

HTTPX async client: https://www.python-httpx.org/async/
"""
from __future__ import annotations

import json
import re

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response

COPIER = "http://127.0.0.1:8080"
PUBLIC_HEADER = "x-fable-gateway"
READ_PREFIXES = {"health", "dashboard", "messages", "orders", "settings", "system", "monitor", "notifications", "paper"}
WRITE_ROUTES = {
    ("POST", "control/kill-switch"), ("POST", "control/dry-run"), ("PUT", "settings"),
    ("PUT", "monitor/switches"), ("POST", "monitor/channels/toggle"), ("POST", "orders/sync"),
    ("POST", "notifications/telegram/test"), ("POST", "notifications/telegram/panel"),
}
# (route, required body) pairs that only reduce exposure; allowed from the public gateway.
PUBLIC_SAFE_WRITES = {("control/kill-switch", True), ("control/dry-run", True)}
MAX_BODY = 256 * 1024


def check_route(method: str, path: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_/-]+", path) or any(part in {"", ".", ".."} for part in path.split("/")):
        raise HTTPException(404, "跟单接口不存在")
    if method in {"GET", "HEAD"}:
        if path.split("/")[0] not in READ_PREFIXES:
            raise HTTPException(404, "跟单接口不存在")
    elif (method, path) not in WRITE_ROUTES:
        raise HTTPException(404, "跟单接口不存在")


def check_public_write(path: str, body: bytes) -> None:
    try:
        enabled = json.loads(body or b"{}").get("enabled")
    except (ValueError, AttributeError):
        enabled = None
    if (path, enabled) not in PUBLIC_SAFE_WRITES:
        raise HTTPException(403, "公网入口只允许停止交易（开启保护或切回模拟）；恢复实盘与修改参数请在本机工作台操作。")


def same_origin(request: Request) -> None:
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if (request.headers.get("origin") != expected
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        raise HTTPException(403, "请从本机统一工作台提交。")


def install(app, transport: httpx.AsyncBaseTransport | None = None) -> None:
    @app.api_route("/api/copier/{path:path}", methods=["GET", "POST", "PUT"])
    async def copier_proxy(path: str, request: Request):
        method = request.method
        check_route(method, path)
        body = b""
        if method != "GET":
            same_origin(request)
            chunks = bytearray()
            async for chunk in request.stream():
                if len(chunks) + len(chunk) > MAX_BODY:
                    raise HTTPException(413, "请求过大")
                chunks.extend(chunk)
            body = bytes(chunks)
            if request.headers.get(PUBLIC_HEADER) == "public":
                check_public_write(path, body)
        headers = {"origin": COPIER} if method != "GET" else {}
        if request.headers.get("content-type"):
            headers["content-type"] = request.headers["content-type"]
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(45, connect=3),
                                         transport=transport) as client:
                response = await client.request(method, f"{COPIER}/api/{path}", params=request.query_params,
                                                content=body, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(503, "跟单服务未运行；在本机执行 .venv/bin/python -m yoyo.copier.manage status 查看。")
        forwarded = {k: v for k, v in response.headers.items() if k == "content-type"}
        return Response(response.content, status_code=response.status_code, headers=forwarded)

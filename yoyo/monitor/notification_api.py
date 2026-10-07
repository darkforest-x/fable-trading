"""Loopback notification-center API: inspect receipts and edit subscriptions.

Only routing preferences are mutable here. These endpoints never send test
messages, expose credentials, retry uncertain deliveries or rewrite outboxes.
"""
from __future__ import annotations

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool
import sqlite3

from .notification_center import NotificationCenter


class RouteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def install(app, runtime, monitor):
    center = NotificationCenter(runtime)
    app.state.notification_center = center

    def snapshot():
        return center.snapshot(monitor.status(), center.clock(monitor.client.clock))

    @app.get("/api/notifications")
    def notifications():
        try:
            return snapshot()
        except (sqlite3.Error, ValueError, KeyError):
            raise HTTPException(503, "通知中心暂不可读，请稍后刷新") from None

    @app.get("/api/notifications/events")
    def events(channel: str | None = None, topic: str | None = None, status: str | None = None,
               side: str | None = None, timeframe: str | None = None,
               search: str = Query("", max_length=48), offset: int = Query(0, ge=0, le=100_000),
               limit: int = Query(50, ge=1, le=200)):
        try:
            return center.events(channel=channel, topic=topic, status=status, side=side, timeframe=timeframe,
                                 search=search, offset=offset, limit=limit, now=center.clock(monitor.client.clock))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except sqlite3.Error:
            raise HTTPException(503, "通知记录暂不可读，请稍后刷新") from None

    @app.put("/api/notifications/routes/{topic}/{channel}")
    def update(topic: str, channel: str, payload: RouteUpdate, request: Request):
        if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or request.headers.get("x-spike-action") != "update-notification-route"):
            raise HTTPException(403, "请从本机通知中心修改订阅")
        try:
            center.update_route(topic, channel, payload.enabled, center.clock(monitor.client.clock))
            return snapshot()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except sqlite3.Error:
            raise HTTPException(503, "订阅设置暂不可读，请刷新核对后再操作") from None

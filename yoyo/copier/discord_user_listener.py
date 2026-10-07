"""
Discord 用户 Token 监听（实验性，违反 Discord ToS，有封号风险）。

默认关闭。仅在 config + .env 同时启用时运行。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from yoyo.copier.config import app_config, env
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)


def user_listener_enabled() -> bool:
    if not env.discord_user_token:
        return False
    if not app_config.user_listener.enabled:
        return False
    if app_config.user_listener.use_pinned_list:
        return True
    return bool(app_config.user_listener.channel_ids)


def _channel_allowed(channel_id: str) -> bool:
    allowed = {str(c) for c in app_config.user_listener.channel_ids}
    return channel_id in allowed


def _process_sync(db: Database, router: ActionRouter, payload: dict[str, Any]) -> None:
    from yoyo.copier.ingest import ingest_message

    author = payload.get("author", {}) or {}
    author_name = author.get("global_name") or author.get("username") or "unknown"
    content = payload.get("content") or ""
    ingest_message(
        db,
        router,
        external_id=str(payload["id"]),
        channel_id=str(payload.get("channel_id", "")),
        author=author_name,
        content=content,
        attachments=[
            {"url": a.get("url"), "filename": a.get("filename")}
            for a in (payload.get("attachments") or [])
        ],
        source="discord_user",
    )


def start_user_listener(db: Database) -> None:
    """Blocking: run discum gateway in current thread."""
    if not user_listener_enabled():
        logger.info("用户 Token 监听未启用")
        return

    try:
        import discum
    except ImportError as e:
        logger.error("请安装 discum: pip install discum — %s", e)
        return

    router = ActionRouter(db)
    channel_ids = list(app_config.user_listener.channel_ids)
    logger.warning(
        "【风险】用户 Token 监听已启动，监听频道: %s — 可能违反 Discord ToS",
        channel_ids,
    )
    db.audit("user_listener_start", ",".join(channel_ids))

    bot = discum.Client(token=env.discord_user_token, log=False)

    @bot.gateway.command
    def on_gateway(resp: Any) -> None:
        if resp.event.ready:
            logger.info("用户 Token 已连接 Discord 网关（实验性监听）")
            return

        if resp.event.message:
            m = resp.parsed.auto()
            if not m:
                return
            ch = str(m.get("channel_id", ""))
            if not _channel_allowed(ch):
                return
            author = m.get("author", {}) or {}
            if author.get("bot") and not author.get("system"):
                return
            logger.info(
                "用户监听收到消息 ch=%s author=%s",
                ch,
                author.get("username"),
            )
            _process_sync(db, router, m)

    bot.gateway.run(auto_reconnect=True)

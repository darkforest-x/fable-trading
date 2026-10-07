"""统一消息入库 + AI 路由（Discord / Telegram 共用）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from yoyo.copier.monitor_switches import channel_enabled, feature_enabled
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)


def ingest_message(
    db: Database,
    router: ActionRouter,
    *,
    external_id: str,
    channel_id: str,
    author: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    source: str = "unknown",
) -> int | None:
    if not feature_enabled(db, "ingest_messages"):
        return None
    if not channel_enabled(db, channel_id):
        return None

    if db.message_exists(external_id):
        return None

    text = content or ""
    if not text and attachments:
        text = "[附件消息]"

    msg_id = db.insert_message(
        discord_message_id=external_id,
        channel_id=str(channel_id),
        author=author or "unknown",
        content=text,
        attachments=attachments or [],
    )

    try:
        async def process() -> None:
            from yoyo.copier.notifications.telegram import notify_channel_message

            if feature_enabled(db, "telegram_forward_raw", False):
                await notify_channel_message(
                    db,
                    channel_id=str(channel_id),
                    author=author or "unknown",
                    content=text,
                    attachments=attachments or [],
                    source=source,
                )
            await router.process_message(
                msg_id,
                text,
                author,
                allow_execute=not source.endswith("_recovery"),
            )

        asyncio.run(process())
    except Exception as e:
        logger.exception("%s 消息处理失败: %s", source, e)
        db.update_message_status(msg_id, "error")
        db.audit("error", str(e), message_id=msg_id)
    return msg_id

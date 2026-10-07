"""
Telegram 社群/频道监听（Telethon 用户会话）。

需在 https://my.telegram.org 创建 API_ID / API_HASH，
首次运行会生成 session 文件（或配置 TELEGRAM_SESSION_STRING）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from yoyo.copier.config import ROOT, app_config, env
from yoyo.copier.ingest import ingest_message
from yoyo.copier.monitor_switches import feature_enabled
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database
from yoyo.copier.telegram_channels import resolve_telegram_entities

logger = logging.getLogger(__name__)


def telegram_listener_enabled() -> bool:
    if not app_config.telegram.enabled:
        return False
    if not env.telegram_api_id or not env.telegram_api_hash:
        return False
    return bool(env.telegram_session_path or env.telegram_session_string)


async def run_telegram_listener(db: Database) -> None:
    from telethon import TelegramClient, events

    if not telegram_listener_enabled():
        logger.info("Telegram 监听未启用（检查 telegram.enabled 与 .env）")
        return

    if not feature_enabled(db, "telegram_listen"):
        logger.info("Telegram 监听开关已关闭（控制中心）")
        while True:
            await asyncio.sleep(60)

    session = env.telegram_session_string or str(
        Path(env.telegram_session_path or ROOT / "data" / "telegram.session")
    )
    client = TelegramClient(
        session,
        int(env.telegram_api_id),
        env.telegram_api_hash,
    )

    router = ActionRouter(db)
    path = ROOT / app_config.telegram.channels_config_path
    if not Path(path).is_absolute():
        path = ROOT / path

    await client.start()
    entities, name_map = await resolve_telegram_entities(client, path)
    if not entities:
        logger.error(
            "没有可用的 Telegram 频道，请编辑 config/telegram_channels.yaml"
        )
        return

    from yoyo.copier.monitor_switches import merge_channel_ids

    merge_channel_ids(db, list(name_map.keys()))
    db.audit("telegram_listener_start", ",".join(name_map.keys()))
    logger.info("【Telegram】实时监听 %s 个社群/频道", len(entities))

    @client.on(events.NewMessage(chats=entities))
    async def on_message(event: events.NewMessage.Event) -> None:
        if not feature_enabled(db, "telegram_listen"):
            return
        if not feature_enabled(db, "ingest_messages"):
            return

        sender = await event.get_sender()
        author = "unknown"
        if sender:
            author = (
                getattr(sender, "username", None)
                or getattr(sender, "first_name", None)
                or str(getattr(sender, "id", ""))
            )

        if getattr(sender, "bot", False):
            return

        chat_id = str(event.chat_id)
        text = event.message.message or ""
        if not text and event.message.media:
            text = "[媒体消息]"

        external_id = f"tg_{chat_id}_{event.message.id}"
        logger.info(
            "Telegram 收到消息 ch=%s (%s) author=%s",
            chat_id,
            name_map.get(chat_id, chat_id),
            author,
        )

        ingest_message(
            db,
            router,
            external_id=external_id,
            channel_id=chat_id,
            author=str(author),
            content=text,
            source="telegram",
        )

    await client.run_until_disconnected()


def start_telegram_listener(db: Database) -> None:
    """在独立线程中运行 asyncio 循环。"""
    try:
        asyncio.run(run_telegram_listener(db))
    except KeyboardInterrupt:
        logger.info("Telegram 监听已停止")
    except Exception as e:
        logger.exception("Telegram 监听异常: %s", e)

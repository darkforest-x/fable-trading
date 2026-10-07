from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

from yoyo.copier.config import app_config, env
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)


class SignalBot(commands.Bot):
    def __init__(self, db: Database, router: ActionRouter) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.db = db
        self.router = router
        raw = app_config.discord.signal_channel_id or "0"
        try:
            self.channel_id = int(raw) if str(raw).isdigit() else 0
        except ValueError:
            self.channel_id = 0
        if not self.channel_id:
            logger.warning(
                "discord.signal_channel_id 未配置，请在 config.yaml 填入 #signal-inbox 的频道 ID"
            )

    async def on_ready(self) -> None:
        logger.info("Discord bot logged in as %s", self.user)
        self.db.audit("discord_ready", str(self.user))
        if self.channel_id:
            ch = self.get_channel(self.channel_id)
            if ch:
                logger.info("正在监听频道: #%s (id=%s)", getattr(ch, "name", "?"), ch.id)
            else:
                logger.error(
                    "无法访问频道 id=%s — 请确认 Bot 已加入该服务器，且频道 ID 正确",
                    self.channel_id,
                )
                self.db.audit("discord_channel_error", f"cannot access channel {self.channel_id}")

    async def on_message(self, message: discord.Message) -> None:
        from yoyo.copier.monitor_switches import feature_enabled

        if not feature_enabled(self.db, "bot_listener"):
            return
        if not feature_enabled(self.db, "ingest_messages"):
            return

        if message.author.bot and message.author != self.user:
            logger.debug("忽略其他 Bot 消息: %s", message.author)
            return
        from yoyo.copier.runtime_config import get_discord

        dc = get_discord(self.db)
        raw = dc.get("signal_channel_id") or "0"
        try:
            expect_ch = int(raw) if str(raw).isdigit() else 0
        except ValueError:
            expect_ch = self.channel_id
        if expect_ch and message.channel.id != expect_ch:
            logger.debug(
                "忽略非目标频道消息 ch=%s 期望=%s",
                message.channel.id,
                self.channel_id,
            )
            return

        allowed = get_discord(self.db).get("allowed_author_names") or []
        author_name = str(message.author.display_name or message.author.name)
        if allowed and not any(a in author_name for a in allowed):
            logger.info("忽略作者不在白名单: %s", author_name)
            return

        logger.info(
            "收到消息 ch=%s author=%s len=%s",
            message.channel.id,
            author_name,
            len(message.content or ""),
        )

        discord_id = str(message.id)
        if self.db.message_exists(discord_id):
            return

        attachments: list[dict[str, Any]] = []
        for att in message.attachments:
            attachments.append(
                {"url": att.url, "filename": att.filename, "content_type": att.content_type}
            )

        content = message.content or ""
        if not content and attachments:
            content = "[图片消息]"

        msg_id = self.db.insert_message(
            discord_message_id=discord_id,
            channel_id=str(message.channel.id),
            author=author_name,
            content=content,
            attachments=attachments,
        )

        image_text = None
        # Image OCR / vision can be added in ocr module

        try:
            await self.router.process_message(msg_id, content, author_name, image_text)
        except Exception as e:
            logger.exception("process_message failed: %s", e)
            self.db.update_message_status(msg_id, "error")
            self.db.audit("error", str(e), message_id=msg_id)

        await self.process_commands(message)


def create_bot(db: Database) -> SignalBot | None:
    if not env.discord_bot_token:
        logger.warning("DISCORD_BOT_TOKEN not set — Discord listener disabled")
        return None
    router = ActionRouter(db)
    return SignalBot(db, router)

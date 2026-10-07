"""
Telegram Bot 监听（无需 my.telegram.org 的 api_id/api_hash）。

适用：能把 Bot 拉进群/频道，且群允许 Bot 读消息。
@BotFather 创建 Bot → 把 Bot 拉进群 → /setprivacy 选 Disable。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from yoyo.copier.config import app_config, env
from yoyo.copier.ingest import ingest_message
from yoyo.copier.monitor_switches import feature_enabled, merge_channel_ids
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database
from yoyo.copier.telegram_control import control_keyboard, panel_text, render_callback_response
from yoyo.copier.telegram_channels import list_configured_channels
from yoyo.copier.notifications.telegram import send_orders_card_snapshot, send_telegram_control_panel

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


def telegram_bot_enabled() -> bool:
    return bool(app_config.telegram.enabled and env.telegram_bot_token)


async def _api(
    client: httpx.AsyncClient,
    method: str,
    *,
    request_timeout: float = 60.0,
    **params: Any,
) -> dict[str, Any]:
    url = API.format(token=env.telegram_bot_token, method=method)
    r = await client.get(url, params=params, timeout=request_timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram Bot API {method}: {data.get('description', data)}")
    return data["result"]


async def _post_api(
    client: httpx.AsyncClient, method: str, payload: dict[str, Any]
) -> dict[str, Any]:
    url = API.format(token=env.telegram_bot_token, method=method)
    r = await client.post(url, json=payload, timeout=20.0)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram Bot API {method}: {data.get('description', data)}")
    return data["result"]


async def _resolve_chat_ids(client: httpx.AsyncClient) -> dict[str, str]:
    """chat_id -> display name"""
    name_map: dict[str, str] = {}
    for ch in list_configured_channels():
        cid = str(ch.get("chat_id") or "").strip()
        username = str(ch.get("username") or "").strip().lstrip("@")
        name = ch.get("name") or username or cid
        if cid:
            name_map[cid] = name
            continue
        if not username:
            logger.warning("频道缺少 chat_id 与 username: %s", ch)
            continue
        try:
            chat = await _api(client, "getChat", chat_id=f"@{username}")
            cid = str(chat["id"])
            name_map[cid] = name
            logger.info("Bot 已解析 @%s -> %s", username, cid)
        except Exception as e:
            logger.warning("无法解析 @%s: %s", username, e)
    return name_map


def _control_chat_id() -> str:
    return str(env.telegram_notify_chat_id or "").strip()


def _is_control_chat(chat_id: str) -> bool:
    return bool(_control_chat_id() and str(chat_id) == _control_chat_id())


def _is_panel_command(text: str) -> bool:
    parts = (text or "").strip().split(maxsplit=1)
    if not parts:
        return False
    head = parts[0].lower()
    if "@" in head:
        head = head.split("@", 1)[0]
    return head in {"/start", "/panel", "/buttons", "/menu", "/help"}


def _is_bug_report(text: str) -> bool:
    stripped = (text or "").strip().lower()
    return stripped.startswith("#bug") or stripped.startswith("/bug")


def _feedback_payload(msg: dict[str, Any], chat_id: str, *, explicit_bug: bool) -> dict[str, Any]:
    reply = msg.get("reply_to_message") or {}
    reply_text = reply.get("text") or reply.get("caption") or ""
    return {
        "chat_id": chat_id,
        "message_id": msg.get("message_id"),
        "reply_to_message_id": reply.get("message_id"),
        "reply_text": str(reply_text)[:2000],
        "explicit_bug": explicit_bug,
    }


async def _send_panel(client: httpx.AsyncClient, db: Database, chat_id: str) -> None:
    await send_telegram_control_panel(db)


async def _handle_callback(
    client: httpx.AsyncClient,
    db: Database,
    callback: dict[str, Any],
) -> bool:
    data = str(callback.get("data") or "")
    if not data.startswith("ctl:"):
        return False
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    callback_id = str(callback.get("id") or "")
    if callback_id:
        try:
            await _api(
                client,
                "answerCallbackQuery",
                request_timeout=5.0,
                callback_query_id=callback_id,
            )
        except Exception as e:
            logger.warning("确认 Telegram 按钮回调失败: %s", e)
    if not _is_control_chat(chat_id):
        logger.warning("拒绝非通知频道按钮请求: %s", chat_id)
        return True
    if data == "ctl:noop":
        return True
    if data == "ctl:panel":
        await _send_panel(client, db, chat_id)
        return True
    if data == "ctl:orders_card_snapshot":
        result = await send_orders_card_snapshot(db)
        if not result.get("ok"):
            await _post_api(
                client,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": f"🔴 持仓截图失败\n<code>{str(result.get('error') or result.get('reason') or 'unknown')[:300]}</code>",
                    "parse_mode": "HTML",
                    "reply_markup": control_keyboard(),
                    "disable_web_page_preview": True,
                },
            )
        return True
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(render_callback_response, data, db),
            timeout=6.0,
        )
    except TimeoutError:
        logger.warning("Telegram 菜单渲染超时: %s", data)
        response = {
            "text": "<b>查询超时</b>\n<code>交易所响应较慢，请稍后点刷新。</code>",
            "reply_markup": control_keyboard(),
        }
    except Exception as e:
        logger.exception("Telegram 菜单渲染失败: %s", e)
        response = {
            "text": f"<b>查询失败</b>\n<code>{str(e)[:180]}</code>",
            "reply_markup": control_keyboard(),
        }
    message_id = message.get("message_id")
    if message_id:
        try:
            await _post_api(
                client,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": response["text"],
                    "parse_mode": "HTML",
                    "reply_markup": response["reply_markup"],
                    "disable_web_page_preview": True,
                },
            )
        except Exception as e:
            if "message is not modified" in str(e):
                logger.info("按钮面板内容未变化，跳过重复发送")
                return True
            logger.warning("编辑按钮面板失败，改为发送新消息: %s", e)
            await _post_api(
                client,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": response["text"],
                    "parse_mode": "HTML",
                    "reply_markup": response["reply_markup"],
                    "disable_web_page_preview": True,
                },
            )
    return True


async def run_telegram_bot_listener(db: Database) -> None:
    if not telegram_bot_enabled():
        logger.info("Telegram Bot 未配置（.env 填 TELEGRAM_BOT_TOKEN）")
        return

    router = ActionRouter(db)
    offset = 0
    recovery_update_id = 0

    async with httpx.AsyncClient() as client:
        try:
            me = await _api(client, "getMe")
            logger.info("【Telegram Bot】已连接 @%s", me.get("username"))
        except Exception as e:
            logger.error("Bot Token 无效: %s", e)
            return

        name_map = await _resolve_chat_ids(client)
        if not name_map:
            logger.warning(
                "没有配置 KOL 监听频道；Telegram Bot 将仅处理通知频道按钮"
            )

        if name_map:
            merge_channel_ids(db, list(name_map.keys()))
        db.audit("telegram_bot_start", ",".join(name_map.keys()))
        allowed = set(name_map.keys())
        if _control_chat_id():
            logger.info("【Telegram Bot】控制按钮频道: %s", _control_chat_id())
        logger.info("【Telegram Bot】监听 %s 个 KOL 群/频道", len(allowed))

        try:
            backlog = await _api(
                client,
                "getUpdates",
                timeout=0,
                allowed_updates=json.dumps(["message", "channel_post", "callback_query"]),
            )
            if backlog:
                recovery_update_id = max(int(upd["update_id"]) for upd in backlog)
                logger.info("准备补抓 Telegram Bot 积压 updates: %s", len(backlog))
        except Exception as e:
            logger.warning("初始化 Telegram Bot offset 失败: %s", e)

        while True:
            try:
                updates = await _api(
                    client,
                    "getUpdates",
                    offset=offset,
                    timeout=50,
                    allowed_updates=json.dumps(["message", "channel_post", "callback_query"]),
                )
            except Exception as e:
                logger.warning("getUpdates 失败: %s", e)
                await asyncio.sleep(5)
                continue

            for upd in updates:
                offset = max(offset, int(upd["update_id"]) + 1)
                is_recovery = int(upd["update_id"]) <= recovery_update_id
                callback = upd.get("callback_query")
                if callback and await _handle_callback(client, db, callback):
                    continue

                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue

                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                text = msg.get("text") or msg.get("caption") or ""
                if _is_control_chat(chat_id) and _is_panel_command(text):
                    if is_recovery:
                        continue
                    await _send_panel(client, db, chat_id)
                    continue
                if _is_control_chat(chat_id) and text:
                    explicit_bug = _is_bug_report(text)
                    db.audit(
                        "telegram_bug_report",
                        text[:1000],
                        payload=_feedback_payload(msg, chat_id, explicit_bug=explicit_bug),
                    )
                    if not is_recovery:
                        try:
                            await _post_api(
                                client,
                                "sendMessage",
                                {
                                    "chat_id": chat_id,
                                    "text": (
                                        "🛠 Bug 已进入待处理队列，我会保留原通知关联用于修复。"
                                        if explicit_bug
                                        else "📝 反馈已进入待处理队列，原通知关联已保留。"
                                    ),
                                    "reply_to_message_id": msg.get("message_id"),
                                    "disable_notification": True,
                                },
                            )
                        except Exception as e:
                            logger.warning("确认 Bug 反馈失败: %s", e)
                    continue

                if not feature_enabled(db, "telegram_listen"):
                    continue
                if chat_id not in allowed:
                    continue

                from_user = msg.get("from") or {}
                if from_user.get("is_bot"):
                    continue

                author = (
                    from_user.get("username")
                    or from_user.get("first_name")
                    or chat.get("title")
                    or "unknown"
                )
                if not text and msg.get("photo"):
                    text = "[图片消息]"

                external_id = f"tg_bot_{chat_id}_{msg['message_id']}"
                logger.info(
                    "Bot 收到消息 ch=%s (%s) author=%s",
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
                    source="telegram_bot_recovery" if is_recovery else "telegram_bot",
                )

            await asyncio.sleep(0.1)


def start_telegram_bot_listener(db: Database) -> None:
    try:
        asyncio.run(run_telegram_bot_listener(db))
    except KeyboardInterrupt:
        logger.info("Telegram Bot 监听已停止")
    except Exception as e:
        logger.exception("Telegram Bot 异常: %s", e)

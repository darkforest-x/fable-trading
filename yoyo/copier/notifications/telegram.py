from __future__ import annotations

import asyncio
import logging
import os
import re
from html import escape
from pathlib import Path
from typing import Any

import httpx

from yoyo.copier.admin_snapshot import capture_orders_card
from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.config import env
from yoyo.copier.exchange import exchange_label
from yoyo.copier.monitor_switches import feature_enabled
from yoyo.copier.store.sqlite import Database
from yoyo.copier.telegram_control import control_keyboard, panel_text

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT_LEN = 3900
PANEL_MESSAGE_ID_KEY = "telegram_control_panel_message_id"
CHANNEL_NAMES = {
    "988830102957736027": "ChartPrime",
    "1226095564073205780": "Woods",
    "1356581750914027590": "比特币飞扬",
    "1131521990814089276": "Arthur行情分析",
}

TITLE_ICONS = {
    "success": "🟢",
    "signal": "🟡",
    "message": "🔵",
    "risk": "🟠",
    "error": "🔴",
    "update": "🟣",
    "system": "⚪",
}


def _styled_title(title: str) -> str:
    if title and title[0] in set(TITLE_ICONS.values()):
        return title
    lower = title.lower()
    if any(word in title for word in ("失败", "异常", "断开")):
        icon = TITLE_ICONS["error"]
    elif any(word in title for word in ("风控", "跳过", "拒绝", "未执行")):
        icon = TITLE_ICONS["risk"]
    elif any(word in title for word in ("下单成功", "提交成功", "已恢复", "连接正常", "已出现")):
        icon = TITLE_ICONS["success"]
    elif any(word in title for word in ("交易更新", "交易结果", "持仓", "止盈", "止损", "平仓", "TP", "SL")):
        icon = TITLE_ICONS["update"]
    elif any(word in title for word in ("开单信号", "信号已识别")):
        icon = TITLE_ICONS["signal"]
    elif "新消息" in title or "频道消息" in title:
        icon = TITLE_ICONS["message"]
    elif "dry" in lower or "模拟" in title:
        icon = TITLE_ICONS["signal"]
    else:
        icon = TITLE_ICONS["system"]
    return f"{icon} {title}"


def telegram_notifications_configured() -> bool:
    return bool(env.telegram_bot_token and env.telegram_notify_chat_id)


def telegram_notifications_enabled(db: Database) -> bool:
    return bool(
        env.telegram_notify_enabled
        and telegram_notifications_configured()
        and not _running_under_pytest()
        and feature_enabled(db, "telegram_notify", True)
    )


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def masked_chat_id() -> str:
    cid = env.telegram_notify_chat_id.strip()
    if len(cid) <= 6:
        return cid
    return f"{cid[:4]}...{cid[-4:]}"


async def send_telegram_notification(
    db: Database,
    title: str,
    lines: list[str],
) -> dict[str, Any]:
    if not telegram_notifications_enabled(db):
        return {
            "ok": False,
            "skipped": True,
            "reason": "telegram_notifications_not_configured_or_disabled",
        }

    display_title = _styled_title(title)
    text = "\n".join([f"<b>{escape(display_title)}</b>", *[escape(line) for line in lines if line]])
    if len(text) > MAX_TEXT_LEN:
        text = text[: MAX_TEXT_LEN - 20] + "\n... truncated"

    payload = {
        "chat_id": env.telegram_notify_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp, data = await _post_telegram(client, "sendMessage", payload)
            if resp.status_code >= 400 or not data.get("ok"):
                reason = data.get("description") or resp.text
                logger.warning("Telegram notification failed: %s", reason)
                db.audit("telegram_notify_error", str(reason)[:500])
                return {"ok": False, "error": reason}
            sent_message_id = data.get("result", {}).get("message_id")
            db.audit(
                "telegram_notify_sent",
                title,
                payload={"message_id": sent_message_id} if sent_message_id else {},
            )
            panel = await _refresh_control_panel(client, db)
            return {"ok": True, "message_id": sent_message_id, "panel": panel}
    except Exception as e:
        logger.warning("Telegram notification exception: %s", e)
        db.audit("telegram_notify_error", str(e)[:500])
        return {"ok": False, "error": str(e)}


async def send_telegram_control_panel(db: Database) -> dict[str, Any]:
    if not telegram_notifications_enabled(db):
        return {
            "ok": False,
            "skipped": True,
            "reason": "telegram_notifications_not_configured_or_disabled",
        }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            return await _refresh_control_panel(client, db)
    except Exception as e:
        db.audit("telegram_panel_error", str(e)[:500])
        return {"ok": False, "error": str(e)}


async def send_orders_card_snapshot(db: Database) -> dict[str, Any]:
    if not telegram_notifications_enabled(db):
        return {
            "ok": False,
            "skipped": True,
            "reason": "telegram_notifications_not_configured_or_disabled",
        }

    try:
        image_path = await asyncio.to_thread(capture_orders_card)
        caption = "\n".join(
            [
                f"<b>{escape(_styled_title('当前持仓与订单截图'))}</b>",
                escape("实时面板快照 · 持仓 + 挂单"),
            ]
        )
        async with httpx.AsyncClient(timeout=25) as client:
            image_bytes = Path(image_path).read_bytes()
            resp, data = await _post_telegram_file(
                client,
                "sendPhoto",
                data={
                    "chat_id": env.telegram_notify_chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                files={
                    "photo": (
                        "orders-card.png",
                        image_bytes,
                        "image/png",
                    )
                },
            )
            if resp.status_code >= 400 or not data.get("ok"):
                reason = data.get("description") or resp.text
                db.audit("telegram_orders_card_error", str(reason)[:500])
                return {"ok": False, "error": reason}
            sent_message_id = data.get("result", {}).get("message_id")
            db.audit(
                "telegram_orders_card_sent",
                "orders card snapshot",
                payload={"message_id": sent_message_id} if sent_message_id else {},
            )
            panel = await _refresh_control_panel(client, db)
            return {"ok": True, "message_id": sent_message_id, "panel": panel}
    except Exception as e:
        logger.warning("Telegram orders card snapshot failed: %s", e)
        db.audit("telegram_orders_card_error", str(e)[:500])
        return {"ok": False, "error": str(e)}


async def _refresh_control_panel(
    client: httpx.AsyncClient,
    db: Database,
) -> dict[str, Any]:
    """Replace the previous panel so the main controls remain the latest message."""
    previous_id = db.get_setting(PANEL_MESSAGE_ID_KEY)
    if previous_id:
        try:
            await _post_telegram(
                client,
                "deleteMessage",
                {
                    "chat_id": env.telegram_notify_chat_id,
                    "message_id": int(previous_id),
                },
            )
        except Exception:
            logger.debug("Unable to delete previous Telegram control panel", exc_info=True)

    resp, data = await _post_telegram(
        client,
        "sendMessage",
        {
            "chat_id": env.telegram_notify_chat_id,
            "text": panel_text(db),
            "parse_mode": "HTML",
            "reply_markup": control_keyboard(),
            "disable_web_page_preview": True,
        },
    )
    if resp.status_code >= 400 or not data.get("ok"):
        reason = data.get("description") or resp.text
        db.audit("telegram_panel_error", str(reason)[:500])
        return {"ok": False, "error": reason}

    message_id = data.get("result", {}).get("message_id")
    if message_id:
        db.set_setting(PANEL_MESSAGE_ID_KEY, str(message_id))
    db.audit("telegram_panel_sent", f"control panel #{message_id or '?'}")
    return {"ok": True, "message_id": message_id}


async def _post_telegram_file(
    client: httpx.AsyncClient,
    method: str,
    *,
    data: dict[str, Any],
    files: dict[str, Any],
    retries: int = 2,
) -> tuple[httpx.Response, dict[str, Any]]:
    url = API.format(token=env.telegram_bot_token, method=method)
    last_resp: httpx.Response | None = None
    last_data: dict[str, Any] = {}
    for attempt in range(retries + 1):
        resp = await client.post(url, data=data, files=files)
        last_resp = resp
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        last_data = payload if isinstance(payload, dict) else {}
        if resp.status_code != 429 and "Too Many Requests" not in str(last_data.get("description") or ""):
            return resp, last_data
        if attempt >= retries:
            return resp, last_data
        await asyncio.sleep(_telegram_retry_after(last_data))
    assert last_resp is not None
    return last_resp, last_data


async def _post_telegram(
    client: httpx.AsyncClient,
    method: str,
    payload: dict[str, Any],
    *,
    retries: int = 2,
) -> tuple[httpx.Response, dict[str, Any]]:
    url = API.format(token=env.telegram_bot_token, method=method)
    last_resp: httpx.Response | None = None
    last_data: dict[str, Any] = {}
    for attempt in range(retries + 1):
        resp = await client.post(url, json=payload)
        last_resp = resp
        try:
            data = resp.json()
        except ValueError:
            data = {}
        last_data = data if isinstance(data, dict) else {}
        if resp.status_code != 429 and "Too Many Requests" not in str(last_data.get("description") or ""):
            return resp, last_data
        if attempt >= retries:
            return resp, last_data
        await asyncio.sleep(_telegram_retry_after(last_data))
    assert last_resp is not None
    return last_resp, last_data


def _telegram_retry_after(data: dict[str, Any]) -> float:
    params = data.get("parameters") if isinstance(data, dict) else {}
    try:
        retry_after = float((params or {}).get("retry_after") or 3)
    except (TypeError, ValueError):
        retry_after = 3
    return min(max(retry_after, 1), 60) + 0.25


async def notify_channel_message(
    db: Database,
    *,
    channel_id: str,
    author: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    source: str = "unknown",
) -> dict[str, Any]:
    channel_name = CHANNEL_NAMES.get(str(channel_id), str(channel_id))
    image_urls = _image_attachment_urls(attachments or [])
    if image_urls:
        return await _send_channel_media_message(
            db,
            channel_name=channel_name,
            author=author,
            content=content,
            source=source,
            image_urls=image_urls,
        )
    return await send_telegram_notification(
        db,
        f"{channel_name} 频道消息",
        [
            f"👤 {author or 'unknown'}",
            f"💬 {_truncate(content, 3000)}",
            f"· {channel_name} · {source}",
        ],
    )


async def _send_channel_media_message(
    db: Database,
    *,
    channel_name: str,
    author: str,
    content: str,
    source: str,
    image_urls: list[str],
) -> dict[str, Any]:
    if not telegram_notifications_enabled(db):
        return {
            "ok": False,
            "skipped": True,
            "reason": "telegram_notifications_not_configured_or_disabled",
        }

    title = _styled_title(f"{channel_name} 频道图片")
    caption = "\n".join(
        [
            f"<b>{escape(title)}</b>",
            escape(f"👤 {author or 'unknown'}"),
            escape(f"💬 {_truncate(content, 700)}"),
            escape(f"· {channel_name} · {source}"),
        ]
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            if len(image_urls) == 1:
                resp, data = await _post_telegram(
                    client,
                    "sendPhoto",
                    {
                        "chat_id": env.telegram_notify_chat_id,
                        "photo": image_urls[0],
                        "caption": caption[:1024],
                        "parse_mode": "HTML",
                    },
                )
            else:
                media = [
                    {
                        "type": "photo",
                        "media": url,
                        **(
                            {
                                "caption": caption[:1024],
                                "parse_mode": "HTML",
                            }
                            if index == 0
                            else {}
                        ),
                    }
                    for index, url in enumerate(image_urls[:10])
                ]
                resp, data = await _post_telegram(
                    client,
                    "sendMediaGroup",
                    {
                        "chat_id": env.telegram_notify_chat_id,
                        "media": media,
                    },
                )
            if resp.status_code >= 400 or not data.get("ok"):
                reason = data.get("description") or resp.text
                db.audit("telegram_media_error", str(reason)[:500], payload={"images": image_urls[:3]})
                return await _send_channel_media_fallback(
                    db,
                    channel_name=channel_name,
                    author=author,
                    content=content,
                    source=source,
                    image_urls=image_urls,
                    reason=str(reason),
                )
            result = data.get("result")
            if isinstance(result, list):
                sent_message_id = result[0].get("message_id") if result else None
            else:
                sent_message_id = (result or {}).get("message_id")
            db.audit(
                "telegram_notify_sent",
                f"{channel_name} 频道图片",
                payload={"message_id": sent_message_id, "images": len(image_urls)},
            )
            panel = await _refresh_control_panel(client, db)
            return {"ok": True, "message_id": sent_message_id, "images": len(image_urls), "panel": panel}
    except Exception as e:
        db.audit("telegram_media_error", str(e)[:500], payload={"images": image_urls[:3]})
        return await _send_channel_media_fallback(
            db,
            channel_name=channel_name,
            author=author,
            content=content,
            source=source,
            image_urls=image_urls,
            reason=str(e),
        )


async def _send_channel_media_fallback(
    db: Database,
    *,
    channel_name: str,
    author: str,
    content: str,
    source: str,
    image_urls: list[str],
    reason: str,
) -> dict[str, Any]:
    lines = [
        f"👤 {author or 'unknown'}",
        f"💬 {_truncate(content, 1200)}",
        f"🖼 图片链接: {' '.join(image_urls[:3])}",
        f"· {channel_name} · {source}",
        f"图片直发失败: {_truncate(reason, 160)}",
    ]
    return await send_telegram_notification(db, f"{channel_name} 频道图片", lines)


def _image_attachment_urls(attachments: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for attachment in attachments:
        url = str(attachment.get("url") or attachment.get("proxy_url") or "").strip()
        if not url:
            continue
        content_type = str(attachment.get("content_type") or attachment.get("contentType") or "").lower()
        filename = str(attachment.get("filename") or "").lower()
        if content_type.startswith("image/") or content_type == "image" or re.search(r"\.(?:png|jpe?g|webp|gif)(?:[?#]|$)", url, re.I) or re.search(r"\.(?:png|jpe?g|webp|gif)$", filename, re.I):
            if url not in urls:
                urls.append(url)
    return urls


def _price_range(result: IntentResult) -> str:
    low = result.entry_low
    high = result.entry_high
    if low is not None and high is not None:
        return f"{low:g}-{high:g}"
    if low is not None:
        return f"{low:g}"
    if high is not None:
        return f"{high:g}"
    return "-"


def _truncate(text: str, limit: int = 500) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def _order_id(exec_result: dict[str, Any] | None) -> str:
    if not exec_result:
        return ""
    response = exec_result.get("response") or {}
    data = response.get("data") or []
    if isinstance(data, list) and data:
        return str(data[0].get("ordId") or "")
    return ""


def _extract_update_symbol(content: str, result: IntentResult) -> str:
    if result.symbol:
        return result.symbol.upper()
    text = content or ""
    patterns = [
        r"#\s*([A-Z0-9]{2,15})(?:USDT\.P|USDT)?\b",
        r"\b([A-Z0-9]{2,15})(?:USDT\.P|USDT)?\s+\d+(?:st|nd|rd|th)?\s+TARGET",
        r"\b([A-Z0-9]{2,15})\s+(?:LONG|SHORT)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            token = m.group(1).upper()
            if token not in {"TARGET", "TARGETS", "ENTRY", "EXIT", "THIS", "LONG", "SHORT"}:
                return token
    return "-"


def _classify_trade_update(content: str, result: IntentResult) -> dict[str, str]:
    text = " ".join((content or "").split())
    lower = text.lower()
    label = {
        "partial_close": "TP/部分止盈",
        "close": "平仓/交易结束",
        "update_sl": "止损更新",
        "update_tp": "止盈更新",
        "cancel": "撤单/取消",
        "hold": "持有/成交更新",
    }.get(result.intent, "交易更新")
    if re.search(r"\ball\s+targets?\s+hit\b|全部止盈|全目标", lower, re.I):
        label = "全部 TP 命中"
    elif re.search(r"\b(?:sl|stop\s*loss)\s*(?:hit|triggered)\b|止损命中|打止损", lower, re.I):
        label = "SL 命中"
    elif re.search(r"\btargets?\s+hit\b|\btp\s*\d*\s*(?:hit|命中)?|止盈命中", lower, re.I):
        label = "TP 命中"
    elif re.search(r"\btrade\s+closed\b|已平仓|平仓", lower, re.I):
        label = "平仓/交易结束"
    elif re.search(r"\b(?:limit\s+)?order\s+(?:has\s+been\s+)?filled\b|限价(?:订单)?已成交|订单已成交|挂单已成交", lower, re.I):
        label = "限价订单已成交"

    target = "-"
    target_m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+TARGET\s+HIT\b", text, re.I)
    if target_m:
        target = f"TP{target_m.group(1)}"
    else:
        tp_m = re.search(r"\bTP\s*([0-9]+)?\s*(?:hit|命中)?", text, re.I)
        if tp_m:
            target = f"TP{tp_m.group(1)}" if tp_m.group(1) else "TP"

    price = "-"
    price_patterns = [
        r"\bat\s*([0-9]+(?:\.[0-9]+)?)\b",
        r"(?:价格|价位|点位)[：:\s]*([0-9]+(?:\.[0-9]+)?)",
        r"(?:止盈|止损|TP|SL)\s*\d*\s*(?:hit|命中)?\s*[:：-]?\s*([0-9]+(?:\.[0-9]+)?)",
    ]
    for pattern in price_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            price = m.group(1)
            break

    return {
        "label": label,
        "symbol": _extract_update_symbol(content, result),
        "target": target,
        "price": price,
    }


def _order_type_label(plan: dict[str, Any]) -> str:
    order_type = str(plan.get("ord_type") or plan.get("order_type") or "").lower()
    entry_note = str(plan.get("entry_note") or "").lower()
    if "market" in order_type or "market" in entry_note:
        return "市价单"
    if "limit" in order_type or "limit" in entry_note:
        return "限价单"
    if plan.get("px") is not None:
        return "限价单"
    return "订单"


async def notify_signal_detected(
    db: Database,
    *,
    message_id: int,
    author: str,
    content: str,
    result: IntentResult,
) -> dict[str, Any]:
    lines = [
        f"📌 {result.inst_id() or '-'} · {(result.side or '-').upper()}",
        f"入场 {_price_range(result)} · 止损 {result.stop_loss:g}"
        if result.stop_loss is not None
        else f"入场 {_price_range(result)} · 止损 -",
        f"止盈 {result.take_profit:g}" if result.take_profit is not None else "止盈 -",
        f"👤 {author or 'unknown'} · 信心 {result.confidence:.2f} · #{message_id}",
        f"💬 {_truncate(content, 420)}",
    ]
    return await send_telegram_notification(
        db,
        f"开单信号 · {result.symbol or '-'} {(result.side or '-').upper()}",
        lines,
    )


async def notify_trade_update(
    db: Database,
    *,
    message_id: int,
    author: str,
    content: str,
    result: IntentResult,
) -> dict[str, Any]:
    update = _classify_trade_update(content, result)
    lines = [
        f"📌 {update['symbol']} · {update['label']}",
        f"目标 {update['target']} · 价格 {update['price']}",
        f"👤 {author or 'unknown'} · 信心 {result.confidence:.2f} · #{message_id}",
        f"💬 {_truncate(content, 520)}",
    ]
    return await send_telegram_notification(db, f"交易更新 · {update['symbol']}", lines)


async def notify_trade_pipeline(
    db: Database,
    *,
    title: str,
    message_id: int,
    author: str,
    content: str,
    result: IntentResult,
    detail: str = "",
    exec_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if exec_result and exec_result.get("dry_run"):
        db.audit(
            "telegram_notify_skipped",
            "dry_run_trade_pipeline",
            message_id=message_id,
            payload={"title": title},
        )
        return {"ok": False, "skipped": True, "reason": "dry_run_notification_disabled"}

    plan = (exec_result or {}).get("plan") or {}
    exchange = str(plan.get("exchange") or exchange_label(db)).upper()
    is_live = exec_result is not None and not exec_result.get("dry_run")
    status = "实盘" if is_live else "模拟/未下单"
    if title.startswith("风控"):
        status = "风控拒绝"
    elif "跳过" in title or "未执行" in title:
        status = "未执行"
    elif "失败" in title:
        status = "失败"

    lines = [
        f"📌 {result.inst_id() or '-'} · {(result.side or '-').upper()} · {status}",
        (
            f"入场 {_price_range(result)} · 止损 {result.stop_loss:g}"
            if result.stop_loss is not None
            else f"入场 {_price_range(result)} · 止损 -"
        )
        + (f" · 止盈 {result.take_profit:g}" if result.take_profit is not None else ""),
    ]
    if title.startswith("风控"):
        lines.append("状态 · 未下单，无新增持仓")
    if plan:
        mode = "全仓" if plan.get("td_mode") == "cross" else str(plan.get("td_mode") or "-")
        pct = float(plan.get("position_pct") or 0)
        if pct >= 0.999:
            position_desc = "全仓"
        elif pct > 0:
            position_desc = f"账户 {pct * 100:.2f}%"
        else:
            position_desc = "-"
        leverage = f"{plan['leverage']}x" if plan.get("leverage") is not None else "-"
        notional_leverage = plan.get("notional_leverage")
        if notional_leverage is not None and notional_leverage != plan.get("leverage"):
            leverage = f"名义 {notional_leverage}x / 交易所 {leverage}"
        lines.append(f"仓位 {position_desc} · {mode} {leverage}")
        margin = (
            f"{float(plan['margin_usdt']):.4f} USDT"
            if plan.get("margin_usdt") is not None
            else "-"
        )
        notional = (
            f"{float(plan['notional_usdt']):.4f} USDT"
            if plan.get("notional_usdt") is not None
            else "-"
        )
        lines.append(f"保证金 {margin} · 名义 {notional}")
        order_bits = []
        order_type_label = _order_type_label(plan)
        order_bits.append(f"订单类型 {order_type_label}")
        if plan.get("px") is not None:
            price_label = "限价委托价" if order_type_label == "限价单" else "委托价"
            order_bits.append(f"{price_label} {float(plan['px']):g}")
        if order_bits:
            lines.append(" · ".join(order_bits))
    oid = _order_id(exec_result)
    if oid:
        lines.append(f"订单 {exchange} · {oid}")
    lines.append(f"👤 {author or 'unknown'} · 信心 {result.confidence:.2f} · #{message_id}")
    if detail:
        lines.append(f"结果 · {detail}")
    lines.append(f"💬 {_truncate(content)}")
    return await send_telegram_notification(db, title, lines)

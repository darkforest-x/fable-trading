"""Telegram Bot API delivery with explicit receipts and conservative retries.

Source: https://core.telegram.org/bots/api#sendphoto, #sendmessage and #responseparameters.
Credentials are loaded only in process from the existing owner configuration.
They never enter API responses, logs, SQLite or exception strings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import requests

from yoyo.monitor import SIGNAL_KIND, TV_INTERVALS
from yoyo.monitor.notification_policy import channel_enabled, delivery_error
from yoyo.monitor.store import now_ms
from yoyo.notify import _load


def credentials():
    try:
        return _load()
    except Exception:
        return None


def message(event):
    """Name each stage and keep original-arrow/confirmation clocks explicit."""
    def clock(value):
        return datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    side = "🟢 多头" if event["side"] == "long" else "🔴 空头"
    symbol = event["symbol"].removesuffix("-SWAP")
    if event["kind"] == SIGNAL_KIND:
        return (f"{symbol} · {event['timeframe']} · {side}\n"
                "指标启动 · 未经 YOLO 确认\n"
                f"收盘价 {event['price']:.10g} · {clock(event['bar_close_ms'])} 北京时间")
    indicator, model = event["indicator"], event["model"]
    return (f"{symbol} · {event['timeframe']} · {side}\n"
            f"YOLO 确认 · 等待 {model['wait_bars']} 根\n"
            f"确认 {event['price']:.10g} · {clock(event['bar_close_ms'])}\n"
            f"原箭头 {indicator['price']:.10g} · {clock(indicator['bar_close_ms'])} 北京时间")


def markup(event):
    """Keep the chart URL behind a single explicit button."""
    tv_symbol = event["symbol"].removesuffix("-SWAP").replace("-", "") + ".P"
    interval = TV_INTERVALS[event["timeframe"]]
    return {"inline_keyboard": [[{"text": "打开 TradingView ↗",
                                  "url": f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}&interval={interval}"}]]}


class TelegramWorker:
    def __init__(self, store, creds=None, sender=None):
        self.store = store
        self.creds = creds if creds is not None else credentials()
        self.sender = sender or requests.post

    def deliver_once(self, now=None):
        now = now if now is not None else now_ms()
        if not self.creds:
            return False
        if not channel_enabled(self.store, "telegram"):
            return False
        row = self.store.claim(now)
        if not row:
            return False
        event, eid = row["event"], row["event_id"]
        error = delivery_error(self.store, event, now, "telegram")
        if error is not None:
            self.store.finish(eid, "skipped", error=error)
            return True
        token, chat = self.creds
        photo = row.get("png")
        # Corrupt or unavailable local media must not consume a valid signal.
        # Fall back before any HTTP attempt; never after an uncertain upload.
        if photo and hashlib.sha256(photo).hexdigest() != row.get("photo_sha256"):
            photo = None
        try:
            url = "https://api.telegram.org/bot" + token
            if photo:
                response = self.sender(url + "/sendPhoto",
                                       data={"chat_id": chat, "caption": message(event),
                                             "show_caption_above_media": "true",
                                             "reply_markup": json.dumps(markup(event), ensure_ascii=False)},
                                       files={"photo": ("imacd-signal.png", photo, "image/png")},
                                       timeout=(6, 20), allow_redirects=False)
            else:
                response = self.sender(url + "/sendMessage",
                                       json={"chat_id": chat, "text": message(event),
                                             "reply_markup": markup(event), "disable_web_page_preview": True},
                                       timeout=(6, 15), allow_redirects=False)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid_response_object")
            if response.status_code >= 500 or (payload.get("ok") is True and response.status_code != 200):
                raise ValueError("telegram_http_delivery_uncertain")
            if payload.get("ok") is True:
                result = payload.get("result")
                if not isinstance(result, dict) or type(result.get("message_id")) is not int or result["message_id"] <= 0:
                    raise ValueError("missing_delivery_receipt")
                if photo and not (isinstance(result.get("photo"), list) and any(
                        isinstance(size, dict) and isinstance(size.get("file_id"), str)
                        and type(size.get("width")) is int and size["width"] > 0
                        and type(size.get("height")) is int and size["height"] > 0
                        for size in result["photo"])):
                    raise ValueError("missing_photo_receipt")
            else:
                code = int(payload.get("error_code", response.status_code))
                params = payload.get("parameters") or {}
                if not isinstance(params, dict):
                    raise ValueError("invalid_error_parameters")
                delay = max(5, min(3600, int(params.get("retry_after", 30))))
        except Exception:
            self.store.finish(eid, "unknown", error="delivery_uncertain_no_automatic_resend")
            return True
        if payload.get("ok") is True:
            self.store.finish(eid, "sent", message_id=payload.get("result", {}).get("message_id"))
        else:
            if code == 429 and row["attempts"] < 5:
                self.store.finish(eid, "pending", error="telegram_rate_limited", due_ms=now + delay * 1000)
            elif code >= 500:
                self.store.finish(eid, "unknown", error="telegram_server_delivery_uncertain")
            else:
                self.store.finish(eid, "failed", error="telegram_rejected_" + str(code))
        return True

    def status(self):
        result = self.store.notification_status("telegram")
        result["sent"] = result.get("sent", 0)
        result["historical_sent"] = self.store.telegram_status().get("sent", 0) - result["sent"]
        result["last_signal_success_ms"] = result.get("last_success_ms")
        probe = self.store.get_meta("notification_probe", {})
        result["probe_status"] = probe.get("status", "not_tested")
        if probe.get("status") == "sent":
            result["last_success_ms"] = max(result.get("last_success_ms") or 0, probe["at_ms"])
        return dict(result, configured=bool(self.creds), enabled=bool(self.creds),
                    delivery_format="chart_with_compact_caption", **self.store.notification_media_status())


def send_startup_probe(store):
    """One quiet, explicitly labelled service test, separate from signal events."""
    previous = store.get_meta("notification_probe")
    if previous:
        return previous
    creds = credentials()
    if not creds:
        return {"status": "not_configured"}
    receipt = {"status": "unknown", "at_ms": now_ms(), "kind": "service_startup_test"}
    store.set_meta("notification_probe", receipt)
    token, chat = creds
    try:
        response = requests.post("https://api.telegram.org/bot" + token + "/sendMessage", json={
            "chat_id": chat, "disable_notification": True, "disable_web_page_preview": True,
            "text": "FABLE · 监控服务启动测试\n\n这台 Mac 已启动 OKX 全部在交易永续合约监控：1H / 4H。\n只按当前 TradingView 主图可见蓄势释放标记推送收盘确认信号及点位。\n\n本机页面：http://127.0.0.1:8766\n此地址在这台 Mac 打开。\n\n这是一条通知链路测试，不是交易信号。历史回填不补发。"
        }, timeout=(6, 15))
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict) and payload.get("ok") is True and type(result.get("message_id")) is int:
            receipt.update(status="sent", message_id=result["message_id"])
        elif isinstance(payload, dict) and payload.get("ok") is False:
            receipt.update(status="failed", error="telegram_rejected")
    except Exception:
        pass
    store.set_meta("notification_probe", receipt)
    return receipt

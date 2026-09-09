"""Telegram Bot API delivery with explicit receipts and conservative retries.

Source: https://core.telegram.org/bots/api#sendphoto, #sendmessage and #responseparameters.
The owner has disabled this channel: workers default off and never load
credentials or claim outboxes while disabled. Explicit test opt-in retains
delivery/receipt coverage. Credentials never enter API responses or logs.
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
    def __init__(self, store, creds=None, sender=None, *, enabled=False):
        self.store = store
        self.enabled = enabled is True
        self.creds = (creds if creds is not None else credentials()) if self.enabled else None
        self.sender = sender or requests.post

    def deliver_once(self, now=None):
        if not self.enabled:
            return False
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
        return dict(result, configured=bool(self.creds), enabled=self.enabled and bool(self.creds),
                    disabled_by_owner=not self.enabled,
                    delivery_format="chart_with_compact_caption", **self.store.notification_media_status())


def send_startup_probe(store):
    """Keep the legacy entry point inert; never read keys or send a probe."""
    return {"status": "disabled", "disabled_by_owner": True}

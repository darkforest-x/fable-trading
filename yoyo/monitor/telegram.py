"""Telegram Bot API delivery with explicit receipts and conservative retries.

Source: https://core.telegram.org/bots/api#sendmessage and #responseparameters.
Credentials are loaded only in process from the existing owner configuration.
They never enter API responses, logs, SQLite or exception strings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import requests

from yoyo.monitor import FRESH_MS
from yoyo.monitor.store import now_ms
from yoyo.notify import _load


def credentials():
    try:
        return _load()
    except Exception:
        return None


def message(event):
    side = "向上 ↑" if event["side"] == "long" else "向下 ↓"
    labels = {"release": "蓄势释放", "entry": "系统启动", "exit": "趋势结束", "retest": "蓄势影线回踩"}
    time = datetime.fromtimestamp(event["bar_close_ms"] / 1000, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    htf = "许可" if event.get("htf_allowed") is True else "未许可" if event.get("htf_allowed") is False else "数据不足"
    symbol = event["symbol"]
    tv_symbol = symbol.removesuffix("-SWAP").replace("-", "") + ".P"
    interval = "60" if event["timeframe"] == "1H" else "240"
    lines = ["FABLE · " + labels.get(event["kind"], event["kind"]),
             f"{symbol} · {event['timeframe']} · {side}",
             f"信号收盘价 {event['price']:.10g}", f"确认时间 {time} 北京时间",
             f"近零蓄势 {event.get('near_zero_bars', 0)} 根 · 精确零轴 {event.get('zero_bars', 0)} 根",
             f"均线密集 {'满足' if event.get('dense') else '未满足'} · 高周期 {htf}"]
    if event["kind"] == "release":
        lines.append("近零蓄势释放观察；是否系统入场请看独立启动信号。")
    elif event["kind"] == "exit":
        lines.append("指标趋势结束；并非账户平仓回执。")
    lines.extend([f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}&interval={interval}",
                  "Mac 监控 · 已收盘确认 · 点位为信号收盘价"])
    return "\n".join(lines)


class TelegramWorker:
    def __init__(self, store, creds=None, sender=None):
        self.store = store
        self.creds = creds if creds is not None else credentials()
        self.sender = sender or requests.post

    def deliver_once(self, now=None):
        now = now if now is not None else now_ms()
        if not self.creds:
            return False
        row = self.store.claim(now)
        if not row:
            return False
        event, eid = row["event"], row["event_id"]
        if not 0 <= now - event["bar_close_ms"] <= FRESH_MS:
            self.store.finish(eid, "skipped", error="signal_expired")
            return True
        token, chat = self.creds
        try:
            response = self.sender("https://api.telegram.org/bot" + token + "/sendMessage",
                                   json={"chat_id": chat, "text": message(event), "disable_web_page_preview": True},
                                   timeout=(6, 15))
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid_response_object")
            if payload.get("ok") is True:
                result = payload.get("result")
                if not isinstance(result, dict) or type(result.get("message_id")) is not int or result["message_id"] <= 0:
                    raise ValueError("missing_delivery_receipt")
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
        result = self.store.telegram_status()
        result["last_signal_success_ms"] = result.get("last_success_ms")
        probe = self.store.get_meta("notification_probe", {})
        result["probe_status"] = probe.get("status", "not_tested")
        if probe.get("status") == "sent":
            result["last_success_ms"] = max(result.get("last_success_ms") or 0, probe["at_ms"])
        return dict(result, configured=bool(self.creds), enabled=bool(self.creds))


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
            "text": "FABLE · 监控服务启动测试\n\n这台 Mac 已启动 OKX 全部在交易永续合约监控：1H / 4H。\n蓄势释放、系统启动与趋势结束会推送确认收盘价；影线回踩在前端查看。\n\n本机页面：http://127.0.0.1:8766\n此地址在这台 Mac 打开。\n\n这是一条通知链路测试，不是交易信号。仅已收盘确认，历史回填不补发。"
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

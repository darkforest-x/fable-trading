"""Private Bark V2 signal delivery, independent of Telegram's journal.

Sources: https://github.com/Finb/bark-server/blob/master/docs/API_V2.md
and router.go / route_push.go in that official repository. POST /push keeps
the device key out of URLs. HTTP 200, code 200 and a server timestamp mean
server acceptance, not an iPhone display/read receipt. Current direct-start or
model-confirmation eligibility, channel cutover and shared freshness gate apply.
No connectivity/startup messages are generated. Errors never include keys.

Both ordinary V12.8 stages and joint alerts use this channel's own receipts.
Mobile navigation uses TradingView's declared /chart/ Universal Link. Its
apple-app-site-association explicitly excludes /chart/?symbol=... from iOS
app routing (checked 2026-09-09); the exact symbol/timeframe URL stays in the
body as a web fallback. App launch does not imply symbol/timeframe navigation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

import requests

from yoyo.monitor import SIGNAL_KIND, MODEL_PROTOCOL, TV_INTERVALS, timeframe_label
from yoyo.monitor.joint_notifications import JOINT_PROTOCOL
from yoyo.monitor.notification_policy import channel_enabled, delivery_error
from yoyo.monitor.store import now_ms
from yoyo.monitor.v130_policy import SIGNAL_KIND as V130_KIND, SIGNAL_PROTOCOL as V130_PROTOCOL

SERVER = "https://api.day.app"
TV_APP_URL = "https://www.tradingview.com/chart/"
POLICY_KEY = "notification_policy:bark:" + MODEL_PROTOCOL
KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,128}")


def save_configuration(endpoint, directory):
    """Save the owner's exact api.day.app device privately, without any push."""
    url = urlsplit(endpoint)
    key = url.path.strip("/")
    if (url.scheme != "https" or url.netloc != "api.day.app" or url.query
            or url.fragment or not KEY_PATTERN.fullmatch(key)):
        raise ValueError("invalid_bark_endpoint")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".bark-", dir=directory)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump({"device_key": key, "enabled": True}, file)
            file.write("\n")
        os.chmod(name, 0o600)
        os.replace(name, directory / "bark.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def credentials(directory):
    """Read only this monitor's private config; never expose malformed values."""
    try:
        data = json.loads((Path(directory) / "bark.json").read_text())
        key = data.get("device_key")
        if data.get("enabled") is True and isinstance(key, str) and KEY_PATTERN.fullmatch(key):
            return key
    except Exception:
        pass
    return None


def message(event):
    """Signal fields only; no device identifier or credentials in the payload."""
    def time(value):
        return datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    side = "向上 ↑" if event["side"] == "long" else "向下 ↓"
    symbol = event["symbol"]
    tv_symbol = symbol.removesuffix("-SWAP").replace("-", "") + ".P"
    interval = TV_INTERVALS[event["timeframe"]]
    web_url = f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}&interval={interval}"
    if event.get("protocol") == JOINT_PROTOCOL and event.get("kind") == "joint":
        source = {"chart": "本周期", "higher": "上级周期", "both": "本周期 + 上级周期"}.get(
            event.get("source"), "本周期")
        stop = event.get("reference_stop")
        reference = "—" if not isinstance(stop, (int, float)) else f"{stop:.10g}"
        title = f"突破+spike · {symbol} · {timeframe_label(event['timeframe'])} · 向上 ↑"
        body = (f"多头框内 · {source}突破\n"
                f"收盘价 {event['price']:.10g} · {time(event['bar_close_ms'])} 北京收盘\n"
                f"原始 SPIKE 时间 {time(event['v9_signal_close_ms'])} 北京时间\n"
                f"参考止损（非成交价） {reference}")
        return {"title": title, "subtitle": "突破+spike 联合监控", "body": body + f"\n\n网页备用：{web_url}",
                "group": "SPIKE lines", "level": "active", "isArchive": "1", "copy": f"OKX:{tv_symbol}",
                "url": TV_APP_URL}
    if event.get("protocol") == V130_PROTOCOL and event.get("kind") == V130_KIND:
        body = (f"锚点 {time(event['anchor_close_ms'])} · 突破 {time(event['breakout_close_ms'])}\n"
                f"回踩 {time(event['retest_close_ms'])} · 最终确认收盘 {time(event['bar_close_ms'])}\n"
                f"确认收盘参考 {event['price']:.10g} · 参考价格 {event['reference_price']:.10g}\n"
                f"风险参考 {event['risk']:.10g} · 初始止损 {event['initial_stop']:.10g}\n"
                "次开参考，不代表成交；后台计算，未逐根核验 TradingView 图上信号")
        return {"title": f"V13.1 · {symbol} · {event['timeframe']} · {side}",
                "subtitle": f"V13.1 {event['timeframe']} 回踩再突破 · 后台确认",
                "body": body + f"\n\n网页备用：{web_url}", "group": "SPIKE V13.1",
                "level": "active", "isArchive": "1", "copy": f"OKX:{tv_symbol}",
                "url": TV_APP_URL}
    if event["kind"] == SIGNAL_KIND:
        subtitle = "V12.8 后台信号 · 未经 YOLO 确认"
        body = f"收盘价 {event['price']:.10g} · {time(event['bar_close_ms'])} 北京时间"
    else:
        indicator, model = event["indicator"], event["model"]
        subtitle = f"V12.8 后台信号 · YOLO 确认 · 等待 {model['wait_bars']} 根"
        body = (f"确认 {event['price']:.10g} · {time(event['bar_close_ms'])}\n"
                f"原始信号 {indicator['price']:.10g} · {time(indicator['bar_close_ms'])} 北京时间")
    # The monitor observes OKX candles; it has no receipt from the user's Pine chart.
    body += "\n来源：后台计算；未核验 TradingView 图上信号"
    return {"title": f"V12.8 · {symbol} · {timeframe_label(event['timeframe'])} · {side}",
            "subtitle": subtitle,
            "body": body + f"\n\n网页备用：{web_url}",
            "group": "SPIKE V12.8", "level": "active", "isArchive": "1",
            # Bark's long-press Copy action uses this value. Ordinary taps
            # only open the URL; do not promise clipboard changes on iOS.
            "copy": f"OKX:{tv_symbol}",
            "url": TV_APP_URL}


class BarkWorker:
    def __init__(self, store, creds=None, sender=None):
        self.store = store
        self.creds = creds if creds is not None else credentials(store.path.parent)
        self.sender = sender or requests.post

    def deliver_once(self, now=None):
        now = now if now is not None else now_ms()
        if not self.creds:
            return False
        if not channel_enabled(self.store, "bark"):
            return False
        row = self.store.claim_bark(now)
        if not row:
            return False
        event, eid = row["event"], row["event_id"]
        error = delivery_error(self.store, event, now, "bark")
        if error is not None:
            self.store.finish_bark(eid, "skipped", error=error)
            return True
        try:
            response = self.sender(SERVER + "/push", json=dict(message(event), device_key=self.creds),
                                   timeout=(6, 15), allow_redirects=False)
            http_code = response.status_code
            if http_code == 429:
                delay = response.headers.get("Retry-After", "30")
                delay = max(5, min(3600, int(delay))) if str(delay).isdigit() else 30
                if row["attempts"] < 5:
                    self.store.finish_bark(eid, "pending", error="bark_rate_limited", due_ms=now + delay * 1000)
                else:
                    self.store.finish_bark(eid, "failed", error="bark_rate_limit_exhausted")
                return True
            if 400 <= http_code < 500:
                self.store.finish_bark(eid, "failed", error="bark_rejected_" + str(http_code))
                return True
            payload = response.json()
            if (http_code != 200 or not isinstance(payload, dict)
                    or type(payload.get("code")) is not int
                    or payload["code"] != 200
                    or type(payload.get("timestamp")) is not int or payload["timestamp"] <= 0):
                raise ValueError("bark_acceptance_not_confirmed")
            self.store.finish_bark(eid, "sent", server_timestamp=payload["timestamp"])
        except Exception:
            self.store.finish_bark(eid, "unknown", error="bark_delivery_uncertain_no_automatic_resend")
        return True

    def status(self):
        result = self.store.notification_status("bark")
        result["sent"] = result.get("sent", 0)
        result["historical_sent"] = self.store.bark_status().get("sent", 0) - result["sent"]
        return dict(result, configured=bool(self.creds), enabled=bool(self.creds),
                    acceptance="bark_server_accepted_not_device_receipt")

"""Private Bark V2 signal delivery, independent of Telegram's journal.

Sources: https://github.com/Finb/bark-server/blob/master/docs/API_V2.md
and router.go / route_push.go in that official repository. POST /push keeps
the device key out of URLs. HTTP 200, code 200 and a server timestamp mean
server acceptance, not an iPhone display/read receipt. Current direct-start or
model-confirmation eligibility, channel cutover and shared freshness gate apply.
No connectivity/startup messages are generated. Errors never include keys.

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

from yoyo.monitor import SIGNAL_KIND, MODEL_PROTOCOL, TV_INTERVALS
from yoyo.monitor.notification_policy import channel_enabled, delivery_error
from yoyo.monitor.store import now_ms

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
    if event["kind"] == SIGNAL_KIND:
        subtitle = "指标启动 · 未经 YOLO 确认"
        body = f"收盘价 {event['price']:.10g} · {time(event['bar_close_ms'])} 北京时间"
    else:
        indicator, model = event["indicator"], event["model"]
        subtitle = f"YOLO 确认 · 等待 {model['wait_bars']} 根"
        body = (f"确认 {event['price']:.10g} · {time(event['bar_close_ms'])}\n"
                f"原箭头 {indicator['price']:.10g} · {time(indicator['bar_close_ms'])} 北京时间")
    return {"title": f"{symbol} · {event['timeframe']} · {side}",
            "subtitle": subtitle,
            "body": body + f"\n\n网页备用：{web_url}",
            "group": "spike IMACD", "level": "active", "isArchive": "1",
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

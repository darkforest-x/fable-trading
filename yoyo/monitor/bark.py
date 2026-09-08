"""Private Bark V2 signal delivery, independent of Telegram's journal.

Sources: https://github.com/Finb/bark-server/blob/master/docs/API_V2.md
and router.go / route_push.go in that official repository. POST /push keeps
the device key out of URLs. HTTP 200, code 200 and a server timestamp mean
server acceptance, not an iPhone display/read receipt. Only the current
confirmed visible marker, channel cutover and shared freshness gate apply.
No connectivity/startup messages are generated. Errors never include keys.
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

from yoyo.monitor import FRESH_MS, SIGNAL_PROTOCOL, TV_INTERVALS
from yoyo.monitor.policy import is_tv_start
from yoyo.monitor.store import now_ms

SERVER = "https://api.day.app"
POLICY_KEY = "notification_policy:bark:" + SIGNAL_PROTOCOL
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
    return {"title": f"{symbol} · {event['timeframe']} · {side}",
            "subtitle": f"蓄势释放 · {event['near_zero_bars']} 根",
            "body": f"启动收盘价 {event['price']:.10g}\n标记K线 {time(event['bar_open_ms'])}\n收盘确认 {time(event['bar_close_ms'])} 北京时间\n按当前主图启动标记条件确认",
            "group": "Fable IMACD", "level": "active", "isArchive": "1",
            "url": f"https://www.tradingview.com/chart/?symbol=OKX%3A{tv_symbol}&interval={interval}"}


class BarkWorker:
    def __init__(self, store, creds=None, sender=None):
        self.store = store
        self.creds = creds if creds is not None else credentials(store.path.parent)
        self.sender = sender or requests.post

    def deliver_once(self, now=None):
        now = now if now is not None else now_ms()
        if not self.creds:
            return False
        policy = self.store.get_meta(POLICY_KEY)
        if not policy or type(policy.get("activated_ms")) is not int:
            return False
        row = self.store.claim_bark(now)
        if not row:
            return False
        event, eid = row["event"], row["event_id"]
        if not is_tv_start(event):
            self.store.finish_bark(eid, "skipped", error="not_visible_tv_start_signal")
            return True
        if event["bar_close_ms"] <= policy["activated_ms"]:
            self.store.finish_bark(eid, "skipped", error="before_bark_activation")
            return True
        timeframe_since = self.store.timeframe_activation(event.get("timeframe"))
        if timeframe_since is None or event["bar_close_ms"] <= timeframe_since:
            self.store.finish_bark(eid, "skipped", error="before_timeframe_activation")
            return True
        if not 0 <= now - event["bar_close_ms"] <= FRESH_MS:
            self.store.finish_bark(eid, "skipped", error="signal_expired")
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
        result = self.store.bark_status(protocol=SIGNAL_PROTOCOL)
        result["sent"] = result.get("sent", 0)
        result["historical_sent"] = self.store.bark_status().get("sent", 0) - result["sent"]
        return dict(result, configured=bool(self.creds), enabled=bool(self.creds),
                    acceptance="bark_server_accepted_not_device_receipt")

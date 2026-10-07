import asyncio
import tempfile
from pathlib import Path

from yoyo.copier.notifications.health_monitor import (
    latest_tunnel_url,
    notify_exchange_transition,
)
from yoyo.copier.store.sqlite import Database


def test_latest_tunnel_url_uses_newest_log():
    with tempfile.TemporaryDirectory() as td:
        old = Path(td) / "old.log"
        new = Path(td) / "new.log"
        old.write_text("https://old-address.trycloudflare.com\n")
        new.write_text("https://new-address.trycloudflare.com\n")
        old.touch()
        new.touch()

        assert latest_tunnel_url((old, new)) == "https://new-address.trycloudflare.com"


def test_exchange_transition_only_notifies_on_state_change(monkeypatch):
    sent = []

    async def fake_send(db, title, lines):
        sent.append(title)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.notifications.health_monitor.send_telegram_notification", fake_send)
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        status = {
            "ok": False,
            "label": "Gate",
            "mode": "Gate Live",
            "error": "network down",
        }
        asyncio.run(notify_exchange_transition(db, status, None))
        asyncio.run(notify_exchange_transition(db, status, True))

    assert sent == ["Gate 连接异常"]

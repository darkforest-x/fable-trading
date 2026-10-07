import tempfile
from pathlib import Path

from yoyo.copier.ingest import ingest_message
from yoyo.copier.store.sqlite import Database


class FakeRouter:
    def __init__(self):
        self.calls = []

    async def process_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_ingest_does_not_forward_raw_messages_by_default(monkeypatch):
    forwarded = []

    async def fake_forward(*args, **kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr("yoyo.copier.notifications.telegram.notify_channel_message", fake_forward)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = FakeRouter()
        message_id = ingest_message(
            db,
            router,
            external_id="raw-1",
            channel_id="1226095564073205780",
            author="Woods",
            content="longed AIO at 0.3 sl: 0.28",
        )

    assert message_id == 1
    assert forwarded == []
    assert len(router.calls) == 1


def test_ingest_forwards_raw_messages_when_enabled(monkeypatch):
    forwarded = []

    async def fake_forward(*args, **kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr("yoyo.copier.notifications.telegram.notify_channel_message", fake_forward)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("monitor_switches", '{"features":{"telegram_forward_raw":true},"channels":{}}')
        router = FakeRouter()
        ingest_message(
            db,
            router,
            external_id="raw-2",
            channel_id="1226095564073205780",
            author="Woods",
            content="longed AIO at 0.3 sl: 0.28",
        )

    assert len(forwarded) == 1
    assert forwarded[0]["channel_id"] == "1226095564073205780"


def test_raw_forwarding_stays_enabled_for_dry_run_trade(monkeypatch):
    forwarded = []

    async def fake_forward(*args, **kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr("yoyo.copier.notifications.telegram.notify_channel_message", fake_forward)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting(
            "monitor_switches",
            '{"features":{"telegram_forward_raw":true},"channels":{"1356581750914027590":true}}',
        )
        db.set_setting("dry_run", "true")
        router = FakeRouter()
        ingest_message(
            db,
            router,
            external_id="raw-dry-1",
            channel_id="1356581750914027590",
            author="比特币飞扬",
            content="飞扬合约策略 具体产品：BTC 进行方向：做空",
        )

    assert len(forwarded) == 1
    assert forwarded[0]["channel_id"] == "1356581750914027590"
    assert forwarded[0]["author"] == "比特币飞扬"


def test_ingest_forwards_attachments_when_raw_forwarding_enabled(monkeypatch):
    forwarded = []

    async def fake_forward(*args, **kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr("yoyo.copier.notifications.telegram.notify_channel_message", fake_forward)

    attachment = {
        "url": "https://cdn.discordapp.com/attachments/1/2/chart.png",
        "filename": "chart.png",
        "content_type": "image/png",
    }
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("monitor_switches", '{"features":{"telegram_forward_raw":true},"channels":{}}')
        router = FakeRouter()
        ingest_message(
            db,
            router,
            external_id="raw-image-1",
            channel_id="1131521990814089276",
            author="Arthur",
            content="[图片消息]",
            attachments=[attachment],
        )

    assert len(forwarded) == 1
    assert forwarded[0]["attachments"] == [attachment]

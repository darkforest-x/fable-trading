"""Unified subscriptions use synthetic events and never contact push services."""
import json

from fastapi.testclient import TestClient
import pytest

from model_fixture import model_event
from test_joint_notifications import joint, stores as joint_stores
from test_two_stage_delivery import activate, worker
from yoyo.monitor import STRATEGY_VERSION
from yoyo.monitor.notification_center import NotificationCenter, routing_error
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.store import Store


def raw(side="long", **kwargs):
    event = model_event(side=side, wait=0, **kwargs)["indicator"]
    event.update(source="live", confirmation="raw", strategy_version=STRATEGY_VERSION,
                 v128_admitted=True, v9_admitted=True, direction=side, is_closed=True,
                 risk=1, timeframe_min=60, source_sha256="a" * 64)
    return event


def setup(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    center = NotificationCenter(tmp_path)
    for channel in ("telegram", "bark"):
        activate(store, channel)
        activate(store, channel, direct=False)
    return store, center


def test_center_setup_cannot_arm_delivery_or_erase_existing_cutovers(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    center = NotificationCenter(tmp_path)
    state = center.snapshot({}, 5)
    assert len(state["routes"]) == 8
    assert all(r["enabled"] and not r["armed"] for r in state["routes"])
    assert all(not c["configured"] and not c["enabled"] for c in state["channels"])
    activate(store, "bark", cutover=20)
    center = NotificationCenter(tmp_path)
    row = next(r for r in center.snapshot({}, 30)["routes"] if r["topic"] == "spike_v128" and r["channel"] == "bark")
    assert row["activated_ms"] == 20
    assert center.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("side", ["long", "short"])
def test_subscription_pause_is_independent_and_reenable_is_forward_only(tmp_path, side):
    store, center = setup(tmp_path)
    event = raw(side)
    now = event["bar_close_ms"] + 20
    assert delivery_error(store, event, now, "telegram") is None
    center.update_route("spike_v128", "telegram", False, now)
    assert delivery_error(store, event, now, "telegram") == "subscription_disabled"
    assert delivery_error(store, event, now, "bark") is None
    center.update_route("spike_v128", "telegram", True, now + 1)
    assert delivery_error(store, event, now + 2, "telegram") == "before_subscription_activation"
    next_event = raw(side, close=event["bar_close_ms"] + 3_600_000)
    assert delivery_error(store, next_event, next_event["bar_close_ms"] + 1, "telegram") is None
    NotificationCenter(tmp_path).update_route("spike_v128", "telegram", True, now + 100)
    with center.connect() as db:
        row = db.execute("SELECT activated_ms FROM routes WHERE topic='spike_v128' AND channel='telegram'").fetchone()
        assert row[0] == now + 1
        assert db.execute("SELECT COUNT(*) FROM route_changes").fetchone()[0] == 2


def test_reenabled_confirmation_route_checks_original_signal_time(tmp_path):
    store, center = setup(tmp_path)
    event = model_event(wait=2)
    boundary = event["indicator"]["bar_close_ms"] + 1
    center.update_route("yolo_confirmation", "telegram", False, boundary)
    center.update_route("yolo_confirmation", "telegram", True, boundary)
    assert routing_error(store, event, event["bar_close_ms"], "telegram") == "before_subscription_activation"
    assert routing_error(store, event, event["bar_close_ms"], "bark") is None


def test_queued_request_rechecks_subscription_without_consuming_other_channel(tmp_path):
    store, center = setup(tmp_path)
    event = raw("short")
    now = event["bar_close_ms"] + 10
    store.upsert_event(event, notify=True, bark_notify=True)
    center.update_route("spike_v128", "telegram", False, now)
    tg_calls, bark_calls = [], []
    assert worker(store, "telegram", tg_calls).deliver_once(now + 1)
    assert worker(store, "bark", bark_calls).deliver_once(now + 1)
    assert tg_calls == [] and len(bark_calls) == 1
    rows = center.events(now=now)["items"]
    assert {r["channel"]: r["status"] for r in rows} == {"telegram": "skipped", "bark": "sent"}


def test_joint_producer_and_sender_share_route_and_never_backfill(tmp_path):
    book, outbox = joint_stores(tmp_path)
    center = NotificationCenter(tmp_path)
    event = joint()
    now = event["bar_close_ms"] + 1
    center.update_route("joint", "telegram", False, now - 1)
    assert book.insert([event], now=now) == 1
    assert outbox.claim(now) is None
    assert outbox.notification_status("bark")["pending"] == 1
    assert delivery_error(outbox, dict(event, price=event["close"]), now, "telegram") == "subscription_disabled"
    center.update_route("joint", "telegram", True, now)
    assert book.insert([event], now=now + 1) == 0
    assert outbox.claim(now + 1) is None


def test_history_merges_channels_and_books_filters_then_paginates_and_redacts(tmp_path):
    store, center = setup(tmp_path)
    book, outbox = joint_stores(tmp_path)
    for side in ("long", "short"):
        event = raw(side)
        store.upsert_event(event, notify=True, bark_notify=True)
        if side == "long":
            store.finish(store.event_id(event), "unknown", error="secret=https://private.example/token")
        else:
            store.finish(store.event_id(event), "sent", message_id=7)
    book.insert([joint()], now=joint()["bar_close_ms"] + 1)
    result = center.events(limit=3, now=100)
    page2 = center.events(offset=3, limit=3, now=100)
    assert result["total"] == page2["total"] == 6
    assert len({r["id"] for r in result["items"] + page2["items"]}) == 6
    assert center.events(channel="bark", topic="joint")["total"] == 1
    short = center.events(channel="telegram", side="short", timeframe="1H", search="test", status="sent")
    assert short["total"] == 1 and short["items"][0]["topic"] == "spike_v128"
    unknown = center.events(status="unknown")["items"][0]
    assert unknown["error"] == "details_redacted"
    assert "private.example" not in json.dumps(center.events())
    status = {"telegram": {"configured": True, "enabled": True}, "bark": {"configured": True, "enabled": True}}
    summary = center.snapshot(status, 100)
    tg = next(c for c in summary["channels"] if c["id"] == "telegram")
    assert tg["counts"]["sent"] == 1 and tg["counts"]["unknown"] == 1 and tg["counts"]["pending"] == 1
    assert center.events(search="' OR 1=1--")["total"] == 0
    with pytest.raises(ValueError):
        center.events(channel="sms")


def test_corrupt_existing_subscription_book_fails_closed(tmp_path):
    store, center = setup(tmp_path)
    with center.connect() as db:
        db.execute("DROP TABLE routes")
    assert routing_error(store, raw(), raw()["bar_close_ms"], "telegram") == "subscription_unavailable"


def test_notification_api_rejects_cross_origin_and_only_edits_routes(tmp_path, monkeypatch):
    monkeypatch.setattr("yoyo.monitor.telegram.credentials", lambda: None)
    monkeypatch.setattr("yoyo.monitor.bark.credentials", lambda *args: None)
    from yoyo.monitor.server import create_app
    app = create_app(tmp_path, start_monitor=False)
    headers = {"origin": "http://testserver", "x-spike-action": "update-notification-route"}
    with TestClient(app) as client:
        assert client.get("/api/notifications").status_code == 200
        assert client.get("/api/notifications/events").json()["items"] == []
        url = "/api/notifications/routes/spike_v128/telegram"
        assert client.put(url, json={"enabled": False}).status_code == 403
        assert client.put(url, json={"enabled": "false"}, headers=headers).status_code == 422
        assert client.put(url, json={"enabled": False, "api_key": "private"}, headers=headers).status_code == 422
        response = client.put(url, json={"enabled": False}, headers=headers)
        assert response.status_code == 200
        row = next(r for r in response.json()["routes"] if r["topic"] == "spike_v128" and r["channel"] == "telegram")
        assert row["enabled"] is False
        assert client.get("/api/notifications/events").json()["total"] == 0
        assert client.get("/api/notifications/events?status=fake").status_code == 400

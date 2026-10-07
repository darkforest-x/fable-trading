"""Joint delivery service wiring and read-only UI receipts; no external sends."""
from fastapi.testclient import TestClient

from yoyo.monitor import FRESH_MS
from yoyo.monitor.joint_notifications import JOINT_PROTOCOL, JointNotificationStore
from yoyo.monitor.server import create_app
from yoyo.monitor.spike_lines_api import classify
from yoyo.monitor.spike_lines_worker import DATABASE, LinesBook


def signal():
    return {"id": "c" * 24, "protocol": JOINT_PROTOCOL, "kind": "joint", "side": "long",
            "symbol": "BTC-USDT-SWAP", "timeframe": "15m", "bar_open_ms": 900_000,
            "bar_close_ms": 1_800_000, "detected_at_ms": 1_801_000, "close": 100.0,
            "source": "chart", "v9_signal_open_ms": 0, "v9_signal_close_ms": 900_000,
            "reference_stop": 99.0}


def test_api_exposes_separate_receipts_without_rewriting_event(tmp_path):
    book = LinesBook(tmp_path / DATABASE)
    delivery = JointNotificationStore(book.path)
    delivery.activate(("bark", "telegram"), 0)
    book.set_meta("activation", {"activated_ms": 0})
    book.insert([signal()], now=1_801_000)
    delivery.finish_bark("c" * 24, "sent", server_timestamp=123)
    delivery.finish("c" * 24, "unknown", error="delivery_uncertain_no_automatic_resend")
    app = create_app(runtime=tmp_path, start_monitor=False)
    app.state.monitor.status_snapshot["joint_notifications"] = {
        "bark": {"enabled": True, "configured": True, "activated_ms": 0},
        "telegram": {"enabled": True, "configured": True, "activated_ms": 0},
    }
    client = TestClient(app)
    events = client.get("/api/lines/events?kind=joint").json()
    assert events["notification_eligible"] is True
    assert events["execution_eligible"] is False
    row = events["items"][0]
    assert row["notifications"]["bark"]["status"] == "sent"
    assert row["notifications"]["telegram"]["status"] == "unknown"
    ledger = client.get("/api/lines/ledger?kind=joint").json()
    assert ledger["items"][0]["notifications"] == row["notifications"]
    assert client.get("/api/lines/events?kind=break").json()["notification_eligible"] is False
    assert client.get("/api/lines/status").json()["notifications"]["telegram"]["enabled"] is True
    with book.connect() as db:
        assert '"notifications"' not in db.execute("SELECT payload FROM events").fetchone()[0]


def test_joint_high_timeframe_freshness_matches_30_minute_delivery_gate():
    row = {**signal(), "timeframe": "4H"}
    assert classify(dict(row), 0, row["bar_close_ms"] + FRESH_MS)["is_fresh"] is True
    assert classify(dict(row), 0, row["bar_close_ms"] + FRESH_MS + 1)["is_fresh"] is False


def test_service_starts_ordinary_and_joint_senders_for_both_channels(tmp_path, monkeypatch):
    from yoyo.monitor import service, telegram
    starts = []

    class InertThread:
        def __init__(self, *, name, **kwargs):
            self.name = name

        def start(self):
            starts.append(self.name)

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(service.threading, "Thread", InertThread)
    monkeypatch.setattr(telegram, "credentials", lambda: ("fake-token", "fake-chat"))
    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    monitor.store.set_meta("migration:spike-v9-reset-v1", {})
    monitor.start()
    assert {"impulse-telegram", "impulse-bark"} <= set(starts)
    assert not {"impulse-joint-bark", "impulse-joint-telegram"} & set(starts)
    assert monitor.telegram.enabled is True
    assert monitor.telegram.status()["configured"] is True
    assert monitor.joint_telegram.enabled is True
    assert monitor.joint_store.path == tmp_path / DATABASE
    assert monitor.joint_notification_status()["telegram"]["activated_ms"] is None
    monitor.joint_store.activate(("telegram", "bark"), 123)
    assert monitor.joint_notification_status()["telegram"]["activated_ms"] == 123
    monitor.close()


def test_ordinary_sender_db_failure_still_allows_joint_delivery(tmp_path):
    from yoyo.monitor.server import create_app
    from yoyo.monitor.spike_lines_worker import DATABASE

    class StopAfterOneWait:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            self.stopped = True
            return self.stopped

    class Worker:
        def __init__(self):
            self.calls = []

        def deliver_once(self, now):
            self.calls.append(now)
            return True

    monitor = create_app(runtime=tmp_path, start_monitor=False).state.monitor
    monitor.stop_event = StopAfterOneWait()
    LinesBook(tmp_path / DATABASE)
    monitor.joint_store = JointNotificationStore(tmp_path / DATABASE)
    monitor.telegram = Worker()
    monitor.joint_telegram = Worker()
    monitor.client.clock = lambda: 1234
    ordinary_get_meta = monitor.store.get_meta

    def fail_arm_read(key, default=None):
        if key == "notification_policy:v128_telegram_arm":
            raise RuntimeError("synthetic_database_read_failure")
        return ordinary_get_meta(key, default)

    monitor.store.get_meta = fail_arm_read
    monitor._deliver_channel("telegram")
    assert monitor.telegram.calls == []
    assert len(monitor.joint_telegram.calls) == 1

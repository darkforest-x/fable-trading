"""Channel isolation, atomic identity and forward-only Bark policy persistence.

All events and receipts are synthetic; no credentials or network are used.
"""
import concurrent.futures
import sqlite3

import pytest

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.store import Store


def event(close_ms=3_600_000, **changes):
    return dict(dict(protocol=SIGNAL_PROTOCOL, symbol="BTC-USDT-SWAP", timeframe="1H",
                     kind=SIGNAL_KIND, side="long", price=100.5,
                     bar_open_ms=close_ms - 3_600_000, bar_close_ms=close_ms,
                     detected_at_ms=close_ms + 1000), **changes)


def test_bark_only_receipt_does_not_create_telegram_delivery(tmp_path, monkeypatch):
    store = Store(tmp_path / "m.sqlite")
    assert store.upsert_event(event(), bark_notify=True)
    assert store.claim(3_602_000) is None
    row = store.claim_bark(3_602_000)
    assert row["attempts"] == 1 and row["event"]["kind"] == SIGNAL_KIND
    monkeypatch.setattr("yoyo.monitor.store.now_ms", lambda: 3_603_000)
    store.finish_bark(row["event_id"], "sent", server_timestamp=3603)
    assert store.claim_bark(3_604_000) is None
    result = store.list_events()[0]
    assert result["notification_status"] == "history"
    assert result["bark_notification_status"] == "sent"
    assert store.telegram_status()["pending"] == 0
    assert store.bark_status()["last_success_ms"] == 3_603_000
    with store.connect() as db:
        receipt = db.execute("SELECT * FROM bark_outbox").fetchone()
    assert receipt["server_timestamp"] == 3603
    assert receipt["attempts"] == 1 and receipt["error"] is None


def test_legacy_telegram_only_call_keeps_bark_as_history(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    assert store.upsert_event(event(), True)
    assert store.claim_bark(3_602_000) is None
    row = store.claim(3_602_000)
    store.finish(row["event_id"], "sent", message_id=23)
    result = store.list_events()[0]
    assert result["notification_status"] == "sent"
    assert result["bark_notification_status"] == "history"
    assert store.bark_status()["last_success_ms"] is None


def test_channels_claim_and_finish_independently(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), notify=True, bark_notify=True)
    tg = store.claim(3_602_000)
    bark = store.claim_bark(3_602_000)
    assert tg["event_id"] == bark["event_id"]
    assert tg["attempts"] == bark["attempts"] == 1
    store.finish(tg["event_id"], "sent", message_id=23)
    assert store.list_events()[0]["bark_notification_status"] == "sending"
    store.finish_bark(bark["event_id"], "failed", error="synthetic_rejection")
    assert store.telegram_status()["sent"] == 1
    assert store.bark_status()["failed"] == 1
    with store.connect() as db:
        assert db.execute("SELECT message_id FROM outbox").fetchone()[0] == 23


@pytest.mark.parametrize("telegram_sent", [False, True])
def test_existing_history_or_tg_receipt_never_backfills_bark(tmp_path, telegram_sent):
    store = Store(tmp_path / "m.sqlite")
    assert store.upsert_event(event(), notify=telegram_sent)
    if telegram_sent:
        row = store.claim(3_602_000)
        store.finish(row["event_id"], "sent", message_id=23)
    again = Store(store.path)
    assert again.activate_bark_policy(3_602_000) == 3_602_000
    assert not again.upsert_event(event(), notify=True, bark_notify=True)
    assert again.event_count() == 1
    assert again.bark_status()["pending"] == 0
    result = again.list_events()[0]
    assert result["bark_notification_status"] == "history"
    assert result["notification_status"] == ("sent" if telegram_sent else "history")


def test_concurrent_insert_and_claim_are_exactly_once_per_channel(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        inserts = list(pool.map(lambda _: store.upsert_event(event(), True, True), range(32)))
        bark_claims = list(pool.map(lambda _: store.claim_bark(3_602_000), range(32)))
        tg_claims = list(pool.map(lambda _: store.claim(3_602_000), range(32)))
    assert sum(inserts) == 1 and store.event_count() == 1
    assert len([r for r in bark_claims if r]) == 1
    assert len([r for r in tg_claims if r]) == 1
    assert store.bark_status()["sending"] == 1
    assert store.telegram_status()["sending"] == 1


def test_event_and_both_outboxes_roll_back_as_one_transaction(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    with store.connect() as db:
        db.execute("""CREATE TRIGGER reject_bark BEFORE INSERT ON bark_outbox
                   BEGIN SELECT RAISE(ABORT, 'synthetic_storage_fault'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_storage_fault"):
        store.upsert_event(event(), notify=True, bark_notify=True)
    assert store.event_count() == 0
    assert store.telegram_status()["pending"] == store.bark_status()["pending"] == 0
    with store.connect() as db:
        db.execute("DROP TRIGGER reject_bark")
    assert store.upsert_event(event(), notify=True, bark_notify=True)
    assert store.telegram_status()["pending"] == store.bark_status()["pending"] == 1


def test_restart_recovery_preserves_receipts_and_never_retries_uncertainty(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), notify=True, bark_notify=True)
    tg = store.claim(3_602_000)
    bark = store.claim_bark(3_602_000)
    store.finish(tg["event_id"], "sent", message_id=23)
    store.upsert_event(event(7_200_000), notify=True, bark_notify=True)
    later_tg = store.claim(7_202_000)
    later_bark = store.claim_bark(7_202_000)
    store.finish_bark(later_bark["event_id"], "sent", server_timestamp=7203)
    store.upsert_event(event(10_800_000), bark_notify=True)
    again = Store(store.path)
    # Merely opening an additional Store cannot interrupt the running worker.
    assert again.bark_status()["sending"] == 1
    again.recover_outbox()
    results = {e["id"]: e for e in again.list_events()}
    assert results[bark["event_id"]]["bark_notification_status"] == "unknown"
    assert results[bark["event_id"]]["notification_status"] == "sent"
    assert results[later_tg["event_id"]]["notification_status"] == "unknown"
    assert results[later_tg["event_id"]]["bark_notification_status"] == "sent"
    assert again.claim(10_802_000) is None
    pending = again.claim_bark(10_802_000)
    assert pending["event"]["bar_close_ms"] == 10_800_000
    assert again.claim_bark(10_802_000) is None
    with again.connect() as db:
        original = db.execute("SELECT * FROM bark_outbox WHERE event_id=?", (bark["event_id"],)).fetchone()
    assert original["attempts"] == 1
    assert original["error"] == "process_interrupted_during_delivery"


def test_bark_retry_due_time_and_attempts_do_not_change_telegram(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), notify=True, bark_notify=True)
    assert store.claim_bark(3_600_999) is None
    bark = store.claim_bark(3_601_000)
    store.finish_bark(bark["event_id"], "pending", error="synthetic_rate_limit", due_ms=3_690_000)
    assert store.claim_bark(3_689_999) is None
    retry = store.claim_bark(3_690_000)
    assert retry["attempts"] == 2 and retry["event_id"] == bark["event_id"]
    assert store.claim(3_690_000)["attempts"] == 1


def test_bark_cutover_is_persistent_and_does_not_modify_tg_policy_or_receipts(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    assert store.activate_notification_policy(2_000_000) == 2_000_000
    store.upsert_event(event(3_600_000, protocol="old"), notify=True, bark_notify=True)
    old_bark = store.claim_bark(3_602_000)
    old_tg = store.claim(3_602_000)
    store.finish_bark(old_bark["event_id"], "sent", server_timestamp=3603)
    store.finish(old_tg["event_id"], "sent", message_id=23)
    store.upsert_event(event(7_200_000, protocol="old"), notify=True, bark_notify=True)
    store.upsert_event(event(10_800_000), notify=True, bark_notify=True)
    assert store.activate_bark_policy(8_000_000) == 8_000_000
    again = Store(store.path)
    assert again.activate_bark_policy(12_000_000) == 8_000_000
    assert again.get_meta("notification_policy:" + SIGNAL_PROTOCOL)["activated_ms"] == 2_000_000
    assert again.get_meta("notification_policy:bark:" + SIGNAL_PROTOCOL) == {
        "activated_ms": 8_000_000, "kind": SIGNAL_KIND}
    assert again.telegram_status()["pending"] == 2
    assert again.telegram_status()["sent"] == 1
    assert again.bark_status()["skipped"] == 1
    assert again.bark_status()["sent"] == 1
    assert again.bark_status(protocol=SIGNAL_PROTOCOL)["pending"] == 1
    row = again.claim_bark(10_802_000)
    assert row["event"]["protocol"] == SIGNAL_PROTOCOL
    assert again.claim_bark(10_802_000) is None


def test_bark_policy_retires_wrong_kind_without_touching_tg(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(kind="release"), notify=True, bark_notify=True)
    store.activate_bark_policy(0)
    result = store.list_events()[0]
    assert result["notification_status"] == "pending"
    assert result["bark_notification_status"] == "skipped"
    assert store.claim_bark(3_602_000) is None


def test_bark_policy_concurrent_activation_preserves_first_cutover(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store.activate_bark_policy, range(100, 132)))
    assert len(set(results)) == 1
    assert results[0] in range(100, 132)
    assert Store(store.path).activate_bark_policy(0) == results[0]
    assert store.get_meta("notification_policy:" + SIGNAL_PROTOCOL) is None


def test_telegram_policy_cannot_retire_bark_pending(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(protocol="old"), notify=True, bark_notify=True)
    store.activate_notification_policy(0)
    assert store.telegram_status()["skipped"] == 1
    assert store.bark_status()["pending"] == 1


def test_bark_protocol_counts_and_event_filters_are_independent(tmp_path, monkeypatch):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(protocol="old"), bark_notify=True)
    old = store.claim_bark(3_602_000)
    monkeypatch.setattr("yoyo.monitor.store.now_ms", lambda: 3_603_000)
    store.finish_bark(old["event_id"], "sent", server_timestamp=3603)
    store.upsert_event(event(7_200_000), notify=True, bark_notify=True)
    current = store.claim_bark(7_202_000)
    monkeypatch.setattr("yoyo.monitor.store.now_ms", lambda: 7_203_000)
    store.finish_bark(current["event_id"], "sent", server_timestamp=7203)
    assert store.bark_status()["sent"] == 2
    assert store.bark_status(protocol="old")["last_success_ms"] == 3_603_000
    assert store.bark_status(protocol=SIGNAL_PROTOCOL)["sent"] == 1
    assert store.bark_status(protocol=SIGNAL_PROTOCOL)["last_success_ms"] == 7_203_000
    result = store.list_events(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND,
                               symbol="BTC-USDT-SWAP", timeframe="1H", side="long")
    assert len(result) == 1
    assert result[0]["notification_status"] == "pending"
    assert result[0]["bark_notification_status"] == "sent"


def test_existing_telegram_schema_migrates_without_receipt_changes(tmp_path):
    path = tmp_path / "m.sqlite"
    store = Store(path)
    store.upsert_event(event(), True)
    tg = store.claim(3_602_000)
    store.finish(tg["event_id"], "sent", message_id=23)
    with store.connect() as db:
        original = tuple(db.execute("SELECT * FROM outbox").fetchone())
        db.execute("DROP TABLE bark_outbox")
    upgraded = Store(path)
    with upgraded.connect() as db:
        assert tuple(db.execute("SELECT * FROM outbox").fetchone()) == original
        assert db.execute("SELECT COUNT(*) FROM bark_outbox").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.execute("INSERT INTO bark_outbox(event_id,status,due_ms,updated_ms) VALUES ('missing','pending',0,0)")
    assert upgraded.list_events()[0]["notification_status"] == "sent"
    assert upgraded.list_events()[0]["bark_notification_status"] == "history"

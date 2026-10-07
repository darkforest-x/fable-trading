"""Receipt and cutover checks for the isolated 突破+spike notification outbox."""
from __future__ import annotations

from copy import deepcopy

from yoyo.monitor import FRESH_MS
from yoyo.monitor.bark import BarkWorker, message as bark_message
from yoyo.monitor.joint_notifications import JOINT_PROTOCOL, JointNotificationStore
from yoyo.monitor.spike_lines_worker import LinesBook
from yoyo.monitor.telegram import TelegramWorker, message as telegram_message

STEP = 900_000
OPEN = 1_800_000
CLOSE = OPEN + STEP


def joint(event_id="joint-1", *, opened=OPEN, detected=None, **changes):
    closed = opened + STEP
    detected = closed + 1 if detected is None else detected
    event = {"id": event_id, "kind": "joint", "protocol": JOINT_PROTOCOL,
             "symbol": "BTC-USDT-SWAP", "timeframe": "15m", "side": "long",
             "bar_open_ms": opened, "bar_close_ms": closed, "detected_at_ms": detected,
             "close": 101.25, "source": "both", "v9_signal_open_ms": opened - STEP,
             "v9_signal_close_ms": opened,
             "reference_stop": 99.5}
    event.update(changes)
    return event


class TelegramOK:
    status_code = 200
    def json(self):
        return {"ok": True, "result": {"message_id": 7}}


class BarkOK:
    status_code = 200
    headers = {}
    def json(self):
        return {"code": 200, "timestamp": 8}


class BarkLimited:
    status_code = 429
    headers = {"Retry-After": "5"}
    def json(self):
        return {"code": 429}


def stores(tmp_path, channels=("telegram", "bark"), activation=1):
    book = LinesBook(tmp_path / "spike-lines-v1.sqlite3")
    outbox = JointNotificationStore(book.path)
    outbox.activate(channels, activation)
    return book, outbox


def test_first_fresh_joint_atomically_seeds_both_immutable_channel_payloads(tmp_path):
    book, outbox = stores(tmp_path)
    event = joint()
    assert book.insert([event], now=event["bar_close_ms"] + 1) == 1
    event["close"] = 1  # the caller cannot mutate the persisted delivery bytes
    telegram = outbox.claim(CLOSE + 2)
    bark = outbox.claim_bark(CLOSE + 2)
    assert telegram["event"]["price"] == bark["event"]["price"] == 101.25
    assert TelegramWorker(outbox, ("token", "chat"), lambda *a, **k: TelegramOK(), enabled=True).deliver_once(CLOSE + 3) is False
    # Finish the claims explicitly, then prove each sender uses its own receipt.
    outbox.finish(telegram["event_id"], "pending", due_ms=CLOSE + 2)
    outbox.finish_bark(bark["event_id"], "pending", due_ms=CLOSE + 2)
    tg_calls, bark_calls = [], []
    assert TelegramWorker(outbox, ("token", "chat"), lambda *a, **k: tg_calls.append((a, k)) or TelegramOK(), enabled=True).deliver_once(CLOSE + 4)
    assert BarkWorker(outbox, "device-key", lambda *a, **k: bark_calls.append((a, k)) or BarkOK()).deliver_once(CLOSE + 4)
    assert len(tg_calls) == len(bark_calls) == 1
    assert "突破+spike" in telegram_message(telegram["event"])
    assert "多头框内" in telegram_message(telegram["event"])
    assert "本周期 + 上级周期" in telegram_message(telegram["event"])
    assert "参考止损（非成交价）" in bark_message(bark["event"])["body"]


def test_cold_start_old_event_and_duplicate_restart_never_backfill(tmp_path):
    book = LinesBook(tmp_path / "spike-lines-v1.sqlite3")
    event = joint(detected=CLOSE + FRESH_MS + 1)
    assert book.insert([event], now=event["detected_at_ms"]) == 1
    outbox = JointNotificationStore(book.path)
    outbox.activate(("telegram", "bark"), CLOSE + FRESH_MS + 2)
    assert book.insert([event], now=CLOSE + FRESH_MS + 3) == 0
    assert outbox.telegram_status()["pending"] == outbox.bark_status()["pending"] == 0


def test_future_stale_and_ordinary_break_are_not_queued(tmp_path):
    book, outbox = stores(tmp_path)
    fresh = joint("fresh")
    future = joint("future", opened=OPEN + STEP, detected=CLOSE + 1)
    stale = joint("stale", detected=CLOSE + FRESH_MS + 1)
    ordinary = joint("break", kind="break", protocol=JOINT_PROTOCOL)
    assert book.insert([future], now=CLOSE + 1) == 1
    assert book.insert([stale], now=stale["detected_at_ms"]) == 1
    assert book.insert([ordinary], now=CLOSE + 1) == 1
    # Detection timestamps are auditable metadata; insertion time, not a
    # forged recent detected_at_ms, controls the fresh delivery boundary.
    forged = deepcopy(fresh)
    forged["id"] = "forged"
    forged["detected_at_ms"] = CLOSE + FRESH_MS + 999
    assert book.insert([forged], now=CLOSE + 1) == 1
    assert outbox.telegram_status()["pending"] == outbox.bark_status()["pending"] == 0


def test_channels_arm_independently_and_later_arm_does_not_replay(tmp_path):
    book, outbox = stores(tmp_path, ("bark",))
    first = joint("first")
    assert book.insert([first], now=CLOSE + 1) == 1
    assert outbox.bark_status()["pending"] == 1
    assert outbox.telegram_status()["pending"] == 0
    outbox.activate(("telegram",), CLOSE + 1)
    assert book.insert([first], now=CLOSE + 2) == 0
    second = joint("second", opened=OPEN + STEP)
    assert book.insert([second], now=second["bar_close_ms"] + 1) == 1
    assert outbox.bark_status()["pending"] == 2
    assert outbox.telegram_status()["pending"] == 1


def test_rate_limit_is_bounded_and_unknown_is_channel_independent(tmp_path):
    book, outbox = stores(tmp_path)
    event = joint()
    book.insert([event], now=CLOSE + 1)
    bark = BarkWorker(outbox, "device-key", lambda *a, **k: BarkLimited())
    telegram = TelegramWorker(outbox, ("token", "chat"), lambda *a, **k: (_ for _ in ()).throw(OSError("lost")), enabled=True)
    assert bark.deliver_once(CLOSE + 2)
    assert outbox.bark_status()["pending"] == 1
    assert telegram.deliver_once(CLOSE + 2)
    assert outbox.telegram_status()["unknown"] == 1
    assert not telegram.deliver_once(CLOSE + 3)
    for stamp in (CLOSE + 5_002, CLOSE + 10_002, CLOSE + 15_002, CLOSE + 20_002):
        assert bark.deliver_once(stamp)
    assert outbox.bark_status()["failed"] == 1
    assert outbox.bark_status()["pending"] == 0


def test_sender_rechecks_freshness_before_network_call(tmp_path):
    book, outbox = stores(tmp_path)
    book.insert([joint()], now=CLOSE + 1)
    calls = []
    worker = BarkWorker(outbox, "device-key", lambda *a, **k: calls.append(1) or BarkOK())
    assert worker.deliver_once(CLOSE + FRESH_MS + 1)
    assert not calls
    assert outbox.bark_status().get("skipped") == 1


def test_restart_marks_inflight_channel_receipts_unknown_without_resend(tmp_path):
    book, outbox = stores(tmp_path)
    book.insert([joint()], now=CLOSE + 1)
    assert outbox.claim(CLOSE + 2)
    assert outbox.claim_bark(CLOSE + 2)
    # A new service object observes the durable in-flight state, rather than
    # guessing whether either external transport accepted the request.
    restarted = JointNotificationStore(book.path)
    restarted.recover_outbox()
    assert restarted.telegram_status()["unknown"] == 1
    assert restarted.bark_status()["unknown"] == 1
    assert not TelegramWorker(restarted, ("token", "chat"), lambda *a, **k: TelegramOK(), enabled=True).deliver_once(CLOSE + 3)


def test_corrupt_joint_is_history_only_and_never_creates_main_store(tmp_path):
    book, outbox = stores(tmp_path)
    invalid = joint(side="short")
    assert book.insert([invalid], now=CLOSE + 1) == 1
    assert outbox.telegram_status()["pending"] == outbox.bark_status()["pending"] == 0
    assert not (tmp_path / "monitor.sqlite3").exists()


def test_future_v9_reference_cannot_be_normalized_as_a_joint(tmp_path):
    book, outbox = stores(tmp_path)
    invalid = joint(v9_signal_open_ms=OPEN + STEP, v9_signal_close_ms=CLOSE + STEP)
    assert book.insert([invalid], now=CLOSE + 1) == 1
    assert outbox.telegram_status()["pending"] == outbox.bark_status()["pending"] == 0

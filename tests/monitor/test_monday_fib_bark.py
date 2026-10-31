"""Independent 1.618 Bark cutovers, atomic dedup and conservative receipts."""
from concurrent.futures import ThreadPoolExecutor
import math
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.monitor.monday_fib import FibBook, FRESH_MS, MINUTE, HOUR, baseline, week_window
from yoyo.monitor.monday_fib_bark import deliver_once
from yoyo.monitor.monday_fib import deliver_once as deliver_telegram_once
from yoyo.monitor.monday_fib_service import MondayFibService, install

NOW = 1792600200000  # 2026-10-21 12:30 America/New_York
READY = NOW + 5 * MINUTE  # touch 5m close: the backtest score exists from here (owner 2026-10-31)
SYMBOL = "BTC-USDT-SWAP"


def candle(t, high=110, confirmed="1"):
    return [str(t), "105", str(high), "100", "105", "1", "1", "105", confirmed]


def history():
    return [candle(t, confirmed="0" if t == NOW // HOUR * HOUR else "1")
            for t in range(week_window(NOW)["start"], NOW // HOUR * HOUR + 1, HOUR)]


def ready_service(tmp_path):
    service = MondayFibService(tmp_path)
    service.symbols = (SYMBOL,)
    service.connected = True
    levels = baseline(history(), NOW)
    service.levels[SYMBOL] = {**levels, "epoch": 0}
    service.client.clock = lambda: NOW
    return service, levels


def armed(tmp_path):
    book = FibBook(tmp_path)
    book.set_enabled(True, NOW - MINUTE)
    book.set_enabled(True, NOW - MINUTE, "bark")
    levels = baseline(history(), NOW)
    book.touch(levels, SYMBOL, "upper", 120, NOW, NOW, "live")
    return book, levels


def scored(event):
    return {"status": "scored", "score": .6}


class Response:
    def __init__(self, code=200, payload=None, headers=None):
        self.status_code = code
        self.payload = payload if payload is not None else {"code": 200, "timestamp": NOW // 1000}
        self.headers = headers or {}

    def json(self):
        return self.payload


def test_existing_telegram_journal_is_preserved_without_backfill(tmp_path):
    book, levels = armed(tmp_path)
    event = book.claim(NOW + 1)
    book.finish(event, "sent", message_id=17)
    with book.db() as db:
        # A pre-Bark installation contains only the original three tables.
        for table in ("bark_outbox", "bark_changes", "bark_subscription"):
            db.execute(f"DROP TABLE {table}")
    before = book.records()
    upgraded = FibBook(tmp_path)
    assert upgraded.records() == before
    assert upgraded.subscription() == book.subscription()
    assert upgraded.subscription("bark")["enabled"] is False
    upgraded.set_enabled(True, NOW + 1, "bark")
    assert upgraded.claim_bark(NOW + 2) is None
    assert not upgraded.touch(levels, SYMBOL, "upper", 120, NOW + MINUTE, NOW + MINUTE, "live")
    assert upgraded.bark_status()["counts"] == {}


def test_same_touch_has_independent_channels_and_single_claim(tmp_path):
    book, levels = armed(tmp_path)
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda _: FibBook(tmp_path).claim_bark(NOW + 1), range(5)))
    event, = [r for r in claims if r]
    book.finish_bark(event, "sent", server_timestamp=123)
    assert book.records()[0]["status"] == "pending"
    assert book.claim(NOW + 2)
    assert not book.touch(levels, SYMBOL, "upper", 120, NOW + 3, NOW, "live")
    assert book.claim_bark(NOW + 4) is None
    assert book.bark_status()["counts"] == {"sent": 1}


def test_touch_and_bark_enqueue_rollback_together(tmp_path):
    book = FibBook(tmp_path)
    book.set_enabled(True, NOW - MINUTE, "bark")
    with book.db() as db:
        db.execute("CREATE TRIGGER fail_bark BEFORE INSERT ON bark_outbox BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        book.touch(baseline(history(), NOW), SYMBOL, "upper", 120, NOW, NOW, "live")
    assert book.records() == []


def test_bark_activation_requires_new_known_touch_and_no_history(tmp_path):
    service, levels = ready_service(tmp_path)
    service.book.set_enabled(True, NOW + 5, "bark")
    service.book.touch(levels, "OLD-USDT-SWAP", "upper", 120, NOW + 10, NOW, "history")
    service.book.touch(levels, "AMBIG-USDT-SWAP", "upper", 120, NOW + 10, NOW, "live")
    assert service.book.claim_bark(NOW + 11) is None
    service.on_candle(SYMBOL, candle(NOW, confirmed="0"), NOW + 10)
    service.on_candle(SYMBOL, candle(NOW, high=120, confirmed="0"), NOW + 20)
    assert service.book.claim_bark(NOW + 21)["symbol"] == SYMBOL
    assert service.book.subscription()["enabled"] is False


def test_bark_switch_does_not_mutate_telegram_and_reenable_is_forward_only(tmp_path):
    book, levels = armed(tmp_path)
    old_telegram = book.subscription(), book.records()
    book.set_enabled(False, NOW + 1, "bark")
    book.set_enabled(True, NOW + 2, "bark")
    assert (book.subscription(), book.records()) == old_telegram
    assert book.claim_bark(NOW + 3) is None
    book.touch(levels, SYMBOL, "lower", 90, NOW + MINUTE, NOW + MINUTE, "live")
    book.set_enabled(False, NOW + MINUTE + 1)
    assert book.claim_bark(NOW + MINUTE + 2)["side"] == "lower"


@pytest.mark.parametrize("code,payload,expected", [
    (200, {"code": 200, "timestamp": 123}, "sent"),
    (200, {"code": 200}, "unknown"),
    (200, {"code": True, "timestamp": 123}, "unknown"),
    (200, {"code": 200, "timestamp": True}, "unknown"),
    (200, [], "unknown"),
    (429, {}, "pending"),
    (403, {}, "failed"),
    (500, {"code": 200, "timestamp": 123}, "unknown"),
])
def test_bark_receipts_and_private_post(tmp_path, code, payload, expected):
    book, _ = armed(tmp_path)
    def sender(url, **kwargs):
        assert url == "https://api.day.app/push"
        assert kwargs["allow_redirects"] is False
        assert kwargs["json"]["device_key"] == "test-device-key"
        assert kwargs["json"]["group"] == "SPIKE 1.618"
        assert kwargs["json"]["ttl"] == 600
        assert kwargs["json"]["title"] == "BTC · 上沿 1.618"
        assert kwargs["json"]["body"].splitlines() == ["评分 0.6000 · 点位 116.18", "10-22 00:30 北京"]
        return Response(code, payload)
    assert deliver_once(book, "test-device-key", sender, NOW + 1, score_lookup=scored)
    assert book.bark_status()["counts"] == {expected: 1}
    assert "test-device-key" not in str(book.bark_status())
    assert book.records()[0]["status"] == "pending"


def test_ambiguous_delivery_and_restart_do_not_duplicate(tmp_path):
    book, levels = armed(tmp_path)
    def timeout(*args, **kwargs):
        raise TimeoutError("secret response must never be recorded")
    assert deliver_once(book, "key", timeout, NOW + 1, score_lookup=scored)
    assert not deliver_once(book, "key", timeout, NOW + 2)
    book.touch(levels, SYMBOL, "lower", 90, NOW + MINUTE, NOW + MINUTE, "live")
    assert book.claim_bark(NOW + MINUTE + 1)
    FibBook(tmp_path).recover()
    assert book.bark_status()["counts"] == {"unknown": 2}
    assert not deliver_once(book, "key", timeout, NOW + MINUTE + 2)
    assert "secret response" not in str(book.bark_status())


def test_rate_limit_retries_are_bounded_and_expired_alerts_do_not_send(tmp_path):
    book, levels = armed(tmp_path)
    def limited(*args, **kwargs):
        return Response(429, {}, {"Retry-After": "5"})
    for i in range(5):
        assert deliver_once(book, "key", limited, NOW + i * 5000 + 1, score_lookup=scored)
    assert book.bark_status()["counts"] == {"failed": 1}
    assert not deliver_once(book, "key", limited, NOW + 25001)
    book.touch(levels, SYMBOL, "lower", 90, NOW + MINUTE, NOW + MINUTE, "live")
    assert not deliver_once(book, "key", limited, READY + FRESH_MS + 1)
    assert book.bark_status()["counts"] == {"failed": 1, "skipped": 1}


def test_disable_after_claim_is_rechecked(tmp_path, monkeypatch):
    book, _ = armed(tmp_path)
    original = book.claim_bark
    def claim(now, event=None):
        event = original(now, event)
        book.set_enabled(False, now, "bark")
        return event
    monkeypatch.setattr(book, "claim_bark", claim)
    assert deliver_once(book, "key", lambda *a, **kw: pytest.fail("disabled route sent"), NOW + 1, score_lookup=scored)
    assert book.bark_status()["counts"] == {"skipped": 1}


def test_bark_api_requires_same_origin_credentials_and_boolean(tmp_path, monkeypatch):
    service, _ = ready_service(tmp_path)
    monkeypatch.setattr("yoyo.monitor.monday_fib_service.configured_credentials", lambda book: None)
    app = FastAPI()
    install(app, service)
    client = TestClient(app)
    path = "/api/monday-fib/bark"
    headers = {"Origin": "http://testserver", "X-Spike-Action": "update-monday-fib-bark"}
    assert client.put(path, json={"enabled": True}).status_code == 403
    assert client.put(path, json={"enabled": "true"}, headers=headers).status_code == 422
    assert client.put(path, json={"enabled": True}, headers=headers).status_code == 409
    monkeypatch.setattr("yoyo.monitor.monday_fib_service.configured_credentials", lambda book: "fake-secret")
    reply = client.put(path, json={"enabled": True}, headers=headers)
    assert reply.json()["bark"]["enabled"] is True
    assert reply.json()["telegram"]["enabled"] is False
    assert "fake-secret" not in reply.text
    generation = reply.json()["bark"]["generation"]
    assert client.put(path, json={"enabled": True}, headers=headers).json()["bark"]["generation"] == generation


def test_bark_thread_reuses_touch_without_starting_another_scanner(tmp_path, monkeypatch):
    service, _ = ready_service(tmp_path)
    service.book.set_enabled(True, NOW - MINUTE, "bark")
    service.on_candle(SYMBOL, candle(NOW, high=120, confirmed="0"), NOW + 1)
    service.client.clock = lambda: NOW + 2
    monkeypatch.setattr("yoyo.monitor.monday_fib_service.configured_credentials", lambda book: "fake-key")
    calls, lookups = [], []
    service.notification_score_lookup = lambda event: (lookups.append(event) or scored(event))
    def send(url, **kwargs):
        calls.append(url)
        service.stop.set()
        return Response()
    monkeypatch.setattr("yoyo.monitor.monday_fib_service.requests.post", send)
    monkeypatch.setattr(service.stop, "wait", lambda timeout: service.stop.set())
    service._send_bark()
    assert len(lookups) == 1
    assert service.snapshot()["bark"]["min_score_exclusive"] == .4
    assert calls == ["https://api.day.app/push"]
    assert service.book.bark_status()["counts"] == {"sent": 1}
    assert len(service.book.records()) == 1


@pytest.mark.parametrize("score,allowed", [
    (.4, False), (.399999, False), (math.nextafter(.4, 1), True), (.400001, True), (.9, True),
    (None, False), (float("nan"), False), (float("inf"), False), ("0.9", False),
    (True, False), (-.2, False), (1.2, False),
])
def test_bark_and_telegram_share_strict_raw_score_and_three_lines(tmp_path, score, allowed):
    book, _ = armed(tmp_path)
    calls = {}
    def sender(url, **kwargs):
        channel = "bark" if url.endswith("/push") else "telegram"
        calls[channel] = kwargs["json"]
        return Response(payload={"ok": True, "result": {"message_id": 17}}) if channel == "telegram" else Response()
    lookup = lambda event: {"status": "scored", "score": score}
    assert deliver_once(book, "key", sender, NOW+1, score_lookup=lookup)
    assert deliver_telegram_once(book, ("fake", "fake"), sender, NOW+1, score_lookup=lookup)
    status = "sent" if allowed else "skipped"
    assert book.bark_status()["counts"] == {status: 1}
    assert book.bark_status()["recent"][0]["attempts"] == int(allowed)
    assert book.records()[0]["status"] == status
    assert bool(calls) is allowed
    if allowed:
        text = calls["bark"]["title"] + "\n" + calls["bark"]["body"]
        assert text == calls["telegram"]["text"]
        assert text.splitlines()[0] == "BTC · 上沿 1.618"
        assert len(text.splitlines()) == 3 and "点位 116.18" in text and "北京" in text
        assert "评分 0.4000 ·" not in text
        assert all(label not in text for label in ["-USDT-SWAP", "纽约时间", "最新价", "网页备用", "https://"])
        assert calls["bark"]["copy"] == "OKX:BTCUSDT.P"


@pytest.mark.parametrize("result", [
    {"status": "feature_error", "score": .9}, {"status": "excluded", "score": .9},
    {"status": "identity_mismatch", "score": .9}, {}, [], "bad",
])
def test_bark_rejects_terminal_or_malformed_scores(tmp_path, result):
    book, _ = armed(tmp_path)
    assert deliver_once(book, "key", lambda *a, **kw: pytest.fail("invalid score sent"), NOW+1,
                        score_lookup=lambda event: result)
    assert book.bark_status()["counts"] == {"skipped": 1}
    assert book.bark_status()["recent"][0]["attempts"] == 0


def test_bark_waits_without_spending_attempts_or_starving_ready_touch(tmp_path):
    book, levels = armed(tmp_path)
    book.touch(levels, SYMBOL, "lower", 90, NOW+1, NOW, "live")
    calls = []
    def sender(url, **kwargs):
        calls.append(kwargs["json"]["title"])
        return Response()
    def lookup(event):
        assert book.bark_status()["counts"].get("sending", 0) == 0
        # A write here also proves score IO does not hold FibBook's write lock.
        with book.db() as db:
            db.execute("UPDATE bark_changes SET enabled=enabled")
        return None if event["side"] == "upper" else scored(event)
    deliver_once(book, "key", sender, NOW+2, score_lookup=lookup)
    assert calls == [] and book.bark_status()["counts"] == {"pending": 2}
    book.recover()
    deliver_once(book, "key", sender, NOW+1002, score_lookup=lookup)
    assert calls == ["BTC · 下沿 1.618"]
    deliver_once(book, "key", sender, NOW+2002, score_lookup=scored)
    assert calls == ["BTC · 下沿 1.618", "BTC · 上沿 1.618"]
    assert all(r["attempts"] == 1 for r in book.bark_status()["recent"])
    assert not deliver_once(book, "key", sender, NOW+3002, score_lookup=scored)


@pytest.mark.parametrize("lookup", [None, lambda event: None, lambda event: 1/0])
def test_bark_missing_score_waits_only_inside_original_freshness(tmp_path, lookup):
    book, _ = armed(tmp_path)
    sender = lambda *a, **kw: pytest.fail("unscored or expired touch sent")
    assert deliver_once(book, "key", sender, NOW+1, score_lookup=lookup)
    assert book.bark_status()["counts"] == {"pending": 1}
    assert book.bark_status()["recent"][0]["attempts"] == 0
    assert deliver_once(book, "key", sender, NOW+FRESH_MS+1, score_lookup=lookup)  # touch 5m bar still open
    assert book.bark_status()["counts"] == {"pending": 1}
    assert not deliver_once(book, "key", sender, READY+FRESH_MS+1, score_lookup=scored)
    assert book.bark_status()["counts"] == {"skipped": 1}


@pytest.mark.parametrize("result", [None, {"status": "scored", "score": .9}])
def test_bark_score_read_racing_disable_cannot_send_or_revive_pending(tmp_path, result):
    book, _ = armed(tmp_path)
    def disable(event):
        book.set_enabled(False, NOW+2, "bark")
        book.set_enabled(True, NOW+3, "bark")
        return result
    assert deliver_once(book, "key", lambda *a, **kw: pytest.fail("disabled touch sent"), NOW+1,
                        score_lookup=disable)
    assert book.bark_status()["counts"] == {"skipped": 1}
    assert book.bark_status()["recent"][0]["attempts"] == 0
    assert book.records()[0]["status"] == "pending"


def test_bark_score_read_cannot_extend_expiry(tmp_path):
    book, _ = armed(tmp_path)
    assert deliver_once(book, "key", lambda *a, **kw: pytest.fail("expired touch sent"), NOW+1,
                        score_lookup=scored, clock=lambda: READY+FRESH_MS+1)
    assert book.bark_status()["counts"] == {"skipped": 1}
    assert book.bark_status()["recent"][0]["attempts"] == 0


def test_bark_concurrent_scored_readers_send_only_once(tmp_path):
    import threading
    book, _ = armed(tmp_path)
    barrier, calls = threading.Barrier(2), []
    def lookup(event):
        barrier.wait(timeout=5)
        return scored(event)
    def sender(url, **kwargs):
        calls.append(kwargs["json"])
        return Response()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: deliver_once(FibBook(tmp_path), "key", sender, NOW+1,
                                                       score_lookup=lookup), range(2)))
    assert results == [True, True]
    assert len(calls) == 1 and book.bark_status()["counts"] == {"sent": 1}
    assert book.bark_status()["recent"][0]["attempts"] == 1


def test_bark_scored_delivery_is_independent_of_disabled_telegram(tmp_path):
    book, _ = armed(tmp_path)
    book.set_enabled(False, NOW+1)
    assert deliver_once(book, "key", lambda *a, **kw: Response(), NOW+2, score_lookup=scored)
    assert book.bark_status()["counts"] == {"sent": 1}
    assert book.records()[0]["status"] == "skipped"

"""V13.1 retest notifications stay isolated and use only mock transports."""
from __future__ import annotations

from yoyo.monitor import FRESH_MS, TIMEFRAMES as STEPS
from yoyo.monitor.bark import BarkWorker, message as bark_message
from yoyo.monitor.joint_notifications import JOINT_PROTOCOL
from yoyo.monitor.notification_center import NotificationCenter, topic_for
from yoyo.monitor.notification_policy import activation, delivery_error
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker, message as telegram_message
from yoyo.monitor.v130_policy import (
    POLICY, SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES, VERSION,
    arm_v130, is_v130_signal,
)


TIMEFRAME = "15m"
STEP = STEPS[TIMEFRAME]
CLOSE = 60 * STEP


def event(*, close=CLOSE, **updates):
    value = {
        "protocol": SIGNAL_PROTOCOL,
        "kind": SIGNAL_KIND,
        "strategy_version": VERSION,
        "source": "live",
        "confirmation": "retest",
        "v130_admitted": True,
        "side": "long",
        "direction": "long",
        "timeframe": TIMEFRAME,
        "timeframe_min": 15,
        "bar_open_ms": close - STEP,
        "bar_close_ms": close,
        "signal_close_time": close,
        "confirmed": True,
        "is_closed": True,
        "ready": True,
        "price": 100.0,
        "reference_price": 100.0,
        "risk": 2.0,
        "initial_stop": 98.0,
        "reference_initial_risk": 2.0,
        "reference_initial_stop": 98.0,
        "anchor_close_ms": close - 24 * STEP,
        "breakout_close_ms": close - 4 * STEP,
        "retest_close_ms": close - STEP,
        "wait_bars": 24,
        "candidate_source": "ordinary_both",
        "source_sha256": "a" * 64,
        "entry_reference": "next_open_reference_not_fill",
        "executable_entry_time": None,
        "is_trade": False,
        "symbol": "BTC-USDT-SWAP",
        "detected_at_ms": close + 500,
    }
    value.update(updates)
    return value


def setup(tmp_path, *, activation_ms=CLOSE - 1):
    store = Store(tmp_path / "monitor.sqlite3")
    # Production starts only after the explicit V9 reset has been recorded.
    store.set_meta("migration:spike-v9-reset-v1", {"activated_ms": 0})
    center = NotificationCenter(tmp_path)
    arm_v130(store, activation_ms)
    return store, center


def enqueue(store, signal, now):
    return store.upsert_event(
        signal,
        notify=delivery_error(store, signal, now, "telegram") is None,
        bark_notify=delivery_error(store, signal, now, "bark") is None,
    )


def test_v130_validator_requires_closed_causal_period_contract():
    assert is_v130_signal(event())
    for updates in (
        {"timeframe": "30m"},
        {"timeframe": "2H", "timeframe_min": 120},
        {"side": "other"},
        {"confirmation": "yolo"},
        {"source": "replay"},
        {"candidate_source": "yolo"},
        {"is_trade": True},
        {"wait_bars": 25},
        {"wait_bars": 23},
        {"bar_open_ms": CLOSE - 2 * STEP},
        {"risk": 2.1},
        {"source_sha256": "not-a-digest"},
        {"breakout_close_ms": CLOSE - 24 * STEP},
        {"retest_close_ms": CLOSE + STEP},
        {"detected_at_ms": CLOSE - 1},
    ):
        assert not is_v130_signal(event(**updates))


def test_arm_is_idempotent_and_keeps_channel_cutovers_independent(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    early = CLOSE - 3 * STEP
    later = CLOSE - 2 * STEP
    store.activate_notification_policy(early, protocol=POLICY, kind=SIGNAL_KIND, retire_obsolete=False)
    store.activate_bark_policy(later, protocol=POLICY, kind=SIGNAL_KIND, retire_obsolete=False)

    first = arm_v130(store, CLOSE - STEP)
    again = arm_v130(store, CLOSE)

    assert first == again
    assert activation(store, "telegram", POLICY) == early
    assert activation(store, "bark", POLICY) == later
    assert store.timeframe_activation("15m", protocol=POLICY) == CLOSE - STEP
    assert store.event_count(kind=SIGNAL_KIND, protocol=SIGNAL_PROTOCOL) == 0


def test_persistence_cutoff_blocks_startup_history_and_dedupes_new_events(tmp_path):
    unarmed = Store(tmp_path / "unarmed" / "monitor.sqlite3")
    assert unarmed.upsert_event(event(), notify=True, bark_notify=True) is False

    store, _ = setup(tmp_path / "old", activation_ms=CLOSE)
    old = event()
    assert is_v130_signal(old)  # The signal is valid; only its final close is old.
    assert delivery_error(store, old, CLOSE + 1_000, "telegram") == "before_notification_policy_activation"
    assert store.upsert_event(old, notify=True, bark_notify=True) is False

    store, center = setup(tmp_path / "fresh", activation_ms=CLOSE - 1)
    fresh = event()
    now = CLOSE + 1_000
    assert topic_for(fresh) == "spike_v130"
    assert enqueue(store, fresh, now) is True
    assert enqueue(store, fresh, now) is False
    assert store.notification_status("telegram")["pending"] == 1
    assert store.notification_status("bark")["pending"] == 1
    rows = center.events(topic="spike_v130")
    assert rows["total"] == 2
    assert {row["channel"] for row in rows["items"]} == {"telegram", "bark"}


def test_v130_delivery_uses_final_close_for_freshness_activation_and_route(tmp_path):
    store, center = setup(tmp_path, activation_ms=CLOSE - 1)
    signal = event()
    # The valid anchor is six hours before activation; only the final
    # confirmation close participates in freshness and forward cutovers.
    assert signal["anchor_close_ms"] == signal["bar_close_ms"] - 6 * 60 * 60 * 1000
    assert is_v130_signal(signal)
    assert delivery_error(store, signal, CLOSE + 1_000, "telegram") is None
    assert delivery_error(store, signal, CLOSE - 1, "telegram") == "signal_expired"
    assert delivery_error(store, signal, CLOSE + FRESH_MS + 1, "bark") == "signal_expired"

    before_activation = event(close=CLOSE - STEP)
    assert delivery_error(store, before_activation, CLOSE + 1_000, "bark") == "before_notification_policy_activation"

    center.update_route("spike_v130", "telegram", False, CLOSE + 1)
    assert delivery_error(store, signal, CLOSE + 1_000, "telegram") == "subscription_disabled"
    assert delivery_error(store, signal, CLOSE + 1_000, "bark") is None

    route = next(row for row in center.snapshot({}, CLOSE + 1_000)["routes"]
                 if row["topic"] == "spike_v130" and row["channel"] == "bark")
    assert route["timeframes"] == list(TIMEFRAMES) == ["15m", "30m", "1H", "4H"]


def test_both_existing_senders_deliver_v130_text_with_mock_transports(tmp_path):
    store, _ = setup(tmp_path, activation_ms=CLOSE - 1)
    signal = event()
    now = CLOSE + 1_000
    assert store.upsert_event(signal, notify=True, bark_notify=True)

    telegram_calls = []

    class TelegramResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 41}}

    def send_telegram(url, **kwargs):
        telegram_calls.append((url, kwargs))
        return TelegramResponse()

    bark_calls = []

    class BarkResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"code": 200, "timestamp": 123}

    def send_bark(url, **kwargs):
        bark_calls.append((url, kwargs))
        return BarkResponse()

    assert TelegramWorker(store, creds=("token", "chat"), sender=send_telegram, enabled=True).deliver_once(now)
    assert BarkWorker(store, creds="device-key", sender=send_bark).deliver_once(now)

    telegram_text = telegram_calls[0][1]["json"]["text"]
    bark_payload = bark_calls[0][1]["json"]
    assert "V13.1 15m 回踩再突破" in telegram_text
    assert "次开参考，不代表成交" in telegram_text
    assert "V13.1 15m 回踩再突破" in bark_payload["subtitle"]
    assert "初始止损 98" in bark_payload["body"]
    assert "次开参考，不代表成交" in bark_payload["body"]
    assert "V12.8" not in telegram_text + bark_payload["subtitle"]
    assert "YOLO" not in telegram_text + bark_payload["subtitle"]
    assert store.notification_status("telegram")["sent"] == 1
    assert store.notification_status("bark")["sent"] == 1


def test_v130_message_helpers_are_explicitly_research_references():
    signal = event()
    assert "V13.1 15m 回踩再突破" in telegram_message(signal)
    assert "最终确认收盘" in telegram_message(signal)
    bark = bark_message(signal)
    assert bark["group"] == "SPIKE V13.1"
    assert "TradingView" in bark["body"]
    assert "V12.8" not in bark["title"]
    assert JOINT_PROTOCOL not in signal["protocol"]


def hourly_event(**updates):
    step = STEPS["1H"]
    close = 60 * step
    fields = dict(timeframe="1H", timeframe_min=60, bar_open_ms=close - step,
                  anchor_close_ms=close - 24 * step, breakout_close_ms=close - 4 * step,
                  retest_close_ms=close - step)
    return event(close=close, **{**fields, **updates})


def test_v131_accepts_every_monitored_period_on_its_own_clock():
    assert is_v130_signal(hourly_event())
    assert not is_v130_signal(hourly_event(timeframe_min=15))
    assert not is_v130_signal(hourly_event(bar_open_ms=60 * STEPS["1H"] - STEPS["15m"]))
    assert "V13.1 1H 回踩再突破" in telegram_message(hourly_event())
    assert bark_message(hourly_event())["title"].startswith("V13.1 · BTC-USDT-SWAP · 1H")


def test_each_period_has_its_own_forward_cutover(tmp_path):
    close = 60 * STEPS["1H"]
    store, _ = setup(tmp_path, activation_ms=close - 1)
    assert {tf: store.timeframe_activation(tf, protocol=POLICY) for tf in TIMEFRAMES} == dict.fromkeys(TIMEFRAMES, close - 1)
    signal = hourly_event()
    assert delivery_error(store, signal, close + 1_000, "telegram") is None
    assert delivery_error(store, hourly_event(detected_at_ms=close + FRESH_MS + 1), close + FRESH_MS + 1, "bark") == "signal_expired"

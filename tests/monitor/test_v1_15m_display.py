"""15m V1 is a closed-bar display stream, independent of Bark authorization."""
from copy import deepcopy
import math

from yoyo.monitor import BARK_TIMEFRAMES, DIRECT_TIMEFRAMES, DIRECT_POLICY, MODEL_PROTOCOL, SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES, TV_INTERVALS
from yoyo.monitor import v1_worker
from yoyo.monitor import notification_policy
from yoyo.monitor.notification_policy import arm_v1_bark, delivery_error
from yoyo.monitor.server import create_app
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store

STEP = 900000
NOW = 1000 * TIMEFRAMES["4H"]


class Client:
    now = NOW
    def synchronize(self): pass
    def clock(self): return self.now
    def instruments(self): return [{"instId": "TEST-USDT-SWAP", "tickSz": "0.01"}]
    def candles(self, symbol, timeframe, previous=None, limit=720):
        step = TIMEFRAMES[timeframe]
        return [{"t": self.now // step * step - step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 10.}], 0


def test_new_15m_scan_notifies_only_after_its_own_cutover(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    with monkeypatch.context() as previous:
        previous.setattr(notification_policy, "BARK_TIMEFRAMES", ("30m", "1H", "4H"))
        original_receipt = arm_v1_bark(store, 0)
    client = Client()
    monkeypatch.setattr(v1_worker, "OKX", lambda: client)
    def replay(candles, higher, timeframe, *, tick, chart_limit):
        close = candles[-1]["t"] + TIMEFRAMES[timeframe]
        event = dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, source="live", confirmation="raw",
                     timeframe=timeframe, timeframe_min=TIMEFRAMES[timeframe] // 60000, direction="long", side="long",
                     bar_open_ms=candles[-1]["t"], bar_close_ms=close, price=100., risk=2.,
                     confirmed=True, is_closed=True)
        return {"events": [event] if timeframe == "15m" else [], "chart": candles,
                "state": {"phase": "ready", "ready": True, "timeframe": timeframe}}
    monkeypatch.setattr(v1_worker, "analyze", replay)
    scanner = v1_worker.V1Scanner(str(store.path))
    scanner.scan_once()
    assert scanner.store.get_meta("display_policy:spike-v1:15m")["activated_ms"] == NOW
    client.now += STEP
    scanner.scan_once()
    assert scanner.store.arm_display_timeframe("15m", client.now) == NOW
    app = create_app(runtime=tmp_path, start_monitor=False)
    get = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/signals")
    for source, close in (("warmup", NOW), ("live", NOW + STEP)):
        rows = get(limit=10, source=source, timeframe="15m", confirmation="raw")["items"]
        assert len(rows) == 1 and rows[0]["bar_close_ms"] == close
        assert rows[0]["source"] == "live" and rows[0]["display_scope"] == source
        persisted = store.get_event(rows[0]["id"])
        assert delivery_error(store, persisted, client.now, "bark") == ("before_timeframe_activation" if source == "warmup" else None)
    assert store.displayed_start_count() == 1
    assert store.direct_event_count() == 1
    assert store.bark_status()["pending"] == 1 and store.telegram_status()["pending"] == 0
    assert store.candidate_counts() == {"pending": 1}
    assert BARK_TIMEFRAMES == DIRECT_TIMEFRAMES == ("15m", "30m", "1H", "4H")
    assert arm_v1_bark(store, client.now + STEP) == original_receipt
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert store.timeframe_activation("15m", protocol=protocol) == NOW
        assert store.timeframe_activation("1H", protocol=protocol) == 0
    assert TV_INTERVALS["15m"] == "15"


def test_15m_adapter_keeps_the_same_v1_features_and_prefix_causality():
    candles = []
    for i in range(380):
        c = 100. + .1 * math.sin(i) if i < 355 else 110. + (i - 355) * 2
        o = c - .1 if i < 355 else 100. if i == 355 else c - 2
        candles.append(dict(t=i * STEP, o=o, h=c + .1, l=o - .1, c=c, v=10. if i < 355 else 100.))
    full = analyze(candles, [], "15m", tick=.01)
    prefix = analyze(candles[:360], [], "15m", tick=.01)
    assert full["chart"][:360] == prefix["chart"]
    assert [r for r in full["events"] if r["bar_open_ms"] < 360 * STEP] == prefix["events"]
    hourly = deepcopy(candles)
    for i, row in enumerate(hourly): row["t"] = i * TIMEFRAMES["1H"]
    same = analyze(hourly, [], "1H", tick=.01)
    assert full["events"]  # Non-vacuous signal comparison.
    assert [(r["bar_open_ms"] // STEP, r["risk"], r["price"]) for r in full["events"]] == [
        (r["bar_open_ms"] // TIMEFRAMES["1H"], r["risk"], r["price"]) for r in same["events"]]

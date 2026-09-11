"""Display partitions use a persisted startup boundary, not notification state."""
import pytest
from fastapi import HTTPException

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, MODEL_KIND, MODEL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.server import create_app

CUTOVER = 1_800_000_000_000


def event(close, *, source="live", timeframe="1H", origin=None):
    row = dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, source=source,
               confirmation="raw", venue="okx", symbol="PEPE-USDT-SWAP", timeframe=timeframe,
               timeframe_min=TIMEFRAMES[timeframe] // 60000, direction="long", side="long",
               bar_close_ms=close, bar_open_ms=close - TIMEFRAMES[timeframe],
               price=1.0, is_closed=True, detected_at_ms=CUTOVER + 10000)
    if origin is not None:
        row.update(protocol=MODEL_PROTOCOL, kind=MODEL_KIND, confirmation="yolo",
                   indicator=event(origin, timeframe=timeframe), model={"status": "confirmed"})
    return row


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    store.set_meta("notification_policy:v1_bark_arm", {"activated_ms": CUTOVER})
    monkeypatch.setattr(app.state.monitor.client, "clock", lambda: CUTOVER + 10000)
    get = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/signals")
    return store, get


@pytest.mark.parametrize("tf", ["30m", "1H", "4H"])
def test_partition_boundary_before_pagination_and_unchanged_lineage(setup, tf):
    store, get = setup
    for close in (CUTOVER - 2, CUTOVER - 1, CUTOVER, CUTOVER + 1):
        store.upsert_event(event(close, timeframe=tf), notify=False, bark_notify=False)
    store.upsert_event(event(CUTOVER - 1, source="replay", timeframe=tf), notify=False, bark_notify=False)
    with store.connect() as db:
        before = list(db.iterdump())
    live = get(limit=1, source="live", timeframe=tf, confirmation="raw")
    assert [r["bar_close_ms"] for r in live["items"]] == [CUTOVER + 1]
    assert live["items"][0]["is_fresh"] is True
    warmup = get(limit=2, source="warmup", timeframe=tf, confirmation="raw")
    assert [r["bar_close_ms"] for r in warmup["items"]] == [CUTOVER, CUTOVER - 1]
    cursor = warmup["next_cursor"]
    last = get(limit=2, source="warmup", timeframe=tf, confirmation="raw",
               before_close_ms=cursor["close_ms"], before_id=cursor["event_id"])
    assert [r["bar_close_ms"] for r in last["items"]] == [CUTOVER - 2]
    assert all(r["source"] == "live" and r["display_scope"] == "warmup" and not r["is_fresh"]
               for r in warmup["items"] + last["items"])
    replay = get(limit=20, source="replay", confirmation="raw")
    assert len(replay["items"]) == 1 and replay["items"][0]["source"] == "replay"
    assert len(get(limit=20, confirmation="raw")["items"]) == 1  # Default is realtime.
    with store.connect() as db:
        assert list(db.iterdump()) == before  # No event, meta or outbox writes.


def test_yolo_uses_original_arrow_and_not_later_confirmation(setup):
    store, get = setup
    for origin in (CUTOVER - 1, CUTOVER + 1):
        store.upsert_event(event(origin + 1000, origin=origin), notify=False, bark_notify=False)
    for scope, origin in (("warmup", CUTOVER - 1), ("live", CUTOVER + 1)):
        rows = get(limit=10, source=scope, confirmation="yolo")["items"]
        assert len(rows) == 1 and rows[0]["indicator"]["bar_close_ms"] == origin
        assert rows[0]["is_fresh"] is (scope == "live")


def test_restart_and_new_notification_settings_do_not_move_display_boundary(setup, tmp_path):
    store, _ = setup
    store.upsert_event(event(CUTOVER + 1), notify=False, bark_notify=False)
    store.set_meta("migration:spike-burst-v1", {"cutoff_ms": CUTOVER + 10000})
    store.activate_bark_policy(CUTOVER + 10000)
    app = create_app(runtime=tmp_path, start_monitor=False)
    get = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/signals")
    assert len(get(limit=10, source="live")["items"]) == 1
    assert get(limit=10, source="warmup")["items"] == []


@pytest.mark.parametrize("receipt", [None, [], {}, {"activated_ms": None}, {"activated_ms": True}, {"activated_ms": -1}])
def test_unknown_boundary_does_not_claim_history_is_live(setup, receipt):
    store, get = setup
    store.set_meta("notification_policy:v1_bark_arm", receipt)
    for source in (None, "live", "warmup"):
        with pytest.raises(HTTPException) as err:
            get(limit=10, source=source)
        assert err.value.status_code == 503
    assert get(limit=10, source="replay")["items"] == []

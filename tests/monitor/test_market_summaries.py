"""The market overview must not deserialize every persisted chart."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from yoyo.monitor.server import create_app
from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store


class FixedClock:
    def clock(self):
        return 2_000_000


def market_row():
    return {
        "symbol": "PEPE-USDT-SWAP", "timeframe": "1H", "active": True,
        "phase": "ready", "bar_close_ms": 1_800_000,
        "chart": [{"t": 0, "o": 1, "h": 2, "l": 0, "c": 1, "v": 4}] * 800,
        "events": [{"id": "archived-event"}] * 200,
    }


def test_market_overview_excludes_persisted_chart_and_events(tmp_path):
    store = Store(tmp_path / "monitor.sqlite")
    row = market_row()
    store.upsert_market(row)

    summaries = store.list_market_summaries()
    assert len(summaries) == 1
    assert summaries[0]["symbol"] == row["symbol"]
    assert "chart" not in summaries[0] and "events" not in summaries[0]

    monitor = Monitor(store, client=FixedClock())
    overview = monitor.markets()
    assert "chart" not in overview[0] and "events" not in overview[0]
    selected = monitor.chart(row["symbol"], row["timeframe"])
    assert selected["candles"] == row["chart"]
    assert selected["events"] == row["events"]


def test_summary_backfill_never_requires_http_to_parse_legacy_chart_payload(tmp_path):
    store = Store(tmp_path / "monitor.sqlite")
    row = market_row()
    store.upsert_market(row)
    with store.connect() as db:
        db.execute("DELETE FROM market_summaries")

    receipt = store.backfill_market_summaries()
    assert receipt == {"backfilled": 1, "skipped": 0}
    summaries = store.list_market_summaries()
    assert summaries == [{key: value for key, value in row.items() if key not in {"chart", "events"}}]
    assert store.get_market(row["symbol"], row["timeframe"])["chart"] == row["chart"]


def test_market_endpoint_rejects_legacy_summary_backfill_until_compact_rows_exist(tmp_path):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    store.set_meta("market_summary_backfill", {"status": "running", "markets": 1, "summaries": 0})
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/markets")
    with pytest.raises(HTTPException) as error:
        endpoint()
    assert error.value.status_code == 503

    store.upsert_market(market_row())
    store.set_meta("market_summary_backfill", {"status": "complete", "markets": 1, "summaries": 1})
    assert endpoint()["total"] == 1

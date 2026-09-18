"""突破 / 突破+spike monitor menus: causality, book semantics, API and page contract."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import spike_lines as sl
from yoyo.monitor import spike_lines_api as api
from yoyo.monitor.spike_lines_worker import LinesBook, parse_daily

WEB = Path(__file__).resolve().parents[2] / "yoyo" / "monitor" / "static"


def _walk(n: int, seed: int, minutes: int = 15) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Down-then-up legs so descending three-point lines form and then break.
    drift = np.concatenate([np.full(n // 2, -0.0006), np.full(n - n // 2, 0.0007)])
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n))
    index = pd.date_range("2026-01-01", periods=n, freq=f"{minutes}min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(100, 200, n)}, index=index)


def _key(event: dict) -> tuple:
    return event["kind"], event["bar_open_ms"], round(event["close"], 10), event.get("a_ms"), event.get("source")


@pytest.mark.parametrize("seed", [3, 11, 29])
def test_events_on_a_prefix_equal_the_full_run_up_to_that_bar(seed):
    """No event may depend on a bar after its own close (no lookahead)."""
    full = _walk(1200, seed)
    whole = sl.analyze(full, "15m", tick=0.001, asset="TEST")
    for cut in (700, 950):
        part = sl.analyze(full.iloc[:cut], "15m", tick=0.001, asset="TEST")
        end = int(full.index[cut - 1].value // 1_000_000)
        expected = [_key(e) for e in whole["breaks"] + whole["joints"] if e["bar_open_ms"] <= end]
        assert sorted(_key(e) for e in part["breaks"] + part["joints"]) == sorted(expected)
    assert whole["breaks"], "fixture should produce at least one break"


def test_breaks_carry_the_line_that_broke_and_it_was_confirmed_before_the_break():
    whole = sl.analyze(_walk(1200, 3), "15m", tick=0.001, asset="TEST")
    for e in whole["breaks"]:
        assert e["a_ms"] < e["b_ms"] < e["c_ms"] < e["born_close_ms"] <= e["bar_open_ms"]
        assert e["a_price"] > e["b_price"] > e["c_price"]
        assert e["close"] > e["line_at_bar"]


def test_complete_buckets_drop_the_open_and_the_gapped_bucket():
    hourly = _walk(10, 1, minutes=60)
    hourly = hourly.drop(hourly.index[4])            # a missing source bar
    two_hour = sl.complete_buckets(hourly, 60, 120)
    # 10 hourly bars -> 5 buckets; one has a hole, none is partial at the end (10 is even)
    assert len(two_hour) == 4
    assert pd.Timestamp("2026-01-01 04:00", tz="UTC") not in two_hour.index
    assert len(sl.complete_buckets(_walk(9, 1, minutes=60), 60, 120)) == 4   # last bucket still open


def test_book_is_insert_only_and_keeps_first_detection(tmp_path):
    book = LinesBook(tmp_path / "lines.sqlite3")
    event = {"id": "a" * 24, "kind": "break", "symbol": "SOL-USDT-SWAP", "timeframe": "15m", "bar_open_ms": 0,
             "bar_close_ms": 900_000, "detected_at_ms": 1_000_000}
    assert book.insert([event]) == 1
    assert book.insert([{**event, "detected_at_ms": 9_999_999}]) == 0
    rows = api.events(tmp_path / "lines.sqlite3", kind="break", now_ms=2_000_000)
    assert len(rows) == 1 and rows[0]["detected_at_ms"] == 1_000_000


def test_display_state_live_late_history():
    base = {"timeframe": "15m", "bar_close_ms": 10_000_000}
    assert api.classify({**base, "detected_at_ms": 10_060_000}, 5_000_000, 10_100_000)["display_state"] == "live"
    assert api.classify({**base, "detected_at_ms": 12_000_000}, 5_000_000, 12_100_000)["display_state"] == "late"
    assert api.classify({**base, "detected_at_ms": 10_060_000}, 20_000_000, 20_100_000)["display_state"] == "history"
    assert api.classify({**base, "detected_at_ms": 10_060_000}, 5_000_000, 10_100_000)["is_fresh"] is True


def test_daily_parser_keeps_only_confirmed_closed_utc_days():
    day = 86_400_000
    rows = [[str(3 * day), "1", "2", "0.5", "1.5", "10", "0", "0", "1"],
            [str(4 * day), "1", "2", "0.5", "1.5", "10", "0", "0", "0"],       # unconfirmed
            [str(2 * day + 1), "1", "2", "0.5", "1.5", "10", "0", "0", "1"]]   # not a UTC day
    assert [r["t"] for r in parse_daily(rows, 5 * day)] == [3 * day]


def test_api_routes_before_and_after_the_worker_started(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app
    client = TestClient(create_app(runtime=tmp_path, start_monitor=False))
    assert client.get("/api/lines/status").json()["configured"] is False
    assert client.get("/api/lines/events", params={"kind": "joint"}).json()["items"] == []
    assert client.get("/api/lines/events", params={"kind": "joint", "timeframe": "1Dutc"}).status_code == 400
    book = LinesBook(api.database(tmp_path))
    book.set_meta("activation", {"activated_ms": 0})
    book.insert([{"id": "b" * 24, "kind": "joint", "symbol": "HYPE-USDT-SWAP", "timeframe": "15m", "bar_open_ms": 1,
                  "bar_close_ms": 900_001, "detected_at_ms": 960_000, "source": "higher"}])
    body = client.get("/api/lines/events", params={"kind": "joint", "timeframe": "15m"}).json()
    assert [row["symbol"] for row in body["items"]] == ["HYPE-USDT-SWAP"]
    assert body["execution_eligible"] is False and body["notification_eligible"] is False
    assert client.get("/api/lines/status").json()["counts"]["joint"] == {"15m": 1}


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get("id"):
            self.ids.add(dict(attrs)["id"])


def test_page_has_both_menus_and_the_shared_view():
    html = (WEB / "index.html").read_text()
    parser = _Ids()
    parser.feed(html)
    assert {"lines-view", "lines-rows", "lines-empty", "lines-timeframes", "lines-search", "nav-joints-count",
            "nav-breaks-count", "load-more-lines"} <= parser.ids
    assert 'data-view="joints"' in html and 'data-view="breaks"' in html
    assert "不推送 · 不下单" in html
    script = (WEB / "app.js").read_text()
    assert "/api/lines/events?kind=" in script and "突破+spike（上级突破）" in script

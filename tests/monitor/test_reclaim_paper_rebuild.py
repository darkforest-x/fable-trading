"""Rebuild candle source: complete bars only, as of the simulated clock, each range fetched once."""
import time

from yoyo.monitor import reclaim_paper_rebuild as rb

MIN = 60_000


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return {"code": "0", "data": self.data}


class FakeSession:
    """history-candles: rows strictly before ``after``, newest first; the newest bar is still open."""

    def __init__(self, newest):
        self.newest, self.calls = newest, []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        after, limit = int(params["after"]), int(params["limit"])
        rows = []
        for ts in range(min(after-MIN, self.newest), self.newest-10_000*MIN, -MIN)[:limit]:
            rows.append([str(ts), "1", "2", "0.5", str(ts % 7+1), "1", "1", "10", "0" if ts == self.newest else "1"])
        return Response(rows)


def cache(tmp_path, newest):
    c = rb.CandleCache(tmp_path/"c.sqlite3", FakeSession(newest))
    rb.GAP_S = 0
    return c


def test_history_client_serves_complete_bars_before_after_and_now(tmp_path):
    now = (int(time.time()*1000)//MIN-5)*MIN
    c = cache(tmp_path, now+4*MIN)
    client = rb.HistoryClient(c, now+30_000)
    rows = client.get("/", {"instId": "A", "bar": "1m", "limit": "10"})
    assert [int(r[0]) for r in rows] == [now-k*MIN for k in range(1, 11)]  # the bar opened at `now` is not complete
    rows = client.get("/", {"instId": "A", "bar": "1m", "limit": "3", "after": str(now-5*MIN)})
    assert [int(r[0]) for r in rows] == [now-6*MIN, now-7*MIN, now-8*MIN]
    assert all(r[8] == "1" for r in rows)


def test_each_range_is_fetched_once_even_when_empty(tmp_path):
    now = (int(time.time()*1000)//MIN-5)*MIN
    c = cache(tmp_path, now)
    c.rows("A", "1m", now-600*MIN, now-100*MIN)
    first = len(c.session.calls)
    c.rows("A", "1m", now-500*MIN, now-200*MIN)
    assert len(c.session.calls) == first
    c.rows("A", "1m", now-20_000*MIN, now-19_000*MIN)  # before the listing: empty but covered
    n = len(c.session.calls)
    assert c.rows("A", "1m", now-20_000*MIN, now-19_000*MIN) == [] and len(c.session.calls) == n


def test_levels_rebuild_matches_the_fib_formula():
    sig = {"week": "2026-10-26", "symbol": "X", "high": 110., "low": 100., "candle_ms": 1793300000000}
    lv = rb.levels_of(sig)
    assert (round(lv["upper"], 6), round(lv["lower"], 6)) == (116.18, 93.82)
    assert lv["end"]-lv["start"] in (24*3_600_000, 25*3_600_000, 23*3_600_000)

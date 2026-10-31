"""Read-only history projection for Monday reclaim touch and close records."""
from datetime import datetime
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from yoyo.monitor.monday_reclaim_paper import ReclaimPaper, install, shanghai_day_start_ms


TZ = ZoneInfo("Asia/Shanghai")
DAY_START = int(datetime(2026, 10, 27, tzinfo=TZ).timestamp()) * 1000
NOW = int(datetime(2026, 10, 27, 0, 30, tzinfo=TZ).timestamp() * 1000)


class FakeClient:
    def __init__(self, now=NOW):
        self.now = now

    def clock(self):
        return self.now


class HistoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = FakeClient()
        fib = SimpleNamespace(client=self.client, lock=threading.RLock(), quotes={}, connected=True)
        self.engine = ReclaimPaper(self.tmp.name, fib, enable_v3=False, enable_v3add=False,
                                   enable_spike=False, enable_v3lock=False, enable_allin3=False,
                                   enable_v3add20=False)

    def tearDown(self):
        self.tmp.cleanup()

    def add_signal(self, symbol, candle_ms, *, score=0.6, ema_order=3, features=None):
        with self.engine.db() as db:
            cur = db.execute(
                "INSERT INTO signals (week,symbol,side,level,target,high,low,candle_ms,observed_ms,status,"
                "score,taken,features,error,ema_order) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, symbol, 1, 100., 110., 120., 90., candle_ms, candle_ms+1,
                 "scored", score, 1, json.dumps(features or {"feature": 1}), None, ema_order))
            return cur.lastrowid

    def add_position(self, signal_id, symbol, *, track=60, fill_ms=None, stage="closed", taken=1,
                     entry=100., notional=100., remaining=None, exit_ms=None, score_net=1., parent_id=None,
                     with_exit=False):
        fill_ms = DAY_START-1000 if fill_ms is None else fill_ms
        remaining = (0. if stage == "closed" else notional) if remaining is None else remaining
        if exit_ms is None and stage == "closed":
            exit_ms = DAY_START-1
        with self.engine.db() as db:
            cur = db.execute(
                "INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,decision_ms,stage,"
                "taken,notional,last_seen_ms,exit_ms,exit_price,reason,net_bp,pnl,remaining_notional,parent_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (signal_id, track, symbol, 1, entry, 110., fill_ms, fill_ms+60_000, stage, taken,
                 notional, fill_ms, exit_ms, 101. if exit_ms is not None else None,
                 "target" if exit_ms is not None else None, score_net, score_net/100, remaining, parent_id))
            position_id = cur.lastrowid
            if with_exit:
                db.execute("INSERT INTO exits (position_id,notional,exit_ms,price,reason,net_bp) "
                           "VALUES (?,?,?,?,?,?)", (position_id, notional, exit_ms, 101., "target", score_net))
            return position_id


class TestReclaimRecordHistory(HistoryFixture):
    def test_shanghai_midnight_boundary_and_exact_signal_hold_exemption(self):
        self.client.now = NOW
        self.assertEqual(shanghai_day_start_ms(NOW), DAY_START)
        held = self.add_signal("SAME-USDT-SWAP", DAY_START-3)
        same_symbol_unheld = self.add_signal("SAME-USDT-SWAP", DAY_START-2)
        zero_remaining = self.add_signal("ZERO-USDT-SWAP", DAY_START-2)
        shadow = self.add_signal("SHADOW-USDT-SWAP", DAY_START-2)
        pending_add = self.add_signal("PENDING-USDT-SWAP", DAY_START-2)
        unfilled = self.add_signal("UNFILLED-USDT-SWAP", DAY_START-2)
        invalid_entry = self.add_signal("INVALID-USDT-SWAP", DAY_START-2)
        exact_boundary = self.add_signal("BOUNDARY-USDT-SWAP", DAY_START)
        self.add_position(held, "SAME-USDT-SWAP", track=5, stage="pending_confirm", taken=1,
                          notional=100, remaining=75)
        self.add_position(zero_remaining, "ZERO-USDT-SWAP", stage="breakeven", taken=1,
                          notional=100, remaining=0)
        self.add_position(shadow, "SHADOW-USDT-SWAP", stage="breakeven", taken=0,
                          notional=100, remaining=100)
        self.add_position(pending_add, "PENDING-USDT-SWAP", stage="pending_fill", taken=1,
                          notional=100, remaining=100)
        self.add_position(unfilled, "UNFILLED-USDT-SWAP", fill_ms=0, stage="pending_confirm", taken=1,
                          notional=100, remaining=100)
        self.add_position(invalid_entry, "INVALID-USDT-SWAP", stage="breakeven", taken=1,
                          entry=0, notional=100, remaining=100)
        with self.engine.db() as db:
            db.execute("UPDATE positions SET remaining_notional=NULL WHERE signal_id=?", (held,))

        history = self.engine.record_history(tab="events")
        history_ids = {row["id"] for row in history["rows"]}
        self.assertIn(same_symbol_unheld, history_ids)
        self.assertIn(zero_remaining, history_ids)
        self.assertIn(shadow, history_ids)
        self.assertIn(pending_add, history_ids)
        self.assertIn(unfilled, history_ids)
        self.assertIn(invalid_entry, history_ids)
        self.assertNotIn(held, history_ids)
        self.assertNotIn(exact_boundary, history_ids)

        today_ids = {row["id"] for row in self.engine.snapshot(list_period="today")["record_signals"]}
        self.assertIn(held, today_ids)
        self.assertNotIn(same_symbol_unheld, today_ids)
        self.assertIn(exact_boundary, today_ids)

    def test_today_snapshot_keeps_all_today_closes_by_exit_and_preserves_recent_stats(self):
        held = self.add_signal("OLD-HELD-USDT-SWAP", DAY_START-10)
        self.add_position(held, "OLD-HELD-USDT-SWAP", stage="breakeven", taken=1,
                          notional=100, remaining=100)
        for i in range(105):
            symbol = f"CLOSE-{i:03d}-USDT-SWAP"
            sid = self.add_signal(symbol, DAY_START+i, score=.7)
            self.add_position(sid, symbol, fill_ms=DAY_START-20_000-i,
                              exit_ms=DAY_START+i, score_net=float(i), with_exit=(i == 0))
        next_day = self.add_signal("NEXT-DAY-USDT-SWAP", DAY_START+86_400_000)
        self.add_position(next_day, "NEXT-DAY-USDT-SWAP", exit_ms=DAY_START+86_400_000)

        recent = self.engine.snapshot()
        today = self.engine.snapshot(list_period="today")
        self.assertEqual(len(recent["tracks"]["60"]["closed"]), 100)
        self.assertEqual(len(today["tracks"]["60"]["closed"]), 100)
        self.assertEqual(len(today["tracks"]["60"]["record_closed"]), 105)
        self.assertEqual(today["record_day_start_ms"], DAY_START)
        self.assertTrue(all(row["fill_ms"] < DAY_START <= row["exit_ms"]
                            for row in today["tracks"]["60"]["record_closed"]))
        self.assertEqual(today["tracks"]["60"]["stats"], recent["tracks"]["60"]["stats"])
        self.assertEqual(today["tracks"]["60"]["latest_funded"], recent["tracks"]["60"]["latest_funded"])
        self.assertEqual(today["tracks"]["60"]["closed"], recent["tracks"]["60"]["closed"])
        self.assertEqual(today["signals"], recent["signals"])
        signal_ids = {row["id"] for row in today["record_signals"]}
        self.assertIn(held, signal_ids)
        self.assertNotIn(next_day, signal_ids)
        self.assertNotIn(held, {row["id"] for row in recent["signals"]})
        exit_row = next(row for row in today["tracks"]["60"]["record_closed"] if row["symbol"] == "CLOSE-000-USDT-SWAP")
        self.assertEqual(len(exit_row["exits"]), 1)

    def test_history_is_bounded_paginated_searchable_scoped_and_score_ordered(self):
        for i in range(110):
            symbol = f"Coin-{i:03d}-USDT-SWAP"
            sid = self.add_signal(symbol, DAY_START-10_000-i, score=i/100, ema_order=i % 5,
                                  features={"index": i})
            self.add_position(sid, symbol, exit_ms=DAY_START-100-i, score_net=float(i), with_exit=(i == 109))
        for i in range(3):
            symbol = f"Shadow-{i}-USDT-SWAP"
            sid = self.add_signal(symbol, DAY_START-100-i, score=None)
            self.add_position(sid, symbol, taken=0, notional=0., exit_ms=DAY_START-50-i)
        boundary = self.add_signal("AFTER-MIDNIGHT-USDT-SWAP", DAY_START+1)
        self.add_position(boundary, "AFTER-MIDNIGHT-USDT-SWAP", exit_ms=DAY_START)

        first = self.engine.record_history(tab="closed", scope="funded", limit=100)
        second = self.engine.record_history(tab="closed", scope="funded", offset=100, limit=100)
        self.assertEqual((first["total"], len(first["rows"]), first["has_more"]), (110, 100, True))
        self.assertEqual((second["total"], len(second["rows"]), second["has_more"]), (110, 10, False))
        self.assertGreaterEqual(first["rows"][0]["score"], first["rows"][-1]["score"])
        self.assertEqual(first["rows"][0]["score"], 1.09)
        self.assertEqual(second["rows"][-1]["score"], 0.)
        decorated = first["rows"][0]
        self.assertEqual(decorated["score"], 1.09)
        self.assertEqual(decorated["features"], {"index": 109})
        self.assertEqual(len(decorated["exits"]), 1)
        self.assertEqual(decorated["ema_order"], 4)

        found = self.engine.record_history(tab="closed", scope="funded", q="cOiN-10", limit=100)
        self.assertEqual(found["total"], 10)
        self.assertTrue(all("coin-10" in row["symbol"].lower() for row in found["rows"]))
        shadow = self.engine.record_history(tab="closed", scope="shadow")
        all_rows = self.engine.record_history(tab="closed", scope="all", offset=100, limit=100)
        self.assertEqual(shadow["total"], 3)
        self.assertEqual(all_rows["total"], 113)
        self.assertEqual(len(all_rows["rows"]), 13)
        self.assertEqual([row["score"] for row in all_rows["rows"][:10]], [i/100 for i in range(9, -1, -1)])
        self.assertTrue(all(row["score"] is None for row in all_rows["rows"][10:]))
        self.assertNotIn(boundary, {row["signal_id"] for row in all_rows["rows"]})

    def test_history_time_sort_is_stable_across_pages_for_touch_and_exit_time(self):
        for i in range(105):
            symbol = f"Timed-{i:03d}-USDT-SWAP"
            candle_ms = DAY_START-1_000 if i in (0, 1) else DAY_START-1_000-i
            exit_ms = DAY_START-2_000 if i in (0, 1) else DAY_START-2_000-i
            sid = self.add_signal(symbol, candle_ms, score=(104-i)/100)
            self.add_position(sid, symbol, exit_ms=exit_ms)

        # Descending timestamps put the higher ID first on a tie; ascending
        # timestamps reverse both the timestamp and ID directions.
        for tab, time_field in (("events", "candle_ms"), ("closed", "exit_ms")):
            for sort, reverse in (("time_desc", True), ("time_asc", False)):
                first = self.engine.record_history(tab=tab, scope="funded", sort=sort, limit=100)
                second = self.engine.record_history(tab=tab, scope="funded", sort=sort, offset=100, limit=100)
                joined = first["rows"] + second["rows"]
                self.assertEqual((first["total"], len(first["rows"]), first["has_more"]), (105, 100, True))
                self.assertEqual((second["total"], len(second["rows"]), second["has_more"]), (105, 5, False))
                self.assertEqual(len({row["id"] for row in joined}), 105)
                keys = [(row[time_field], row["id"]) for row in joined]
                expected = sorted(keys, key=lambda pair: pair, reverse=reverse)
                self.assertEqual(keys, expected)

            scored = self.engine.record_history(tab=tab, scope="funded", limit=100)
            self.assertEqual(scored["rows"][0]["score"], 1.04)

    def test_history_time_order_clauses_put_null_timestamps_last(self):
        with self.engine.db() as db:
            db.execute("CREATE TEMP TABLE sort_probe (id INTEGER, candle_ms INTEGER, exit_ms INTEGER, score REAL)")
            db.executemany("INSERT INTO sort_probe VALUES (?,?,?,?)",
                            [(1, None, None, .5), (2, 10, 10, .5), (3, 10, 10, .5)])
            for tab, alias in (("events", "s"), ("closed", "p")):
                for sort, expected in (("time_desc", [3, 2, 1]), ("time_asc", [2, 3, 1])):
                    order = self.engine._history_order_by(tab, sort)
                    rows = db.execute(f"SELECT {alias}.id FROM sort_probe {alias} ORDER BY {order}").fetchall()
                    self.assertEqual([row[0] for row in rows], expected)

        for sort in ("descending", "score DESC; DROP TABLE positions"):
            with self.assertRaisesRegex(ValueError, "sort must be"):
                self.engine.record_history(sort=sort)

    def test_history_api_validates_filters_and_reads_without_mutation(self):
        sid = self.add_signal("API-USDT-SWAP", DAY_START-1, score=.1, features={"x": 1})
        self.add_position(sid, "API-USDT-SWAP", exit_ms=DAY_START-1, with_exit=True)
        older = self.add_signal("API-OLDER-USDT-SWAP", DAY_START-2, score=.9)
        self.add_position(older, "API-OLDER-USDT-SWAP", exit_ms=DAY_START-2)
        before_positions = self.rows("SELECT * FROM positions ORDER BY id")
        before_tracks = self.rows("SELECT * FROM tracks ORDER BY track")
        app = FastAPI()
        install(app, self.engine)
        routes = {route.path: route for route in app.routes if getattr(route, "path", None)}
        history_route = routes["/api/monday-reclaim-paper/history"]
        valid = history_route.endpoint(track="60", tab="events", scope="funded", q="", offset=0,
                                       limit=1, sort="time_desc")
        payload = json.loads(valid.body)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["rows"][0]["id"], sid)
        self.assertEqual(payload["rows"][0]["features"], {"x": 1})
        snapshot_route = routes["/api/monday-reclaim-paper"]
        today = json.loads(snapshot_route.endpoint(list_period="today").body)
        self.assertEqual(today["record_day_start_ms"], DAY_START)

        schema = app.openapi()
        history_parameters = {p["name"]: p["schema"] for p in
                              schema["paths"]["/api/monday-reclaim-paper/history"]["get"]["parameters"]}
        self.assertEqual(history_parameters["tab"]["enum"], ["events", "closed"])
        self.assertEqual(history_parameters["scope"]["enum"], ["funded", "shadow", "all"])
        self.assertEqual(history_parameters["sort"]["enum"], ["score", "time_desc", "time_asc"])
        self.assertEqual(history_parameters["sort"]["default"], "score")
        self.assertEqual((history_parameters["offset"]["minimum"], history_parameters["limit"]["minimum"],
                          history_parameters["limit"]["maximum"], history_parameters["q"]["maxLength"]),
                         (0, 1, 100, 100))
        self.assertEqual(history_parameters["track"]["enum"], ["60", "5", "v3", "v3add", "spike", "v3lock", "allin3", "v3add20", "live-allin3"])
        self.assertEqual(schema["paths"]["/api/monday-reclaim-paper"]["get"]["parameters"][0]["schema"]["enum"],
                         ["recent", "today"])
        self.assertEqual(self.rows("SELECT * FROM positions ORDER BY id"), before_positions)
        self.assertEqual(self.rows("SELECT * FROM tracks ORDER BY track"), before_tracks)
        with self.assertRaisesRegex(ValueError, "sort must be"):
            history_route.endpoint(track="60", tab="events", scope="funded", q="", offset=0,
                                  limit=1, sort="bad")

    def rows(self, sql):
        with self.engine.db() as db:
            return [dict(row) for row in db.execute(sql)]

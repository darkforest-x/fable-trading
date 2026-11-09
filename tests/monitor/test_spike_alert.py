"""Market-wide spike alert: threshold, window, cooldown, live-only, per side, delivery receipts."""
import json
import tempfile
import unittest

from yoyo.monitor.spike_alert import THRESHOLD, SpikeAlert, alert_text, install

MIN = 60_000
T0 = 1_792_000_000_000 // MIN * MIN


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


class SpikeAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def sender(self, url, json=None, **kwargs):
        self.sent.append((url, json))
        return FakeResponse(200, {"ok": True, "result": {"message_id": 1}} if "telegram" in url else {"code": 200, "timestamp": 1})

    def alert(self, threshold=8, **kw):
        return SpikeAlert(self.tmp.name, threshold=threshold, telegram_creds=("token", "chat"), bark_key="k"*22, sender=self.sender,
                          clock=lambda: T0, **kw)

    def test_owner_threshold_is_25_coins(self):
        self.assertEqual(THRESHOLD, 25)
        a = SpikeAlert(self.tmp.name, telegram_creds=("t", "c"), bark_key="", sender=self.sender, clock=lambda: T0)
        self.touch(a, 24)
        self.assertEqual(a.events.qsize(), 0)
        self.touch(a, 1, start=24, minute=3)
        self.assertEqual((a.events.qsize(), a.snapshot()["threshold"]), (1, 25))

    def touch(self, a, n, side="lower", start=0, minute=0, source="live"):
        for k in range(start, start+n):
            a.on_touch({}, f"C{k}-USDT-SWAP", side, 1., T0, T0+minute*MIN, source)

    def test_seven_coins_do_not_alert_and_the_eighth_does(self):
        a = self.alert()
        self.touch(a, 7)
        self.assertEqual(a.events.qsize(), 0)
        self.touch(a, 1, start=7, minute=4)
        self.assertEqual(a.events.qsize(), 1)
        alert = a.events.get_nowait()
        self.assertEqual((alert["n"], alert["side"]), (8, "lower"))
        self.assertEqual(a.deliver(alert), {"telegram": "sent", "bark": "sent"})
        self.assertEqual([r["telegram"] for r in a.snapshot()["alerts"]], ["sent"])
        self.assertIn("8 个币同时触及下方 1.618", self.sent[0][1]["text"])

    def test_touches_older_than_five_minutes_drop_out(self):
        a = self.alert()
        self.touch(a, 7)
        self.touch(a, 1, start=7, minute=5)
        self.assertEqual(a.events.qsize(), 0)

    def test_one_alert_per_side_per_cooldown_and_sides_are_independent(self):
        a = self.alert()
        self.touch(a, 8)
        self.touch(a, 8, start=8, minute=10)
        self.assertEqual(a.events.qsize(), 1)
        self.touch(a, 8, side="upper", minute=10)
        self.assertEqual(a.events.qsize(), 2)
        self.touch(a, 8, start=20, minute=31)
        self.assertEqual(a.events.qsize(), 3)

    def test_history_and_repeated_symbols_do_not_count(self):
        a = self.alert()
        self.touch(a, 10, source="history")
        for _ in range(10):
            a.on_touch({}, "SAME-USDT-SWAP", "lower", 1., T0, T0, "live")
        self.assertEqual(a.events.qsize(), 0)

    def test_cooldown_survives_restart(self):
        a = self.alert()
        self.touch(a, 8)
        b = self.alert()
        self.touch(b, 8, start=8, minute=5)
        self.assertEqual(b.events.qsize(), 0)

    def test_uncertain_delivery_is_recorded_not_retried(self):
        def flaky(url, json=None, **kw):
            raise TimeoutError("slow")
        a = SpikeAlert(self.tmp.name, threshold=8, telegram_creds=("t", "c"), bark_key="", sender=flaky, clock=lambda: T0)
        self.touch(a, 8)
        self.assertEqual(a.deliver(a.events.get_nowait()), {"telegram": "unknown"})
        row = a.snapshot()["alerts"][0]
        self.assertEqual((row["telegram"], row["bark"]), ("unknown", "no_channel"))

    def test_text_names_every_trigger_coin(self):
        # Owner 2026-11-09: “有多少显示多少” — the push lists all coins, not the first 12.
        text = alert_text(dict(side="upper", last_ms=T0, n=15, symbols=[f"S{k}-USDT-SWAP" for k in range(15)]))
        self.assertIn("15 个币同时触及上方 1.618", text); self.assertIn("S14", text); self.assertNotIn("等", text); self.assertIn("只通知", text)

    def test_api_is_read_only(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        a = self.alert()
        self.touch(a, 8)
        app = FastAPI(); install(app, a)
        r = TestClient(app).get("/api/spike-alerts")
        self.assertEqual((r.status_code, r.json()["threshold"], len(r.json()["alerts"])), (200, 8, 1))
        self.assertTrue(r.json()["notify_only"])


    def test_active_until_acknowledged_by_any_page(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        a = self.alert()
        self.touch(a, 8)
        app = FastAPI(); install(app, a); client = TestClient(app)
        snap = client.get("/api/spike-alerts").json()
        self.assertEqual(len(snap["active"]), 1)
        self.assertIn("全市场插针报警", snap["active"][0]["text"])
        aid = snap["active"][0]["id"]
        url = f"/api/spike-alerts/{aid}/ack"
        self.assertEqual(client.post(url).status_code, 403)
        headers = {"origin": "http://testserver", "x-spike-action": "ack-spike-alert"}
        self.assertEqual(client.post(url, headers={**headers, "origin": "https://evil.invalid"}).status_code, 403)
        first = client.post(url, headers=headers).json()
        self.assertEqual(client.post(url, headers=headers).json(), first)
        self.assertEqual(client.get("/api/spike-alerts").json()["active"], [])
        self.assertEqual(client.post("/api/spike-alerts/999/ack", headers=headers).status_code, 404)

    def test_active_expires_after_six_hours(self):
        now = [T0]
        a = SpikeAlert(self.tmp.name, threshold=8, telegram_creds=("t", "c"), bark_key="", sender=self.sender, clock=lambda: now[0])
        self.touch(a, 8)
        self.assertEqual(len(a.snapshot()["active"]), 1)
        now[0] += 6*60*MIN+1
        self.assertEqual(a.snapshot()["active"], [])

    def test_test_alert_is_local_and_keeps_the_cooldown_free(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        a = self.alert()
        app = FastAPI(); install(app, a); client = TestClient(app)
        headers = {"origin": "http://testserver", "x-spike-action": "test-spike-alert"}
        self.assertEqual(client.post("/api/spike-alerts/test").status_code, 403)
        r = client.post("/api/spike-alerts/test", headers=headers).json()
        row = client.get("/api/spike-alerts").json()["active"][0]
        self.assertEqual((row["id"], row["test"], row["telegram"], row["bark"]), (r["id"], 1, "test", "test"))
        self.assertEqual((a.events.qsize(), self.sent), (0, []))
        self.touch(a, 8)
        self.assertEqual(a.events.qsize(), 1)


    def test_stream_pushes_on_connect_and_on_change_only(self):
        import asyncio
        a = self.alert()
        async def collect():
            out, gone = [], [False]
            async def disconnected():
                return gone[0]
            stream = a.event_stream(disconnected, heartbeat=.05)
            out.append(await stream.__anext__())          # snapshot on connect
            self.assertEqual(len(a.subscribers), 1)
            out.append(await stream.__anext__())          # nothing changed: heartbeat only
            self.touch(a, 8)                              # an alert arrives while the page is connected
            out.append(await stream.__anext__())
            gone[0] = True
            await stream.aclose()
            return out
        chunks = asyncio.run(collect())
        data = [json.loads(c.split("data: ", 1)[1]) for c in chunks if c.startswith("event: alerts")]
        self.assertEqual([len(d["active"]) for d in data], [0, 1])
        self.assertEqual(chunks[1], ": ping\n\n")
        self.assertEqual(a.subscribers, set())

if __name__ == "__main__":
    unittest.main()


class SpikeAlertLiveWindowTests(unittest.TestCase):
    """Owner 2026-11-05 alarm response desk: read-only window facts and a bounded push."""

    def setUp(self):
        import sqlite3
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.now = [T0]
        touches = sqlite3.connect(root/"monday-fib.sqlite3")
        touches.execute("CREATE TABLE touches (week TEXT, symbol TEXT, side TEXT, level REAL, price REAL, observed_ms INTEGER, candle_ms INTEGER, "
                        "source TEXT, generation INTEGER, status TEXT, due_ms INTEGER, error TEXT, message_id INTEGER, PRIMARY KEY(week,symbol,side))")
        rows = [("SOL-USDT-SWAP", "lower", 100., 99.9, T0+10, T0, "live"), ("ADA-USDT-SWAP", "lower", 1., .999, T0+MIN*20+5, T0+MIN*20, "live"),
                ("XRP-USDT-SWAP", "upper", 2., 2.01, T0+5, T0, "live"), ("DOT-USDT-SWAP", "lower", 5., 4.9, T0+5, T0, "history"),
                ("LINK-USDT-SWAP", "lower", 9., 8.9, T0+MIN*45, T0+MIN*45, "live")]
        touches.executemany("INSERT INTO touches (week,symbol,side,level,price,observed_ms,candle_ms,source) VALUES ('w',?,?,?,?,?,?,?)", rows)
        touches.commit(); touches.close()
        paper = sqlite3.connect(root/"monday-reclaim-paper.sqlite3")
        paper.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, track INTEGER, symbol TEXT, fill_ms INTEGER, taken INTEGER)")
        paper.executemany("INSERT INTO positions (track,symbol,fill_ms,taken) VALUES (?,?,?,?)",
                          [(8060, "SOL-USDT-SWAP", T0+MIN, 1), (8260, "SOL-USDT-SWAP", T0+MIN, 1),
                           (8060, "ADA-USDT-SWAP", T0+MIN*2, 0), (60, "SOL-USDT-SWAP", T0+MIN, 1),
                           (7060, "BTC-USDT-SWAP", T0-3600_000, 1)])
        paper.commit(); paper.close()
        self.a = SpikeAlert(self.tmp.name, threshold=2, telegram_creds=None, bark_key="", sender=lambda *a, **k: None, clock=lambda: self.now[0])
        self.a.telegram_creds = None

    def tearDown(self):
        self.tmp.cleanup()

    def quote(self, symbol, price, at=None):
        at = self.now[0] if at is None else at
        self.a.on_quote(symbol, {"price": price, "high": price, "low": price, "candle_ms": at, "received_ms": at})

    def test_alert_captures_reference_prices_and_serves_window_facts(self):
        self.quote("BTC-USDT-SWAP", 70000.); self.quote("ETH-USDT-SWAP", 3500.); self.quote("SOL-USDT-SWAP", 99.5)
        for k in range(2):
            self.a.on_touch({}, f"C{k}-USDT-SWAP", "lower", 1., T0, T0+MIN, "live")
        self.now[0] = T0 + 2*MIN
        self.quote("BTC-USDT-SWAP", 69300.)
        live = self.a.snapshot()["active"][0]["live"]
        self.assertEqual(live["reference"]["BTC-USDT-SWAP"], {"at": 70000., "now": 69300.})
        self.assertEqual(live["reference"]["ETH-USDT-SWAP"], {"at": 3500., "now": 3500.})
        got = {t["symbol"]: t for t in live["touches"]}
        self.assertEqual(set(got), {"SOL-USDT-SWAP", "ADA-USDT-SWAP"}, "same side, live source, inside the 30-minute window only")
        self.assertEqual((got["SOL-USDT-SWAP"]["level"], got["SOL-USDT-SWAP"]["price"], got["ADA-USDT-SWAP"]["price"]), (100., 99.5, None))
        self.assertEqual(live["window_end_ms"], T0 + MIN + 30*MIN)
        self.assertEqual({p["track"]: p["opened"] for p in live["paper"]}["live-wick6x3"], 1, "only taken live- positions in the window count")
        self.assertEqual({p["track"]: p["opened"] for p in live["paper"]}["live-wick6x3mid"], 1)
        self.assertNotIn("ref_prices", self.a.snapshot()["alerts"][0])

    def test_shown_alert_counts_every_coin_until_the_cooldown_ends(self):
        """Owner 2026-11-09: 25 is the trigger; the shown count and coins are complete (11-09 05:55 froze at 25 of 27)."""
        import sqlite3
        book = sqlite3.connect(self.a.touch_book)
        book.executemany("INSERT INTO touches (week,symbol,side,level,price,observed_ms,candle_ms,source) VALUES ('w',?,'lower',1,1,?,?,'live')",
                         [("C0-USDT-SWAP", T0+MIN+1, T0+MIN), ("C1-USDT-SWAP", T0+MIN+2, T0+MIN)])
        book.commit()
        for k in range(2):
            self.a.on_touch({}, f"C{k}-USDT-SWAP", "lower", 1., T0+MIN+1+k, T0+MIN, "live")
        book.execute("INSERT INTO touches (week,symbol,side,level,price,observed_ms,candle_ms,source) VALUES ('w','C9-USDT-SWAP','lower',1,1,?,?,'live')",
                     (T0+MIN+30, T0+MIN))  # same candle, arrived after the alert fired
        book.commit()
        self.now[0] = T0 + 25*MIN
        row = self.a.snapshot()["alerts"][0]
        self.assertEqual(row["trigger_n"], 2)
        self.assertEqual(row["symbols"], ["C0-USDT-SWAP", "C1-USDT-SWAP", "C9-USDT-SWAP", "ADA-USDT-SWAP"],
                         "touch order; SOL before the first touch, LINK after the cooldown, the upper side and history excluded")
        self.assertEqual((row["n"], row["complete"], row["follow_end_ms"]), (4, False, T0 + 31*MIN))
        self.assertIn("2 个币同时触及", row["text"], "the pushed text keeps the firing moment")
        self.assertEqual(self.a.snapshot()["active"][0]["n"], len(self.a.snapshot()["active"][0]["symbols"]))
        self.now[0] = T0 + 34*MIN
        self.assertTrue(self.a.snapshot()["alerts"][0]["complete"])
        book.execute("INSERT INTO touches (week,symbol,side,level,price,observed_ms,candle_ms,source) VALUES ('w2','LATE-USDT-SWAP','lower',1,1,?,?,'live')",
                     (T0+40*MIN, T0+20*MIN))
        book.commit(); book.close()
        self.assertEqual(self.a.snapshot()["alerts"][0]["n"], 4, "frozen once the window has closed")
        test = self.a.snapshot()["alerts"][0]
        self.a.test_alert()
        row = self.a.snapshot()["alerts"][0]
        self.assertEqual((row["test"], row["n"], row["trigger_n"], row["complete"]), (1, 2, 2, True))
        self.assertEqual(test["id"], self.a.snapshot()["alerts"][1]["id"])

    def test_stale_quotes_are_not_shown_as_current(self):
        self.quote("SOL-USDT-SWAP", 99.5, at=T0 - 10*MIN)
        for k in range(2):
            self.a.on_touch({}, f"C{k}-USDT-SWAP", "lower", 1., T0, T0+MIN, "live")
        live = self.a.snapshot()["active"][0]["live"]
        self.assertIsNone({t["symbol"]: t for t in live["touches"]}["SOL-USDT-SWAP"]["price"])
        self.assertEqual(live["reference"]["BTC-USDT-SWAP"], {"at": None, "now": None})

    def test_push_is_flag_driven_throttled_and_ends_after_ack(self):
        pushed = []
        self.a._changed = lambda: pushed.append(self.now[0])
        self.a.subscribers.add(("loop", "queue"))
        self.quote("SOL-USDT-SWAP", 1.)
        self.assertFalse(self.a.tick(), "no hot alert: quotes cost nothing")
        for k in range(2):
            self.a.on_touch({}, f"C{k}-USDT-SWAP", "lower", 1., T0, T0+MIN, "live")
        self.now[0] += 5000
        self.assertTrue(self.a.tick())
        self.assertFalse(self.a.tick(), "nothing changed since the last push")
        self.quote("BTC-USDT-SWAP", 70000.)
        self.now[0] += 1000
        self.assertFalse(self.a.tick(), "throttled to one push per PUSH_EVERY_MS")
        self.now[0] += 3000
        self.assertTrue(self.a.tick())
        alert_id = self.a.snapshot()["active"][0]["id"]
        self.a.acknowledge(alert_id)
        self.quote("BTC-USDT-SWAP", 69000.)
        self.now[0] += 5000
        self.assertFalse(self.a.tick(), "acknowledged: no more live pushes")
        self.now[0] = T0 + 2*3600_000
        self.a.on_touch({}, "Z-USDT-SWAP", "upper", 1., T0, self.now[0], "live")
        self.assertFalse(self.a.tick(), "a touch with no hot alert does not start pushes")

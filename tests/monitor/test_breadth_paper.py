"""Breadth paper account: band, zero followers, busy, six-day exit, SOL-only equity, one-shot notices."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from yoyo.monitor import breadth_paper as bp
from yoyo.monitor.breadth_paper import BreadthPaper

MIN, DAY = 60_000, 86_400_000
T0 = 1_792_000_000_000 // (5*MIN) * (5*MIN)  # a 5m bar open


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, prices):
        self.prices = prices  # (symbol, ms) -> open, or callable
        self.now = T0
        self.calls = 0

    def clock(self):
        return self.now

    def get(self, path, params):
        self.calls += 1
        after = int(params["after"])
        rows = []
        for ts in (after-MIN, after-2*MIN, after-3*MIN):
            o = self.prices(params["instId"], ts)
            if o is None:
                continue
            rows.append([str(ts), str(o), str(o*1.001), str(o*0.999), str(o), "1", "1", "1", "1"])
        return rows


class BreadthPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sent = []
        book = sqlite3.connect(str(self.dir/"monday-fib.sqlite3"))
        book.execute("""CREATE TABLE touches (week TEXT, symbol TEXT, side TEXT, level REAL, price REAL, observed_ms INTEGER,
                        candle_ms INTEGER, source TEXT, generation INTEGER, status TEXT, due_ms INTEGER, error TEXT,
                        message_id INTEGER, PRIMARY KEY(week,symbol,side))""")
        book.commit(); book.close()
        self.table = {}

    def tearDown(self):
        self.tmp.cleanup()

    def price(self, symbol, ms):
        return self.table.get((symbol, ms), {"SOL-USDT-SWAP": 100., "BTC-USDT-SWAP": 80000., "ETH-USDT-SWAP": 2500.}[symbol])

    def sender(self, url, json=None, **kwargs):
        self.sent.append((url, json))
        return FakeResponse(200, {"ok": True} if "telegram" in url else {"code": 200})

    def engine(self, client=None, **kw):
        self.client = client or FakeClient(self.price)
        return BreadthPaper(self.dir, self.client, telegram_creds=("t", "c"), bark_key="k"*22, sender=self.sender, **kw)

    def touch(self, n, at, start=0, side="lower", week="2026-10-26"):
        book = sqlite3.connect(str(self.dir/"monday-fib.sqlite3"))
        for k in range(start, start+n):
            book.execute("INSERT INTO touches (week,symbol,side,candle_ms,source) VALUES (?,?,?,?, 'live')",
                         (week, f"C{k}-USDT-SWAP", side, at+(k % 5)*MIN))
        book.commit(); book.close()

    def run_to(self, e, ms):
        self.client.now = ms
        e.step(ms)

    def snapshot(self, e):
        return e.snapshot()

    def test_band_edges_and_upper_side_are_ignored(self):
        e = self.engine()
        self.touch(30, T0+5*MIN)                     # 30 coins: below band
        self.touch(63, T0+10*MIN, start=100)         # 63 coins: above band
        self.touch(40, T0+15*MIN, start=300, side="upper")
        self.run_to(e, T0+DAY)
        self.assertEqual(self.snapshot(e)["signals"], [])

    def test_signal_with_zero_followers_opens_sol_and_shadow_legs(self):
        e = self.engine()
        bar = T0+5*MIN
        self.touch(31, bar)
        self.run_to(e, bar+5*MIN+MIN)                # bar closed: watched
        s = self.snapshot(e)["signals"]
        self.assertEqual((len(s), s[0]["status"], s[0]["n"]), (1, "watching", 31))
        decision = bar+5*MIN+30*MIN
        self.run_to(e, decision+bp.GRACE_MS)
        snap = self.snapshot(e)
        self.assertEqual(snap["signals"][0]["status"], "taken")
        legs = {p["symbol"]: p for p in snap["positions"]}
        self.assertEqual(set(legs), set(bp.LEGS))
        self.assertEqual((legs["SOL-USDT-SWAP"]["funded"], legs["BTC-USDT-SWAP"]["funded"]), (1, 0))
        self.assertEqual(legs["SOL-USDT-SWAP"]["entry_ms"], decision)
        self.assertEqual(legs["SOL-USDT-SWAP"]["exit_due_ms"], decision+6*DAY)
        self.assertEqual(len([u for u, _ in self.sent if "telegram" in u]), 1)

    def test_a_follower_inside_thirty_minutes_cancels(self):
        e = self.engine()
        bar = T0+5*MIN
        self.touch(40, bar)
        self.touch(1, bar+25*MIN, start=500)         # one new coin in the observation window
        self.run_to(e, bar+5*MIN+30*MIN+bp.GRACE_MS)
        s = self.snapshot(e)
        self.assertEqual((s["signals"][0]["status"], s["signals"][0]["new_n"], s["positions"]), ("followers", 1, []))
        self.assertEqual(self.sent, [])

    def test_six_day_exit_moves_equity_only_for_sol_and_notifies_once(self):
        e = self.engine()
        bar = T0+5*MIN
        decision = bar+35*MIN
        self.table[("SOL-USDT-SWAP", decision+6*DAY)] = 110.
        self.table[("BTC-USDT-SWAP", decision+6*DAY)] = 72000.
        self.touch(50, bar)
        self.run_to(e, decision+bp.GRACE_MS)
        self.run_to(e, decision+6*DAY+bp.GRACE_MS)
        snap = self.snapshot(e)
        legs = {p["symbol"]: p for p in snap["positions"]}
        self.assertAlmostEqual(legs["SOL-USDT-SWAP"]["net_bp"], 1000-10)
        self.assertAlmostEqual(legs["BTC-USDT-SWAP"]["net_bp"], -1000-10)
        self.assertAlmostEqual(snap["account"]["equity"], 1000*(1+0.099))
        self.assertEqual(snap["signals"][0]["status"], "closed")
        self.run_to(e, decision+7*DAY)
        tg = [j["text"] for u, j in self.sent if "telegram" in u]
        self.assertEqual(len(tg), 2)
        self.assertIn("平仓", tg[1])

    def test_a_second_signal_while_sol_is_held_is_busy(self):
        e = self.engine()
        first = T0+5*MIN
        self.touch(35, first)
        self.run_to(e, first+35*MIN+bp.GRACE_MS)
        second = first+2*DAY
        self.touch(35, second, start=1000, week="2026-11-02")
        self.run_to(e, second+35*MIN+bp.GRACE_MS)
        statuses = {s["bar_ms"]: s["status"] for s in self.snapshot(e)["signals"]}
        self.assertEqual((statuses[first], statuses[second]), ("taken", "busy"))

    def test_bars_before_start_are_ignored(self):
        self.touch(40, T0-60*MIN)
        e = self.engine()
        self.run_to(e, T0+DAY)
        self.assertEqual(self.snapshot(e)["signals"], [])

    def test_missing_entry_price_retries_then_gives_up(self):
        e = self.engine(client=FakeClient(lambda s, ms: None))
        bar = T0+5*MIN
        self.touch(40, bar)
        self.run_to(e, bar+35*MIN+bp.GRACE_MS)
        self.assertEqual(self.snapshot(e)["signals"][0]["status"], "entering")
        self.run_to(e, bar+35*MIN+bp.PRICE_GIVEUP_MS+MIN)
        self.assertEqual(self.snapshot(e)["signals"][0]["status"], "price_missing")
        self.assertEqual(self.snapshot(e)["positions"], [])

    def test_restart_never_resends_an_interrupted_notice(self):
        e = self.engine()
        with e.db() as db:
            db.execute("INSERT INTO notices (signal_id,kind,text,telegram,bark,created_ms) VALUES (1,'entry','x','attempting','pending',0)")
        e2 = self.engine()
        e2.deliver()
        n = self.snapshot(e2)["notices"][0]
        self.assertEqual((n["telegram"], n["bark"]), ("unknown", "sent"))
        self.assertEqual(len([u for u, _ in self.sent if "telegram" in u]), 0)


if __name__ == "__main__":
    unittest.main()


class BreadthOnPaperPageTests(unittest.TestCase):
    """The paper page reads live-breadth through /api/monday-reclaim-paper (Stitch 2762455ffefe4f5a9784c2e2e0fd4db5)."""

    def setUp(self):
        import os
        from fastapi.testclient import TestClient
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FABLE_IMPULSE_RUNTIME"] = self.tmp.name
        from yoyo.monitor.server import create_app
        self.app = create_app(start_monitor=False)
        self.client = TestClient(self.app)
        e = self.app.state.breadth_paper
        now = e.clock()
        with e.db() as db:
            db.execute("INSERT INTO signals (id,bar_ms,n,symbols,decision_ms,new_n,new_symbols,status,created_ms,evaluated_ms) "
                       "VALUES (1,?,36,'[]',?,0,'[]','taken',?,?)", (now-3*DAY, now-3*DAY+35*MIN, now-3*DAY, now-3*DAY+36*MIN))
            for sym, funded in (("SOL-USDT-SWAP", 1), ("BTC-USDT-SWAP", 0), ("ETH-USDT-SWAP", 0)):
                db.execute("INSERT INTO positions (signal_id,symbol,funded,notional,entry_ms,entry,exit_due_ms,stage) VALUES (1,?,?,1000,?,100,?,'open')",
                           (sym, funded, now-3*DAY+35*MIN, now+3*DAY))
            db.execute("INSERT INTO positions (signal_id,symbol,funded,notional,entry_ms,entry,exit_due_ms,exit_ms,exit,gross_bp,fee_bp,net_bp,pnl,stage) "
                       "VALUES (9,'SOL-USDT-SWAP',1,1000,?,100,?,?,104,400,10,390,39,'closed')", (now-20*DAY, now-14*DAY, now-14*DAY))

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_lists_the_account_like_the_other_tracks(self):
        d = self.client.get("/api/monday-reclaim-paper?list_period=today&track=live-breadth").json()
        t = d["tracks"]["live-breadth"]
        legs = {p["symbol"]: p for p in t["open"]}
        self.assertEqual((legs["SOL-USDT-SWAP"]["taken"], legs["BTC-USDT-SWAP"]["taken"], legs["BTC-USDT-SWAP"]["note"]), (1, 0, "comparison"))
        self.assertTrue(all(p["track"] == bp.TRACK_ID and p["stage"] == "holding" and p["remaining_notional"] is None for p in t["open"]))
        self.assertEqual((t["stats"]["taken_closed"], t["stats"]["wins"], t["equity"]), (1, 1, 1000.0))
        self.assertEqual(d["live-breadth"]["signals"][0]["status"], "taken")
        self.assertTrue(d["live-breadth"]["signals"][0]["breadth"])
        self.assertTrue(d["tracks"]["spike"].get("detail_omitted"))

    def test_analytics_and_history_route_to_the_breadth_ledger(self):
        a = self.client.get("/api/monday-reclaim-paper/analytics?track=live-breadth&days=0").json()
        self.assertEqual(a["track"], str(bp.TRACK_ID))
        h = self.client.get("/api/monday-reclaim-paper/history?track=live-breadth&tab=closed&scope=funded").json()
        self.assertEqual((h["total"], h["rows"][0]["reason"]), (1, "time_6d"))
        h = self.client.get("/api/monday-reclaim-paper/history?track=live-breadth&tab=events").json()
        self.assertEqual(h["total"], 1)

    def test_backtest_list_reads_the_thirteen_saved_cases(self):
        p = self.client.get("/api/strategy-backtest?track=live-breadth").json()
        self.assertEqual((p["total"], p["rows"][0]["symbol"]), (13, "SOLUSDT"))

"""Paper engine for the Monday 1.618 limit-reclaim v2 rule: fills, confirmation, exits, score gate."""
import json
import math
import tempfile
import threading
import unittest
from pathlib import Path

from yoyo.monitor.monday_fib import FibBook, week_window
from yoyo.monitor.monday_reclaim_paper import MODEL, ReclaimPaper, features, settle, bars

ROOT = Path(__file__).resolve().parents[2]
MIN, H5, H1 = 60_000, 300_000, 3_600_000


class FakeClient:
    """OKX-shaped candles: flat 105 unless overridden per (bar, open time)."""

    def __init__(self, now):
        self.now = now
        self.override = {}

    def clock(self):
        return self.now

    def get(self, path, params):
        step = {"5m": H5, "1H": H1, "1m": MIN}[params["bar"]]
        last = self.now//step*step
        rows = []
        for k in range(int(params["limit"])):
            ts = last-k*step
            o, h, l, c = self.override.get((params["bar"], ts), (105., 105., 105., 105.))
            rows.append([str(ts), str(o), str(h), str(l), str(c), "1", "1", "1000", "1" if ts+step <= self.now else "0"])
        return rows


class FakeFib:
    def __init__(self, runtime, client):
        self.client, self.book, self.lock, self.quotes = client, FibBook(runtime), threading.Lock(), {}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.win = week_window(1_792_000_000_000)
        self.levels = {**self.win, "high": 110., "low": 100., "upper": 116.18, "lower": 93.82}
        self.touch = self.win["end"]+30*H1+7*MIN
        self.client = FakeClient(self.touch+2000)
        self.client.override[("5m", self.touch//H5*H5-30*MIN)] = (105., 105., 99.9, 104.)
        self.client.override[("1H", self.win["end"]-H1)] = (105., 109., 104., 108.)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, threshold):
        spec = json.loads(MODEL.read_text()); spec["top40_threshold"] = threshold
        path = Path(self.tmp.name)/"model.json"; path.write_text(json.dumps(spec))
        return ReclaimPaper(self.tmp.name, FakeFib(self.tmp.name, self.client), model_path=path)

    def rows(self, e, sql):
        with e.db() as db:
            return [dict(r) for r in db.execute(sql)]

    def confirm(self, e, close):
        for m, bar, step in ((60, "1H", H1), (5, "5m", H5)):
            decision = self.touch//step*step+2*step
            self.client.override[(bar, decision-step)] = (94., max(94., close), min(94., close), close)
        self.client.now = self.touch//H1*H1+2*H1+5000
        e.decide(self.client.now)

    def test_reclaim_then_target(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        pos = self.rows(e, "SELECT * FROM positions ORDER BY track")
        self.assertEqual([p["track"] for p in pos], [5, 60]); self.assertTrue(all(p["taken"] == 1 and p["notional"] == 100 for p in pos))
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 95., "low": 93.9, "price": 94.5})
        self.confirm(e, 94.5)
        self.assertEqual({p["stage"] for p in self.rows(e, "SELECT * FROM positions")}, {"breakeven"})
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 100.5, "low": 98., "price": 99.})
        pos = self.rows(e, "SELECT * FROM positions")
        self.assertEqual({p["reason"] for p in pos}, {"target"})
        net = settle(1, 93.82, 100., "target")[2]
        eq = self.rows(e, "SELECT * FROM tracks")
        self.assertTrue(all(abs(t["equity"]-(1000+100*net/1e4)) < 1e-9 for t in eq))

    def test_failed_reclaim_exits_at_close(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        self.confirm(e, 93.0)
        pos = self.rows(e, "SELECT * FROM positions")
        self.assertEqual({(p["reason"], p["exit_price"]) for p in pos}, {("failed_reclaim", 93.0)})
        self.assertTrue(all(p["fee_bp"] == 7 and p["net_slip_bp"] == p["net_bp"]-10 for p in pos))

    def test_breakeven_stop_beats_target_in_same_minute(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 101., "low": 93.5, "price": 96.})
        self.assertEqual({(p["reason"], p["exit_price"]) for p in self.rows(e, "SELECT * FROM positions")}, {("breakeven", 93.82)})

    def test_fill_minute_target_needs_close_beyond(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch, "high": 100.2, "low": 93.7, "price": 99.})
        self.assertEqual({p["stage"] for p in self.rows(e, "SELECT * FROM positions")}, {"pending_confirm"})
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch, "high": 100.2, "low": 93.7, "price": 100.1})
        self.assertEqual({p["reason"] for p in self.rows(e, "SELECT * FROM positions")}, {"target"})

    def test_low_score_is_shadow_only(self):
        e = self.engine(2.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        self.assertEqual({p["taken"] for p in self.rows(e, "SELECT * FROM positions")}, {0})
        self.confirm(e, 93.0)
        self.assertTrue(all(t["equity"] == 1000 for t in self.rows(e, "SELECT * FROM tracks")))
        self.assertEqual(self.rows(e, "SELECT status, taken FROM signals"), [{"status": "scored", "taken": 0}])

    def test_majors_are_excluded(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "BTC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        self.assertEqual(self.rows(e, "SELECT COUNT(*) n FROM positions"), [{"n": 0}])

    def test_features_follow_backtest_definitions(self):
        now = self.client.now
        five = bars(self.client.get("/", {"bar": "5m", "limit": "300"}), "5m", now)
        hourly = bars(self.client.get("/", {"bar": "1H", "limit": "300"}), "1H", now)
        f = features(five, hourly, 1, self.levels, self.touch, True, 3)
        self.assertAlmostEqual(f["log_approach"], math.log1p(30))
        self.assertAlmostEqual(f["monday_close_side"], .2)
        self.assertAlmostEqual(f["toward24"], 0.)
        self.assertAlmostEqual(f["log_vol_ratio"], math.log(12))
        self.assertEqual((f["opp_first"], f["cluster24"], f["side"]), (1, 3., 1.))

    def test_frozen_model_matches_experiment(self):
        exp = ROOT/"experiments/active/exp-monday-limit-reclaim-recent-20261025-v1/score_model.json"
        self.assertEqual(json.loads(MODEL.read_text()), json.loads(exp.read_text()))


if __name__ == "__main__":
    unittest.main()

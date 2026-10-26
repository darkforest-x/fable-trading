"""Paper engine for the Monday 1.618 limit-reclaim v2 rule: fills, confirmation, exits, score gate."""
import json
import math
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from yoyo.monitor.monday_fib import FibBook, week_window
from yoyo.monitor.monday_reclaim_paper import MODEL, V3, V3_TRACK, ReclaimPaper, PaperCloseError, features, settle, bars, install

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
        self.connected = True


class PaperFixture(unittest.TestCase):
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

    def engine(self, threshold, enable_v3=False):
        spec = json.loads(MODEL.read_text()); spec["top40_threshold"] = threshold
        path = Path(self.tmp.name)/"model.json"; path.write_text(json.dumps(spec))
        return ReclaimPaper(self.tmp.name, FakeFib(self.tmp.name, self.client), model_path=path, enable_v3=enable_v3)

    def rows(self, e, sql):
        with e.db() as db:
            return [dict(r) for r in db.execute(sql)]

    def confirm(self, e, close):
        for m, bar, step in ((60, "1H", H1), (5, "5m", H5)):
            decision = self.touch//step*step+2*step
            self.client.override[(bar, decision-step)] = (94., max(94., close), min(94., close), close)
        self.client.now = self.touch//H1*H1+2*H1+5000
        e.decide(self.client.now)


class PaperTests(PaperFixture):
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

    def test_latest_funded_fill_survives_the_exit_ordered_history_cap(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        with e.db() as db:
            db.execute("UPDATE positions SET stage='closed', exit_ms=?, net_bp=1 WHERE track=60", (self.touch+1000,))
            for i in range(101):
                db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,stage,taken,notional,exit_ms,net_bp) "
                           "VALUES (?,60,'SHADOW-USDT-SWAP',-1,120,110,?,'closed',0,0,?,1)",
                           (1000+i, self.touch+2000+i, self.touch+3000+i))
        data = e.snapshot()
        self.assertEqual(len(data['tracks']['60']['closed']), 100)
        self.assertTrue(all(r['taken'] == 0 for r in data['tracks']['60']['closed']))
        self.assertEqual(data['tracks']['60']['latest_funded']['symbol'], 'ABC-USDT-SWAP')
        self.assertEqual(data['tracks']['5']['latest_funded']['stage'], 'pending_confirm')

    def manual_fixture(self, side='lower'):
        e = self.engine(-1.)
        if side == 'upper':
            self.client.override[("5m", self.touch//H5*H5-30*MIN)] = (105., 111., 104., 106.)
        e.handle_touch(self.levels, 'ABC-USDT-SWAP', side, 94., self.touch+500, self.touch)
        price = 94.5 if side == 'lower' else 115.
        e.fib.quotes['ABC-USDT-SWAP'] = dict(price=price, high=price, low=price,
            candle_ms=self.touch, received_ms=self.client.now)
        p = self.rows(e, 'SELECT * FROM positions WHERE track=60')[0]
        return e, p

    def test_partial_then_full_preserves_original_trade_and_total_cost(self):
        for side in ['lower', 'upper']:
            with self.subTest(side=side):
                if side == 'upper':
                    self.tmp.cleanup(); self.tmp = tempfile.TemporaryDirectory()
                e, p = self.manual_fixture(side)
                a = e.close_position(p['id'], 25, 0, str(uuid.uuid4()))
                self.assertEqual(a['remaining_notional'], 75)
                partial = e.snapshot()['tracks']['60']['open'][0]
                self.assertEqual(partial['notional'], 100)
                self.assertEqual(partial['remaining_notional'], 75)
                self.assertEqual(partial['stage'], 'pending_confirm')
                self.assertEqual(len(partial['exits']), 1)
                b = e.close_position(p['id'], 50, 1, str(uuid.uuid4()))
                self.assertEqual(b['closed_notional'], 37.5)
                c = e.close_position(p['id'], 100, 2, str(uuid.uuid4()))
                self.assertEqual(c['remaining_notional'], 0)
                track = e.snapshot()['tracks']['60']; closed = track['closed'][0]
                self.assertEqual(closed['reason'], 'manual')
                self.assertAlmostEqual(closed['fee_bp'], 7)
                self.assertAlmostEqual(closed['pnl'], sum(x['pnl'] for x in [a,b,c]))
                self.assertAlmostEqual(track['equity'], 1000+closed['pnl'])
                self.assertEqual(track['stats']['taken_closed'], 1)
                self.assertEqual(e.snapshot()['tracks']['5']['equity'], 1000)

    def test_partial_then_automatic_target_settles_only_remaining_and_keeps_weighted_stats(self):
        e, p = self.manual_fixture()
        first = e.close_position(p['id'], 25, 0, str(uuid.uuid4()))
        e.handle_quote(p['symbol'], dict(candle_ms=self.touch+MIN, price=100.5, high=100.5, low=99.))
        track = e.snapshot()['tracks']['60']; closed = track['closed'][0]
        expected = first['pnl']+75*settle(1, p['entry'], p['target'], 'target')[2]/1e4
        self.assertAlmostEqual(closed['pnl'], expected)
        self.assertAlmostEqual(closed['fee_bp'], 7*.25+4*.75)
        self.assertAlmostEqual(track['equity'], 1000+expected)
        e.handle_quote(p['symbol'], dict(candle_ms=self.touch+2*MIN, price=101, high=101, low=99))
        self.assertAlmostEqual(e.snapshot()['tracks']['60']['equity'], track['equity'])

    def test_duplicate_requests_are_idempotent_across_threads_and_restarts(self):
        e, p = self.manual_fixture(); key = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: e.close_position(p['id'], 25, 0, key), range(2)))
        self.assertEqual(results[0], results[1])
        restarted = self.engine(-1.)
        self.assertEqual(restarted.close_position(p['id'], 25, 0, key), results[0])
        self.assertEqual(len(self.rows(e, 'SELECT * FROM exits')), 1)
        with self.assertRaises(PaperCloseError): e.close_position(p['id'], 50, 0, key)
        with self.assertRaises(PaperCloseError): e.close_position(p['id'], 25, 0, str(uuid.uuid4()))

    def test_invalid_stale_and_shadow_closes_leave_account_unchanged(self):
        e, p = self.manual_fixture()
        for pct in [0, -1, 101, float('nan'), True, '25']:
            with self.assertRaises(PaperCloseError): e.close_position(p['id'], pct, 0, str(uuid.uuid4()))
        e.fib.quotes[p['symbol']]['received_ms'] -= 16000
        with self.assertRaises(PaperCloseError): e.close_position(p['id'], 100, 0, str(uuid.uuid4()))
        e.fib.quotes[p['symbol']]['received_ms'] = self.client.now
        e.fib.connected = False
        with self.assertRaises(PaperCloseError): e.close_position(p['id'], 100, 0, str(uuid.uuid4()))
        with e.db() as db: db.execute('UPDATE positions SET taken=0 WHERE id=?', (p['id'],))
        e.fib.connected = True
        with self.assertRaises(PaperCloseError): e.close_position(p['id'], 100, 0, str(uuid.uuid4()))
        self.assertEqual(e.snapshot()['tracks']['60']['equity'], 1000)
        self.assertEqual(self.rows(e, 'SELECT COUNT(*) n FROM exits')[0]['n'], 0)

    def test_automatic_exit_wins_and_old_quotes_do_not_backdate_manual_remainder(self):
        e, p = self.manual_fixture()
        e.close_position(p['id'], 25, 0, str(uuid.uuid4()))
        e.handle_quote(p['symbol'], dict(price=101, high=101, low=99, candle_ms=self.touch,
                                       received_ms=self.client.now-1))
        self.assertEqual(e.snapshot()['tracks']['60']['open'][0]['remaining_notional'], 75)
        e.fib.quotes[p['symbol']].update(price=101, high=101, low=99)
        with self.assertRaises(PaperCloseError): e.close_position(p['id'], 100, 1, str(uuid.uuid4()))
        self.assertEqual(e.snapshot()['tracks']['60']['closed'][0]['reason'], 'target')
        self.assertGreaterEqual(e.snapshot()['tracks']['60']['closed'][0]['exit_ms'], self.client.now)

    def test_due_unresolved_confirmation_blocks_a_later_manual_fill(self):
        e, p = self.manual_fixture()
        self.client.now = p['decision_ms']+5000
        self.client.get = lambda *args: []
        e.fib.quotes[p['symbol']].update(candle_ms=self.client.now//MIN*MIN, received_ms=self.client.now)
        with self.assertRaisesRegex(PaperCloseError, '确认结算'):
            e.close_position(p['id'], 25, 0, str(uuid.uuid4()))
        self.assertEqual(e.snapshot()['tracks']['60']['equity'], 1000)

    def test_analytics_http_is_read_only_and_rejects_unknown_track_or_period(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        app = FastAPI(); install(app, e)
        before = self.rows(e, "SELECT * FROM positions")
        account = self.rows(e, "SELECT * FROM tracks")
        with TestClient(app) as client:
            for track in [60, 5]:
                for days in [0, 7, 30]:
                    r = client.get(f"/api/monday-reclaim-paper/analytics?track={track}&days={days}")
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.json()["track"], str(track))
                    self.assertEqual(r.json()["summary"]["open_count"], 1)
            for query in ["track=15", "days=-1", "days=365", "track=abc"]:
                self.assertEqual(client.get("/api/monday-reclaim-paper/analytics?"+query).status_code, 422)
        self.assertEqual(self.rows(e, "SELECT * FROM positions"), before)
        self.assertEqual(self.rows(e, "SELECT * FROM tracks"), account)

    def test_close_http_requires_same_origin_action_and_valid_body(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e, p = self.manual_fixture(); app = FastAPI(); install(app, e)
        client = TestClient(app)
        url = f'/api/monday-reclaim-paper/positions/{p["id"]}/close'
        body = dict(percentage=25, expected_revision=0, request_id=str(uuid.uuid4()))
        self.assertEqual(client.post(url, json=body).status_code, 403)
        headers = {'origin':'http://testserver', 'x-spike-action':'close-reclaim-paper'}
        self.assertEqual(client.post(url, json=body, headers={**headers,'origin':'https://evil.invalid'}).status_code, 403)
        self.assertEqual(client.post(url, json={**body,'percentage':True}, headers=headers).status_code, 422)
        response = client.post(url, json=body, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['remaining_notional'], 75)
        self.assertEqual(client.post(url, json=body, headers=headers).json(), response.json())

    def test_partial_releases_capacity_using_remaining_not_original_notional(self):
        e, p = self.manual_fixture()
        for i in range(9):
            e.handle_touch(self.levels, f'ALT{i}-USDT-SWAP', 'lower', 94., self.touch+500, self.touch)
        result = e.close_position(p['id'], 25, 0, str(uuid.uuid4()))
        e.handle_touch(self.levels, 'NEW-USDT-SWAP', 'lower', 94., self.touch+500, self.touch)
        allocated = self.rows(e, "SELECT notional FROM positions WHERE symbol='NEW-USDT-SWAP' AND track=60")[0]['notional']
        self.assertAlmostEqual(allocated, 25+result['pnl'])

    def test_automatic_and_manual_writers_cannot_realize_more_than_original_position(self):
        e, p = self.manual_fixture()
        def manual():
            try: return e.close_position(p['id'], 50, 0, str(uuid.uuid4()))
            except PaperCloseError: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(manual)
            b = pool.submit(e.handle_quote, p['symbol'], dict(candle_ms=self.touch+MIN, price=101, high=101, low=99))
            a.result(); b.result()
        exits = self.rows(e, f'SELECT * FROM exits WHERE position_id={p["id"]}')
        self.assertAlmostEqual(sum(x['notional'] for x in exits), 100)
        self.assertAlmostEqual(e.snapshot()['tracks']['60']['equity'], 1000+sum(x['pnl'] for x in exits))


class V3TrackTests(PaperFixture):
    """v3: score-dependent target, -30 percent backstop, linear notional, isolated equity."""

    def v3(self, score):
        e = self.engine(-1., enable_v3=True)
        e.model.score = lambda feats: score
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        return e, self.rows(e, f"SELECT * FROM positions WHERE track={V3_TRACK}")[0]

    def test_far_target_and_linear_notional_from_score_0_6(self):
        e, p = self.v3(.7)
        sz = json.loads(V3.read_text())["sizing"]
        self.assertEqual((p["taken"], p["target"]), (1, 116.18))
        self.assertAlmostEqual(p["notional"], 1000*min(sz["fraction"]*(.7-sz["cut"])/sz["mean_excess"], sz["cap"]))
        self.assertAlmostEqual(p["backstop"], 93.82*.7)
        self.assertEqual(set(e.snapshot()["tracks"]), {"60", "5", "v3"})

    def test_range_0618_target_between_0_5_and_0_6(self):
        _, p = self.v3(.55)
        self.assertEqual(p["taken"], 1); self.assertAlmostEqual(p["target"], 106.18)

    def test_below_0_5_is_a_near_target_shadow(self):
        e, p = self.v3(.45)
        self.assertEqual((p["taken"], p["notional"], p["target"]), (0, 0., 100.))
        self.confirm(e, 93.0)
        self.assertEqual(e.snapshot()["tracks"]["v3"]["equity"], 1000)

    def test_backstop_exits_before_confirmation_at_taker(self):
        e, p = self.v3(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 60., "price": 62.})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={V3_TRACK}")[0]
        self.assertEqual((c["reason"], c["exit_price"]), ("backstop", p["backstop"]))
        self.assertEqual(c["fee_bp"], 7)
        net = settle(1, 93.82, p["backstop"], "backstop")[2]
        self.assertAlmostEqual(e.snapshot()["tracks"]["v3"]["equity"], 1000+p["notional"]*net/1e4)
        self.assertEqual(e.snapshot()["tracks"]["60"]["equity"], 1000)

    def test_backstop_counts_in_the_fill_minute_and_beats_the_target(self):
        e, p = self.v3(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch, "high": 120., "low": 60., "price": 118.})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={V3_TRACK}")[0]
        self.assertEqual(c["reason"], "backstop")

    def test_reclaim_replaces_backstop_with_entry_stop(self):
        e, p = self.v3(.7)
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 96., "low": 93.5, "price": 95.})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={V3_TRACK}")[0]
        self.assertEqual((c["reason"], c["exit_price"]), ("breakeven", 93.82))

    def test_far_target_after_reclaim(self):
        e, p = self.v3(.7)
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 117., "low": 95., "price": 116.5})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={V3_TRACK}")[0]
        self.assertEqual((c["reason"], c["exit_price"]), ("target", 116.18))

    def test_analytics_and_api_accept_v3(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e, _ = self.v3(.7)
        app = FastAPI(); install(app, e)
        r = TestClient(app).get("/api/monday-reclaim-paper/analytics?track=v3")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

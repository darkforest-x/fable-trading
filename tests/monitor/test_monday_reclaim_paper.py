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
from yoyo.monitor.monday_reclaim_paper import (MODEL, V3, V3_TRACK, V3ADD_TRACK, V3ADD_LEG, BE_OFFSET_BP, SPIKE_TRACK, V3LOCK_TRACK, ALLIN3_TRACK, V3ADD20_TRACK, V3ADD20_LEG, ReclaimPaper,
                                               PaperCloseError, features, settle, bars, install, ema_order)

ROOT = Path(__file__).resolve().parents[2]
MIN, H5, H1, M15 = 60_000, 300_000, 3_600_000, 900_000
BE = 93.82*(1+BE_OFFSET_BP/1e4)  # breakeven stop after a reclaim, entry +0.17 percent


class FakeClient:
    """OKX-shaped candles: flat 105 unless overridden per (bar, open time)."""

    def __init__(self, now):
        self.now = now
        self.override = {}

    def clock(self):
        return self.now

    def get(self, path, params):
        step = {"5m": H5, "1H": H1, "1m": MIN, "15m": M15}[params["bar"]]
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

    def engine(self, threshold, enable_v3=False, enable_v3add=False, enable_spike=False, enable_v3lock=False,
               enable_allin3=False, enable_v3add20=False):
        spec = json.loads(MODEL.read_text()); spec["top40_threshold"] = threshold
        path = Path(self.tmp.name)/"model.json"; path.write_text(json.dumps(spec))
        return ReclaimPaper(self.tmp.name, FakeFib(self.tmp.name, self.client), model_path=path, enable_v3=enable_v3,
                            enable_v3add=enable_v3add, enable_spike=enable_spike, enable_v3lock=enable_v3lock,
                            enable_allin3=enable_allin3, enable_v3add20=enable_v3add20)

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
        self.assertEqual({(p["reason"], p["exit_price"]) for p in self.rows(e, "SELECT * FROM positions")}, {("breakeven", BE)})

    def test_reclaim_close_inside_the_fee_band_exits_at_the_close(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        self.confirm(e, 93.9)
        pos = self.rows(e, "SELECT * FROM positions")
        self.assertEqual({(p["reason"], p["exit_price"], p["fee_bp"]) for p in pos}, {("reclaimed_inside_be", 93.9, 7)})

    def test_breakeven_armed_before_the_offset_keeps_the_entry_stop(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        self.confirm(e, 94.5)
        with e.db() as db:
            db.execute("UPDATE positions SET be_price=NULL")
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 96., "low": 93.9, "price": 95.})
        self.assertEqual({p["stage"] for p in self.rows(e, "SELECT * FROM positions")}, {"breakeven"})
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1+MIN, "high": 96., "low": 93.5, "price": 95.})
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

    def test_paged_snapshot_retains_complete_stats_and_only_returned_exit_legs(self):
        e = self.engine(-1.)
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        with e.db() as db:
            for i in range(160):
                row = db.execute("INSERT INTO positions (signal_id,track,symbol,side,entry,target,fill_ms,stage,taken,notional,exit_ms,net_bp) "
                    "VALUES (?,60,'OLD-USDT-SWAP',1,100,110,?,'closed',1,100,?,?)",
                    (1000+i, self.touch+10000-i, self.touch+i, 2 if i % 2 else -1))
                db.execute("INSERT INTO exits (position_id,notional,exit_ms,price,reason,net_bp) VALUES (?,100,?,101,'target',?)",
                           (row.lastrowid, self.touch+i, 2 if i % 2 else -1))
        value = e.snapshot()['tracks']['60']
        self.assertEqual(len(value['closed']), 100)
        self.assertEqual(value['stats'], dict(taken_closed=160, wins=80, mean_net_bp=.5,
                                            shadow_closed=160, shadow_mean_net_bp=.5))
        self.assertEqual(value['latest_funded']['fill_ms'], self.touch+10000)
        self.assertNotIn(value['latest_funded']['id'], {r['id'] for r in value['closed']})
        for row in value['closed'] + [value['latest_funded']]:
            self.assertEqual([leg['position_id'] for leg in row['exits']], [row['id']])

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
                self.assertEqual(closed['score'], partial['score'])
                self.assertIsInstance(closed['score'], float)
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


class NotificationScoreTests(PaperFixture):
    def event(self, side="lower", symbol="ABC-USDT-SWAP", week=None, candle_ms=None, observed_ms=None):
        return dict(week=week or self.win["week"], symbol=symbol, side=side,
                    candle_ms=self.touch if candle_ms is None else candle_ms,
                    observed_ms=self.touch+500 if observed_ms is None else observed_ms)

    def insert_signal(self, e, event, side_value, score, status="scored"):
        with e.db() as db:
            db.execute("INSERT INTO signals (week,symbol,side,candle_ms,observed_ms,status,score) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (event["week"], event["symbol"], side_value, event["candle_ms"],
                        event["observed_ms"], status, score))

    def ledger_snapshot(self, e):
        tables = ("signals", "positions", "exits", "manual_requests", "tracks", "refills")
        with e.db() as db:
            return {table: tuple(tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                    for table in tables}

    def test_reads_exact_raw_score_without_mutating_paper_ledger(self):
        e = self.engine(2.)
        e.model.score = lambda _features: .400001
        event = self.event()
        e.handle_touch(self.levels, event["symbol"], event["side"], 93.8,
                       event["observed_ms"], event["candle_ms"])
        before = self.ledger_snapshot(e)

        self.assertEqual(e.notification_score(event), {"status": "scored", "score": .400001})
        self.assertEqual(self.ledger_snapshot(e), before)
        self.assertEqual(self.rows(e, "SELECT DISTINCT taken FROM signals"), [{"taken": 0}])

    def test_week_and_direction_do_not_reuse_another_score(self):
        e = self.engine(2.)
        lower = self.event("lower")
        upper = self.event("upper", candle_ms=self.touch+H5, observed_ms=self.touch+H5+500)
        previous_week = self.event("lower", week=week_window(self.win["start"]-1)["week"])
        self.insert_signal(e, lower, 1, .71)
        self.insert_signal(e, upper, -1, .32)
        self.insert_signal(e, previous_week, 1, .99)

        self.assertEqual(e.notification_score(lower), {"status": "scored", "score": .71})
        self.assertEqual(e.notification_score(upper), {"status": "scored", "score": .32})
        self.assertEqual(e.notification_score(previous_week), {"status": "scored", "score": .99})

    def test_timestamp_mismatch_is_terminal(self):
        e = self.engine(2.)
        exact = self.event()
        self.insert_signal(e, exact, 1, .8)
        for changed in ({"candle_ms": exact["candle_ms"]+H5},
                        {"observed_ms": exact["observed_ms"]+1}):
            candidate = {**exact, **changed}
            self.assertEqual(e.notification_score(candidate),
                             {"status": "identity_mismatch", "score": None})

    def test_missing_row_is_pending_and_invalid_side_is_rejected(self):
        e = self.engine(2.)
        self.assertIsNone(e.notification_score(self.event()))
        with self.assertRaisesRegex(ValueError, "side must"):
            e.notification_score(self.event("unexpected"))

    def test_feature_error_and_excluded_rows_are_returned_as_terminal(self):
        e = self.engine(2.)
        e.client.get = lambda *_args, **_kwargs: []
        feature_error = self.event()
        e.handle_touch(self.levels, feature_error["symbol"], feature_error["side"], 93.8,
                       feature_error["observed_ms"], feature_error["candle_ms"])
        excluded = self.event(symbol="BTC-USDT-SWAP")
        e.handle_touch(self.levels, excluded["symbol"], excluded["side"], 93.8,
                       excluded["observed_ms"], excluded["candle_ms"])

        self.assertEqual(e.notification_score(feature_error), {"status": "feature_error", "score": None})
        self.assertEqual(e.notification_score(excluded), {"status": "excluded", "score": None})


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
        self.assertEqual((c["reason"], c["exit_price"]), ("breakeven", BE))

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


class V3AddTests(PaperFixture):
    """v3add: v3 plus an equal-notional add 5 percent beyond the entry for scores >= 0.6, sharing the parent's exits."""

    def v3add(self, score):
        e = self.engine(-1., enable_v3=True, enable_v3add=True)
        e.model.score = lambda feats: score
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        return e

    def legs(self, e):
        parent, = self.rows(e, f"SELECT * FROM positions WHERE track={V3ADD_TRACK}")
        child = self.rows(e, f"SELECT * FROM positions WHERE track={V3ADD_LEG}")
        return parent, (child[0] if child else None)

    def equity(self, e, key):
        return e.snapshot()["tracks"][key]["equity"]

    def test_no_add_below_score_0_6(self):
        self.assertIsNone(self.legs(self.v3add(.55))[1])

    def test_add_rests_from_score_0_6(self):
        e = self.v3add(.7)
        parent, child = self.legs(e)
        self.assertAlmostEqual(child["entry"], 93.82*.95)
        self.assertEqual((child["stage"], child["taken"], child["notional"], child["parent_id"]), ("pending_fill", 0, 0., parent["id"]))
        self.assertEqual((child["target"], child["backstop"], child["decision_ms"]), (parent["target"], parent["backstop"], parent["decision_ms"]))
        snap = e.snapshot()["tracks"]
        self.assertEqual(set(snap), {"60", "5", "v3", "v3add"})
        self.assertEqual((len(snap["v3add"]["open"]), len(snap["v3add"]["pending_adds"])), (1, 1))

    def test_filled_add_exits_with_the_parent_at_its_breakeven(self):
        e = self.v3add(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 89., "price": 90.})
        parent, child = self.legs(e)
        self.assertEqual((child["stage"], child["taken"]), ("pending_confirm", 1))
        self.assertAlmostEqual(child["notional"], parent["notional"])
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 96., "low": 93.5, "price": 95.})
        parent, child = self.legs(e)
        self.assertEqual({(p["reason"], p["exit_price"]) for p in (parent, child)}, {("breakeven", BE)})
        self.assertGreater(child["net_bp"], 500)
        self.assertAlmostEqual(self.equity(e, "v3add"), 1000+parent["pnl"]+child["pnl"])
        v3, = self.rows(e, f"SELECT * FROM positions WHERE track={V3_TRACK}")
        self.assertAlmostEqual(self.equity(e, "v3"), 1000+v3["pnl"])
        self.assertEqual(len(e.snapshot()["tracks"]["v3add"]["closed"]), 2)

    def test_failed_reclaim_is_judged_on_the_parent_entry(self):
        e = self.v3add(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 89., "price": 90.})
        self.confirm(e, 93.0)
        parent, child = self.legs(e)
        self.assertEqual({(p["reason"], p["exit_price"]) for p in (parent, child)}, {("failed_reclaim", 93.0)})
        self.assertGreater(child["gross_bp"], 0); self.assertLess(parent["gross_bp"], 0)

    def test_unfilled_add_expires_at_the_parent_decision(self):
        e = self.v3add(.7)
        parent, _ = self.legs(e)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": parent["decision_ms"], "high": 94., "low": 85., "price": 93.})
        _, child = self.legs(e)
        self.assertEqual((child["stage"], child["reason"], child["taken"]), ("closed", "add_expired", 0))
        snap = e.snapshot()["tracks"]["v3add"]
        self.assertEqual((snap["pending_adds"], [r["track"] for r in snap["closed"]]), ([], []))

    def test_unfilled_add_is_cancelled_by_the_decision_run(self):
        e = self.v3add(.7)
        self.confirm(e, 94.5)
        parent, child = self.legs(e)
        self.assertEqual((parent["stage"], child["reason"]), ("breakeven", "add_expired"))
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 94., "low": 85., "price": 86.})
        parent, child = self.legs(e)
        self.assertEqual((parent["reason"], child["taken"], child["pnl"]), ("breakeven", 0, 0))

    def test_add_is_cancelled_when_the_parent_reaches_its_target_first(self):
        e = self.v3add(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 117., "low": 95., "price": 116.5})
        parent, child = self.legs(e)
        self.assertEqual((parent["reason"], child["reason"], child["taken"]), ("target", "add_cancelled", 0))

    def test_add_fills_on_the_way_to_the_parent_backstop(self):
        e = self.v3add(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 60., "price": 62.})
        parent, child = self.legs(e)
        self.assertEqual({(p["reason"], p["exit_price"]) for p in (parent, child)}, {("backstop", parent["backstop"])})
        self.assertAlmostEqual(child["notional"], parent["notional"])
        self.assertAlmostEqual(self.equity(e, "v3add"), 1000+parent["pnl"]+child["pnl"])

    def test_analytics_counts_both_legs_in_the_v3add_account(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e = self.v3add(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 89., "price": 90.})
        app = FastAPI(); install(app, e)
        r = TestClient(app).get("/api/monday-reclaim-paper/analytics?track=v3add")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["summary"]["open_count"], 2)
        self.assertEqual(TestClient(app).get("/api/monday-reclaim-paper/analytics?track=v3").json()["summary"]["open_count"], 1)



class SpikeTrackTests(PaperFixture):
    """spike-HW v1: score >= 0.6 and 15m EMA order >= 3, near target, -18 percent backstop, 10 slots at 5x."""

    def spike(self, score, symbol="ABC-USDT-SWAP"):
        e = getattr(self, "_e", None) or self.engine(-1., enable_spike=True)
        self._e = e
        e.model.score = lambda feats: score
        e.handle_touch(self.levels, symbol, "lower", 93.8, self.touch+500, self.touch)
        rows = self.rows(e, f"SELECT * FROM positions WHERE track={SPIKE_TRACK} AND symbol='{symbol}'")
        return e, (rows[0] if rows else None)

    def falling_15m(self):
        last = (self.touch//H5*H5)//M15*M15-M15
        for k in range(300):
            c = 100.+k*.2  # older bars higher: a falling market into the touch
            self.client.override[("15m", last-k*M15)] = (c, c, c, c)

    def test_ema_order_counts_side_aligned_pairs(self):
        rising = [(k*M15, 0, 0, 0, 100.+k, 0) for k in range(300)]
        self.assertEqual((ema_order(rising, 1, 400*M15), ema_order(rising, -1, 400*M15)), (4, 0))
        with self.assertRaises(ValueError):
            ema_order(rising[:50], 1, 400*M15)

    def test_qualifying_touch_takes_a_slot_at_five_times_margin(self):
        e, p = self.spike(.7)
        self.assertEqual((p["taken"], p["target"], p["note"]), (1, 100., None))
        self.assertAlmostEqual(p["notional"], 1000/10*5)
        self.assertAlmostEqual(p["backstop"], 93.82*.82)
        self.assertEqual(self.rows(e, "SELECT ema_order FROM signals"), [{"ema_order": 4}])
        self.assertIn("spike", e.snapshot()["tracks"])

    def test_low_order_is_a_shadow_row(self):
        self.falling_15m()
        e, p = self.spike(.7)
        self.assertEqual((p["taken"], p["notional"], p["note"]), (0, 0., "order_0"))
        self.assertEqual(self.rows(e, "SELECT ema_order FROM signals"), [{"ema_order": 0}])

    def test_snapshot_keeps_original_order_score_after_recent_signal_page_expires(self):
        e, p = self.spike(.7)
        self.assertEqual(e.snapshot()["tracks"]["spike"]["open"][0]["ema_order"], 4)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 101., "low": 94., "price": 100.})
        self.touch += H5
        self.client.now = self.touch+2000
        for k in range(101):
            self.spike(.7, f"NEW{k}-USDT-SWAP")
        snap = e.snapshot()
        self.assertNotIn(p["signal_id"], {s["id"] for s in snap["signals"]})
        closed = next(r for r in snap["tracks"]["spike"]["closed"] if r["id"] == p["id"])
        self.assertEqual((closed["ema_order"], closed["score"]), (4, .7))
        with e.db() as db:
            db.execute("UPDATE signals SET ema_order=NULL WHERE id=?", (p["signal_id"],))
        self.assertIsNone(next(r for r in e.snapshot()["tracks"]["spike"]["closed"] if r["id"] == p["id"])["ema_order"])

    def test_no_row_below_score_0_6(self):
        e, p = self.spike(.55)
        self.assertIsNone(p)

    def test_ten_slots_then_skip(self):
        for k in range(11):
            e, p = self.spike(.7, f"A{k}-USDT-SWAP")
        rows = self.rows(e, f"SELECT taken, note FROM positions WHERE track={SPIKE_TRACK} ORDER BY id")
        self.assertEqual(sum(r["taken"] for r in rows), 10)
        self.assertEqual(rows[-1], {"taken": 0, "note": "no_slot"})

    def test_backstop_exit_and_loss_capped_at_margin(self):
        e, p = self.spike(.7)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 70., "price": 72.})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={SPIKE_TRACK}")[0]
        self.assertEqual((c["reason"], c["exit_price"]), ("backstop", p["backstop"]))
        self.assertAlmostEqual(c["pnl"], 500*settle(1, 93.82, p["backstop"], "backstop")[2]/1e4)
        e2, q = self.spike(.7, "XYZ-USDT-SWAP")
        self.confirm(e2, 60.)  # failed reclaim far beyond the margin
        c = self.rows(e2, f"SELECT * FROM positions WHERE track={SPIKE_TRACK} AND symbol='XYZ-USDT-SWAP'")[0]
        self.assertEqual(c["reason"], "failed_reclaim")
        self.assertAlmostEqual(c["pnl"], -c["notional"]/5)  # margin = equity after the first loss / 10

    def test_analytics_and_api_accept_spike(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e, _ = self.spike(.7)
        app = FastAPI(); install(app, e)
        r = TestClient(app).get("/api/monday-reclaim-paper/analytics?track=spike")
        self.assertEqual((r.status_code, r.json()["summary"]["open_count"]), (200, 1))


class V3LockTests(PaperFixture):
    """v3lock: the v3 rule with the reclaim stop at entry +0.5 percent, in its own account."""
    LOCK = 93.82*1.005

    def v3lock(self, score=.7):
        e = self.engine(-1., enable_v3=True, enable_v3lock=True)
        e.model.score = lambda feats: score
        e.handle_touch(self.levels, "ABC-USDT-SWAP", "lower", 93.8, self.touch+500, self.touch)
        return e

    def row(self, e, track):
        return self.rows(e, f"SELECT * FROM positions WHERE track={track}")[0]

    def test_entry_target_size_and_backstop_match_v3(self):
        e = self.v3lock()
        a, b = self.row(e, V3_TRACK), self.row(e, V3LOCK_TRACK)
        for k in ("taken", "target", "notional", "backstop", "decision_ms"):
            self.assertEqual(a[k], b[k])
        self.assertEqual(set(e.snapshot()["tracks"]), {"60", "5", "v3", "v3lock"})
        self.assertEqual(e.snapshot()["v3lock"], {"breakeven_offset_bp": 50.})

    def test_reclaim_arms_the_lock_and_stops_there(self):
        e = self.v3lock()
        self.confirm(e, 94.5)
        self.assertAlmostEqual(self.row(e, V3LOCK_TRACK)["be_price"], self.LOCK)
        self.assertAlmostEqual(self.row(e, V3_TRACK)["be_price"], BE)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 95., "low": 94., "price": 94.1})
        c = self.row(e, V3LOCK_TRACK)
        self.assertEqual(c["reason"], "breakeven"); self.assertAlmostEqual(c["exit_price"], self.LOCK)
        self.assertGreater(c["net_bp"], 40)
        self.assertEqual(self.row(e, V3_TRACK)["stage"], "breakeven")  # v3 keeps its +0.17 percent stop
        snap = e.snapshot()["tracks"]
        self.assertAlmostEqual(snap["v3lock"]["equity"], 1000+c["pnl"])
        self.assertEqual(snap["v3"]["equity"], 1000)

    def test_reclaim_close_short_of_the_lock_exits_at_the_close(self):
        e = self.v3lock()
        self.confirm(e, 94.1)
        c = self.row(e, V3LOCK_TRACK)
        self.assertEqual((c["reason"], c["exit_price"]), ("reclaimed_inside_be", 94.1))
        self.assertEqual(self.row(e, V3_TRACK)["stage"], "breakeven")

    def test_disabled_without_v3(self):
        e = self.engine(-1., enable_v3lock=True)
        self.assertIsNone(e.v3lock)
        self.assertNotIn("v3lock", e.snapshot()["tracks"])

    def test_analytics_and_api_accept_v3lock(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e = self.v3lock()
        app = FastAPI(); install(app, e)
        r = TestClient(app).get("/api/monday-reclaim-paper/analytics?track=v3lock")
        self.assertEqual((r.status_code, r.json()["summary"]["open_count"]), (200, 1))


class AllIn3Tests(PaperFixture):
    """allin3: score >= 0.6, opposite 1.618 target, -30 percent backstop, all-in 3x, one at a time, refill below 100 USDT."""

    def enter(self, score=.7, symbol="ABC-USDT-SWAP"):
        e = getattr(self, "_e", None) or self.engine(-1., enable_allin3=True)
        self._e = e
        e.model.score = lambda feats: score
        e.handle_touch(self.levels, symbol, "lower", 93.8, self.touch+500, self.touch)
        rows = self.rows(e, f"SELECT * FROM positions WHERE track={ALLIN3_TRACK} AND symbol='{symbol}'")
        return e, (rows[0] if rows else None)

    def track(self, e):
        return self.rows(e, f"SELECT * FROM tracks WHERE track={ALLIN3_TRACK}")[0]

    def test_all_in_three_times_equity_with_far_target_and_backstop(self):
        e, p = self.enter()
        self.assertEqual((p["taken"], p["target"], p["note"]), (1, 116.18, None))
        self.assertAlmostEqual(p["notional"], 3000.)
        self.assertAlmostEqual(p["backstop"], 93.82*.7)
        snap = e.snapshot()
        self.assertEqual((snap["tracks"]["allin3"]["stakes"], snap["tracks"]["allin3"]["injected"], snap["tracks"]["allin3"]["refills"]), (1, 1000., []))
        self.assertEqual(snap["allin3"]["leverage"], 3.)

    def test_no_row_below_score_0_6(self):
        _, p = self.enter(.55)
        self.assertIsNone(p)

    def test_one_position_at_a_time(self):
        self.enter()
        e, q = self.enter(.8, "XYZ-USDT-SWAP")
        self.assertEqual((q["taken"], q["notional"], q["note"]), (0, 0., "busy"))

    def test_backstop_blow_up_refills_one_stake(self):
        e, p = self.enter()
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 60., "price": 62.})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={ALLIN3_TRACK}")[0]
        self.assertEqual((c["reason"], c["exit_price"]), ("backstop", p["backstop"]))
        loss = 3000*settle(1, 93.82, p["backstop"], "backstop")[2]/1e4
        self.assertAlmostEqual(c["pnl"], loss)
        t = self.track(e)
        self.assertEqual((t["equity"], t["stakes"]), (1000., 2))
        r = self.rows(e, "SELECT * FROM refills")[0]
        self.assertAlmostEqual(r["equity_before"], 1000+loss); self.assertEqual(r["equity_after"], 1000.)
        snap = e.snapshot()["tracks"]["allin3"]
        self.assertEqual((snap["injected"], snap["multiple"]), (2000., .5))
        _, q = self.enter(.7, "XYZ-USDT-SWAP")
        self.assertAlmostEqual(q["notional"], 3000.)  # the slot is free again, sized on the new stake
        self.client.now = self.touch+5*MIN  # analytics only counts exits up to now
        a = e.analytics("allin3")["summary"]
        self.assertEqual(a["refill_count"], 1)
        self.assertAlmostEqual(a["refilled"], -loss); self.assertAlmostEqual(a["realized_pnl"], loss)
        self.assertGreater(a["max_drawdown_pct"], 90)
        self.assertAlmostEqual(e.analytics("allin3")["coverage"]["ledger_difference"], 0.)
        curve = [round(x["equity"], 6) for x in e.analytics("allin3")["curve"]]
        self.assertEqual(curve[:3], [1000., round(1000+loss, 6), 1000.])

    def test_target_win_compounds_into_the_next_size(self):
        e, p = self.enter()
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 117., "low": 95., "price": 116.5})
        c = self.rows(e, f"SELECT * FROM positions WHERE track={ALLIN3_TRACK}")[0]
        self.assertEqual((c["reason"], c["exit_price"]), ("target", 116.18))
        equity = 1000+3000*settle(1, 93.82, 116.18, "target")[2]/1e4
        self.assertAlmostEqual(self.track(e)["equity"], equity)
        self.client.now = self.touch+2000  # entry features need the pre-touch history window
        _, q = self.enter(.7, "XYZ-USDT-SWAP")
        self.assertAlmostEqual(q["notional"], 3*equity)

    def test_loss_beyond_the_margin_is_capped_then_refilled(self):
        e, p = self.enter()
        self.confirm(e, 60.)
        c = self.rows(e, f"SELECT * FROM positions WHERE track={ALLIN3_TRACK}")[0]
        self.assertEqual((c["reason"], c["pnl"]), ("failed_reclaim", -1000.))
        self.assertEqual(self.rows(e, "SELECT equity_before FROM refills"), [{"equity_before": 0.}])
        self.assertEqual(self.track(e)["stakes"], 2)

    def test_analytics_and_api_accept_allin3(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e, _ = self.enter()
        app = FastAPI(); install(app, e)
        r = TestClient(app).get("/api/monday-reclaim-paper/analytics?track=allin3")
        self.assertEqual((r.status_code, r.json()["summary"]["open_count"], r.json()["summary"]["refill_count"]), (200, 1, 0))


class V3Add20Tests(PaperFixture):
    """v3add20: the v3add rule with margin = realized equity / 20 per leg at 3x, at most 20 funded legs."""

    def enter(self, score=.7, symbol="ABC-USDT-SWAP"):
        e = getattr(self, "_e", None) or self.engine(-1., enable_v3=True, enable_v3add20=True)
        self._e = e
        e.model.score = lambda feats: score
        e.handle_touch(self.levels, symbol, "lower", 93.8, self.touch+500, self.touch)
        return e

    def legs(self, e, symbol="ABC-USDT-SWAP"):
        parent, = self.rows(e, f"SELECT * FROM positions WHERE track={V3ADD20_TRACK} AND symbol='{symbol}'")
        child = self.rows(e, f"SELECT * FROM positions WHERE track={V3ADD20_LEG} AND symbol='{symbol}'")
        return parent, (child[0] if child else None)

    def test_parent_posts_one_twentieth_of_equity_at_three_times(self):
        e = self.enter()
        parent, child = self.legs(e)
        self.assertEqual((parent["taken"], parent["target"], parent["note"]), (1, 116.18, None))
        self.assertAlmostEqual(parent["notional"], 1000/20*3)
        self.assertAlmostEqual(parent["backstop"], 93.82*.7)
        self.assertEqual((child["stage"], child["entry"]), ("pending_fill", 93.82*.95))
        snap = e.snapshot()
        self.assertEqual(set(snap["tracks"]), {"60", "5", "v3", "v3add20"})
        self.assertEqual(snap["v3add20"]["slots"], 20)

    def test_below_0_5_is_a_shadow_and_0_5_to_0_6_has_no_add(self):
        e = self.enter(.45)
        parent, child = self.legs(e)
        self.assertEqual((parent["taken"], parent["notional"], child), (0, 0., None))
        parent, child = self.legs(self.enter(.55, "XYZ-USDT-SWAP"), "XYZ-USDT-SWAP")
        self.assertEqual((parent["taken"], child), (1, None))

    def test_add_takes_its_own_slot_sized_on_equity_at_its_fill(self):
        e = self.enter()
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 89., "price": 90.})
        parent, child = self.legs(e)
        self.assertEqual((child["stage"], child["taken"]), ("pending_confirm", 1))
        self.assertAlmostEqual(child["notional"], 150.)
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 96., "low": 93.5, "price": 95.})
        parent, child = self.legs(e)
        self.assertEqual({(p["reason"], p["exit_price"]) for p in (parent, child)}, {("breakeven", BE)})
        self.assertAlmostEqual(e.snapshot()["tracks"]["v3add20"]["equity"], 1000+parent["pnl"]+child["pnl"])
        self.client.now = self.touch//H1*H1+4*H1  # analytics only counts exits up to now
        self.assertEqual(e.analytics("v3add20")["summary"]["closed_count"], 2)

    def test_twenty_funded_legs_then_no_slot(self):
        for k in range(21):
            e = self.enter(.55, f"A{k}-USDT-SWAP")
        rows = self.rows(e, f"SELECT taken, note FROM positions WHERE track={V3ADD20_TRACK} ORDER BY id")
        self.assertEqual(sum(r["taken"] for r in rows), 20)
        self.assertEqual(rows[-1], {"taken": 0, "note": "no_slot"})

    def test_add_without_a_slot_is_cancelled(self):
        for k in range(19):
            self.enter(.55, f"A{k}-USDT-SWAP")
        e = self.enter(.7)
        self.enter(.55, "LAST-USDT-SWAP")  # the 20th slot goes to a later parent
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 89., "price": 90.})
        _, child = self.legs(e)
        self.assertEqual((child["stage"], child["reason"], child["taken"]), ("closed", "add_no_slot", 0))

    def test_win_compounds_and_loss_is_capped_at_the_margin(self):
        e = self.enter()
        self.confirm(e, 94.5)
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch//H1*H1+3*H1, "high": 117., "low": 95., "price": 116.5})
        parent, _ = self.legs(e)
        equity = 1000+150*settle(1, 93.82, 116.18, "target")[2]/1e4
        self.assertAlmostEqual(parent["pnl"], equity-1000)
        self.client.now = self.touch+2000
        self.enter(.55, "XYZ-USDT-SWAP")
        p, _ = self.legs(e, "XYZ-USDT-SWAP")
        self.assertAlmostEqual(p["notional"], equity/20*3)
        self.confirm(e, 60.)
        p, _ = self.legs(e, "XYZ-USDT-SWAP")
        self.assertEqual(p["reason"], "failed_reclaim"); self.assertAlmostEqual(p["pnl"], -equity/20)

    def test_analytics_and_api_accept_v3add20(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        e = self.enter()
        e.handle_quote("ABC-USDT-SWAP", {"candle_ms": self.touch+MIN, "high": 94., "low": 89., "price": 90.})
        app = FastAPI(); install(app, e)
        r = TestClient(app).get("/api/monday-reclaim-paper/analytics?track=v3add20")
        self.assertEqual((r.status_code, r.json()["summary"]["open_count"]), (200, 2))


if __name__ == "__main__":
    unittest.main()

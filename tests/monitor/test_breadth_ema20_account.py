"""Forward ledger tests for breadth-ema20's three independent signal branches."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yoyo.monitor.breadth_ema20_account import (
    BAR_MS,
    BTC_HOLD_MS,
    BTC_SYMBOL,
    BreadthEma20Paper,
)


T0 = 1_800_000_000_000 // BAR_MS * BAR_MS


class FakeClock:
    def __init__(self, now):
        self.now = int(now)

    def __call__(self):
        return self.now


class BreadthEma20PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock(T0 - BAR_MS)
        self.engine = BreadthEma20Paper(Path(self.tmp.name), clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def bar(self, symbol, bar_open_ms, *, open_=100.0, high=None, low=None, close=None,
            next_open=None, next_open_ms=None):
        high = open_ if high is None else high
        low = open_ if low is None else low
        close = open_ if close is None else close
        return {
            "symbol": symbol,
            "bar_open_ms": bar_open_ms,
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "next_open_ms": next_open_ms,
            "next_open": next_open,
        }

    def event(self, bar_open_ms, bars, candidates=(), **kwargs):
        return {
            "event_id": f"binance-usdm-5m:{bar_open_ms}",
            "bar_open_ms": bar_open_ms,
            "close_ms": bar_open_ms + BAR_MS,
            "is_replay": False,
            "is_realtime": True,
            "bars_by_symbol": {row["symbol"]: row for row in bars},
            "candidates": list(candidates),
            "funding_events": [],
            **kwargs,
        }

    def btc_candidate(self, branch, decision_ms, *, entry_price=50_000.0, **kwargs):
        candidate = {
            "event_id": f"source-{branch}-{decision_ms}",
            "branch": branch,
            "known_ms": decision_ms - 30 * 60_000,
            "decision_ms": decision_ms,
            "entry_ms": decision_ms,
            "symbol": BTC_SYMBOL,
            "next_open_ms": decision_ms,
            "next_open": entry_price,
        }
        candidate.update(kwargs)
        return candidate

    def ema_snapshot(self, decision_ms, *, close=95_000.0, ema20=100_000.0, complete=True,
                     bar_close_ms=None, asof_ms=None):
        return {
            "close": close,
            "ema20": ema20,
            "bar_close_ms": decision_ms - 1,
            "complete": complete,
            "asof_ms": decision_ms - 1,
        } | ({"bar_close_ms": bar_close_ms} if bar_close_ms is not None else {}) | (
            {"asof_ms": asof_ms} if asof_ms is not None else {}
        )

    def test_ordinary_ema_veto_occupies_shared_btc_slot_and_cannot_be_reentered(self):
        decision = T0 + BAR_MS
        veto = self.btc_candidate("BTC_ordinary", decision, btc_4h=self.ema_snapshot(decision, close=101_000, ema20=100_000))
        first = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=49_900, close=50_050, next_open=50_000,
                                         next_open_ms=decision)], [veto])
        result = self.engine.process_closed_bar(first)
        self.assertEqual(result["candidate_results"][0]["status"], "vetoed")
        self.assertEqual(result["candidate_results"][0]["reason"], "ema20_veto_slot_reserved")
        self.assertEqual(self.engine.snapshot()["open_positions"], 0)
        self.assertEqual(self.engine.snapshot()["btc_reserved_until_ms"], decision + BTC_HOLD_MS)

        urgent_same_clock = self.btc_candidate("BTC_case", decision, entry_price=50_000)
        repeated = self.engine.process_closed_bar({**first, "candidates": [veto, urgent_same_clock]})
        self.assertEqual([x["branch"] for x in repeated["candidate_results"]], ["BTC_case", "BTC_ordinary"])
        self.assertEqual(repeated["candidate_results"][0]["status"], "blocked")
        self.assertEqual(repeated["candidate_results"][0]["reason"], "btc_shared_6d_reservation")

    def test_same_clock_case_has_priority_and_ema_gate_requires_complete_asof_4h(self):
        decision = T0 + BAR_MS
        ordinary = self.btc_candidate("BTC_ordinary", decision, btc_4h=self.ema_snapshot(decision))
        case = self.btc_candidate("BTC_case", decision)
        event = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_010, low=49_990,
                                        close=50_000, next_open=51_000, next_open_ms=decision)],
                           [ordinary, case])
        result = self.engine.process_closed_bar(event)
        self.assertEqual([r["branch"] for r in result["candidate_results"]], ["BTC_case", "BTC_ordinary"])
        self.assertEqual(result["candidate_results"][0]["status"], "open")
        self.assertEqual(result["candidate_results"][1]["status"], "blocked")
        positions = self.engine.snapshot()["positions"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["branch"], "BTC_case")
        self.assertEqual(positions[0]["entry"], 51_000.0)  # next open, never the signal close

        bad_decision = decision + BAR_MS
        bad = self.btc_candidate("BTC_ordinary", bad_decision,
                                 btc_4h=self.ema_snapshot(bad_decision, complete=False))
        next_event = self.event(decision, [self.bar(BTC_SYMBOL, decision, open_=51_000, high=51_000, low=51_000,
                                                     close=51_000, next_open=51_000, next_open_ms=decision + BAR_MS)],
                                [bad])
        out = self.engine.process_closed_bar(next_event)
        # The ordinary source's independent six-day reservation was also consumed
        # before it encountered the case-owned shared reservation.
        self.assertEqual(out["candidate_results"][0]["reason"], "source_6d_reservation")

    def test_incomplete_or_stale_4h_ema_vetoes_but_still_occupies_btc_slot(self):
        decision = T0 + BAR_MS
        candidate = self.btc_candidate("BTC_ordinary", decision,
                                       btc_4h=self.ema_snapshot(decision, complete=False))
        result = self.engine.process_closed_bar(self.event(
            T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_000, low=50_000, close=50_000,
                          next_open=50_000, next_open_ms=decision)], [candidate]))
        self.assertEqual(result["candidate_results"][0]["status"], "vetoed")
        self.assertEqual(self.engine.snapshot()["btc_reserved_until_ms"], decision + BTC_HOLD_MS)

    def test_alt_basket_is_equal_weight_parallel_with_btc_and_excluded_constituents_removed(self):
        decision = T0 + BAR_MS
        alt = {
            "event_id": f"ALT|{decision}",
            "branch": "ALT",
            "known_ms": decision,
            "decision_ms": decision,
            "entry_ms": decision,
            "initial_n": 3,
            "expected_member_n": 2,
            "members": [{"symbol": "BTCUSDT"}, {"symbol": "ADAUSDT"}, {"symbol": "XRPUSDT"}],
        }
        case = self.btc_candidate("BTC_case", decision)
        bars = [
            self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100, low=49_900, close=50_050,
                     next_open=50_500, next_open_ms=decision),
            self.bar("ADAUSDT", T0, open_=10.0, high=10.1, low=9.9, close=10.0,
                     next_open=11.0, next_open_ms=decision),
            self.bar("XRPUSDT", T0, open_=1.0, high=1.01, low=.99, close=1.0,
                     next_open=1.25, next_open_ms=decision),
        ]
        result = self.engine.process_closed_bar(self.event(T0, bars, [alt, case]))
        self.assertEqual([r["branch"] for r in result["candidate_results"]], ["BTC_case", "ALT"])
        self.assertEqual([r["status"] for r in result["candidate_results"]], ["open", "open"])
        snap = self.engine.snapshot()
        self.assertEqual(snap["open_positions"], 3)
        self.assertEqual(snap["open_btc_positions"], 1)
        self.assertEqual(snap["open_alt_positions"], 2)
        alt_positions = [p for p in snap["positions"] if p["branch"] == "ALT"]
        self.assertEqual({p["symbol"] for p in alt_positions}, {"ADAUSDT", "XRPUSDT"})
        self.assertEqual([p["notional"] for p in alt_positions], [250.0, 250.0])
        self.assertAlmostEqual(snap["btc_equity"], 499.75)
        self.assertAlmostEqual(snap["alt_equity"], 499.75)

        blocked = {
            "event_id": f"ALT|{decision + BAR_MS}", "branch": "ALT", "known_ms": decision + BAR_MS,
            "decision_ms": decision + BAR_MS, "entry_ms": decision + BAR_MS,
            "initial_n": 2,
            "expected_member_n": 1, "members": [{"symbol": "DOGEUSDT"}],
        }
        next_bars = [self.bar("ADAUSDT", decision, open_=11, high=11.1, low=10.9, close=11,
                              next_open=11.1, next_open_ms=decision + BAR_MS),
                     self.bar("XRPUSDT", decision, open_=1.25, high=1.3, low=1.2, close=1.25,
                              next_open=1.26, next_open_ms=decision + BAR_MS),
                     self.bar(BTC_SYMBOL, decision, open_=50_500, high=50_500, low=50_500, close=50_500,
                              next_open=50_500, next_open_ms=decision + BAR_MS)]
        blocked_result = self.engine.process_closed_bar(self.event(decision, next_bars, [blocked]))
        self.assertEqual(blocked_result["candidate_results"][0]["reason"], "alt_24h_spacing")

    def test_alt_two_initial_including_excluded_major_can_open_one_remaining_alt(self):
        decision = T0 + BAR_MS
        alt = {
            "event_id": f"ALT|{decision}", "branch": "ALT", "known_ms": decision,
            "decision_ms": decision, "entry_ms": decision, "initial_n": 2,
            "expected_member_n": 1,
            "members": [{"symbol": "BTCUSDT"}, {"symbol": "DOGEUSDT", "next_open_ms": decision,
                         "next_open": .2}],
        }
        event = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100,
                                        low=49_900, close=50_000, next_open=50_000,
                                        next_open_ms=decision)], [alt])
        result = self.engine.process_closed_bar(event)
        self.assertEqual(result["candidate_results"][0]["status"], "open")
        snap = self.engine.snapshot()
        self.assertEqual(snap["open_alt_positions"], 1)
        alt_position = next(p for p in snap["positions"] if p["branch"] == "ALT")
        self.assertEqual(alt_position["symbol"], "DOGEUSDT")
        self.assertEqual(alt_position["notional"], 500.0)

    def test_case_partial_limit_gap_stop_and_six_day_deadline_close(self):
        decision = T0 + BAR_MS
        case = self.btc_candidate("BTC_case", decision, entry_price=50_000)
        self.engine.process_closed_bar(self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=49_900, high=50_000, low=49_800,
                                                               close=50_000, next_open=50_000, next_open_ms=decision)], [case]))
        pos = self.engine.snapshot()["positions"][0]
        target = pos["target_price"]
        self.assertEqual(target, float(int(target)))  # whole-USDT policy-4 rounding

        entry_ms = decision
        # A target gap fills half at its limit; the same candle then takes the runner's hard stop.
        up = self.event(entry_ms, [self.bar(BTC_SYMBOL, entry_ms, open_=target + 5, high=target + 30,
                                            low=50_000 * .94, close=target + 10,
                                            next_open=target + 10, next_open_ms=entry_ms + BAR_MS)])
        self.engine.process_closed_bar(up)
        snap = self.engine.snapshot()
        self.assertEqual(snap["open_positions"], 0)
        with self.engine.db() as db:
            fills = [dict(r) for r in db.execute("SELECT kind,qty,price,fee_rate FROM fills ORDER BY id")]
        self.assertEqual([f["kind"] for f in fills], ["entry_market", "target_limit_maker", "stop_6pct"])
        self.assertAlmostEqual(fills[1]["qty"], fills[0]["qty"] * .5)
        self.assertEqual(fills[1]["fee_rate"], .0002)
        self.assertEqual(fills[2]["fee_rate"], .0005)

        # A separate case reaches the target on an earlier bar, then its runner is
        # closed at the exact 6-day next open (not at the preceding bar close).
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock(T0 - BAR_MS)
        self.engine = BreadthEma20Paper(Path(self.tmp.name), clock=self.clock)
        self.engine.process_closed_bar(self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_000, low=49_900,
                                                               close=50_000, next_open=50_000, next_open_ms=decision)],
                                                   [case]))
        p = self.engine.snapshot()["positions"][0]
        deadline = p["deadline_ms"]
        settle_row = {"symbol": BTC_SYMBOL, "time_ms": decision, "rate": .0001,
                      "mark_price": 50_000, "available": True}
        settlement = self.event(decision, [self.bar(BTC_SYMBOL, decision, open_=50_000,
                                                    high=p["target_price"] + 10, low=49_500, close=p["target_price"],
                                                    next_open=p["target_price"], next_open_ms=decision + BAR_MS)],
                                funding_open_events=[settle_row],
                                funding_open_due=[{"symbol": BTC_SYMBOL, "time_ms": decision, "due": True,
                                                   "available": True, "rate": .0001, "mark_price": 50_000}])
        self.engine.process_closed_bar(settlement)
        after_settle = self.engine.snapshot()["positions"][0]
        self.assertIsNone(after_settle["first_exit_ms"])  # limit is paused on funding settlement bar
        self.assertGreater(after_settle["target_price"], p["target_price"])
        partial = self.event(decision + BAR_MS, [self.bar(BTC_SYMBOL, decision + BAR_MS, open_=50_000,
                                                          high=after_settle["target_price"] + 10, low=49_500,
                                                          close=after_settle["target_price"], next_open=50_000,
                                                          next_open_ms=decision + 2 * BAR_MS)])
        self.engine.process_closed_bar(partial)
        self.assertEqual(self.engine.snapshot()["open_positions"], 1)
        with self.engine.db() as db:
            db.execute("UPDATE account SET cursor_close_ms=? WHERE id=1", (deadline - BAR_MS,))
            db.execute("UPDATE positions SET last_bar_open_ms=? WHERE stage='open'", (deadline - 2 * BAR_MS,))
        next_signal = self.btc_candidate("BTC_ordinary", deadline,
                                         btc_4h=self.ema_snapshot(deadline))
        final = self.event(deadline - BAR_MS, [self.bar(BTC_SYMBOL, deadline - BAR_MS, open_=60_000,
                                                        high=60_100, low=59_900, close=60_000,
                                                        next_open=61_000, next_open_ms=deadline)],
                           [next_signal])
        final_result = self.engine.process_closed_bar(final)
        self.assertEqual(final_result["candidate_results"][0]["status"], "open")
        final_snapshot = self.engine.snapshot()
        closed = next(p for p in final_snapshot["positions"] if p["branch"] == "BTC_case")
        self.assertEqual(closed["exit_ms"], deadline)
        self.assertEqual(closed["exit_price"], 61_000)
        self.assertEqual(closed["exit_reason"], "time_6d")
        self.assertEqual(closed["first_exit_ms"], decision + BAR_MS)
        ordinary_open = next(p for p in final_snapshot["positions"] if p["branch"] == "BTC_ordinary")
        self.assertEqual(ordinary_open["entry_ms"], deadline)
        self.assertEqual(ordinary_open["entry"], 61_000)
        self.assertAlmostEqual(ordinary_open["notional"], final_snapshot["btc_equity"] / (1 - .0005))
        self.assertEqual(final_snapshot["open_btc_positions"], 1)

    def test_due_funding_missing_is_retryable_then_applied_once_and_restart_is_idempotent(self):
        decision = T0 + BAR_MS
        ordinary = self.btc_candidate("BTC_ordinary", decision, entry_price=50_000,
                                      btc_4h=self.ema_snapshot(decision))
        first = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100, low=49_900,
                                         close=50_000, next_open=50_000, next_open_ms=decision)], [ordinary])
        self.engine.process_closed_bar(first)
        before_cursor = self.engine.cursor_ms()
        settle_ms = decision
        missing_open = self.event(settle_ms, [], funding_due=[])
        no_open = self.engine.process_closed_bar(missing_open)
        self.assertFalse(no_open["processed"])
        self.assertEqual(no_open["reason"], "missing_open_bar")
        self.assertEqual(self.engine.cursor_ms(), before_cursor)
        retry = self.event(settle_ms, [self.bar(BTC_SYMBOL, settle_ms, open_=50_000, high=50_100, low=49_900,
                                                close=50_000, next_open=50_000, next_open_ms=settle_ms + BAR_MS)],
                           funding_open_due=[{"symbol": BTC_SYMBOL, "time_ms": settle_ms, "due": None,
                                              "available": False}])
        missing = self.engine.process_closed_bar(retry)
        self.assertFalse(missing["processed"])
        self.assertTrue(missing["retryable"])
        self.assertEqual(missing["reason"], "missing_funding")
        self.assertEqual(self.engine.cursor_ms(), before_cursor)

        completed = {**retry,
                     "funding_open_events": [{"symbol": BTC_SYMBOL, "time_ms": settle_ms, "rate": .001,
                                              "mark_price": 50_000, "available": True}],
                     "funding_open_due": [{"symbol": BTC_SYMBOL, "time_ms": settle_ms, "due": True,
                                           "available": True, "rate": .001, "mark_price": 50_000}]}
        applied = self.engine.process_closed_bar(completed)
        self.assertTrue(applied["processed"])
        self.assertEqual(self.engine.cursor_ms(), settle_ms + BAR_MS)
        snap = self.engine.snapshot()
        self.assertAlmostEqual(snap["btc_equity"], 499.25)  # entry fee .25 + funding .50
        pos = next(p for p in snap["positions"] if p["branch"] == "BTC_ordinary")
        self.assertAlmostEqual(pos["funding_pnl"], -.5)
        self.assertTrue(pos["funding_complete"])

        restarted = BreadthEma20Paper(Path(self.tmp.name), clock=self.clock)
        again = restarted.process_closed_bar(completed)
        self.assertTrue(again["duplicate"])
        self.assertEqual(restarted.snapshot()["btc_equity"], snap["btc_equity"])
        self.assertEqual(restarted.open_symbols(), [BTC_SYMBOL])

    def test_stop_before_close_boundary_settlement_is_not_charged(self):
        decision = T0 + BAR_MS
        case = self.btc_candidate("BTC_case", decision)
        self.engine.process_closed_bar(self.event(
            T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100, low=49_900,
                          close=50_000, next_open=50_000, next_open_ms=decision)], [case]))
        close_ms = decision + BAR_MS
        stop_bar = self.event(
            decision,
            [self.bar(BTC_SYMBOL, decision, open_=50_000, high=50_050, low=46_000,
                      close=47_000, next_open=47_000, next_open_ms=close_ms)],
            funding_events=[{"symbol": BTC_SYMBOL, "time_ms": close_ms, "rate": .001,
                             "mark_price": 50_000, "available": True}],
            funding_due=[{"symbol": BTC_SYMBOL, "time_ms": close_ms, "due": True,
                          "available": True, "rate": .001, "mark_price": 50_000}],
        )
        self.assertTrue(self.engine.process_closed_bar(stop_bar)["processed"])
        pos = self.engine.snapshot()["positions"][0]
        self.assertEqual(pos["exit_reason"], "stop_6pct")
        self.assertEqual(pos["funding_pnl"], 0.0)
        with self.engine.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM funding").fetchone()[0], 0)

    def test_entry_boundary_cost_counts_but_favorable_funding_income_is_excluded(self):
        decision = T0 + BAR_MS
        candidate = self.btc_candidate("BTC_ordinary", decision, entry_price=50_000,
                                       btc_4h=self.ema_snapshot(decision))
        event = self.event(
            T0,
            [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100, low=49_900, close=50_000,
                      next_open=50_000, next_open_ms=decision)],
            [candidate],
            funding_events=[{"symbol": BTC_SYMBOL, "time_ms": decision, "rate": -.001,
                             "mark_price": 50_000, "available": True}],
            funding_due=[{"symbol": BTC_SYMBOL, "time_ms": decision, "due": True,
                          "available": True, "rate": -.001, "mark_price": 50_000}],
        )
        self.assertEqual(self.engine.process_closed_bar(event)["candidate_results"][0]["status"], "open")
        snap = self.engine.snapshot()
        pos = snap["positions"][0]
        self.assertAlmostEqual(snap["btc_equity"], 499.75)
        self.assertEqual(pos["funding_pnl"], 0.0)
        self.assertTrue(pos["funding_complete"])
        with self.engine.db() as db:
            row = dict(db.execute("SELECT payment,status FROM funding").fetchone())
        self.assertEqual(row, {"payment": 0.0, "status": "boundary_income_excluded"})

    def test_missing_candidate_entry_open_retries_without_consuming_cursor_or_reservation(self):
        decision = T0 + BAR_MS
        candidate = self.btc_candidate("BTC_case", decision)
        candidate.pop("next_open")
        candidate.pop("next_open_ms")
        event = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100,
                                         low=49_900, close=50_000)], [candidate])
        missing = self.engine.process_closed_bar(event)
        self.assertFalse(missing["processed"])
        self.assertEqual(missing["reason"], "missing_entry_open")
        self.assertIsNone(self.engine.cursor_ms())
        self.assertEqual(self.engine.snapshot()["btc_reserved_until_ms"], 0)
        retry = {**event, "bars_by_symbol": {BTC_SYMBOL: self.bar(
            BTC_SYMBOL, T0, open_=50_000, high=50_100, low=49_900, close=50_000,
            next_open=50_250, next_open_ms=decision)}}
        opened = self.engine.process_closed_bar(retry)
        self.assertEqual(opened["candidate_results"][0]["status"], "open")
        self.assertEqual(self.engine.snapshot()["positions"][0]["entry"], 50_250)

    def test_replay_prestart_and_out_of_order_bars_never_create_historical_entries(self):
        decision = T0 + BAR_MS
        replay_candidate = self.btc_candidate("BTC_case", decision, entry_price=50_000)
        replay = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_100, low=49_900,
                                          close=50_000, next_open=50_000, next_open_ms=decision)], [replay_candidate])
        replay["is_replay"] = True
        replay["is_realtime"] = False
        self.engine.process_closed_bar(replay)
        self.assertEqual(self.engine.snapshot()["open_positions"], 0)
        self.assertEqual(self.engine.snapshot()["signals"][0]["reason"], "non_realtime_or_replay")

        stale_open = T0 - 2 * BAR_MS
        out_of_order = self.event(stale_open, [self.bar(BTC_SYMBOL, stale_open, open_=50_000, high=50_000, low=50_000,
                                                         close=50_000, next_open=50_000, next_open_ms=stale_open + BAR_MS)])
        self.assertEqual(self.engine.process_closed_bar(out_of_order)["reason"], "bar_gap")

    def test_startup_rejects_prestart_decision_but_accepts_future_decision_from_observation_window(self):
        # A signal whose observation window closed before account activation is not replayed.
        prestart_decision = T0 - 2 * BAR_MS
        prestart = self.btc_candidate("BTC_case", prestart_decision, entry_price=50_000)
        event = self.event(T0, [self.bar(BTC_SYMBOL, T0, open_=50_000, high=50_000, low=50_000,
                                         close=50_000, next_open=50_000, next_open_ms=T0 + BAR_MS)], [prestart])
        out = self.engine.process_closed_bar(event)
        self.assertEqual(out["candidate_results"][0]["reason"], "pre_start_signal")
        self.assertEqual(self.engine.snapshot()["open_positions"], 0)

        decision = T0 + 2 * BAR_MS
        pending = self.btc_candidate("BTC_case", decision, entry_price=50_000)
        live_decision = self.event(T0 + BAR_MS, [self.bar(BTC_SYMBOL, T0 + BAR_MS, open_=50_000, high=50_100,
                                                          low=49_900, close=50_000, next_open=50_000,
                                                          next_open_ms=decision)], [pending])
        # The first event above committed at T0+BAR; its next contiguous event closes at T0+2BAR.
        future_out = self.engine.process_closed_bar(live_decision)
        self.assertEqual(future_out["candidate_results"][0]["status"], "open")
        self.assertEqual(self.engine.snapshot()["positions"][0]["entry_ms"], decision)


if __name__ == "__main__":
    unittest.main()

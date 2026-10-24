"""Behavioral contract tests for the isolated Nautilus historical replay."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Run with the isolated Nautilus 1.231.0 runtime")

from yoyo.layers.l3_backtest.replay_native import ReplayEngine


START = 1_800_000_000


def make_engine(cash="10000"):
    return ReplayEngine({
        "symbol": "BTC-USDT-SWAP",
        "cash": cash,
        "period_seconds": 300,
        "price_precision": 2,
        "size_precision": 8,
    })


def bar(at, opened, high, low, close):
    return {
        "time": at,
        "open": str(opened),
        "high": str(high),
        "low": str(low),
        "close": str(close),
        "volume": "100",
    }


def user_id(number):
    return f"{number:032d}"


class ReplayNativeTests(unittest.TestCase):
    def setUp(self):
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.close()

    def engine(self, cash="10000"):
        engine = make_engine(cash)
        self.engines.append(engine)
        return engine

    def seed(self, engine):
        return engine.command({"op": "advance", "bars": [bar(START, 100, 100, 100, 100)]})

    def test_seed_market_order_fills_at_last_close_and_market_close_is_taker(self):
        engine = self.engine()
        state = self.seed(engine)
        self.assertEqual(state["clock"], START + 300)
        self.assertEqual(state["price"], "100.00")

        state = engine.command({
            "op": "order", "id": user_id(1), "side": "long", "kind": "market", "notional": "1000",
        })
        self.assertEqual(len(state["fills"]), 1)
        self.assertEqual(state["fills"][0]["role"], "entry")
        self.assertEqual(state["fills"][0]["price"], "100.00")
        self.assertEqual(state["fills"][0]["fee"], "0.50000000")
        self.assertEqual(state["positions"][0]["remaining"], "10.00000000")

        state = engine.command({
            "op": "close", "id": user_id(2), "position_id": user_id(1), "fraction": "1",
        })
        self.assertEqual(len(state["positions"]), 0)
        self.assertEqual(state["closed"][0]["net_pnl"], "-1.00000000")
        self.assertEqual(state["closed"][0]["fees"], "1.00000000")
        self.assertEqual([fill["role"] for fill in state["fills"]], ["entry", "manual_close"])
        self.assertEqual(state["ledger"]["matched"], True)

    def test_eight_decimal_prices_and_sizes_survive_native_conversion(self):
        engine = ReplayEngine({
            "symbol": "BTC-USDT-SWAP", "cash": "100", "period_seconds": 300,
            "price_precision": 8, "size_precision": 8,
        })
        self.engines.append(engine)
        state = engine.command({"op": "advance", "bars": [bar(START, "0.12345678", "0.12345678", "0.12345678", "0.12345678")]})
        self.assertEqual(state["price"], "0.12345678")
        state = engine.command({
            "op": "order", "id": user_id(5), "side": "long", "kind": "market", "notional": "10",
        })
        self.assertEqual(state["fills"][0]["price"], "0.12345678")
        self.assertEqual(state["positions"][0]["quantity"], "81.00000664")

    def test_resting_limits_fill_only_on_next_bar_and_same_price_orders_are_independent(self):
        engine = self.engine()
        self.seed(engine)
        items = [
            {"id": user_id(10), "side": "long", "kind": "limit", "notional": "990", "price": "99"},
            {"id": user_id(11), "side": "long", "kind": "limit", "notional": "495", "price": "99"},
        ]
        state = engine.command({"op": "orders", "items": items})
        self.assertEqual(len(state["positions"]), 0)
        self.assertEqual(len(state["fills"]), 0)
        self.assertEqual([row["status"] for row in state["orders"]], ["open", "open"])

        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 101, 98, 100)]})
        self.assertEqual({pos["id"] for pos in state["positions"]}, {user_id(10), user_id(11)})
        self.assertEqual([fill["price"] for fill in state["fills"]], ["99.00", "99.00"])
        self.assertEqual([fill["fee"] for fill in state["fills"]], ["0.19800000", "0.09900000"])

    def test_ohlc_high_precedes_low_and_native_limit_target_cancels_stop(self):
        engine = self.engine()
        self.seed(engine)
        state = engine.command({
            "op": "order", "id": user_id(20), "side": "long", "kind": "market", "notional": "1000",
            "stop": "95", "target": "105",
        })
        stop_id = state["positions"][0]["stop_order_id"]
        target_id = state["positions"][0]["target_order_id"]

        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 110, 90, 100)]})
        self.assertEqual(state["positions"], [])
        self.assertEqual([fill["role"] for fill in state["fills"]], ["entry", "target"])
        self.assertEqual(state["fills"][-1]["price"], "105.00")
        self.assertEqual(state["fills"][-1]["fee"], "0.21000000")
        self.assertEqual(state["closed"][0]["reason"], "target")
        self.assertNotIn(stop_id, {str(order.client_order_id) for order in engine._engine.cache.orders_open()})
        self.assertNotIn(target_id, {str(order.client_order_id) for order in engine._engine.cache.orders_open()})

    def test_stop_market_fills_at_stop_and_pays_taker_fee(self):
        engine = self.engine()
        self.seed(engine)
        engine.command({
            "op": "order", "id": user_id(25), "side": "long", "kind": "market", "notional": "1000",
            "stop": "95", "target": "110",
        })
        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 104, 90, 100)]})
        self.assertEqual(state["positions"], [])
        stop_fill = state["fills"][-1]
        self.assertEqual(stop_fill["role"], "stop")
        self.assertEqual(stop_fill["price"], "95.00")
        self.assertEqual(stop_fill["fee"], "0.47500000")
        self.assertEqual(state["closed"][0]["reason"], "stop")
        self.assertEqual(state["closed"][0]["net_pnl"], "-50.97500000")

    def test_partial_close_replaces_both_protections_at_remaining_size(self):
        engine = self.engine()
        self.seed(engine)
        state = engine.command({
            "op": "order", "id": user_id(30), "side": "long", "kind": "market", "notional": "1000",
            "stop": "95", "target": "110",
        })
        old_ids = {state["positions"][0]["stop_order_id"], state["positions"][0]["target_order_id"]}
        state = engine.command({
            "op": "close", "id": user_id(31), "position_id": user_id(30), "fraction": "0.5",
        })
        pos = state["positions"][0]
        self.assertEqual(pos["remaining"], "5.00000000")
        new_ids = {pos["stop_order_id"], pos["target_order_id"]}
        self.assertTrue(old_ids.isdisjoint(new_ids))
        native_open = {str(order.client_order_id): order for order in engine._engine.cache.orders_open()}
        self.assertTrue(new_ids.issubset(native_open))
        self.assertTrue(old_ids.isdisjoint(native_open))
        self.assertEqual({str(native_open[key].quantity) for key in new_ids}, {"5.00000000"})

        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 111, 99, 109)]})
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["closed"][0]["quantity"], "10.00000000")
        self.assertEqual(state["closed"][0]["gross_pnl"], "50.00000000")
        self.assertEqual(state["closed"][0]["reason"], "target")
        self.assertEqual(len(engine._engine.cache.orders_open()), 0)

    def test_one_x_reserve_prevalidates_entire_two_sided_batch(self):
        engine = self.engine()
        self.seed(engine)
        engine.command({
            "op": "order", "id": user_id(40), "side": "long", "kind": "market", "notional": "8000",
        })
        before = engine.snapshot()
        with self.assertRaisesRegex(ValueError, "insufficient 1x available capital"):
            engine.command({"op": "orders", "items": [
                {"id": user_id(41), "side": "long", "kind": "market", "notional": "1000"},
                {"id": user_id(42), "side": "short", "kind": "market", "notional": "1000"},
            ]})
        after = engine.snapshot()
        self.assertEqual(len(after["positions"]), len(before["positions"]))
        self.assertEqual(len(after["orders"]), len(before["orders"]))
        self.assertEqual(len(after["fills"]), len(before["fills"]))

    def test_hedged_long_and_short_positions_are_independent(self):
        engine = self.engine()
        self.seed(engine)
        state = engine.command({"op": "orders", "items": [
            {"id": user_id(45), "side": "long", "kind": "market", "notional": "1000"},
            {"id": user_id(46), "side": "short", "kind": "market", "notional": "1000"},
        ]})
        self.assertEqual({(pos["id"], pos["side"]) for pos in state["positions"]}, {
            (user_id(45), "long"), (user_id(46), "short"),
        })
        self.assertEqual(len(state["fills"]), 2)

    def test_nonpositive_equity_blocks_new_orders_without_fake_liquidation(self):
        engine = self.engine()
        self.seed(engine)
        engine.command({
            "op": "order", "id": user_id(47), "side": "short", "kind": "market", "notional": "9500",
        })
        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 300, 100, 300)]})
        self.assertLess(float(state["equity"]), 0)
        self.assertEqual([pos["id"] for pos in state["positions"]], [user_id(47)])
        with self.assertRaisesRegex(ValueError, "equity is nonpositive"):
            engine.command({
                "op": "order", "id": user_id(48), "side": "long", "kind": "market", "notional": "1",
            })
        self.assertEqual([pos["id"] for pos in engine.snapshot()["positions"]], [user_id(47)])

    def test_floating_profit_does_not_expand_one_x_cash_reserve(self):
        engine = self.engine()
        self.seed(engine)
        before = engine.command({
            "op": "order", "id": user_id(49), "side": "long", "kind": "market", "notional": "1000",
        })["available"]
        after = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 200, 100, 200)]})["available"]
        self.assertEqual(before, after)


    def test_expiry_is_cancelled_before_the_expiry_containing_bar(self):
        engine = self.engine()
        self.seed(engine)
        state = engine.command({
            "op": "order", "id": user_id(50), "side": "long", "kind": "limit", "notional": "1000",
            "price": "95", "expires": START + 300 + 299,
        })
        self.assertEqual(state["orders"][0]["status"], "open")
        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 101, 90, 100)]})
        self.assertEqual(state["orders"][0]["status"], "expired")
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["fills"], [])

    def test_stable_ids_replay_to_the_same_state_and_duplicate_order_is_idempotent(self):
        commands = [
            {"op": "advance", "bars": [bar(START, 100, 100, 100, 100)]},
            {"op": "order", "id": user_id(60), "side": "short", "kind": "market", "notional": "1000",
             "stop": "105", "target": "95"},
            {"op": "close", "id": user_id(61), "position_id": user_id(60), "fraction": "0.5"},
        ]
        first = self.engine()
        first_result = None
        for command in commands:
            first_result = first.command(command)
        duplicate = first.command(commands[1])
        self.assertEqual(len(duplicate["fills"]), len(first_result["fills"]))

        second = self.engine()
        second_result = None
        for command in commands:
            second_result = second.command(command)
        self.assertEqual([f["id"] for f in first_result["fills"]], [f["id"] for f in second_result["fills"]])
        self.assertEqual([p["id"] for p in first_result["positions"]], [p["id"] for p in second_result["positions"]])
        self.assertEqual(first_result["positions"][0]["stop_order_id"], second_result["positions"][0]["stop_order_id"])
        self.assertEqual(first_result["positions"][0]["target_order_id"], second_result["positions"][0]["target_order_id"])

    def test_two_same_price_targets_fill_independently(self):
        engine = self.engine()
        self.seed(engine)
        state = engine.command({"op": "orders", "items": [
            {"id": user_id(70), "side": "long", "kind": "market", "notional": "1000", "stop": "95", "target": "105"},
            {"id": user_id(71), "side": "long", "kind": "market", "notional": "500", "stop": "95", "target": "105"},
        ]})
        self.assertEqual(len(state["positions"]), 2)
        state = engine.command({"op": "advance", "bars": [bar(START + 300, 100, 110, 90, 100)]})
        self.assertEqual(state["positions"], [])
        targets = [fill for fill in state["fills"] if fill["role"] == "target"]
        self.assertEqual({fill["position_id"] for fill in targets}, {user_id(70), user_id(71)})
        self.assertEqual([fill["price"] for fill in targets], ["105.00", "105.00"])
        self.assertEqual(len(engine._engine.cache.orders_open()), 0)

    def test_jsonl_worker_emits_only_one_json_response_per_request(self):
        requests = [
            {"op": "init", "config": {
                "symbol": "BTC-USDT-SWAP", "cash": "10000", "period_seconds": 300,
                "price_precision": 8, "size_precision": 8,
            }},
            {"op": "advance", "bars": [bar(START, 100, 100, 100, 100)]},
            {"op": "order", "id": user_id(80), "side": "long", "kind": "limit", "notional": "100",
             "price": "100"},
            {"op": "shutdown"},
        ]
        root = Path(__file__).resolve().parents[2]
        process = subprocess.run(
            [sys.executable, "-m", "yoyo.layers.l3_backtest.replay_native"],
            input="\n".join(json.dumps(request) for request in requests) + "\n",
            text=True,
            capture_output=True,
            cwd=root,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(responses), len(requests))
        self.assertTrue(all(set(response) in ({"ok", "state"}, {"ok", "error"}) for response in responses))
        self.assertTrue(all(isinstance(response["ok"], bool) for response in responses))
        self.assertTrue(responses[0]["ok"] and responses[1]["ok"] and not responses[2]["ok"] and responses[3]["ok"])
        self.assertIn("限价单", responses[2]["error"])


if __name__ == "__main__":
    unittest.main()

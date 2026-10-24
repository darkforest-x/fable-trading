"""Causal Monday MA6 preparation; fills remain owned by Nautilus."""
import numpy as np
import pandas as pd
import unittest

from yoyo.evaluation.monday_ma6_signals import prepare
from yoyo.evaluation.monday_parameter_signals import base_context


def frame():
    start = pd.Timestamp("2025-12-29T05:00:00Z")  # NY Monday 00:00
    end = pd.Timestamp("2026-01-12T05:15:00Z")
    index = pd.date_range(start, end, freq="5min", inclusive="left")
    n = len(index)
    out = pd.DataFrame(dict(open=np.full(n, 99.6), high=np.full(n, 99.8),
                            low=np.full(n, 99.4), close=np.full(n, 99.6),
                            count=np.full(n, 5)), index=index)

    monday = (index >= pd.Timestamp("2026-01-05T05:00:00Z")) & (index < pd.Timestamp("2026-01-06T05:00:00Z"))
    out.loc[monday, ["open", "high", "low", "close"]] = [100.1, 101.0, 100.0, 100.1]

    after_monday = index >= pd.Timestamp("2026-01-06T05:00:00Z")
    out.loc[after_monday, ["open", "high", "low", "close"]] = [99.8, 99.99, 99.75, 99.95]

    touch = pd.Timestamp("2026-01-07T05:00:00Z")
    after_touch = index >= touch
    out.loc[after_touch, ["open", "high", "low", "close"]] = [99.8, 99.99, 99.75, 99.98]
    out.loc[touch, ["open", "high", "low", "close"]] = [99.8, 99.99, 99.3, 99.98]
    return out


class MondayMa6SignalTests(unittest.TestCase):
    def test_first_post_monday_touch_confirms_after_four_complete_favorable_15m_closes(self):
        f = frame()
        result = prepare(f, "BTCUSDT", {"seed": 17})
        opp = result["opportunities"].query("week == '2026-01-05' and side == 1").iloc[0]

        self.assertEqual(opp.status, "confirmed")
        self.assertEqual(opp.confirm_15m_time, "2026-01-07 05:45:00+00:00")
        self.assertGreater(opp.eligible_random_after_confirm, 0)
        orders = result["orders"]
        signal = orders[orders.population.eq("signal")]
        self.assertEqual(len(signal), 1)
        row = signal.iloc[0]
        self.assertLess(row.entry, row.cancel_boundary)
        self.assertGreater(row.entry, row.stop)
        self.assertLess(row.entry_i, row.expire_i)
        self.assertLess(row.expire_i, row.end_i)
        self.assertEqual(row.target_price, row.target_monday)
        self.assertEqual(row.trade_id, row.pair_id)
        self.assertEqual(set(orders.population), {"signal", "random"})
        self.assertTrue((orders.pair_id == row.pair_id).all())
        self.assertEqual(orders[orders.population.eq("random")].pair_id.nunique(), 1)
        self.assertEqual(len(orders[orders.population.eq("random")]), 3)
        self.assertFalse(any(c in orders.columns for c in ("pnl", "return_r", "exit_i")))

    def test_range_return_in_confirmation_candle_blocks_order_before_submission(self):
        f = frame()
        # The fourth candidate 15m candle closes favorably, but its middle 5m
        # high reaches the Monday low before that confirmation close.
        f.loc[pd.Timestamp("2026-01-07T05:50:00Z"), ["open", "high", "low", "close"]] = [
            99.9, 100.05, 99.8, 99.95]
        result = prepare(f, "BTCUSDT", {"seed": 17})
        opp = result["opportunities"].query("week == '2026-01-05' and side == 1").iloc[0]

        self.assertEqual(opp.status, "invalid")
        self.assertEqual(opp.reason, "returned_to_monday_range")
        self.assertEqual(opp.invalid_i, f.index.get_loc(pd.Timestamp("2026-01-07T05:50:00Z")))
        self.assertTrue(result["orders"].empty)

    def test_confirmation_cannot_bridge_a_missing_15m_interval(self):
        f = frame().drop(pd.date_range("2026-01-07T05:15:00Z", periods=3, freq="5min"))
        result = prepare(f, "BTCUSDT", {"seed": 17})
        opp = result["opportunities"].query("week == '2026-01-05' and side == 1").iloc[0]

        self.assertNotEqual(opp.status, "confirmed")
        self.assertTrue(result["orders"].empty)

    def test_prefix_extension_does_not_change_earlier_signal_geometry(self):
        f = frame()
        whole_result = prepare(f, "BTCUSDT", {"seed": 17})
        cut = f.loc[:pd.Timestamp("2026-01-07T06:00:00Z")]
        prefix_result = prepare(cut, "BTCUSDT", {"seed": 17})
        whole = whole_result["opportunities"]
        prefix = prefix_result["opportunities"]
        a = whole.query("week == '2026-01-05' and side == 1").iloc[0]
        b = prefix.query("week == '2026-01-05' and side == 1").iloc[0]
        self.assertEqual((a.touch_i, a.confirm_i, a.level), (b.touch_i, b.confirm_i, b.level))
        whole_order = whole_result["orders"].query("population == 'signal'").iloc[0]
        prefix_order = prefix_result["orders"].query("population == 'signal'").iloc[0]
        self.assertEqual((whole_order.entry_i, whole_order.entry, whole_order.stop),
                         (prefix_order.entry_i, prefix_order.entry, prefix_order.stop))

    def test_submission_bucket_and_geometry_ignore_future_open_and_ohlc(self):
        original = frame()
        baseline = prepare(original, "BTCUSDT", {"seed": 17})
        baseline_signal = baseline["orders"].query("population == 'signal'").iloc[0]

        changed = original.copy()
        future = changed.index > changed.index[int(baseline_signal.entry_i)]
        changed.loc[future, ["open", "high", "low", "close"]] = [
            150.0, 150.2, 149.8, 150.1]
        perturbed = prepare(changed, "BTCUSDT", {"seed": 17})
        perturbed_signal = perturbed["orders"].query("population == 'signal'").iloc[0]

        self.assertNotEqual(changed.open.iloc[int(baseline_signal.entry_i) + 1],
                            original.open.iloc[int(baseline_signal.entry_i) + 1])
        baseline_next_bucket = base_context(original, "BTCUSDT")["bucket"][int(baseline_signal.entry_i) + 1]
        perturbed_next_bucket = base_context(changed, "BTCUSDT")["bucket"][int(baseline_signal.entry_i) + 1]
        self.assertNotEqual(baseline_next_bucket, perturbed_next_bucket)
        for column in ("bucket", "causalvolbucket", "entry", "stop", "risk",
                       "target_monday", "target_far", "cancel_boundary"):
            self.assertEqual(baseline_signal[column], perturbed_signal[column], column)

    def test_crypto_unknown_gap_after_touch_stops_monitoring(self):
        f = frame()
        missing = pd.date_range("2026-01-07T05:15:00Z", periods=3, freq="5min")
        f = f.drop(missing)
        result = prepare(f, "BTCUSDT", {"seed": 17})
        opp = result["opportunities"].query("week == '2026-01-05' and side == 1").iloc[0]

        self.assertEqual(opp.status, "invalid_unobservable")
        self.assertEqual(opp.reason, "source_gap")
        self.assertEqual(opp.confirm_i, -1)
        self.assertTrue(result["orders"].empty)

    def test_post_confirmation_third_extreme_keeps_opportunity_confirmed_and_cancels(self):
        f = frame()
        for when, low in [("2026-01-07T06:00:00Z", 99.2),
                          ("2026-01-07T06:15:00Z", 99.1),
                          ("2026-01-07T06:30:00Z", 99.0)]:
            f.loc[pd.Timestamp(when), "low"] = low
        result = prepare(f, "BTCUSDT", {"seed": 17})
        opp = result["opportunities"].query("week == '2026-01-05' and side == 1").iloc[0]
        signal_id = result["orders"].query("population == 'signal'").iloc[0].trade_id
        cancels = result["updates"].query("trade_id == @signal_id and action == 'cancel'")

        self.assertEqual(opp.status, "confirmed")
        self.assertEqual(opp.post_confirm_invalid_reason, "third_new_extreme")
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels.iloc[0].status, "third_new_extreme")
        self.assertEqual(cancels.iloc[0].bar_i,
                         f.index.get_loc(pd.Timestamp("2026-01-07T06:40:00Z")))

    def test_gold_normal_session_gap_does_not_cancel_pending_order(self):
        f = frame()
        # Reopen one hour after the submitted confirmation order. Gold MA
        # history carries across this session gap; confirmation itself already
        # completed on four continuous bars before the close.
        missing = pd.date_range("2026-01-07T06:00:00Z", periods=12, freq="5min")
        f = f.drop(missing)
        result = prepare(f, "XAUUSD", {"seed": 17})
        signal = result["orders"].query("population == 'signal'")

        self.assertEqual(len(signal), 1)
        updates = result["updates"]
        self.assertFalse(updates.action.eq("cancel").any())
        self.assertTrue((updates.action == "reprice").any())
        reopen_i = f.index.get_loc(pd.Timestamp("2026-01-07T07:00:00Z"))
        self.assertTrue((updates.bar_i >= reopen_i).any())

if __name__ == "__main__":
    unittest.main()

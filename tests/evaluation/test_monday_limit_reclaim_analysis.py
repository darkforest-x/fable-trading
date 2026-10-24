"""Synthetic analysis checks; no candles or framework replay are used."""
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import pandas as pd

from yoyo.evaluation.monday_limit_reclaim_analysis import (
    _cell_stats,
    _common_scope_name,
    _attempt_metrics,
    _matched_metrics,
    COMMON6,
    _scope_coverage,
    _scope_frames,
    _write_chinese_metrics,
    build_strict_matches,
    prepare_orders,
    prepare_trades,
    trade_stats,
)


def trade_row(
    trade_id,
    population="signal",
    control_id=0,
    pair_id="pair",
    entry="2022-12-20T15:00:00Z",
    exit_="2022-12-20T15:10:00Z",
    reason=5,
    closed=True,
    gross_bp=3.0,
    fee_bp=5.0,
    return_bp=-2.0,
    symbol="SOLUSDT",
    asset_group="ALTCOINS",
    execution_minutes=1,
    source_group=None,
):
    return {
        "trade_id": trade_id,
        "pair_id": pair_id,
        "symbol": symbol,
        "asset_group": asset_group,
        "source_group": source_group or ("crypto_1m" if execution_minutes == 1 else "crypto_5m"),
        "week": "2022-12-19",
        "side": 1,
        "execution_minutes": execution_minutes,
        "reclaim_minutes": 5,
        "exit_mode": "range_0.5",
        "population": population,
        "control_id": control_id,
        "bucket": -20,
        "entry_interval_open": entry,
        "framework_exit_time": exit_,
        "reason": reason,
        "closed": closed,
        "basis": "net",
        "gross_bp": gross_bp,
        "fee_bp": fee_bp,
        "return_bp": return_bp,
        "reclaim_status": "reclaimed" if reason == 5 else "pending",
        "touch_wick_only_reclaim": 1.0,
        "next_close_inside": 0.0,
        "holding_minutes_upper": 10.0,
    }


def four_trade_pair(**overrides):
    rows = [trade_row("s", population="signal", **overrides)]
    rows.extend(
        trade_row(f"c{i}", population="random", control_id=i, return_bp=float(i),
                  gross_bp=float(i + 2), fee_bp=1.0, **overrides)
        for i in (1, 2, 3)
    )
    return rows


class MondayLimitReclaimAnalysisTests(unittest.TestCase):
    def test_fee_can_make_an_entry_stop_a_net_loss_and_censor_is_not_a_win(self):
        rows = [
            trade_row("loss-after-fee", gross_bp=3.0, fee_bp=5.0, return_bp=-2.0),
            trade_row("unresolved", reason=0, closed=False, gross_bp=80.0,
                      fee_bp=0.0, return_bp=80.0),
        ]
        stats = trade_stats(prepare_trades(pd.DataFrame(rows)))
        self.assertEqual(stats["filled_n"], 2)
        self.assertEqual(stats["closed_n"], 1)
        self.assertEqual(stats["censored_n"], 1)
        self.assertEqual(stats["win_n"], 0)
        self.assertEqual(stats["loss_n"], 1)
        self.assertEqual(stats["entry_stop_n"], 1)
        self.assertEqual(stats["gross_win_rate"], 1.0)
        self.assertEqual(stats["net_win_rate"], 0.0)
        self.assertEqual(stats["profit_factor"], 0.0)

    def test_invalid_native_timestamps_fail_closed(self):
        row = trade_row("bad-date")
        row["entry_interval_open"] = "not-a-timestamp"
        with self.assertRaisesRegex(ValueError, "invalid entry/exit timestamps"):
            prepare_trades(pd.DataFrame([row]))

    def test_strict_match_rejects_wrong_actual_week_and_fewer_than_three_controls(self):
        good = prepare_trades(pd.DataFrame(four_trade_pair()))
        matched = build_strict_matches(good)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched.iloc[0].control_1_return_bp, 1.0)
        self.assertEqual(matched.iloc[0].control_2_return_bp, 2.0)
        self.assertEqual(matched.iloc[0].control_3_return_bp, 3.0)
        self.assertEqual(matched.iloc[0].delta_return_bp, -4.0)

        wrong_week = pd.DataFrame(four_trade_pair())
        wrong_week.loc[wrong_week.trade_id.eq("c3"), "entry_interval_open"] = "2022-12-27T15:00:00Z"
        wrong_week = prepare_trades(wrong_week)
        self.assertTrue(build_strict_matches(wrong_week).empty)

        incomplete = prepare_trades(pd.DataFrame(four_trade_pair()).iloc[:3])
        self.assertTrue(build_strict_matches(incomplete).empty)

    def test_common6_match_requires_all_four_events_inside_the_shared_range(self):
        rows = four_trade_pair(
            entry="2022-11-01T00:30:00Z",
            exit_="2022-11-01T01:30:00Z",
        )
        rows[-1]["entry_interval_open"] = "2022-10-31T23:00:00Z"
        matched = build_strict_matches(prepare_trades(pd.DataFrame(rows)))
        self.assertEqual(len(matched), 1)
        self.assertFalse(bool(matched.iloc[0].common6_in_range))

    def test_split_period_excludes_a_closed_position_crossing_the_cut(self):
        rows = four_trade_pair(
            entry="2022-12-31T23:55:00Z",
            exit_="2023-01-01T00:00:00Z",
        )
        matches = build_strict_matches(prepare_trades(pd.DataFrame(rows)))
        self.assertEqual(len(matches), 1)
        self.assertTrue(bool(matches.iloc[0].cross_cut))
        self.assertEqual(matches.iloc[0].entry_period, "pre2023")
        self.assertEqual(_matched_metrics(matches, "ALTCOINS", "all")[0]["strict_match_n"], 1)
        self.assertEqual(_matched_metrics(matches, "ALTCOINS", "pre2023"), [])

    def test_altcoin_matrix_combines_one_and_five_minute_sources(self):
        rows = [
            trade_row("sol", symbol="SOLUSDT", execution_minutes=1),
            trade_row("shib", symbol="1000SHIBUSDT", execution_minutes=5,
                      source_group="crypto_5m"),
        ]
        prepared = prepare_trades(pd.DataFrame(rows))
        catalog = [
            {"symbol": "SOLUSDT", "execution_minutes": 1},
            {"symbol": "1000SHIBUSDT", "execution_minutes": 5},
        ]
        result = _cell_stats(prepared, "ALTCOINS", "all", catalog)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_minutes"], "mixed")
        self.assertEqual(result[0]["assets_present_n"], 2)
        self.assertEqual(result[0]["assets_expected_n"], 630)
        self.assertFalse(result[0]["scope_complete"])

    def test_crypto_total_excludes_gold_and_common6_partial_is_explicit(self):
        raw_rows = [
            trade_row("btc", symbol="BTCUSDT", asset_group="BTC", execution_minutes=1),
            trade_row("gold", symbol="XAUUSD", asset_group="XAUUSD", execution_minutes=1,
                      source_group="gold_1m", gross_bp=12.0, fee_bp=0.0, return_bp=12.0),
        ]
        raw_rows[1]["basis"] = "gross"
        rows = prepare_trades(pd.DataFrame(raw_rows))
        scopes = dict(_scope_frames(rows, "COMMON6_CRYPTO_PARTIAL"))
        self.assertEqual(set(scopes), {
            "CRYPTO_ALL", "BTC", "ETH", "XAUUSD", "ALTCOINS", "COMMON6_CRYPTO_PARTIAL"
        })
        self.assertEqual(set(scopes["CRYPTO_ALL"]["symbol"]), {"BTCUSDT"})
        self.assertEqual(set(scopes["XAUUSD"]["symbol"]), {"XAUUSD"})
        catalog = [{"symbol": "BTCUSDT", "execution_minutes": 1}, {"symbol": "XAUUSD", "execution_minutes": 1}]
        crypto_stats = _cell_stats(scopes["CRYPTO_ALL"], "CRYPTO_ALL", "all", catalog)[0]
        gold_stats = _cell_stats(scopes["XAUUSD"], "XAUUSD", "all", catalog)[0]
        self.assertEqual(crypto_stats["return_basis"], "net")
        self.assertEqual(gold_stats["return_basis"], "gross")
        self.assertEqual(crypto_stats["mean_return_bp"], -2.0)
        self.assertEqual(gold_stats["mean_return_bp"], 12.0)
        self.assertEqual(_scope_coverage("CRYPTO_ALL", 1, catalog), (1, 6, False))
        self.assertEqual(_scope_coverage("CRYPTO_ALL", 5, catalog), (1, 632, False))
        self.assertEqual(_scope_coverage("COMMON6_CRYPTO_PARTIAL", 5, catalog), (1, 6, False))
        self.assertEqual(_common_scope_name(catalog), "COMMON6_CRYPTO_PARTIAL")
        full_catalog = [{"symbol": symbol, "execution_minutes": 1} for symbol in COMMON6]
        self.assertEqual(_common_scope_name(full_catalog), "COMMON6_CRYPTO")
        full_catalog[2]["execution_minutes"] = 5
        self.assertEqual(_common_scope_name(full_catalog), "COMMON6_CRYPTO_PARTIAL")

    def test_expired_attempt_is_zero_exposure_but_filled_censor_is_unknown(self):
        ids = ["s", "c1", "c2", "c3"]
        orders = pd.DataFrame([
            {
                "trade_id": trade_id,
                "pair_id": "attempt-pair",
                "symbol": "SOLUSDT",
                "asset_group": "ALTCOINS",
                "source_group": "crypto_1m",
                "week": "2022-12-19",
                "side": 1,
                "execution_minutes": 1,
                "reclaim_minutes": 5,
                "exit_mode": "range_0.5",
                "population": "signal" if trade_id == "s" else "random",
                "control_id": 0 if trade_id == "s" else int(trade_id[1]),
                "bucket": -20,
                "submission_time": "2022-12-20T15:00:00Z",
                "intended_entry_time": "2022-12-20T15:01:00Z",
            }
            for trade_id in ids
        ])
        audit = [
            {"trade_id": "s", "status": "expired"},
            {"trade_id": "c1", "status": "expired"},
            {"trade_id": "c2", "status": "filled"},
            {"trade_id": "c3", "status": "expired"},
        ]
        trades = prepare_trades(pd.DataFrame([
            trade_row("c2", population="random", control_id=2, pair_id="attempt-pair",
                      reason=0, closed=False, return_bp=900.0),
        ]))
        counts, pairs = prepare_orders(orders, audit, trades)
        self.assertEqual(int(counts.loc[counts.attempt_status.eq("expired"), "attempt_n"].sum()), 3)
        self.assertEqual(len(pairs), 1)
        self.assertFalse(bool(pairs.iloc[0].known))
        self.assertEqual(pairs.iloc[0].unknown_censored_n, 1)
        self.assertTrue(pd.isna(pairs.iloc[0].control_attempt_mean_bp))

    def test_attempt_summary_merges_altcoin_source_minutes_in_one_cell(self):
        count_parts = []
        pair_parts = []
        for symbol, execution_minutes, source_group in (
            ("SOLUSDT", 1, "crypto_1m"),
            ("1000SHIBUSDT", 5, "crypto_5m"),
        ):
            order_rows = []
            audit = []
            for trade_id, population, control_id in (
                ("s", "signal", 0), ("c1", "random", 1),
                ("c2", "random", 2), ("c3", "random", 3),
            ):
                order_rows.append({
                    "trade_id": trade_id,
                    "pair_id": "attempt-pair",
                    "symbol": symbol,
                    "asset_group": "ALTCOINS",
                    "source_group": source_group,
                    "week": "2022-12-19",
                    "side": 1,
                    "execution_minutes": execution_minutes,
                    "reclaim_minutes": 5,
                    "exit_mode": "range_0.5",
                    "population": population,
                    "control_id": control_id,
                    "bucket": -20,
                    "submission_time": "2022-12-20T15:00:00Z",
                    "intended_entry_time": "2022-12-20T15:01:00Z",
                })
                audit.append({"trade_id": trade_id, "status": "expired"})
            counts, pairs = prepare_orders(pd.DataFrame(order_rows), audit, pd.DataFrame())
            count_parts.append(counts)
            pair_parts.append(pairs)

        attempts = _attempt_metrics(
            pd.concat(count_parts, ignore_index=True),
            pd.concat(pair_parts, ignore_index=True),
            "COMMON6_CRYPTO_PARTIAL",
        )
        alt_cell = attempts.loc[
            attempts.analysis_scope.eq("ALTCOINS")
            & attempts.period.eq("all")
            & attempts.confirm_minutes.eq(5)
            & attempts.target.eq("range_0.5")
        ]
        self.assertEqual(len(alt_cell), 1)
        self.assertEqual(alt_cell.iloc[0].source_minutes, "mixed")
        self.assertEqual(alt_cell.iloc[0].attempt_known_pair_n, 2)
        self.assertEqual(alt_cell.iloc[0].signal_expired_attempt_n, 2)
        self.assertEqual(alt_cell.iloc[0].control_expired_attempt_n, 6)

    def test_chinese_core_metrics_translate_scope_period_target_and_rate_units(self):
        metrics = pd.DataFrame([{
            "analysis_scope": "CRYPTO_ALL",
            "period": "post2023",
            "target": "opposite_1618",
            "net_win_rate": 0.25,
            "censored_n": 2,
            "entry_stop_n": 1,
        }])
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "core.csv"
            _write_chinese_metrics(metrics, output_path)
            translated = pd.read_csv(output_path)
        self.assertEqual(translated.loc[0, "范围"], "加密资产总计")
        self.assertEqual(translated.loc[0, "时段"], "2023年起")
        self.assertEqual(translated.loc[0, "目标"], "对侧1.618")
        self.assertEqual(translated.loc[0, "净胜率(%)"], 25.0)
        self.assertEqual(translated.loc[0, "未决数"], 2)
        self.assertEqual(translated.loc[0, "移到开仓价后止损数"], 1)


if __name__ == "__main__":
    unittest.main()

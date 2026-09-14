"""Synthetic V9 boundary, reference-cost, causality and serial-reversal checks."""
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation.spike_exit_policy_study import StreamContext
from yoyo.evaluation.spike_v9 import VERSION, entry_decision, replay_v9, v9_admissions


def context(start="2025-01-01", asset="ETH"):
    index = pd.date_range(start, periods=40, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100.,
                         "atr": 1., "rv": 2., "s20": 99., "e20": 99.,
                         "ropeHigh": 99., "ropeLow": 101., "md": 1., "sb": 0.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[15], "long_signal"] = True
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "data_gap": pd.Series(False, index=index), "tick": .01}
    return StreamContext(Path("."), "synthetic", {}, cache,
                         pd.DataFrame(columns=["signal_bar_open", "signal_i"]), 60,
                         {"venue": "synthetic", "symbol": "ETHUSDC", "asset": asset,
                          "asset_type": "crypto_perpetual", "timeframe_min": 60})


@pytest.mark.parametrize("asset,expected", [("USDC", False), ("usdc", False),
                                           ("ETH", True), ("USDCX", True),
                                           ("", False), (None, False)])
def test_exact_base_currency_not_quote_or_substring(asset, expected):
    result = entry_decision(asset, 1., pd.Timestamp("2025-01-06", tz="UTC"))
    assert result["v9_bundle_allowed"] is expected


@pytest.mark.parametrize("rv,expected", [(49.99, True), (50., True), (50.001, False),
                                        (float("nan"), False), (float("inf"), False),
                                        (-1., False), (None, False)])
def test_volume_boundary_and_unknown_are_explicit(rv, expected):
    assert entry_decision("ETH", rv, pd.Timestamp("2025-01-06", tz="UTC"))["v9_bundle_allowed"] is expected


@pytest.mark.parametrize("stamp,expected", [("2025-01-04 23:59:59Z", True),
                                            ("2025-01-05 00:00:00Z", False),
                                            ("2025-01-05 23:59:59Z", False),
                                            ("2025-01-06 00:00:00Z", True),
                                            ("2025-01-05 07:59:59+08:00", True),
                                            ("2025-01-05 08:00:00+08:00", False),
                                            ("2025-01-06 07:59:59+08:00", False),
                                            ("2025-01-06 08:00:00+08:00", True)])
def test_sunday_uses_explicit_utc_scheduled_open(stamp, expected):
    assert entry_decision("ETH", 2., pd.Timestamp(stamp))["v9_bundle_allowed"] is expected


@pytest.mark.parametrize("stamp", [pd.NaT, pd.Timestamp("2025-01-06")])
def test_missing_or_naive_clock_does_not_pass(stamp):
    row = entry_decision("ETH", 2., stamp)
    assert not row["v9_bundle_allowed"]
    assert row["v9_bundle_reasons"] == "scheduled_open_unknown"


def test_all_reasons_are_kept_when_filters_overlap():
    row = entry_decision("USDC", 51., pd.Timestamp("2025-01-05", tz="UTC"))
    assert row["v9_bundle_reasons"].split("|") == ["base_usdc", "volume_ratio_gt50", "scheduled_open_utc_sunday"]


def test_signal_saturday_close_into_sunday_is_filtered():
    c = context(start="2025-01-04 08:00")
    row = v9_admissions(c).iloc[15]  # Saturday 23:00 bar -> Sunday 00:00 next open.
    assert row.v8 and not row.v9
    assert row.v9_reason == "scheduled_open_utc_sunday"


def test_reference_cost_is_causal_and_not_next_open_execution_cost():
    c = context()
    bars = c.cache["bars"]
    bars.loc[bars.index[16], "open"] = 103.
    row = v9_admissions(c).iloc[15]
    assert row.v9
    assert row.asset_type == "crypto_perpetual"
    assert row.reference_price == 100.
    assert row.reference_initial_stop == 98.
    assert row.reference_risk_fraction == pytest.approx(.02)
    assert row.reference_cost_r == pytest.approx(.1)
    assert row.risk_basis == "confirmation_close_reference_not_fill"


def test_filtered_opposite_still_closes_existing_trade():
    c = context()
    index = c.cache["bars"].index
    c.cache["signals"].loc[index[20], "short_signal"] = True
    c.cache["bars"].loc[index[20], "rv"] = 51.
    trades, _, _, decisions = replay_v9(c)
    assert decisions.v9.iloc[15] and not decisions.v9.iloc[20]
    assert len(trades) == 1
    assert trades.exit_reason.iloc[0] == "opposite_v6_next_open"
    assert trades.exit_time.iloc[0] == index[21]


def test_new_combined_mask_preserves_v8_input_and_blocks_usdc():
    c = context(asset="USDC")
    raw = c.cache["signals"].copy(deep=True)
    bb = c.cache["bb"].copy(deep=True)
    trades, _, _, decisions = replay_v9(c)
    assert decisions.v8.sum() == 1 and decisions.v9.sum() == 0
    assert trades.empty
    pd.testing.assert_frame_equal(raw, c.cache["signals"])
    pd.testing.assert_frame_equal(bb, c.cache["bb"])


def test_future_ohlc_rv_mutation_does_not_change_past_decisions_or_costs():
    c = context()
    before = v9_admissions(c).iloc[:20].copy()
    c.cache["bars"].iloc[25:, c.cache["bars"].columns.get_indexer(["close", "high", "low", "rv"])] = [200., 201., 199., 100.]
    pd.testing.assert_frame_equal(before, v9_admissions(c).iloc[:20])


def test_unknown_risk_after_gap_does_not_invent_a_zero_cost():
    c = context()
    c.cache["data_gap"].iloc[13] = True
    row = v9_admissions(c).iloc[15]
    assert row.risk_status == "reference_unavailable"
    assert pd.isna(row.reference_cost_r)


def test_unmarked_missing_next_bar_cancels_pending_entry_at_arrival():
    c = context(start="2025-01-04 00:00")
    before = v9_admissions(c).iloc[:16].copy()
    old_index = c.cache["bars"].index
    shifted = old_index[:16].append(old_index[16:] + pd.Timedelta(days=1))
    for key in ("bars", "signals", "v1_signals", "bb", "data_gap"):
        c.cache[key].index = shifted
    trades, fills, events, evidence = replay_v9(c)
    # A future gap removes pandas' whole-index frequency annotation only.
    pd.testing.assert_frame_equal(before, evidence.iloc[:16], check_freq=False)
    assert evidence.v9.iloc[15]  # Scheduled Saturday entry was allowed at close.
    assert shifted[16].dayofweek == 6  # Missing bars only become known on Sunday.
    assert trades.empty and fills.empty
    assert not c.cache["data_gap"].any()  # Input metadata remains immutable.
    for frame in (trades, fills, events):
        assert "strategy_version" in frame and "arm" in frame


def test_replay_outputs_identify_v9_without_relabeling_parent_engine():
    c = context()
    trades, fills, events, _ = replay_v9(c)
    assert not trades.empty and not fills.empty
    for frame in (trades, fills, events):
        assert frame.strategy_version.eq(VERSION).all()
        assert frame.arm.eq("v9").all()


def test_pine_retains_original_structural_and_reference_engines():
    root = Path(__file__).resolve().parents[2]
    old = (root / "yoyo/evaluation/pine/spike_burst_v8.pine").read_text()
    new = (root / "yoyo/evaluation/pine/spike_burst_v9.pine").read_text()
    for begin, end in [("// BEGIN LEGACY ENGINE", "// Keep raw opposite confirmations"),
                       ("// BEGIN REFERENCE STATE", "// END REFERENCE STATE"),
                       ("// BEGIN UNCHANGED V2 RISK HELPERS", "// END UNCHANGED V2 RISK HELPERS")]:
        assert old[old.index(begin):old.index(end)] == new[new.index(begin):new.index(end)]
    assert 'dayofweek(time_close, "UTC")' in new
    assert 'str.upper(syminfo.basecurrency)' in new
    assert 'syminfo.currency' not in new
    assert 'confirmedSignal := confirmedSignal and v9EntryAllowed' in new
    assert 'confirmedShortSignal := confirmedShortSignal and v9EntryAllowed' in new
    assert 'shorttitle="SPIKE V9"' in new
    assert new.count('alertcondition(') == old.count('alertcondition(')

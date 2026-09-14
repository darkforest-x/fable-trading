"""Synthetic contracts for SPIKE V1 triple-exit ledger aggregation only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v1_triple_stats as stats


def _rows(net_r: list[float], *, arm: str = "baseline", censored: list[bool] | None = None,
          dedup: list[bool] | None = None, base: str = "BTC") -> pd.DataFrame:
    censored = censored or [False] * len(net_r)
    dedup = dedup or [True] * len(net_r)
    entry = pd.date_range("2025-01-01", periods=len(net_r), freq="D", tz="UTC")
    return pd.DataFrame({"event_id": [f"e{i}" for i in range(len(net_r))], "arm": arm,
                         "entry_time": entry, "exit_time": entry + pd.Timedelta(hours=1), "net_r": net_r,
                         "net_return": np.array(net_r) * .1, "gross_return": np.array(net_r) * .1 + .002,
                         "risk_fraction_at_entry": .1, "mfe_r": 1., "mae_r": .5, "censored": censored,
                         "venue": "binance", "symbol": "BTCUSDT", "base_asset": base, "timeframe_min": 60,
                         "dedup_keep": dedup, "entry_day": entry.strftime("%Y-%m-%d")})


def test_raw_and_dedup_scopes_do_not_replace_an_early_filtered_duplicate() -> None:
    frame = stats.normalize_outcomes(_rows([1., 9.], dedup=[True, False]))
    assert len(stats.select_scope(frame, dedup=False, ex_rave=False)) == 2
    dedup = stats.select_scope(frame, dedup=True, ex_rave=False)
    assert len(dedup) == 1
    assert stats.arm_metrics(dedup)["sum_net_r"] == pytest.approx(1.)


def test_identity_dedup_canonicalizes_denomination_wrappers_and_controls_inherit_parent() -> None:
    first = _rows([1.], arm="baseline", base="1000PEPE").assign(
        event_id="pepe_4h", entry_time=pd.Timestamp("2025-10-01T00:00:00Z"),
        entry_day="2025-10-01", timeframe_min=240, venue="binance", dedup_keep=True)
    # A later arm of the same native event must receive the baseline's choice.
    first_arm = first.assign(arm="triple")
    second = _rows([2.], arm="baseline", base="PEPE").assign(
        event_id="pepe_15m", entry_time=pd.Timestamp("2025-10-01T00:00:00Z"),
        entry_day="2025-10-01", timeframe_min=15, venue="okx", dedup_keep=True)
    inch = _rows([3.], arm="baseline", base="1INCH").assign(
        event_id="oneinch", entry_time=pd.Timestamp("2025-10-01T00:00:00Z"),
        entry_day="2025-10-01", timeframe_min=60, venue="gate", dedup_keep=True)
    outcomes = stats.normalize_outcomes(pd.concat([first, first_arm, second, inch], ignore_index=True))
    random_control = _rows([0.], arm="baseline", base="OTHER").assign(
        event_id="control_loser", parent_event_id="pepe_15m", entry_time=pd.Timestamp("2025-10-03T00:00:00Z"),
        entry_day="2025-10-03", dedup_keep=True)
    native, controls, identity = stats.apply_identity_dedup(outcomes, stats.normalize_outcomes(random_control))
    by_event = identity.set_index("event_id")
    assert list(identity.columns) == ["event_id", "original_base_asset", "base_asset", "dedup_keep", "original_dedup_keep"]
    assert (by_event.loc["pepe_4h", "base_asset"], by_event.loc["pepe_4h", "dedup_keep"]) == ("PEPE", True)
    assert (by_event.loc["pepe_15m", "base_asset"], by_event.loc["pepe_15m", "dedup_keep"]) == ("PEPE", False)
    assert (by_event.loc["oneinch", "base_asset"], by_event.loc["oneinch", "dedup_keep"]) == ("1INCH", True)
    assert native.loc[native.event_id.eq("pepe_4h") & native.arm.eq("triple"), "dedup_keep"].item()
    assert (controls.iloc[0].base_asset, controls.iloc[0].dedup_keep) == ("PEPE", False)


def test_matched_excess_collapses_twenty_controls_to_one_parent_observation() -> None:
    native = _rows([3.]).assign(event_id="parent")
    controls = pd.concat([_rows([1.]).assign(event_id=f"c{i}", parent_event_id="parent") for i in range(20)], ignore_index=True)
    paired, audit = stats.matched_excess(native, controls)
    assert len(paired) == 1
    assert (paired.iloc[0].control_mean_net_r, paired.iloc[0].excess_net_r) == (pytest.approx(1.), pytest.approx(2.))
    assert (audit.iloc[0].control_rows, audit.iloc[0].closed_controls, audit.iloc[0].match_status) == (20, 20, "matched")


def test_metrics_exclude_censoring_and_drawdown_has_zero_prefix() -> None:
    frame = _rows([2., -3., 99.], censored=[False, False, True])
    metrics = stats.arm_metrics(frame)
    assert (metrics["trades"], metrics["censored"], metrics["mean_net_r"], metrics["closed_event_cumulative_r_maxdd"]) == (2, 1, pytest.approx(-.5), pytest.approx(-3.))


def test_same_exit_clock_is_aggregated_before_event_curve_drawdown() -> None:
    frame = _rows([2., -3.])
    frame.loc[:, "exit_time"] = pd.Timestamp("2025-01-02T00:00:00Z")
    # A same-clock aggregate is -1R, rather than a lexical-ID path of +2 then -3.
    assert stats.arm_metrics(frame)["closed_event_cumulative_r_maxdd"] == pytest.approx(-1.)


def test_top_five_positive_contribution_is_not_all_positive_trades() -> None:
    metrics = stats.arm_metrics(_rows([10., 9., 8., 7., 6., 5., -1.]))
    assert (metrics["top5_positive_net_r"], metrics["sum_net_r"], metrics["sum_without_top5_net_r"]) == (pytest.approx(40.), pytest.approx(44.), pytest.approx(4.))


def test_cluster_bootstrap_is_seeded_and_clusters_asset_day_not_rows() -> None:
    frame = _rows([1., 3., -2.]).assign(base_asset=["BTC", "BTC", "ETH"], entry_day=["2025-01-01", "2025-01-01", "2025-01-02"])
    first, second = stats.cluster_bootstrap(frame, "net_r", draws=200, seed=7), stats.cluster_bootstrap(frame, "net_r", draws=200, seed=7)
    assert first == second
    assert first["clusters"] == 2
    assert first["mean"] == pytest.approx(2 / 3)


def test_strict_development_excludes_a_trade_that_exits_after_cut() -> None:
    frame = _rows([1., 2.])
    frame.loc[0, ["entry_time", "exit_time"]] = [pd.Timestamp("2025-08-31T00:00:00Z"), pd.Timestamp("2025-09-02T00:00:00Z")]
    labeled = stats.time_labels(stats.normalize_outcomes(frame))
    assert not labeled.loc[0, "strict_dev"]
    assert labeled.loc[0, "cross_cut_excluded"]
    assert labeled.loc[1, "strict_dev"]


def test_opportunity_capture_uses_baseline_mfe_not_candidate_truncated_mfe() -> None:
    baseline = _rows([4.], arm="baseline").assign(event_id="same", mfe_r=8.)
    triple = _rows([2.], arm="triple").assign(event_id="same", mfe_r=.5)
    events, summary = stats.baseline_opportunity_capture(pd.concat([baseline, triple], ignore_index=True))
    assert events.iloc[0].baseline_opportunity_capture == pytest.approx(.25)
    assert summary.iloc[0].baseline_mfe_bucket == "gt2_to_10"


def test_paired_baseline_triple_delta_separates_newly_closed_events() -> None:
    baseline = pd.concat([_rows([1.], arm="baseline").assign(event_id="both"),
                          _rows([np.nan], arm="baseline", censored=[True]).assign(event_id="baseline_censored")], ignore_index=True)
    triple = pd.concat([_rows([3.], arm="triple").assign(event_id="both"),
                        _rows([2.], arm="triple").assign(event_id="baseline_censored")], ignore_index=True)
    events, summary = stats.paired_baseline_triple_delta(pd.concat([baseline, triple], ignore_index=True))
    all_scope = summary.loc[summary.asset_scope.eq("all")].iloc[0]
    assert set(events.closure_case) == {"both_closed", "triple_only_closed"}
    assert (all_scope.both_closed_delta_net_r, all_scope.triple_only_closed_net_r, all_scope.observed_total_delta_net_r) == (pytest.approx(2.), pytest.approx(2.), pytest.approx(4.))
    assert all_scope.reconciliation_difference_net_r == pytest.approx(0.)


def test_build_writes_compact_summary_from_synthetic_stream_ledgers(tmp_path) -> None:
    results = tmp_path / "results"
    stream = results / "original6253" / "streams" / "synthetic"
    stream.mkdir(parents=True)
    outcomes = pd.concat([_rows([1.], arm="baseline"), _rows([2.], arm="triple"), _rows([3.], arm="filtered_triple")], ignore_index=True)
    outcomes.to_csv(stream / "outcomes.csv.gz", index=False, compression="gzip")
    receipt = stats.build(results)
    summary = pd.read_csv(results / "stats" / "summary.csv")
    assert receipt["cohorts"]["original6253"]["status"] == "complete"
    assert set(stats.SUMMARY_COLUMNS).issubset(summary.columns)
    assert set(summary.event_scope) == {"raw", "dedup"}
    assert {"primary", "diagnostic", "period", "month", "year", "timeframe_min", "venue", "symbol"}.issubset(set(summary.group_type))
    assert {"mean_net_r_ci95_low", "mean_net_r_ci95_high", "closed_event_cumulative_r_maxdd"}.issubset(summary.columns)


def test_fixed_test_inference_excludes_development_entries() -> None:
    native = pd.concat([_rows([1.], arm="baseline"), _rows([3.], arm="baseline")], ignore_index=True)
    native.loc[0, "entry_time"] = pd.Timestamp("2025-08-01T00:00:00Z")
    native.loc[0, "exit_time"] = pd.Timestamp("2025-08-02T00:00:00Z")
    native.loc[1, "entry_time"] = pd.Timestamp("2025-10-01T00:00:00Z")
    native.loc[1, "exit_time"] = pd.Timestamp("2025-10-02T00:00:00Z")
    native = stats.time_labels(stats.normalize_outcomes(native))
    controls = native.copy().assign(parent_event_id=native.event_id, event_id=["c0", "c1"], net_r=[0., 1.])
    summary, _, paired = stats.primary_statistics(native, controls, analysis_scope="dedup", infer_excess=True, timeframe_min=60)
    row = summary.loc[summary.arm.eq("baseline") & summary.scope.eq("all")].iloc[0]
    assert (row.events, row.trades, len(paired), paired.entry_time.min()) == (1, 1, 1, pd.Timestamp("2025-10-01T00:00:00Z"))

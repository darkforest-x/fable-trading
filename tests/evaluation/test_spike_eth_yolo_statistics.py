"""Synthetic contracts for frozen ETH V9 YOLO ledger aggregation."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_eth_yolo_statistics import (
    DEFAULT_TIMEFRAMES,
    make_attribution,
    run,
    week_block_bootstrap,
)


def _config():
    return {
        "lower_timeframes": {"3m": "1m", "5m": "1m", "15m": "5m", "30m": "15m", "1H": "15m", "4H": "1H", "1Dutc": "4H"},
        "statistics_seed": 915152, "bootstrap_draws": 2000, "permutations": 9999,
    }


def _write_ledgers(directory, *, include_daily_signal=True):
    signals, trades, controls = [], [], []
    entry = pd.Timestamp("2026-08-01T00:00:00Z")
    for position, timeframe in enumerate(DEFAULT_TIMEFRAMES):
        for suffix, hit, lower_hit, same_score, lower_score, rv in (
            ("a", True, True, .1, .5, 1.), ("b", False, False, .9, .5, 1.), ("c", False, True, .2, .2, 1.),
        ):
            if timeframe == "1Dutc" and not include_daily_signal:
                continue
            key = f"{timeframe}_{suffix}"
            signals.append({"event_key": key, "timeframe": timeframe, "same_hit": hit, "lower_hit": lower_hit,
                            "same_score": same_score, "lower_score": lower_score, "rv": rv, "side": 1})
        # Keep 1Dutc empty to prove required output rows do not disappear.
        if timeframe == "1Dutc":
            continue
        rows = [("a", 1., 1.2, .01, .012, False), ("b", -1., -.8, -.01, -.008, False),
                ("c", float("nan"), float("nan"), float("nan"), float("nan"), True)]
        for arm in ("v9", "v9_same", "v9_lower", "v9_both"):
            for suffix, net_r, gross_r, net_return, gross_return, censored in rows:
                # The serial arms retain the source keys here; aggregation must not
                # confuse their separate replay outcomes with detector cohorts.
                stamp = entry + pd.Timedelta(days=position, minutes={"a": 0, "b": 1, "c": 2}[suffix])
                trade = {"arm": arm, "event_key": f"{timeframe}_{suffix}", "timeframe": timeframe, "side": 1,
                         "entry_time": stamp, "exit_time": stamp + pd.Timedelta(minutes=10), "signal_i": position,
                         "net_r": net_r, "gross_r": gross_r, "net_return": net_return, "gross_return": gross_return,
                         "initial_risk_frac": .01, "censored": censored}
                trades.append(trade)
                controls.append({"arm": arm, "event_key": trade["event_key"], "entry_time": stamp,
                                 "matched": suffix == "a" and not censored, "target_net_r": net_r,
                                 "control_net_r": .25 if suffix == "a" else float("nan")})
    pd.DataFrame(trades).to_csv(directory / "trades.csv", index=False)
    pd.DataFrame(controls).to_csv(directory / "controls.csv", index=False)
    pd.DataFrame(signals).to_csv(directory / "signals_detected.csv", index=False)


def test_run_handles_zero_censoring_matched_denominators_and_stable_ties(tmp_path):
    _write_ledgers(tmp_path)
    result = run(tmp_path, _config())

    assert result["complete"] is True
    for name in ("summary.csv", "descriptives.csv", "association.csv", "ranking.csv", "diagnostics.csv", "attribution.csv", "coverage.csv", "summary.json"):
        assert (tmp_path / name).is_file()
    summary = pd.read_csv(tmp_path / "summary.csv")
    same_hit = summary.loc[(summary.population == "v9_detection_group") & (summary.timeframe == "3m") & (summary.group == "same_hit")].iloc[0]
    assert same_hit.n == 1 and same_hit.closed == 1 and same_hit.matched_pairs == 1
    assert same_hit.paired_target_mean_r == pytest.approx(1.)
    assert same_hit.paired_control_mean_r == pytest.approx(.25)
    assert same_hit.net_winrate == 1.
    assert same_hit.net_winrate_wilson95_low < same_hit.net_winrate
    assert same_hit.net_winrate_wilson95_high == same_hit.net_winrate
    empty = summary.loc[(summary.population == "v9_detection_group") & (summary.timeframe == "1Dutc") & (summary.group == "all")].iloc[0]
    assert empty.n == 0 and empty.closed == 0 and pd.isna(empty.net_winrate)

    ranking = pd.read_csv(tmp_path / "ranking.csv")
    same = ranking.loc[(ranking.timeframe == "3m") & (ranking.feature == "same_score")].iloc[0]
    assert same.auc_rank == 0.0  # Higher confidence is attached to the losing synthetic trade.
    lower = ranking.loc[(ranking.timeframe == "3m") & (ranking.feature == "lower_score")].iloc[0]
    assert lower.top_decile_n == 1 and lower.top_cutoff_tie_total == 2 and lower.top_cutoff_tie_selected == 1
    assert lower.top_mean_net_r == pytest.approx(1.)  # event key "3m_a" wins the tied boundary.
    rv = ranking.loc[(ranking.timeframe == "3m") & (ranking.feature == "rv")].iloc[0]
    assert not bool(rv.ranking_available) and rv.unavailable_reason == "constant_score"

    attribution = pd.read_csv(tmp_path / "attribution.csv")
    row = attribution.loc[(attribution.timeframe == "3m") & (attribution.arm == "v9")].iloc[0]
    assert row.net10_r_trades == 0 and row.max_cumulative_net_r_drawdown == pytest.approx(1.)
    payload = json.loads((tmp_path / "summary.json").read_text())
    assert all("sha256" in item for item in payload["output_files"].values())


def test_run_rejects_closed_trade_without_finite_outcome(tmp_path):
    _write_ledgers(tmp_path)
    trades = pd.read_csv(tmp_path / "trades.csv")
    trades.loc[0, "net_r"] = float("nan")
    trades.to_csv(tmp_path / "trades.csv", index=False)
    with pytest.raises(ValueError, match="closed trade has a non-finite outcome"):
        run(tmp_path, _config())


def test_missing_configured_timeframe_is_a_zero_row_but_unexpected_one_rejects(tmp_path):
    _write_ledgers(tmp_path, include_daily_signal=False)
    run(tmp_path, _config())
    summary = pd.read_csv(tmp_path / "summary.csv")
    daily = summary.loc[(summary.population == "v9_detection_group") & (summary.timeframe == "1Dutc") & (summary.group == "all")].iloc[0]
    assert daily.n == 0 and daily.closed == 0

    trades = pd.read_csv(tmp_path / "trades.csv")
    trades.loc[0, "timeframe"] = "not_frozen"
    trades.to_csv(tmp_path / "trades.csv", index=False)
    with pytest.raises(ValueError, match="unexpected timeframes"):
        run(tmp_path, _config())


def test_coverage_keeps_detected_nontraded_signal_in_its_own_denominator(tmp_path):
    _write_ledgers(tmp_path)
    trades = pd.read_csv(tmp_path / "trades.csv")
    trades = trades.loc[~((trades.arm == "v9") & (trades.event_key == "3m_b"))]
    trades.to_csv(tmp_path / "trades.csv", index=False)
    run(tmp_path, _config())
    coverage = pd.read_csv(tmp_path / "coverage.csv").set_index("timeframe")
    assert coverage.loc["3m", "signals_n"] == 3
    assert coverage.loc["3m", "actual_v9_trades_n"] == 2
    assert coverage.loc["3m", "signal_same_hit_n"] == 1
    assert coverage.loc["3m", "actual_v9_same_hit_n"] == 1


def test_trade_copies_of_detection_columns_must_match_canonical_signal_ledger(tmp_path):
    _write_ledgers(tmp_path)
    trades = pd.read_csv(tmp_path / "trades.csv")
    signals = pd.read_csv(tmp_path / "signals_detected.csv").set_index("event_key")
    for column in ("same_hit", "lower_hit", "same_score", "lower_score", "rv"):
        trades[column] = trades.event_key.map(signals[column])
    trades.to_csv(tmp_path / "trades.csv", index=False)
    run(tmp_path, _config())

    trades.loc[0, "same_score"] = .99
    trades.to_csv(tmp_path / "trades.csv", index=False)
    with pytest.raises(ValueError, match="same_score identity drift"):
        run(tmp_path, _config())


def test_iso_week_bootstrap_keeps_nonconsecutive_trade_indices_and_drawdown_starts_at_zero():
    blocks = pd.DataFrame(
        {"entry_time": pd.to_datetime(["2026-08-03T00:00:00Z", "2026-08-10T00:00:00Z"]),
         "same_hit": [True, False], "net_r": [1., -1.]},
        index=[9, 41],
    )
    bootstrap = week_block_bootstrap(blocks, "same_hit", draws=2000, seed=7)
    assert bootstrap["bootstrap_blocks"] == 2
    assert bootstrap["bootstrap_valid_draws"] > 0

    events = pd.DataFrame({
        "arm": ["v9", "v9", "v9"], "timeframe": ["3m", "3m", "3m"],
        "event_key": ["a", "b", "c"], "censored": [False, False, False],
        "net_r": [-2., 1., -3.],
        "exit_time": pd.to_datetime(["2026-08-01T00:00:00Z", "2026-08-01T01:00:00Z", "2026-08-01T02:00:00Z"]),
    })
    attribution = make_attribution(events, ("3m",))
    assert attribution.iloc[0].max_cumulative_net_r_drawdown == pytest.approx(4.)

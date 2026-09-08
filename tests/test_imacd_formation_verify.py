"""Deliberate saved-ledger corruption tests using an invented four-trade pool."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.imacd_formation_verify import verify_saved


FOLDS = (("development", "2023-01-01", "2025-01-01"), ("validation", "2025-01-01", "2026-01-01"))
POLICIES = ("P00", "P02", "Q01", "Q02")
PERIODS = (15, 60, 240)
MASKS = {"P00": [True, True, True, True], "P02": [True, False, False, True],
         "Q01": [True, True, True, False], "Q02": [True, True, False, False]}
NETS = np.array([80.0, -40.0, 180.0, -20.0])


def label(symbol, period, fold, stamp, index, side, net):
    step = pd.Timedelta(minutes=period)
    return dict(symbol=symbol, minutes=period, fold=fold, signal_i=index, entry_i=index+1,
        exit_i=index+2, side=side, signal_open_time=stamp.isoformat(),
        signal_decision_close_time=(stamp+step).isoformat(), entry_open_time=(stamp+step).isoformat(),
        exit_bar_open_time=(stamp+2*step).isoformat(), exit_time=(stamp+2*step).isoformat(),
        exit_kind="natural", entry_price=100.0, exit_price=100 * (1 + side * (net+20)/10000),
        gross_bp=net+20, net_bp=net)


@pytest.fixture
def ledgers(tmp_path):
    data, results = tmp_path / "data", tmp_path / "results"
    data.mkdir()
    results.mkdir()
    events, controls, accepted, summary, tails, coverage, portfolios, daily = [], [], [], [], [], [], [], []
    for fold, start_text, end_text in FOLDS:
        start, end = pd.Timestamp(start_text, tz="UTC"), pd.Timestamp(end_text, tz="UTC")
        for period in PERIODS:
            local = []
            for j, net in enumerate(NETS):
                stamp = start + pd.Timedelta(days=10+j*2)
                index = 400+j*500
                side = 1 if j % 2 == 0 else -1
                event_id = f"T00_{period}_{int(stamp.timestamp())}"
                r = dict(event_id=event_id, **label("T00", period, fold, stamp, index, side, net),
                    month=stamp.strftime("%Y-%m"), near_zero_bars=12+j,
                    contraction_ratio=[.9, 1.1, .8, .8][j], proximity_atr=[.5, .5, 1.5, .5][j],
                    formation_memory_ratio=[.8, .7, .9, 1.2][j],
                    formation_memory_recent_width=[.8, .7, .9, 1.2][j], formation_memory_background_width=1.0,
                    control_n=3, control_mean_net_bp=0.0, excess_bp=net,
                    **{p: MASKS[p][j] for p in POLICIES})
                local.append(r)
                events.append(r)
                for k, cnet in enumerate([-10.0, 0.0, 10.0]):
                    ci = index-10-k
                    cstamp = stamp-pd.Timedelta(minutes=period)*(10+k)
                    controls.append(dict(event_id=event_id, control_i=ci,
                        **label("T00", period, fold, cstamp, ci, side, cnet)))
            for k in range(54):
                coverage.append(dict(fold=fold, minutes=period, symbol=f"T{k:02}",
                    bars=20000 if k == 0 else 0, signals=4 if k == 0 else 0))
            base_final = (np.prod(1 + NETS/10000) + 53) / 54
            for p in POLICIES:
                select = np.array(MASKS[p])
                q = NETS[select]
                final = (np.prod(1 + q/10000) + 53) / 54
                winners, losses = int((q > 0).sum()), int((q <= 0).sum())
                kept_tail = int(select[2])
                raw_p = {15: .01, 60: .02, 240: .03}[period]
                adj_p = {15: .03, 60: .04, 240: .04}[period]
                summary.append(dict(fold=fold, minutes=period, policy=p,
                    baseline_n=4, n=len(q), retained_pct=25*len(q), symbols=1, universe_symbols=54,
                    matched_n=len(q), matched_pct=100.0, mean_gross_bp=q.mean()+20,
                    mean_net_bp=q.mean(), median_net_bp=np.median(q), win_pct=100*winners/len(q),
                    loss_count=losses, loss_removed_pct=100*(1-losses/2), winner_retained_pct=50*winners,
                    control_net_bp=0.0, matched_case_net_bp=q.mean(), excess_bp=q.mean(), boundary_marks=0,
                    tail_n=1, tail_retained_n=kept_tail, tail_count_pct=100*kept_tail,
                    tail_profit_pct=100*kept_tail, portfolio_net_pct=(final-1)*100,
                    portfolio_mdd_pct=max(0, (1-final)*100),
                    incremental_net_pct=np.nan if p == "P00" else (final-base_final)*100,
                    incremental_p=np.nan if p == "P00" else raw_p,
                    incremental_p_holm_validation=adj_p if fold == "validation" and p == "Q02" else np.nan))
                tails.append(dict(fold=fold, minutes=period, policy=p,
                    event_id=local[2]["event_id"], kept=bool(kept_tail), net_bp=180.0))
                portfolios.append(dict(fold=fold, minutes=period, policy=p, symbol="T00",
                    initial_equity=1.0, ending_equity=np.prod(1+q/10000),
                    net_return_pct=(np.prod(1+q/10000)-1)*100, trades=len(q)))
                accepted.extend(dict(event_id=r["event_id"], policy=p) for r in local if r[p])
                # Explicitly use last actual close of each UTC calendar day.
                ticks = [start]
                current_day = start
                while current_day < end:
                    ticks.append(current_day + pd.Timedelta(days=1) - pd.Timedelta(minutes=period))
                    current_day += pd.Timedelta(days=1)
                ticks.append(end)
                values = np.linspace(1.0, final, len(ticks))
                daily.extend(dict(fold=fold, minutes=period, policy=p, time=t.isoformat(), equity=v)
                             for t, v in zip(ticks, values))
    files = {"events": (data, events), "controls": (data, controls), "accepted": (data, accepted),
             "summary": (results, summary), "right_tail": (results, tails), "coverage": (results, coverage),
             "portfolios": (results, portfolios), "equity_daily": (results, daily)}
    for name, (directory, rows) in files.items():
        pd.DataFrame(rows).to_csv(directory / (name + (".csv.gz" if directory == data else ".csv")), index=False)
    return data, results


def alter(ledgers, name, mutate):
    data, results = ledgers
    path = data / (name+".csv.gz") if name in {"events", "controls", "accepted"} else results / (name+".csv")
    frame = pd.read_csv(path)
    changed = mutate(frame)
    (changed if changed is not None else frame).to_csv(path, index=False)


def test_consistent_four_trade_pool_passes_without_any_price_source(ledgers):
    audit = verify_saved(*ledgers)
    assert audit["passed"], audit["failed_checks"]
    assert all(audit["checks"].values())
    assert audit["event_count"] == 24
    assert audit["control_count"] == 72
    assert audit["holdout_consumptions"] == 0
    assert audit["no_new_price_evaluation"]
    assert any("volatility" in text for text in audit["limitations"])


@pytest.mark.parametrize("file,column,value,expected", [
    ("events", "near_zero_bars", 11, "qualified_runs_at_least_12"),
    ("events", "entry_i", 402, "events_next_open_entry_index"),
    ("events", "entry_open_time", "2026-01-01T00:00:00+00:00", "events_development_validation_only_no_cross_fold"),
    ("events", "net_bp", 81.0, "events_fixed_20bp_cost"),
    ("events", "P00", False, "p00_is_all_original_events"),
    ("events", "P02", False, "p02_preserves_old_contraction_and_position"),
    ("events", "Q01", False, "q01_is_memory_only"),
    ("events", "Q02", False, "q02_adds_unchanged_position_only"),
    ("events", "formation_memory_ratio", .5, "memory_ratio_from_stored_widths"),
    ("events", "control_n", 2, "control_counts_exact_and_max_3"),
    ("events", "control_mean_net_bp", 1.0, "only_complete_3_control_means"),
    ("events", "excess_bp", 81.0, "matched_excess_exact"),
    ("controls", "side", -1, "controls_share_parent_coin_period_fold_side_month"),
    ("summary", "n", 99, "summary_counts_losses_means_and_tail_retention"),
    ("summary", "loss_count", 99, "summary_counts_losses_means_and_tail_retention"),
    ("summary", "mean_net_bp", 999.0, "summary_counts_losses_means_and_tail_retention"),
    ("summary", "tail_profit_pct", 99.0, "summary_counts_losses_means_and_tail_retention"),
    ("right_tail", "kept", False, "saved_tail_membership_and_outcomes"),
    ("equity_daily", "equity", 1.1, "daily_initial_nav_is_one_and_values_finite"),
    ("portfolios", "ending_equity", 2.0, "54_equal_cash_sleeves_reproduce_reported_pool_net"),
    ("portfolios", "trades", 99, "accepted_entries_unique_allowed_and_match_portfolio_trade_counts"),
])
def test_tampered_scalar_fails_named_invariant(ledgers, file, column, value, expected):
    def mutate(frame):
        frame.loc[0, column] = value
    alter(ledgers, file, mutate)
    audit = verify_saved(*ledgers)
    assert not audit["passed"]
    assert expected in audit["failed_checks"]


def test_duplicate_event_and_reused_control_are_detected(ledgers):
    alter(ledgers, "events", lambda f: pd.concat([f, f.iloc[[0]]], ignore_index=True))
    alter(ledgers, "controls", lambda f: pd.concat([f, f.iloc[[0]]], ignore_index=True))
    audit = verify_saved(*ledgers)
    assert "unique_event_identity" in audit["failed_checks"]
    assert "controls_not_reused" in audit["failed_checks"]


def test_control_cannot_be_an_original_release(ledgers):
    def mutate(frame):
        frame.loc[0, ["signal_i", "control_i"]] = 400
    alter(ledgers, "controls", mutate)
    assert "controls_exclude_original_releases" in verify_saved(*ledgers)["failed_checks"]


def test_two_controls_do_not_receive_a_partial_mean(ledgers):
    alter(ledgers, "controls", lambda f: f.drop(index=0))
    def mutate(frame):
        frame.loc[0, "control_n"] = 2
    alter(ledgers, "events", mutate)
    audit = verify_saved(*ledgers)
    assert audit["checks"]["control_counts_exact_and_max_3"]
    assert "only_complete_3_control_means" in audit["failed_checks"]


def test_holm_does_not_accept_a_diagnostic_arm_as_fourth_candidate(ledgers):
    def mutate(frame):
        index = frame.index[(frame.fold == "validation") & (frame.policy == "P02")][0]
        frame.loc[index, "incremental_p_holm_validation"] = .03
    alter(ledgers, "summary", mutate)
    assert "candidate_holm_is_exactly_three_validation_periods" in verify_saved(*ledgers)["failed_checks"]


def test_holm_must_use_three_period_multiplier(ledgers):
    def mutate(frame):
        index = frame.index[(frame.fold == "validation") & (frame.policy == "Q02")][0]
        frame.loc[index, "incremental_p_holm_validation"] = .02
    alter(ledgers, "summary", mutate)
    assert "candidate_holm_is_exactly_three_validation_periods" in verify_saved(*ledgers)["failed_checks"]


def test_daily_close_cannot_be_relabelled_as_day_start(ledgers):
    def mutate(frame):
        frame.loc[1, "time"] = "2023-01-01T00:00:00+00:00"
    alter(ledgers, "equity_daily", mutate)
    assert "daily_marks_keep_actual_close_times_and_complete_calendar" in verify_saved(*ledgers)["failed_checks"]


def test_missing_history_cash_cannot_be_silently_dropped(ledgers):
    alter(ledgers, "coverage", lambda f: f.drop(index=1))
    assert "coverage_has_same_54_symbols_every_fold_period" in verify_saved(*ledgers)["failed_checks"]


def test_missing_required_field_is_schema_error(ledgers):
    alter(ledgers, "events", lambda f: f.drop(columns="formation_memory_background_width"))
    with pytest.raises(ValueError, match="missing columns"):
        verify_saved(*ledgers)

"""Independent saved-ledger checks for the frozen V3 formation comparison.

Source: the V3 PROJECT_PLAN and the written CSV contract of
``imacd_launch_research``. This verifier does not import the runner, replay
features, read OHLCV, fetch market data, or create new outcomes. It reads only
development (2023-2024) and validation (2025) event/accounting ledgers. The
final 2025 bar may be marked at 2026-01-01 00:00 UTC; no 2026 signal or entry
is permitted. Stored feature arithmetic is checked, not reconstructed from
prices. Stored daily marks can bound but cannot reproduce full-bar drawdown.

``verify_saved`` returns explicit pass/fail invariants. Missing or malformed
schema raises ValueError; numerical inconsistency returns ``passed=False``.
Control volatility quintiles and exact month-boundary full-resolution NAVs
are not saved, so their actual matching/raw permutation p-values cannot be
independently reconstructed here. The six-candidate Holm arithmetic is
checked against the stored raw p-values without importing its implementation.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


POLICIES = ("P00", "S01", "H00", "H01")
PERIODS = (15, 60, 240)
BOUNDS = {
    "development": (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "validation": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
}
UNIVERSE_SIZE = 54
GROUP = ["fold", "minutes", "policy"]


def _read(path: Path, required: list[str]) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f"Unreadable ledger: {path.name}") from exc
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} missing columns: {missing}")
    return frame


def _near(actual, expected) -> bool:
    return bool(np.allclose(actual, expected, rtol=1e-8, atol=1e-8, equal_nan=True))


def _times(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(frame[column], utc=True, errors="coerce")


def _label_checks(frame: pd.DataFrame) -> dict[str, bool]:
    signal = _times(frame, "signal_open_time")
    confirmed = _times(frame, "signal_decision_close_time")
    entry = _times(frame, "entry_open_time")
    exit_time = _times(frame, "exit_time")
    exit_bar = _times(frame, "exit_bar_open_time")
    duration = pd.to_timedelta(frame.minutes, unit="min")
    natural = frame.exit_kind.eq("natural")
    boundary = frame.exit_kind.eq("boundary_mark")
    finite_fields = ["signal_i", "entry_i", "exit_i", "entry_price", "exit_price", "gross_bp", "net_bp"]
    fields_finite = np.isfinite(frame[finite_fields].to_numpy(float)).all()
    integer_indexes = frame[["signal_i", "entry_i", "exit_i"]].mod(1).eq(0).all().all()
    clock = bool(signal.notna().all() and confirmed.notna().all() and entry.notna().all()
                 and exit_time.notna().all() and exit_bar.notna().all()
                 and confirmed.eq(signal + duration).all()
                 and entry.eq(confirmed).all()
                 and exit_bar.eq(signal + (frame.exit_i - frame.signal_i) * duration).all()
                 and exit_time.loc[natural].eq(exit_bar.loc[natural]).all()
                 and exit_time.loc[boundary].eq((exit_bar + duration).loc[boundary]).all()
                 and frame.exit_i.ge(frame.entry_i).all()
                 and frame.loc[natural, "exit_i"].gt(frame.loc[natural, "entry_i"]).all()
                 and (natural | boundary).all())
    bounds_ok = frame.fold.isin(BOUNDS).all() and frame.minutes.isin(PERIODS).all()
    for fold, group in frame.groupby("fold"):
        if fold not in BOUNDS:
            bounds_ok = False
            continue
        start, end = BOUNDS[fold]
        ix = group.index
        bounds_ok = bool(bounds_ok and signal.loc[ix].ge(start).all()
                         and signal.loc[ix].lt(end).all() and entry.loc[ix].lt(end).all()
                         and exit_time.loc[ix].le(end).all()
                         and exit_time.loc[ix].ge(entry.loc[ix]).all())
    expected_gross = frame.side * (frame.exit_price / frame.entry_price - 1) * 10000
    return {
        "finite_prices_and_integer_indexes": bool(fields_finite and integer_indexes
            and frame.entry_price.gt(0).all() and frame.exit_price.gt(0).all()
            and frame.signal_i.ge(340).all() and frame.side.isin([-1, 1]).all()),
        "next_open_entry_index": bool(frame.entry_i.eq(frame.signal_i + 1).all()),
        "confirmation_entry_exit_clocks": clock,
        "development_validation_only_no_cross_fold": bool(bounds_ok),
        "gross_from_frozen_entry_exit": _near(frame.gross_bp, expected_gross),
        "fixed_20bp_cost": _near(frame.gross_bp - frame.net_bp, 20),
    }


def verify_saved(data_dir: str | Path, results_dir: str | Path) -> dict:
    """Check the saved V3 files once each; never access raw price sources."""
    data_dir, results_dir = Path(data_dir), Path(results_dir)
    label_columns = ["event_id", "symbol", "minutes", "fold", "signal_i", "entry_i", "exit_i",
        "side", "signal_open_time", "signal_decision_close_time", "entry_open_time",
        "exit_bar_open_time", "exit_time", "exit_kind", "entry_price", "exit_price", "gross_bp", "net_bp"]
    events = _read(data_dir / "events.csv.gz", label_columns + [*POLICIES, "month", "near_zero_bars",
        "contraction_ratio", "proximity_atr", "formation_memory_ratio", "formation_memory_recent_width",
        "formation_memory_background_width", "control_n", "control_mean_net_bp", "excess_bp", "signal_close_price",
        "prior_box_high", "prior_box_low", "box_breakout", "breakout_score",
        "htf_known", "htf_index", "htf_md", "htf_atr", "htf_close_ms", "htf_score"])
    controls = _read(data_dir / "controls.csv.gz", label_columns + ["control_i"])
    accepted = _read(data_dir / "accepted.csv.gz", ["event_id", "policy"])
    summary_fields = ["baseline_n", "n", "retained_pct", "symbols", "universe_symbols", "matched_n",
        "matched_pct", "mean_gross_bp", "mean_net_bp", "median_net_bp", "win_pct", "loss_count",
        "loss_removed_pct", "winner_retained_pct", "control_net_bp", "matched_case_net_bp", "excess_bp",
        "boundary_marks", "tail_n", "tail_retained_n", "tail_count_pct", "tail_profit_pct"]
    summary = _read(results_dir / "summary.csv", GROUP + summary_fields + ["portfolio_net_pct",
        "portfolio_mdd_pct", "incremental_p", "incremental_p_holm_validation", "incremental_net_pct"])
    tails = _read(results_dir / "right_tail.csv", GROUP + ["event_id", "kept", "net_bp"])
    coverage = _read(results_dir / "coverage.csv", ["fold", "minutes", "symbol", "bars", "signals"])
    portfolios = _read(results_dir / "portfolios.csv", GROUP + ["symbol", "initial_equity",
        "ending_equity", "net_return_pct", "trades"])
    equity = _read(results_dir / "equity_daily.csv", GROUP + ["time", "equity"])
    checks = {"unique_event_identity": bool(not events.event_id.duplicated().any()),
              "qualified_runs_at_least_12": bool(events.near_zero_bars.ge(12).all()),
              "boolean_policy_columns": bool(all(events[p].isin([True, False]).all() for p in POLICIES))}
    checks.update({"events_" + key: value for key, value in _label_checks(events).items()})
    checks.update({"controls_" + key: value for key, value in _label_checks(controls).items()})
    derived_ids = (events.symbol.astype(str) + "_" + events.minutes.astype(str) + "_"
                   + (_times(events, "signal_open_time").astype("int64") // 10**9).astype(str))
    checks["event_ids_match_signal_clock"] = bool(events.event_id.eq(derived_ids).all())
    checks["event_month_matches_signal_open"] = bool(events.month.eq(_times(events, "signal_open_time").dt.strftime("%Y-%m")).all())

    recent, background = events.formation_memory_recent_width, events.formation_memory_background_width
    finite = np.isfinite(recent) & np.isfinite(background)
    expected_ratio = np.divide(recent, background, out=np.full(len(events), np.nan), where=background.gt(0))
    expected_ratio[(background == 0) & (recent == 0)] = 1.0
    expected_ratio[(background == 0) & (recent > 0)] = np.inf
    checks["memory_ratio_from_stored_widths"] = _near(events.formation_memory_ratio, expected_ratio)
    checks["memory_widths_nonnegative_when_available"] = bool((recent.isna() | recent.ge(0)).all()
        and (background.isna() | background.ge(0)).all())
    breakout=np.where(events.side>0,events.signal_close_price>events.prior_box_high,
                      events.signal_close_price<events.prior_box_low)
    known=events.htf_known.eq(True)
    same=known & (events.htf_md*events.side>0)
    checks["p00_is_all_original_events"] = bool(events.P00.eq(True).all())
    checks["s01_is_strict_prior12_box_break"] = bool(events.S01.eq(breakout).all() and events.box_breakout.eq(breakout).all())
    checks["h00_is_known_coverage_only"] = bool(events.H00.eq(known).all())
    checks["h01_is_known_same_md_direction"] = bool(events.H01.eq(same).all())
    higher_ms=events.minutes.map({15:60,60:240,240:1440})*60000
    open_ms=_times(events,'signal_open_time').astype('int64')//1000000
    expected_close=(open_ms//higher_ms)*higher_ms
    checks["known_higher_is_expected_close_at_local_open"] = bool(
        events.loc[known,'htf_close_ms'].eq(expected_close.loc[known]).all()
        and events.loc[known,'htf_close_ms'].le(open_ms.loc[known]).all())
    checks["known_higher_has_340_bar_independent_warmup"] = bool(
        events.loc[known,'htf_index'].ge(340).all()
        and np.isfinite(events.loc[known,['htf_md','htf_atr']].to_numpy()).all()
        and events.loc[known,'htf_atr'].gt(0).all())
    checks["unknown_higher_values_are_missing"] = bool(events.loc[~known,['htf_md','htf_atr','htf_close_ms']].isna().all().all())
    checks["higher_score_is_directional_normalized_md"] = _near(events.htf_score,events.side*events.htf_md/events.htf_atr)

    checks["controls_not_reused"] = bool(not controls.duplicated(["symbol", "minutes", "fold", "control_i"]).any())
    checks["control_index_matches_its_signal_index"] = bool(controls.control_i.eq(controls.signal_i).all())
    release_keys = set(zip(events.symbol, events.minutes, events.fold, events.signal_i))
    checks["controls_exclude_original_releases"] = not any(
        key in release_keys for key in zip(controls.symbol, controls.minutes, controls.fold, controls.signal_i))
    means = controls.groupby("event_id").net_bp.agg(["mean", "count"]).reindex(events.event_id)
    counts = means["count"].fillna(0).to_numpy()
    expected_control = means["mean"].where(means["count"].eq(3)).to_numpy()
    checks["control_counts_exact_and_max_3"] = bool(events.control_n.eq(counts).all() and events.control_n.between(0, 3).all())
    checks["only_complete_3_control_means"] = _near(events.control_mean_net_bp, expected_control)
    checks["matched_excess_exact"] = _near(events.excess_bp, events.net_bp - expected_control)
    parent = events[["event_id", "symbol", "minutes", "fold", "side", "month"]].drop_duplicates("event_id")
    linked = controls.merge(parent, on="event_id", how="left", suffixes=("", "_parent"), indicator=True)
    checks["controls_share_parent_coin_period_fold_side_month"] = bool(
        linked._merge.eq("both").all()
        and all(linked[col].eq(linked[col + "_parent"]).all() for col in ["symbol", "minutes", "fold", "side"])
        and _times(linked, "signal_open_time").dt.strftime("%Y-%m").eq(linked.month).all())

    keys = {(fold, period, policy) for fold in BOUNDS for period in PERIODS for policy in POLICIES}
    checks["summary_has_exactly_all_24_policy_groups"] = bool(
        not summary.duplicated(GROUP).any() and set(summary[GROUP].itertuples(index=False, name=None)) == keys)
    summary_ok, tail_ok = True, True
    expected_tails = []
    for row in summary.itertuples(index=False):
        if (row.fold, row.minutes, row.policy) not in keys:
            summary_ok = False
            continue
        base = events.loc[events.fold.eq(row.fold) & events.minutes.eq(row.minutes)]
        q = base.loc[base[row.policy].eq(True)]
        matched = q.loc[q.excess_bp.notna()]
        tail = base.sort_values(["net_bp", "event_id"], ascending=[False, True]).head(max(1, int(np.ceil(len(base) / 10))))
        kept = tail.loc[tail[row.policy].eq(True)]
        losses, winners = base.net_bp.le(0).sum(), base.net_bp.gt(0).sum()
        positive_tail = tail.net_bp.clip(lower=0).sum()
        expected = dict(baseline_n=len(base), n=len(q), retained_pct=100 * len(q) / len(base) if len(base) else np.nan,
            symbols=q.symbol.nunique(), universe_symbols=54, matched_n=len(matched),
            matched_pct=100 * len(matched) / len(q) if len(q) else np.nan,
            mean_gross_bp=q.gross_bp.mean(), mean_net_bp=q.net_bp.mean(), median_net_bp=q.net_bp.median(),
            win_pct=q.net_bp.gt(0).mean() * 100, loss_count=q.net_bp.le(0).sum(),
            loss_removed_pct=(1 - q.net_bp.le(0).sum() / losses) * 100 if losses else np.nan,
            winner_retained_pct=q.net_bp.gt(0).sum() / winners * 100 if winners else np.nan,
            control_net_bp=matched.control_mean_net_bp.mean(), matched_case_net_bp=matched.net_bp.mean(),
            excess_bp=matched.excess_bp.mean(), boundary_marks=q.exit_kind.eq("boundary_mark").sum(),
            tail_n=len(tail), tail_retained_n=len(kept), tail_count_pct=100 * len(kept) / len(tail) if len(tail) else np.nan,
            tail_profit_pct=kept.net_bp.clip(lower=0).sum() / positive_tail * 100 if positive_tail else np.nan)
        summary_ok = summary_ok and all(_near(getattr(row, name), value) for name, value in expected.items())
        expected_tails.extend((row.fold, row.minutes, row.policy, r.event_id, bool(getattr(r, row.policy)), r.net_bp)
                              for r in tail.itertuples(index=False))
    checks["summary_counts_losses_means_and_tail_retention"] = bool(summary_ok)
    expected_tail = pd.DataFrame(expected_tails, columns=GROUP + ["event_id", "kept", "net_bp"])
    order = GROUP + ["event_id"]
    actual_tail = tails[expected_tail.columns].sort_values(order).reset_index(drop=True)
    expected_tail = expected_tail.sort_values(order).reset_index(drop=True)
    tail_ok = len(actual_tail) == len(expected_tail)
    if tail_ok:
        tail_ok = bool(actual_tail.drop(columns="net_bp").equals(expected_tail.drop(columns="net_bp"))
                       and _near(actual_tail.net_bp, expected_tail.net_bp))
    checks["saved_tail_membership_and_outcomes"] = tail_ok

    candidate = summary.loc[summary.fold.eq("validation") & summary.policy.isin(["S01","H01"])].sort_values("incremental_p")
    holm_ok = len(candidate) == 6 and set(candidate.minutes) == set(PERIODS) and set(candidate.policy)=={"S01","H01"}
    if holm_ok:
        ps = candidate.incremental_p.to_numpy(float)
        expected_holm = np.maximum.accumulate(np.minimum(1, ps * np.arange(6,0,-1)))
        holm_ok = bool(np.isfinite(ps).all() and ((ps >= 0) & (ps <= 1)).all()
                       and _near(candidate.incremental_p_holm_validation, expected_holm))
    other = summary.loc[~(summary.fold.eq("validation") & summary.policy.isin(["S01","H01"]))]
    checks["candidate_holm_is_exactly_six_validation_comparisons"] = bool(holm_ok and other.incremental_p_holm_validation.isna().all())

    equity_keys = set(equity[GROUP].itertuples(index=False, name=None))
    daily_clock_ok = equity_keys == keys
    initial_ok = final_ok = mdd_ok = True
    for key, group in equity.groupby(GROUP, sort=False):
        if key not in keys:
            daily_clock_ok = False
            continue
        fold, period, policy = key
        start, end = BOUNDS[fold]
        times = pd.DatetimeIndex(_times(group, "time"))
        days = pd.date_range(start, end, freq="D", inclusive="left")
        expected_times = pd.DatetimeIndex([start, *(days + pd.Timedelta(days=1) - pd.Timedelta(minutes=period)), end])
        daily_clock_ok = bool(daily_clock_ok and times.equals(expected_times))
        values = group.equity.to_numpy(float)
        initial_ok = bool(initial_ok and np.isfinite(values).all() and _near(values[0], 1))
        rows = summary.loc[summary.fold.eq(fold) & summary.minutes.eq(period) & summary.policy.eq(policy)]
        if len(rows) != 1:
            final_ok = mdd_ok = False
            continue
        final_ok = bool(final_ok and _near((values[-1] - 1) * 100, rows.portfolio_net_pct.iloc[0]))
        daily_mdd = np.max(1 - values / np.maximum.accumulate(values)) * 100
        mdd_ok = bool(mdd_ok and rows.portfolio_mdd_pct.iloc[0] + 1e-8 >= daily_mdd)
    checks["daily_marks_keep_actual_close_times_and_complete_calendar"] = daily_clock_ok
    checks["daily_initial_nav_is_one_and_values_finite"] = initial_ok
    checks["daily_final_nav_matches_reported_pool"] = final_ok
    checks["full_mdd_not_below_daily_mdd"] = mdd_ok

    expected_coverage_keys = {(fold, period) for fold in BOUNDS for period in PERIODS}
    coverage_ok = not coverage.duplicated(["fold", "minutes", "symbol"]).any()
    coverage_ok = coverage_ok and set(coverage[["fold", "minutes"]].itertuples(index=False, name=None)) == expected_coverage_keys
    universe = set(coverage.symbol)
    coverage_ok = coverage_ok and len(universe) == UNIVERSE_SIZE
    for _, group in coverage.groupby(["fold", "minutes"]):
        coverage_ok = coverage_ok and set(group.symbol) == universe
    checks["coverage_has_same_54_symbols_every_fold_period"] = bool(coverage_ok)
    portfolios_ok = not portfolios.duplicated(GROUP + ["symbol"]).any()
    portfolios_ok = portfolios_ok and portfolios.initial_equity.eq(1).all()
    portfolios_ok = portfolios_ok and _near(portfolios.net_return_pct, (portfolios.ending_equity - 1) * 100)
    pool_ok = True
    for key in keys:
        fold, period, policy = key
        available = coverage.loc[coverage.fold.eq(fold) & coverage.minutes.eq(period)].set_index("symbol")
        p = portfolios.loc[portfolios.fold.eq(fold) & portfolios.minutes.eq(period) & portfolios.policy.eq(policy)]
        expected_symbols = set(available.loc[available.bars.gt(0)].index)
        portfolios_ok = portfolios_ok and set(p.symbol) == expected_symbols
        pool_nav = (p.ending_equity.sum() + UNIVERSE_SIZE - len(p)) / UNIVERSE_SIZE
        s = summary.loc[summary.fold.eq(fold) & summary.minutes.eq(period) & summary.policy.eq(policy)]
        pool_ok = pool_ok and len(s) == 1 and _near((pool_nav - 1) * 100, s.portfolio_net_pct.iloc[0])
    checks["per_coin_portfolio_contract_and_missing_history_cash"] = bool(portfolios_ok)
    checks["54_equal_cash_sleeves_reproduce_reported_pool_net"] = bool(pool_ok)

    accepted_ok = not accepted.duplicated(["event_id", "policy"]).any() and accepted.policy.isin(POLICIES).all()
    al = accepted.merge(events[["event_id", "symbol", "minutes", "fold", *POLICIES]], on="event_id", how="left", indicator=True)
    accepted_ok = accepted_ok and al._merge.eq("both").all()
    for policy in POLICIES:
        accepted_ok = accepted_ok and al.loc[al.policy.eq(policy), policy].eq(True).all()
    counts = al.groupby(GROUP + ["symbol"]).size()
    for row in portfolios.itertuples(index=False):
        accepted_ok = accepted_ok and row.trades == counts.get((row.fold, row.minutes, row.policy, row.symbol), 0)
    checks["accepted_entries_unique_allowed_and_match_portfolio_trade_counts"] = bool(accepted_ok)
    failed = [name for name, passed in checks.items() if not passed]
    return dict(passed=not failed, checks=checks, failed_checks=failed, event_count=len(events),
                control_count=len(controls), no_new_price_evaluation=True, holdout_consumptions=0,
                limitations=[
                    "Stored widths validate ratio arithmetic, not a raw-OHLC feature replay.",
                    "Control volatility buckets/source md signs are not persisted; matching those source features is not independently replayed.",
                    "Daily display marks cannot reconstruct full-bar MDD or exact month-boundary raw permutation p-values; Holm arithmetic uses saved p-values.",
                ])

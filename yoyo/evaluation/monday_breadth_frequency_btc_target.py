"""One-variable touch-share sensitivity for the BTC funded case target.

The entry rule and funded BTC exit are frozen from the already selected
``case_target_funded_BTC`` candidate. Only the trailing-28-day touch-share
threshold changes (5, 2, 1, 0.5 percent). Existing VectorBT/funding fills are
read-only inputs; only entry times absent from that cache are simulated and
written under this experiment.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_inputs as inputs
from yoyo.evaluation import monday_breadth_opt_payoffs as payoffs
from yoyo.evaluation import monday_breadth_opt_selection as search


ROOT = Path(__file__).resolve().parents[2]
SOURCE = search.OUT
OUT = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1/btc_target"
INCREMENTAL_DIR = OUT / "incremental_fills"
INCREMENTAL_CACHE = INCREMENTAL_DIR / "btc_funded_limit_incremental.parquet"
THRESHOLDS = (5.0, 2.0, 1.0, 0.5)
BASE_CANDIDATE = "case_target_funded_BTC"
BASE_GRID_CANDIDATE = "casegrid_BTC_117"
INITIAL_MIN = 1
HOLD_HOURS = 144
STOP_PCT = 6
TARGET_NET_POINTS = 10000.0
CASE_ENTRY = study.CASE_ENTRY
LEDGER_END = pd.Timestamp("2026-10-28", tz="UTC")
TIER_GATES = {
    "freq36": {"early_min": 50, "2023_2024_min": 72, "2025_min": 36, "2025_months_min": 8},
    "freq72": {"early_min": 50, "2023_2024_min": 144, "2025_min": 72, "2025_months_min": 8},
}
EPOCH_NAMES = {0: "pre2023", 1: "2023_2024", 2: "2025", 3: "2026_to_ledger_end"}
FILL_COLUMNS = ["entry_time", "policy_id", "exit_time", "entry_open", "exit_vwap", "net_points",
                "net_pct", "exit_legs", "first_exit_price", "first_exit_time", "reason", "ambiguous",
                "peak_pct", "funding_points", "funded_points", "funded_pct", "rule"]


def policy():
    """Return the exact first funded case policy, with frozen six-day/6% settings."""
    policies = funding.funded_policies()
    p = policies[0]
    if p.key != "btc_funded_limit_144_6_0_0_0_1":
        raise ValueError(f"Unexpected funded case policy order: {p.key}")
    if p.hours != HOLD_HOURS or p.stop_pct != STOP_PCT or p.take_fraction != 1:
        raise ValueError(f"Unexpected frozen policy parameters: {p}")
    return p


def signal_frame():
    frame, _ = inputs.cached_partition(5, 30)
    slots, folds = search.time_axes(frame)
    breadth, diffusion, confirmation = search.rules(frame, 5, 30)
    return frame, slots, folds, breadth, diffusion, confirmation


def candidate_events(frame: pd.DataFrame, slots: np.ndarray, folds: np.ndarray,
                     diffusion: dict[str, np.ndarray], confirmation: dict[str, np.ndarray],
                     threshold_pct: float) -> pd.DataFrame:
    """Apply the frozen rule with only its 28-day market-share threshold changed."""
    share = frame.market_share_pct.to_numpy(dtype=float)
    initial = frame.initial.to_numpy(dtype=np.int64)
    mask = (frame.monitor_fraction.eq(1).to_numpy()
            & (initial >= INITIAL_MIN)
            & (share >= threshold_pct)
            & diffusion["ratio_le_25pct"]
            & confirmation["alt_move_ge2"])
    indices = np.flatnonzero(mask)
    pieces = []
    for epoch in range(4):
        chosen = search.admissions(indices[folds[indices] == epoch], slots,
                                   HOLD_HOURS * 12, search.ENDS[epoch])
        if not len(chosen):
            continue
        part = frame.iloc[chosen][["minutes", "known", "decision", "initial", "new_coins",
                                   "market_share_pct", "alt_move", "reclaim_fraction",
                                   "monitor_fraction"]].copy()
        part["epoch"] = epoch
        part["epoch_name"] = EPOCH_NAMES[epoch]
        part["threshold_pct"] = threshold_pct
        part["asset"] = "BTC"
        part["hours"] = HOLD_HOURS
        part["candidate_id"] = (BASE_CANDIDATE if threshold_pct == 5.0
                                 else f"{BASE_CANDIDATE}_share_{threshold_pct:g}pct")
        part["new_ratio_pct"] = 100 * part.new_coins / part.initial
        pieces.append(part)
    if not pieces:
        return pd.DataFrame(columns=["minutes", "known", "decision", "initial", "new_coins",
                                     "market_share_pct", "alt_move", "reclaim_fraction",
                                     "monitor_fraction", "epoch", "epoch_name", "threshold_pct",
                                     "asset", "hours", "candidate_id", "new_ratio_pct"])
    return pd.concat(pieces, ignore_index=True).sort_values("decision", kind="stable").reset_index(drop=True)


def baseline_case_fills() -> pd.DataFrame:
    path = SOURCE / "exits/funded_case_check/locked_events.parquet"
    events = pd.read_parquet(path)
    rows = events[(events.candidate_id == BASE_CANDIDATE) & (events.policy_id == 1000)].copy()
    if len(rows) != 53:
        raise ValueError(f"Frozen 5% BTC baseline is no longer 53 events: {len(rows)}")
    return rows


def compare_baseline(admissions: pd.DataFrame, baseline: pd.DataFrame, shared: pd.DataFrame) -> dict:
    """Require exact event-level and funded-fill parity against the prior 53-trade run."""
    cols = ["known", "decision", "initial", "new_coins", "epoch"]
    a = admissions[cols].sort_values("decision").reset_index(drop=True)
    b = baseline[cols].sort_values("decision").reset_index(drop=True)
    keys_equal = len(a) == len(b)
    for col in ("known", "decision"):
        keys_equal &= a[col].equals(b[col])
    for col in ("initial", "new_coins", "epoch"):
        keys_equal &= np.array_equal(a[col].to_numpy(dtype=np.int64), b[col].to_numpy(dtype=np.int64))
    if len(a) != 53 or not keys_equal:
        raise AssertionError(f"5% admissions are not identical to prior 53 events ({len(a)} vs {len(b)})")
    old_cache = shared[shared.policy_id.eq(0)].copy()
    joined = baseline.merge(old_cache, left_on="decision", right_on="entry_time",
                            suffixes=("_case", "_cache"), validate="one_to_one")
    if len(joined) != 53:
        raise AssertionError(f"Only {len(joined)} of 53 original entries exist in the read-only funded cache")
    numeric = ["entry_open", "exit_vwap", "net_points", "net_pct", "exit_legs", "first_exit_price",
               "reason", "ambiguous", "peak_pct", "funding_points", "funded_points", "funded_pct"]
    max_abs_diff = 0.0
    for col in numeric:
        left = pd.to_numeric(joined[f"{col}_case"], errors="coerce").to_numpy(dtype=float)
        right = pd.to_numeric(joined[f"{col}_cache"], errors="coerce").to_numpy(dtype=float)
        diff = np.nanmax(np.abs(left - right)) if len(left) else 0.0
        max_abs_diff = max(max_abs_diff, float(diff))
        if not np.allclose(left, right, rtol=0, atol=1e-9, equal_nan=True):
            raise AssertionError(f"Baseline funded cache differs from prior case output for {col}: {diff}")
    if not joined.exit_time_case.equals(joined.exit_time_cache):
        raise AssertionError("Baseline exit times differ from prior funded cache")
    return {"baseline_candidate_id": BASE_CANDIDATE, "expected_events": 53,
            "admitted_events": len(a), "admission_keys_exact": True,
            "read_only_cache_matches": len(joined), "funded_fill_fields_exact": True,
            "max_absolute_numeric_difference": max_abs_diff,
            "min_initial_count": int(baseline.initial.min()),
            "policy_key": str(baseline.rule.iloc[0])}


def empty_fill_frame() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in FILL_COLUMNS})


def proxy_count(proxy_ns: np.ndarray, start, end) -> int:
    lo = pd.Timestamp(start).as_unit("ns").value
    hi = pd.Timestamp(end).as_unit("ns").value
    a = np.searchsorted(proxy_ns, lo, side="left")
    b = np.searchsorted(proxy_ns, hi, side="right")
    return int(b - a)


def cached_btc_frame(times: pd.DatetimeIndex):
    """Prepare the original VectorBT input from recorded OHLC only; never repair via REST."""
    times = pd.DatetimeIndex(times).unique().sort_values()
    needed = [(t, t + pd.Timedelta(hours=HOLD_HOURS)) for t in times]
    raw, receipt = payoffs.load_full_ohlc("BTCUSDT", times.min(),
                                         times.max() + pd.Timedelta(hours=HOLD_HOURS),
                                         needed_intervals=needed, repair_missing=False)
    if raw.empty:
        return raw, pd.DataFrame(), receipt
    frame = sim.prepare(raw)
    hist = funding.rates("BTCUSDT", history=True).copy()
    hist = hist[(hist.index >= frame.index[0]) & (hist.index <= frame.index[-1])]
    mark_missing = hist.mark_price.isna()
    proxy = frame.open.reindex(hist.index)
    hist["mark_price"] = hist.mark_price.where(~mark_missing, proxy.to_numpy())
    hist["price_proxy"] = mark_missing
    obs = hist.realized_rate * hist.mark_price
    series = obs.reindex(frame.index, fill_value=0.0)
    frame["settled_pre"] = series.fillna(0.0).cumsum().shift(1, fill_value=0.0)
    frame["settled_bar"] = series
    frame["scheduled"] = ((frame.index.hour % 8 == 0) & (frame.index.minute == 0)).astype(float)
    if not hist.index.to_series().map(lambda t: t.hour % 8 == 0 and t.minute == 0).all():
        raise ValueError("BTC funding changed its eight-hour UTC session schedule")
    receipt["repair_missing"] = False
    receipt["required_entry_to_deadline_intervals"] = len(needed)
    return frame, hist, receipt


def attach_funding_proxy(fills: pd.DataFrame, proxy_ns: np.ndarray) -> pd.DataFrame:
    fills = fills.copy()
    if fills.empty:
        fills["funding_proxy_settlement_n"] = pd.Series(dtype="int64")
        fills["funding_proxy_used"] = pd.Series(dtype="bool")
    else:
        fills["funding_proxy_settlement_n"] = [proxy_count(proxy_ns, r.entry_time, r.exit_time)
                                                for r in fills.itertuples()]
        fills["funding_proxy_used"] = fills.funding_proxy_settlement_n.gt(0)
    return fills


def incremental_fills(entry_times: pd.DatetimeIndex, shared: pd.DataFrame,
                      proxy_times: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    """Read shared caches, then simulate only genuinely new target-policy entry times."""
    INCREMENTAL_DIR.mkdir(parents=True, exist_ok=True)
    policy0 = policy()
    shared_target = shared[shared.policy_id.eq(0)].copy()
    shared_times = pd.DatetimeIndex(shared_target.entry_time).unique()
    old_incremental = pd.read_parquet(INCREMENTAL_CACHE) if INCREMENTAL_CACHE.is_file() else empty_fill_frame()
    if not old_incremental.empty:
        old_incremental = old_incremental.drop_duplicates("entry_time", keep="last")
    incremental_times = (pd.DatetimeIndex(old_incremental.entry_time).unique()
                         if not old_incremental.empty else pd.DatetimeIndex([], tz="UTC"))
    all_times = pd.DatetimeIndex(entry_times).unique().sort_values()
    required = all_times.difference(shared_times)
    todo = required.difference(incremental_times)
    quote_receipt = {"status": "not_needed", "repair_missing": False, "rest_repairs": []}
    if len(todo):
        btc, history, receipt = cached_btc_frame(todo)
        quote_receipt = receipt
        trial = sim.simulate(btc, todo, [policy0], batch_size=12) if len(btc) else pd.DataFrame()
        if len(trial):
            trial["funding_points"] = funding.charged(trial, history, {0: policy0})
            trial["funded_points"] = trial.net_points - trial.funding_points
            trial["funded_pct"] = 100 * trial.funded_points / trial.entry_open
            trial["rule"] = policy0.key
            trial = attach_funding_proxy(trial, proxy_times.as_unit("ns").asi8)
            trial = trial.reindex(columns=FILL_COLUMNS + ["funding_proxy_settlement_n", "funding_proxy_used"])
        else:
            trial = empty_fill_frame()
            trial["funding_proxy_settlement_n"] = pd.Series(dtype="int64")
            trial["funding_proxy_used"] = pd.Series(dtype="bool")
        if not trial.empty:
            trial.attrs["quote_receipt"] = receipt
        if len(trial):
            overlap_shared = set(pd.DatetimeIndex(trial.entry_time)).intersection(set(shared_times))
            if overlap_shared:
                raise AssertionError(f"Incremental cache attempted to repeat {len(overlap_shared)} shared fills")
            combined = pd.concat([old_incremental, trial], ignore_index=True)
            combined = combined.drop_duplicates("entry_time", keep="last").sort_values("entry_time")
            tmp = INCREMENTAL_CACHE.with_suffix(".tmp")
            combined.to_parquet(tmp, index=False)
            tmp.replace(INCREMENTAL_CACHE)
            old_incremental = combined
    else:
        if not INCREMENTAL_CACHE.exists():
            old_incremental.to_parquet(INCREMENTAL_CACHE, index=False)
    inc_times = pd.DatetimeIndex(old_incremental.entry_time).unique() if len(old_incremental) else pd.DatetimeIndex([], tz="UTC")
    unresolved = all_times.difference(shared_times.union(inc_times))
    metadata = {"requested_unique_entry_times": len(all_times),
                "shared_cache_target_policy_reused": int(all_times.isin(shared_times).sum()),
                "new_incremental_entries_simulated_this_run": int(len(todo)),
                "incremental_cache_total_entries": len(inc_times),
                "unresolved_entry_times_without_complete_fill": len(unresolved),
                "unresolved_entry_times": [t.isoformat() for t in unresolved],
                "quote_receipt": quote_receipt}
    return old_incremental, metadata


def fee_fields(fills: pd.DataFrame) -> pd.DataFrame:
    f = fills.copy()
    if f.empty:
        return f
    f["exit_fee_rate"] = np.where(f.reason.eq(3), sim.MAKER, sim.TAKER)
    f["entry_fee_points"] = f.entry_open * sim.TAKER
    f["exit_fee_points"] = f.exit_vwap * f.exit_fee_rate
    f["gross_price_points"] = f.exit_vwap - f.entry_open
    f["total_trade_fees_points"] = f.entry_fee_points + f.exit_fee_points
    f["calculated_net_points"] = f.gross_price_points - f.total_trade_fees_points
    if not np.allclose(f.net_points, f.calculated_net_points, rtol=0, atol=1e-7, equal_nan=True):
        raise AssertionError("Computed explicit entry/exit fee points do not reconcile to VectorBT net_points")
    f["entry_taker_fee_rate"] = sim.TAKER
    f["exit_fee_rate"] = f.exit_fee_rate.astype(float)
    f["exit_reason"] = f.reason.map({1: "time_limit_144h", 2: "stop_6pct", 3: "funding_adjusted_10000_point_limit", 4: "trend_exit"})
    f["actual_holding_hours"] = (f.exit_time - f.entry_time).dt.total_seconds() / 3600
    return f


def merge_event_fills(events: pd.DataFrame, baseline: pd.DataFrame,
                      shared: pd.DataFrame, incremental: pd.DataFrame,
                      proxy_times: pd.DatetimeIndex) -> pd.DataFrame:
    shared0 = shared[shared.policy_id.eq(0)].copy()
    shared0 = attach_funding_proxy(shared0, proxy_times.as_unit("ns").asi8)
    shared0["fill_source"] = "shared_read_only_funded_cache"
    inc = incremental.copy()
    if not inc.empty:
        inc["fill_source"] = "this_experiment_incremental_cache"
    # Exact baseline rows take precedence and remain visibly tied to the 53-trade run.
    base = baseline.copy()
    base["policy_id"] = 0
    base["entry_time"] = base.decision
    base["fill_source"] = "prior_case_target_53_locked_events"
    base = attach_funding_proxy(base, proxy_times.as_unit("ns").asi8)
    fills = pd.concat([base, shared0, inc], ignore_index=True, sort=False)
    fills = fills.drop_duplicates("entry_time", keep="first")
    needed = fills[FILL_COLUMNS + ["funding_proxy_settlement_n", "funding_proxy_used", "fill_source"]]
    merged = events.merge(needed, left_on="decision", right_on="entry_time", how="left",
                          validate="many_to_one", suffixes=("", "_fill"))
    merged["fill_status"] = np.where(merged.exit_time.notna(), "filled", "no_complete_fill_available")
    merged = fee_fields(merged)
    merged["policy_key"] = policy().key
    merged["funding_adjusted_target_net_points"] = TARGET_NET_POINTS
    merged["stop_pct"] = STOP_PCT
    merged["max_hold_hours"] = HOLD_HOURS
    return merged.sort_values(["threshold_pct", "decision"], ascending=[False, True], kind="stable").reset_index(drop=True)


def financial_metrics(rows: pd.DataFrame) -> dict:
    priced = rows[rows.fill_status.eq("filled") & rows.funded_pct.notna()].sort_values("decision")
    if not len(priced):
        return {"filled_n": 0, "mean_funded_pct": np.nan, "median_funded_pct": np.nan,
                "win_pct": np.nan, "worst_funded_pct": np.nan, "dd_pct": np.nan,
                "growth_pct": np.nan, "funded_points_sum": 0.0, "funding_points_sum": 0.0,
                "fees_points_sum": 0.0}
    vals = priced.funded_pct.to_numpy(float)
    equity = np.r_[1.0, np.cumprod(1 + vals / 100)]
    return {"filled_n": len(priced), "mean_funded_pct": float(vals.mean()),
            "median_funded_pct": float(np.median(vals)), "win_pct": float(100 * (vals > 0).mean()),
            "worst_funded_pct": float(vals.min()),
            "dd_pct": float(100 * (1 - np.min(equity / np.maximum.accumulate(equity)))),
            "growth_pct": float(100 * (equity[-1] - 1)),
            "funded_points_sum": float(priced.funded_points.sum()),
            "funding_points_sum": float(priced.funding_points.sum()),
            "fees_points_sum": float(priced.total_trade_fees_points.sum())}


def period_tables(events: pd.DataFrame, first_signal: pd.Timestamp, end_signal: pd.Timestamp):
    rules = []
    periods = []
    annual = []
    monthly = []
    start_year = int(first_signal.year)
    final_year = int((end_signal - pd.Timedelta(nanoseconds=1)).year)
    for candidate_id, rows in events.groupby("candidate_id", sort=False):
        threshold = float(rows.threshold_pct.iloc[0])
        for epoch, name in EPOCH_NAMES.items():
            part = rows[rows.epoch.eq(epoch)]
            months = part.decision.dt.strftime("%Y-%m").nunique()
            metric = financial_metrics(part)
            periods.append({"candidate_id": candidate_id, "threshold_pct": threshold, "epoch": epoch,
                            "period": name, "admitted_n": len(part), "months_with_signals": int(months),
                            "first_decision": part.decision.min() if len(part) else pd.NaT,
                            "last_decision": part.decision.max() if len(part) else pd.NaT,
                            **metric})
        for year in range(start_year, final_year + 1):
            part = rows[rows.decision.dt.year.eq(year)]
            month_count = part.decision.dt.strftime("%Y-%m").nunique()
            exposure_days = ((LEDGER_END - pd.Timestamp("2026-01-01", tz="UTC")).days
                             if year == 2026 else (366 if pd.Timestamp(year=year, month=1, day=1).is_leap_year else 365))
            freq_annualized = len(part) * 365.2425 / max(1, exposure_days)
            metric = financial_metrics(part)
            annual.append({"candidate_id": candidate_id, "threshold_pct": threshold, "year": year,
                           "admitted_n": len(part), "priced_n": metric["filled_n"],
                           "months_with_signals": int(month_count),
                           "annualized_admissions_per_year": freq_annualized,
                           "target_36_per_year_met": bool(freq_annualized >= 36),
                           "target_72_per_year_met": bool(freq_annualized >= 72),
                           "first_decision": part.decision.min() if len(part) else pd.NaT,
                           "last_decision": part.decision.max() if len(part) else pd.NaT,
                           **metric})
        final_month = LEDGER_END - pd.offsets.MonthBegin(1)
        for month in pd.date_range(pd.Timestamp(year=start_year, month=1, day=1, tz="UTC"),
                                   final_month, freq="MS", tz="UTC"):
            # The final analysis window ends 2026-10-28 exclusive.
            if month >= LEDGER_END:
                continue
            next_month = month + pd.offsets.MonthBegin(1)
            part = rows[(rows.decision >= month) & (rows.decision < next_month)]
            metric = financial_metrics(part)
            priced = part[part.fill_status.eq("filled") & part.funded_pct.notna()]
            monthly.append({"candidate_id": candidate_id, "threshold_pct": threshold,
                            "month": month.strftime("%Y-%m"), "admitted_n": len(part),
                            "priced_n": metric["filled_n"], "funded_points_sum": metric["funded_points_sum"],
                            "mean_funded_pct": metric["mean_funded_pct"], "win_pct": metric["win_pct"],
                            "funding_points_sum": metric["funding_points_sum"],
                            "fees_points_sum": metric["fees_points_sum"]})
    periods_df = pd.DataFrame(periods)
    annual_df = pd.DataFrame(annual)
    monthly_df = pd.DataFrame(monthly)
    for threshold in THRESHOLDS:
        rows = events[events.threshold_pct.eq(threshold)]
        counts = rows.groupby("epoch").size().to_dict()
        y2025 = rows[rows.decision.dt.year.eq(2025)]
        rules.append({"candidate_id": rows.candidate_id.iloc[0] if len(rows) else
                      (BASE_CANDIDATE if threshold == 5 else f"{BASE_CANDIDATE}_share_{threshold:g}pct"),
                      "threshold_pct": threshold, "asset": "BTC", "minutes": 5, "wait_min": 30,
                      "initial_min": INITIAL_MIN, "breadth": f"share>={threshold:g}%",
                      "diffusion": "ratio_le_25pct (4*new_coins <= initial)",
                      "confirmation": "alt_move_ge2 (alt median observed move >=2%)",
                      "hold_hours": HOLD_HOURS, "exit_policy": policy().key,
                      "goal_net_points": TARGET_NET_POINTS, "stop_pct": STOP_PCT,
                      "max_hold_hours": HOLD_HOURS, "pre2023_n": int(counts.get(0, 0)),
                      "2023_2024_n": int(counts.get(1, 0)), "2025_n": int(counts.get(2, 0)),
                      "2025_active_months": int(y2025.decision.dt.strftime("%Y-%m").nunique()),
                      "2026_n": int(counts.get(3, 0)),
                      "first_decision": rows.decision.min() if len(rows) else pd.NaT,
                      "last_decision": rows.decision.max() if len(rows) else pd.NaT})
    rules_df = pd.DataFrame(rules)
    for tier, gate in TIER_GATES.items():
        eligible_col = f"eligible_{tier}"
        rules_df[eligible_col] = (rules_df.pre2023_n.ge(gate["early_min"])
                                  & rules_df["2023_2024_n"].ge(gate["2023_2024_min"])
                                  & rules_df["2025_n"].ge(gate["2025_min"])
                                  & rules_df["2025_active_months"].ge(gate["2025_months_min"]))
    chosen36 = rules_df.loc[rules_df.eligible_freq36, "threshold_pct"].max() if rules_df.eligible_freq36.any() else np.nan
    chosen72 = rules_df.loc[rules_df.eligible_freq72, "threshold_pct"].max() if rules_df.eligible_freq72.any() else np.nan
    rules_df["selected_freq36"] = rules_df.threshold_pct.eq(chosen36) if pd.notna(chosen36) else False
    rules_df["selected_freq72"] = rules_df.threshold_pct.eq(chosen72) if pd.notna(chosen72) else False
    rules_df["selection_source"] = "pre-2026 frequency gates only; 2026 never selects"
    rules_df["selection_status"] = np.select(
        [rules_df.selected_freq36, rules_df.selected_freq72], ["selected_freq36", "selected_freq72"], default="sensitivity_only")
    return rules_df, periods_df, annual_df, monthly_df


def sep16_case_table(frame: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    matches = frame[frame.decision.eq(CASE_ENTRY)]
    if len(matches) != 1:
        raise ValueError(f"Expected one original Sep 16 decision row, found {len(matches)}")
    feature = matches.iloc[0]
    records = []
    for threshold in THRESHOLDS:
        candidate_id = BASE_CANDIDATE if threshold == 5 else f"{BASE_CANDIDATE}_share_{threshold:g}pct"
        subset = events[events.candidate_id.eq(candidate_id)].sort_values("decision")
        selected = subset[subset.decision.eq(CASE_ENTRY)]
        share_ok = pd.notna(feature.market_share_pct) and feature.market_share_pct >= threshold
        base_ok = (int(feature.initial) >= INITIAL_MIN
                   and int(feature.initial) >= 4 * int(feature.new_coins)
                   and pd.notna(feature.alt_move) and feature.alt_move >= 2
                   and feature.monitor_fraction == 1)
        signal_pass = bool(share_ok and base_ok)
        prior = subset[subset.decision.lt(CASE_ENTRY)]
        blocker = prior.iloc[-1] if len(prior) and CASE_ENTRY < prior.iloc[-1].decision + pd.Timedelta(hours=HOLD_HOURS) else None
        chosen = selected.iloc[0] if len(selected) else None
        failed = []
        if not share_ok: failed.append("touch_share_below_threshold_or_unknown")
        if int(feature.initial) < INITIAL_MIN: failed.append("initial_below_original_minimum")
        if int(feature.initial) < 4 * int(feature.new_coins): failed.append("new_ratio_above_25pct")
        if pd.isna(feature.alt_move) or feature.alt_move < 2: failed.append("alt_median_move_below_2pct")
        if feature.monitor_fraction != 1: failed.append("incomplete_observation_clock")
        if signal_pass and not len(selected) and blocker is not None: failed.append("blocked_by_prior_144h_position")
        records.append({"candidate_id": candidate_id, "threshold_pct": threshold,
                        "case_known": CASE_ENTRY - pd.Timedelta(minutes=30), "case_decision": CASE_ENTRY,
                        "initial": int(feature.initial), "new_coins": int(feature.new_coins),
                        "new_ratio_pct": 100 * int(feature.new_coins) / int(feature.initial),
                        "share28_pct": feature.market_share_pct, "alt_move_pct": feature.alt_move,
                        "monitor_fraction": feature.monitor_fraction, "signal_passes_rule": signal_pass,
                        "admitted_and_traded": bool(len(selected) and selected.iloc[0].fill_status == "filled"),
                        "prior_blocking_entry": blocker.decision if blocker is not None else pd.NaT,
                        "failure_or_block_reason": ";".join(failed),
                        "fill_source": chosen.fill_source if chosen is not None else "",
                        "entry_open": chosen.entry_open if chosen is not None else np.nan,
                        "exit_time": chosen.exit_time if chosen is not None else pd.NaT,
                        "exit_vwap": chosen.exit_vwap if chosen is not None else np.nan,
                        "gross_price_points": chosen.gross_price_points if chosen is not None else np.nan,
                        "entry_fee_points": chosen.entry_fee_points if chosen is not None else np.nan,
                        "exit_fee_points": chosen.exit_fee_points if chosen is not None else np.nan,
                        "funding_points": chosen.funding_points if chosen is not None else np.nan,
                        "funded_points": chosen.funded_points if chosen is not None else np.nan,
                        "funded_pct": chosen.funded_pct if chosen is not None else np.nan,
                        "exit_reason": chosen.exit_reason if chosen is not None else ""})
    return pd.DataFrame(records)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source_cache_stat_before = {"path": str(funding.FUNDED_CACHE),
                                "bytes": funding.FUNDED_CACHE.stat().st_size,
                                "mtime_ns": funding.FUNDED_CACHE.stat().st_mtime_ns}
    frame, slots, folds, breadth, diffusion, confirmation = signal_frame()
    all_events = pd.concat([candidate_events(frame, slots, folds, diffusion, confirmation, threshold)
                            for threshold in THRESHOLDS], ignore_index=True)
    if all_events.duplicated(["candidate_id", "decision"]).any():
        raise AssertionError("Duplicate admitted event within one threshold candidate")
    baseline = baseline_case_fills()
    shared = pd.read_parquet(funding.FUNDED_CACHE)
    parity = compare_baseline(all_events[all_events.threshold_pct.eq(5.0)], baseline, shared)
    if int(baseline.initial.min()) != INITIAL_MIN:
        raise AssertionError(f"Original initial lower bound changed: {baseline.initial.min()} vs {INITIAL_MIN}")

    historic_rates = funding.rates("BTCUSDT", history=True)
    proxy_times = historic_rates.index[historic_rates.mark_price.isna()]
    incremental, cache_meta = incremental_fills(pd.DatetimeIndex(all_events.decision), shared, proxy_times)
    events = merge_event_fills(all_events, baseline, shared, incremental, proxy_times)
    if events[events.threshold_pct.eq(5.0)].shape[0] != 53:
        raise AssertionError("5% event output lost original 53 events")
    if not (events[events.threshold_pct.eq(5.0)].fill_source.eq("prior_case_target_53_locked_events").all()):
        raise AssertionError("5% baseline rows were not reused from the frozen prior case result")
    if int((events.fill_source == "this_experiment_incremental_cache").sum()) < 0:
        raise AssertionError("Invalid incremental-source accounting")

    first_signal = frame.decision.min()
    end_signal = LEDGER_END
    rules, periods, annual, monthly = period_tables(events, first_signal, end_signal)
    sep16 = sep16_case_table(frame, events)

    # Persist the primary paired-event feed before secondary summaries.
    events.to_parquet(OUT / "threshold_events.parquet", index=False)
    rules.to_csv(OUT / "threshold_rules.csv", index=False)
    periods.to_csv(OUT / "period_summary.csv", index=False)
    annual.to_csv(OUT / "annual_frequency.csv", index=False)
    monthly.to_csv(OUT / "monthly_distribution.csv", index=False)
    sep16.to_csv(OUT / "sep16_case.csv", index=False)
    quote_receipt = cache_meta.pop("quote_receipt")
    (OUT / "price_receipt.json").write_text(json.dumps(quote_receipt, indent=2, ensure_ascii=False) + "\n")
    parity["baseline_cache_path"] = str(funding.FUNDED_CACHE)
    parity["baseline_cache_read_only"] = True
    parity["input_feature_window_first_decision"] = str(first_signal)
    parity["input_feature_window_end_exclusive"] = str(end_signal)
    parity["entry_fill_cache"] = cache_meta
    (OUT / "baseline_parity.json").write_text(json.dumps(parity, indent=2, ensure_ascii=False) + "\n")
    contract = {
        "experiment": "exp-monday-breadth-frequency-20261108-v1/btc_target",
        "single_variable": "share threshold only: 5, 2, 1, 0.5 percent; denominator, 5m window, 30m wait, ratio gate, alt move gate, initial floor, funded exit unchanged",
        "frozen_entry": {"minutes": 5, "wait_min": 30, "initial_min": INITIAL_MIN,
                         "diffusion": "4*new_coins <= initial", "confirmation": "alt median observed move >=2%",
                         "touch_share": "initial lower-touch symbols / trailing 28-day any-direction touch-universe proxy; unknown share fails closed"},
        "frozen_exit": {"policy": policy().key, "target": "funding-adjusted limit aimed at +10000 net USDT points after fees and settled funding",
                        "stop_pct": STOP_PCT, "max_hold_hours": HOLD_HOURS,
                        "entry_fee": "taker 0.05%", "limit_exit_fee": "maker 0.02%",
                        "stop_or_time_exit_fee": "taker 0.05%", "funding": "settled Binance UM rate × mark; legacy missing mark price proxied by BTC contract open per source implementation"},
        "single_position": "reset each source epoch; accept first chronological signal and block next entries for max(24h, 144h)=144h",
        "time_splits_utc": {"pre2023": "source start through 2022-12-31", "2023_2024": "2023-01-01 through 2024-12-31",
                            "2025": "selection frequency gate", "2026": "locked descriptive review through 2026-10-28 exclusive"},
        "historical_frequency_gates": TIER_GATES,
        "threshold_selection": "For each tier, choose the highest tested threshold (nearest original 5%) meeting only pre-2026 frequency gates; 2026 never chooses. If none passes, retain all four as sensitivity-only.",
        "cache_discipline": {"shared_funded_cache": str(funding.FUNDED_CACHE), "shared_cache_write": False,
                              "only_missing_entry_times_simulated": True,
                              "new_fill_cache": str(INCREMENTAL_CACHE),
                              "underlying_execution": "existing VectorBT amount-based sim.simulate and funding.charged adapter; no hand-built OHLC fill engine"},
        "baseline_check": "5% candidate admissions and its 53 funded fills must match the previous case_target_funded_BTC event ledger exactly; original minimum initial count is 1 and remains explicit",
        "limitations": "Research sensitivity only; 2026 is partial through the source ledger end; lower thresholds are not automatically validated or eligible for alert deployment; do not sum across thresholds as independent opportunities.",
    }
    (OUT / "selection_contract.json").write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
    source_cache_stat_after = {"path": str(funding.FUNDED_CACHE),
                               "bytes": funding.FUNDED_CACHE.stat().st_size,
                               "mtime_ns": funding.FUNDED_CACHE.stat().st_mtime_ns}
    if source_cache_stat_before != source_cache_stat_after:
        raise AssertionError("Shared source funding cache changed during study")
    manifest = {"builder": "yoyo/evaluation/monday_breadth_frequency_btc_target.py",
                "candidate_rows": len(rules), "event_rows": len(events),
                "priced_event_rows": int(events.funded_pct.notna().sum()),
                "incremental_fill_rows": len(incremental),
                "source_cache_before_and_after": source_cache_stat_after,
                "outputs": sorted(p.name for p in OUT.iterdir() if p.is_file()),
                "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(rules[["candidate_id", "threshold_pct", "pre2023_n", "2023_2024_n", "2025_n",
                 "2025_active_months", "2026_n", "eligible_freq36", "eligible_freq72",
                 "selected_freq36", "selected_freq72"]].to_string(index=False), flush=True)
    print("Sep 16 case", sep16[["threshold_pct", "share28_pct", "signal_passes_rule",
                               "admitted_and_traded", "prior_blocking_entry", "entry_open",
                               "exit_time", "funded_points", "funded_pct", "failure_or_block_reason"]].to_string(index=False), flush=True)
    print("Cache reuse/simulation", json.dumps(cache_meta, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

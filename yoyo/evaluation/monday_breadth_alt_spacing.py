"""Measure 24h/12h/6h admissions for the frozen freq36_ALT basket.

The three clocks differ only in minimum signal-to-signal entry spacing. Signal
membership and 6h trade outcomes are read from the existing frozen feature and
VectorBT payoff caches. No quote, funding or exchange data are fetched here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_inputs as inputs
from yoyo.evaluation import monday_breadth_opt_selection as selection


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-monday-breadth-alt-spacing-20261108-v1"
OUT = ROOT / "experiments/active" / EXPERIMENT_ID
FREQUENCY = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1"
EXTENSION = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1"
BTC_LOCK = EXTENSION / "risk/integrated_btc_events.parquet"
BTC_REFERENCE = EXTENSION / "risk/accounts.csv"
ALT_CONTROL = EXTENSION / "alt/control_paired_events.csv"
RAW_RULE_ID = "freq36_ALT"
GAPS_HOURS = (24, 12, 6)
HOLD_HOURS = 6
HOLD_SLOTS = HOLD_HOURS * 12
DATA_END = pd.Timestamp("2026-10-28T00:00:00Z")
OOS_START = pd.Timestamp("2026-01-01T00:00:00Z")
ALT_EXCLUDED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "USTCUSDT"}
PERIODS = tuple(str(year) for year in range(2020, 2026)) + (
    "2026_to_2026-10-27",
    "all_history",
)


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unavailable"


def _utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True)


def load_rule_and_raw() -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """Load the single locked rule and all matching pre-spacing signal rows."""
    rules = pd.read_csv(FREQUENCY / "fixed/locked_rules.csv")
    rule = rules[rules.candidate_id.eq(RAW_RULE_ID)]
    if len(rule) != 1:
        raise ValueError(f"Expected exactly one frozen {RAW_RULE_ID} rule")
    r = rule.iloc[0]
    expected = {
        "asset": "ALT", "minutes": 5, "wait_min": 0, "breadth": "share_5",
        "diffusion": "any", "confirmation": "none", "hold_hours": HOLD_HOURS,
    }
    for name, value in expected.items():
        actual = r[name]
        if isinstance(value, str):
            equal = str(actual) == value
        else:
            equal = int(actual) == value
        if not equal:
            raise ValueError(f"Frozen {RAW_RULE_ID} identity changed at {name}: {actual!r}")

    frame, returns = inputs.cached_partition(5, 0)
    if not frame.decision.eq(frame.known).all():
        raise ValueError("The frozen ALT rule requires zero wait: decision must equal known")
    slots, folds = selection.time_axes(frame)
    indices = selection.rule_indices(frame, 5, 0, "share_5", "any", "none")
    # share_5 is only an activity-universe proxy; the frozen rule additionally
    # required at least two initial constituents.
    indices = indices[frame.initial.to_numpy()[indices] >= 2]
    if not len(indices):
        raise ValueError("Frozen pre-spacing ALT rule produced no raw candidates")
    decisions = pd.to_datetime(frame.decision.iloc[indices], utc=True)
    if decisions.max() >= DATA_END:
        raise ValueError(f"Raw source contains an entry at or beyond the frozen cutoff {DATA_END}")
    if returns.shape[1:] != (len(inputs.HOLDS), len(inputs.ASSETS)):
        raise ValueError(f"Unexpected cached payoff shape {returns.shape}")
    return frame, returns, indices, np.column_stack((slots, folds))


def load_member_counts(frame: pd.DataFrame, indices: np.ndarray) -> dict[pd.Timestamp, int]:
    """Count the original equal-weight ALT members, excluding the frozen four."""
    path = inputs.OUT / "features/members.csv.gz"
    members = pd.read_csv(path, usecols=["minutes", "known", "symbol"])
    members["known"] = _utc(members.known)
    wanted = set(pd.to_datetime(frame.known.iloc[indices], utc=True))
    members = members[
        members.minutes.eq(5)
        & members.known.isin(wanted)
        & ~members.symbol.isin(ALT_EXCLUDED)
    ]
    counts = members.groupby("known", sort=False).symbol.nunique()
    return {pd.Timestamp(k): int(v) for k, v in counts.items()}


def admit(indices: np.ndarray, slots: np.ndarray, folds: np.ndarray, gap_hours: int) -> np.ndarray:
    """Greedy chronological admissions, resetting only at frozen source epochs."""
    if gap_hours < HOLD_HOURS:
        raise ValueError("Entry interval cannot be shorter than the unchanged 6h holding period")
    selected: list[int] = []
    for epoch in range(4):
        next_slot = -1
        end_slot = selection.ENDS[epoch]
        local = indices[folds[indices] == epoch]
        for index in local:
            slot = int(slots[index])
            # Match the source protocol: purge a fill that reaches/crosses the
            # evaluation epoch boundary, while retaining the next epoch reset.
            if slot + HOLD_SLOTS >= end_slot:
                continue
            if slot >= next_slot:
                selected.append(int(index))
                next_slot = slot + gap_hours * 12
    return np.asarray(selected, dtype=np.int64)


def compound_and_settlement_dd(rows: pd.DataFrame, field: str = "net_pct") -> tuple[float, float]:
    """Return fee-only growth and close-to-close settlement drawdown."""
    if rows.empty or rows[field].isna().any():
        return np.nan, np.nan
    values = rows.sort_values("decision", kind="stable")[field].to_numpy(dtype=float)
    equity = np.r_[1.0, np.cumprod(1.0 + values / 100.0)]
    drawdown = 100.0 * (1.0 - equity / np.maximum.accumulate(equity))
    return 100.0 * (equity[-1] - 1.0), float(drawdown.max())


def _entry_metrics(rows: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    rows = rows.sort_values("decision", kind="stable")
    decisions = pd.DatetimeIndex(rows.decision)
    returns = rows.net_pct.dropna().to_numpy(dtype=float)
    growth, dd = compound_and_settlement_dd(rows)
    if len(decisions):
        bounded = pd.DatetimeIndex([start, *decisions.tolist(), end])
        boundary_gaps = (bounded[1:] - bounded[:-1]) / pd.Timedelta(days=1)
        max_gap_days = float(boundary_gaps.max())
        gaps = (decisions[1:] - decisions[:-1]) / pd.Timedelta(hours=1)
        max_interentry = float(gaps.max()) if len(gaps) else np.nan
        exit_times = decisions + pd.Timedelta(hours=HOLD_HOURS)
        idle = np.r_[
            (decisions[0] - start) / pd.Timedelta(hours=1),
            ((decisions[1:] - exit_times[:-1]) / pd.Timedelta(hours=1)).to_numpy(),
            (end - exit_times[-1]) / pd.Timedelta(hours=1),
        ]
        max_idle = float(np.maximum(idle, 0.0).max())
    else:
        max_gap_days = (end - start) / pd.Timedelta(days=1)
        max_interentry = np.nan
        max_idle = float((end - start) / pd.Timedelta(hours=1))
    return {
        "selected": int(len(rows)),
        "priced": int(rows.net_pct.notna().sum()),
        "mean_net_pct": float(np.mean(returns)) if len(returns) else np.nan,
        "win_pct": float(100.0 * np.mean(returns > 0)) if len(returns) else np.nan,
        "fee_only_branch_growth_pct": growth,
        "settlement_drawdown_pct": dd,
        "max_interentry_gap_hours": max_interentry,
        "max_gap_including_period_edges_days": float(max_gap_days),
        "max_idle_after_exit_hours": max_idle,
        "active_months": int(rows.decision.dt.strftime("%Y-%m").nunique()),
    }


def _calendar_scope(rows: pd.DataFrame, period: str) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    if period == "all_history":
        start = pd.Timestamp("2020-01-01T00:00:00Z")
        return rows, start, DATA_END
    if period == "2026_to_2026-10-27":
        return rows[rows.decision.ge(OOS_START) & rows.decision.lt(DATA_END)], OOS_START, DATA_END
    year = int(period)
    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    return rows[rows.decision.ge(start) & rows.decision.lt(end)], start, end


def _portfolio_curve(btc: pd.DataFrame, alt: pd.DataFrame) -> dict:
    """Compute separate 1x sleeves from 50% starting capital, sampled at exits."""
    events = []
    for row in btc.itertuples():
        events.append((pd.Timestamp(row.exit_time), "BTC", float(row.net_pct)))
    for row in alt.itertuples():
        events.append((pd.Timestamp(row.decision) + pd.Timedelta(hours=HOLD_HOURS), "ALT", float(row.net_pct)))
    events.sort(key=lambda item: (item[0], item[1]))
    wealth = {"BTC": 1.0, "ALT": 1.0}
    curve = [1.0]
    # Apply simultaneous sleeve settlements together before sampling combined equity.
    cursor = 0
    while cursor < len(events):
        stamp = events[cursor][0]
        while cursor < len(events) and events[cursor][0] == stamp:
            _, sleeve, value = events[cursor]
            wealth[sleeve] *= 1.0 + value / 100.0
            cursor += 1
        curve.append(0.5 * (wealth["BTC"] + wealth["ALT"]))
    values = np.asarray(curve, dtype=float)
    dd = 100.0 * (1.0 - values / np.maximum.accumulate(values))
    btc_growth = 100.0 * (wealth["BTC"] - 1.0)
    alt_growth = 100.0 * (wealth["ALT"] - 1.0)
    return {
        "btc_fee_only_growth_pct": btc_growth,
        "alt_fee_only_growth_pct": alt_growth,
        "combo_50_50_fee_only_growth_pct": 0.5 * (btc_growth + alt_growth),
        "btc_settlement_drawdown_pct": _simple_dd(btc),
        "alt_settlement_drawdown_pct": _simple_dd(alt),
        "combo_50_50_settlement_drawdown_pct": float(dd.max()),
    }


def _simple_dd(rows: pd.DataFrame) -> float:
    _, dd = compound_and_settlement_dd(rows)
    return dd


def load_locked_btc() -> tuple[pd.DataFrame, pd.DataFrame]:
    btc = pd.read_parquet(BTC_LOCK).copy()
    btc["decision"] = _utc(btc.decision)
    btc["exit_time"] = _utc(btc.exit_time)
    btc["net_pct"] = pd.to_numeric(btc.net_pct, errors="raise")
    btc = btc[btc.decision.lt(DATA_END)].sort_values("decision", kind="stable").reset_index(drop=True)
    if len(btc) != 159 or int(btc.decision.ge(OOS_START).sum()) != 23:
        raise ValueError("The frozen BTC lock changed from 159 total / 23 through 2026-10-27")
    if btc.net_pct.isna().any():
        raise ValueError("Frozen BTC lock has missing fee-only outcomes; refusing to treat them as zero")
    if (btc.exit_time <= btc.decision).any():
        raise ValueError("Frozen BTC lock contains an invalid exit clock")
    if (btc.exit_time.to_numpy()[:-1] > btc.decision.to_numpy()[1:]).any():
        raise ValueError("Frozen BTC source has overlapping entries; cannot compose 1x sleeves")
    reference = pd.read_csv(BTC_REFERENCE)
    old = reference[
        reference.strategy.eq("below_plus_case")
        & reference.scope.eq("2026_funded")
        & reference.sleeve_deployment.eq(1.0)
        & reference.extra_roundtrip_pp.eq(0.0)
    ]
    if len(old) != 1:
        raise ValueError("Expected one frozen 2026 funded/marked BTC+ALT reference")
    return btc, old.iloc[0]


def _control_reference(selected_times: set[pd.Timestamp]) -> dict:
    controls = pd.read_csv(ALT_CONTROL, parse_dates=["decision"])
    controls["decision"] = _utc(controls.decision)
    controls = controls[
        controls.strategy.eq("baseline_6h")
        & controls.basis.eq("fee_net")
        & controls.eligible_80pct.astype(bool)
        & controls.decision.isin(selected_times)
    ]
    n = len(controls)
    return {
        "matched_baseline_events": n,
        "matched_baseline_actual_mean_pct": float(controls.actual_pct.mean()) if n else np.nan,
        "matched_baseline_random_mean_pct": float(controls.random_pct.mean()) if n else np.nan,
        "matched_baseline_excess_mean_pp": float(controls.paired_delta_pp.mean()) if n else np.nan,
        "matched_scope": "only pre-existing >=80%-member-supported 2026 baseline entry times retained in this cadence; added entries have no matched controls",
    }


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame, returns, raw_indices, axes = load_rule_and_raw()
    slots = axes[:, 0].astype(np.int64)
    folds = axes[:, 1].astype(np.int64)
    member_counts = load_member_counts(frame, raw_indices)

    raw = frame.iloc[raw_indices][[
        "known", "decision", "initial", "new_coins", "market_share_pct", "monitor_fraction"
    ]].copy().reset_index(drop=True)
    raw["known"] = _utc(raw.known)
    raw["decision"] = _utc(raw.decision)
    raw["frame_index"] = raw_indices
    raw["epoch"] = folds[raw_indices]
    raw["year"] = raw.decision.dt.year
    raw["year_month"] = raw.decision.dt.strftime("%Y-%m")
    raw["event_id"] = raw.decision.map(lambda t: f"{RAW_RULE_ID}|{t.isoformat()}")
    raw["member_count"] = raw.decision.map(member_counts).fillna(0).astype(int)
    raw["net_pct"] = returns[raw_indices, inputs.HOLDS.index(HOLD_HOURS), inputs.ASSETS.index("ALT")]
    raw["priced"] = raw.net_pct.notna()
    if raw.event_id.duplicated().any() or raw.decision.duplicated().any():
        raise ValueError("Raw pre-spacing event timestamps are not unique")
    if raw.member_count.le(0).any():
        raise ValueError("One or more raw events have no mapped ALT constituents")

    selected_by_gap: dict[int, set[int]] = {}
    for gap in GAPS_HOURS:
        selected_by_gap[gap] = set(admit(raw_indices, slots, folds, gap).tolist())
        raw[f"selected_{gap}h"] = raw.frame_index.isin(selected_by_gap[gap])
    selected_24 = selected_by_gap[24]
    baseline_locked = pd.read_parquet(FREQUENCY / "fixed/locked_events.parquet")
    baseline_locked = baseline_locked[baseline_locked.candidate_id.eq(RAW_RULE_ID)].copy()
    baseline_locked["decision"] = _utc(baseline_locked.decision)
    baseline_times = set(baseline_locked.decision)
    generated_24 = set(raw.loc[raw.selected_24h, "decision"])
    if len(generated_24) != 239 or generated_24 != baseline_times:
        raise ValueError(f"24h baseline admissions failed parity: got {len(generated_24)} events")
    cached_baseline = raw[raw.selected_24h].set_index("decision").net_pct.sort_index()
    locked_returns = baseline_locked.set_index("decision").net_pct.sort_index()
    common = cached_baseline.index.intersection(locked_returns.index)
    if len(common) != 239 or not np.allclose(
        cached_baseline.loc[common], locked_returns.loc[common], atol=1e-10, rtol=0,
    ):
        raise ValueError("24h baseline return parity failed against the frozen VectorBT result")

    rows = []
    monthly = []
    period_rows = [(str(year), year) for year in range(2020, 2027)]
    period_rows[-1] = ("2026_to_2026-10-27", 2026)
    period_rows.append(("all_history", None))
    for period, year in period_rows:
        if period == "all_history":
            sample_raw = raw
            start = pd.Timestamp("2020-01-01T00:00:00Z")
            end = DATA_END
        elif period == "2026_to_2026-10-27":
            sample_raw = raw[raw.decision.ge(OOS_START) & raw.decision.lt(DATA_END)]
            start, end = OOS_START, DATA_END
        else:
            start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
            end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
            sample_raw = raw[raw.year.eq(year)]
        base_set = set(sample_raw.loc[sample_raw.selected_24h, "frame_index"].astype(int))
        for gap in GAPS_HOURS:
            all_selected = sample_raw[sample_raw[f"selected_{gap}h"]].copy()
            current_set = set(all_selected.frame_index.astype(int))
            added = all_selected[~all_selected.frame_index.isin(base_set)]
            removed = sample_raw[
                sample_raw.frame_index.isin(base_set - current_set)
            ]
            metrics = _entry_metrics(all_selected, start, end)
            matched = _control_reference(
                set(all_selected.loc[all_selected.year.eq(2026), "decision"])
                & baseline_times
            ) if period == "2026_to_2026-10-27" else {
                "matched_baseline_events": np.nan,
                "matched_baseline_actual_mean_pct": np.nan,
                "matched_baseline_random_mean_pct": np.nan,
                "matched_baseline_excess_mean_pp": np.nan,
                "matched_scope": "available only on the pre-existing 2026 baseline member subset",
            }
            rows.append({
                "minimum_gap_hours": gap,
                "period": period,
                "raw_rule_signals": int(len(sample_raw)),
                "selected": metrics["selected"],
                "priced": metrics["priced"],
                "net_add_vs_24h": int(metrics["selected"] - len(base_set)),
                "new_vs_24h": int(len(added)),
                "new_priced": int(added.net_pct.notna().sum()),
                "replaced_24h": int(len(removed)),
                "replaced_priced": int(removed.net_pct.notna().sum()),
                "common_with_24h": int(len(current_set & base_set)),
                "new_mean_net_pct": float(added.net_pct.mean()) if added.net_pct.notna().any() else np.nan,
                "new_win_pct": float(100.0 * added.net_pct.dropna().gt(0).mean()) if added.net_pct.notna().any() else np.nan,
                "replaced_mean_net_pct": float(removed.net_pct.mean()) if removed.net_pct.notna().any() else np.nan,
                "replaced_win_pct": float(100.0 * removed.net_pct.dropna().gt(0).mean()) if removed.net_pct.notna().any() else np.nan,
                "mean_net_pct": metrics["mean_net_pct"],
                "win_pct": metrics["win_pct"],
                "fee_only_branch_growth_pct": metrics["fee_only_branch_growth_pct"],
                "settlement_drawdown_pct": metrics["settlement_drawdown_pct"],
                "max_interentry_gap_hours": metrics["max_interentry_gap_hours"],
                "max_gap_including_period_edges_days": metrics["max_gap_including_period_edges_days"],
                "max_idle_after_exit_hours": metrics["max_idle_after_exit_hours"],
                "active_months": metrics["active_months"],
                **matched,
                "funding_coverage": "not applied in this fee-only table; see sibling marks_2026.csv for explicit 2026 funding coverage",
                "mark_to_market_coverage": "not included in this settlement-only table; see sibling marks_2026.csv for 2026 5m path coverage",
                "notes": "Entry outcomes are the existing 6h VectorBT payoff cache after 5bp taker on each fill; DD is settlement DD, not marked DD. 12h/6h spacing is post-hoc.",
            })
            scoped_months = all_selected.groupby("year_month", sort=True)
            for month, group in scoped_months:
                month_added = group[~group.frame_index.isin(base_set)]
                monthly.append({
                    "minimum_gap_hours": gap,
                    "period": period,
                    "year_month": month,
                    "raw_rule_signals": int(sample_raw.year_month.eq(month).sum()),
                    "selected": int(len(group)),
                    "priced": int(group.net_pct.notna().sum()),
                    "new_vs_24h": int(len(month_added)),
                    "mean_net_pct": float(group.net_pct.mean()),
                    "win_pct": float(100.0 * group.net_pct.dropna().gt(0).mean()) if group.net_pct.notna().any() else np.nan,
                })

    spacing = pd.DataFrame(rows)
    spacing.to_csv(OUT / "spacing_summary.csv", index=False)
    pd.DataFrame(monthly).to_csv(OUT / "monthly_summary.csv", index=False)
    raw.to_csv(OUT / "events.csv", index=False)

    btc, old_reference = load_locked_btc()
    portfolio = []
    for gap in GAPS_HOURS:
        alt_all = raw[raw[f"selected_{gap}h"]].copy()
        for period, _ in period_rows:
            btc_scope, start, end = _calendar_scope(btc, period)
            alt_scope, _, _ = _calendar_scope(alt_all, period)
            if alt_scope.net_pct.isna().any():
                # Never drop an unknown selected event from the branch account.
                combo = {key: np.nan for key in (
                    "btc_fee_only_growth_pct", "alt_fee_only_growth_pct",
                    "combo_50_50_fee_only_growth_pct", "btc_settlement_drawdown_pct",
                    "alt_settlement_drawdown_pct", "combo_50_50_settlement_drawdown_pct",
                )}
            else:
                combo = _portfolio_curve(btc_scope, alt_scope)
            total_legs = len(btc_scope) + len(alt_scope)
            row = {
                "minimum_gap_hours": gap,
                "period": period,
                "btc_legs": int(len(btc_scope)),
                "alt_legs": int(len(alt_scope)),
                "total_legs": int(total_legs),
                "distinct_entry_times": int(pd.concat([
                    btc_scope.decision.rename("decision"),
                    alt_scope.decision.rename("decision"),
                ]).nunique()),
                **combo,
                "original_24h_2026_funded_growth_pct": float(old_reference.growth_pct),
                "original_24h_2026_marked_drawdown_pct": float(old_reference.max_dd_pct),
                "original_24h_2026_reference_legs": int(old_reference.btc_n + old_reference.alt_n),
                "funding_coverage": "fee-only in this table; sibling marks_2026.csv separately reports 2026 funding coverage",
                "mark_to_market_coverage": "settlement DD in this table; sibling marks_2026.csv separately reports 2026 5m mark coverage",
                "notes": "50/50 means two independent, unlevered 1x sleeves funded with half the initial capital each; profits remain in sleeve; settlement DD samples exits only. Original 2026 funded/5m marked numbers are shown as a separate frozen 24h reference.",
            }
            portfolio.append(row)
    pd.DataFrame(portfolio).to_csv(OUT / "portfolio_summary.csv", index=False)

    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "builder": "yoyo/evaluation/monday_breadth_alt_spacing.py",
        "builder_commit": _git_head(),
        "source_rule": "freq36_ALT: 5m, zero wait, share_5, any, none, fixed 6h; initial >=2; exclude BTC/ETH/SOL/USTC from ALT constituents",
        "single_variable": "minimum entry interval 24h to 12h or 6h; all other signal, member, hold, fee and frozen BTC rules unchanged",
        "hypothesis_status": "post-hoc assumption, not preregistered",
        "raw_pre_spacing_rule_signals": int(len(raw)),
        "baseline_24h_parity": {
            "selected_events": int(len(generated_24)),
            "matches_frozen_lock": True,
            "matches_cached_vectorbt_returns": True,
        },
        "vectorbt_payoff_source": "experiments/active/exp-monday-breadth-optimization-20261108-v1/payoffs/event_outcomes_minutes5_wait0.csv.gz",
        "source_entry_cutoff_exclusive_utc": DATA_END.isoformat(),
        "entry_and_exit_fee": "5bp taker per fill at the existing cached VectorBT event payoff; funding omitted",
        "btc_lock_source": str(BTC_LOCK.relative_to(ROOT)),
        "btc_lock_entries": int(len(btc)),
        "btc_lock_2026_entries": int(btc.decision.ge(OOS_START).sum()),
        "control_source": str(ALT_CONTROL.relative_to(ROOT)),
        "control_limit": "Only existing 2026 matched controls for original baseline entry clocks are reused; no controls inferred for new entries.",
        "risk_limit": "spacing_summary.csv and portfolio_summary.csv contain fee-only returns and settlement drawdown only. The separate mark_2026.py -> marks_2026.csv reports 2026 5m mark-to-market and funding coverage by cadence; missing observations stay explicit. Existing 24h funded/marked reference remains separately identified.",
        "outputs": ["spacing_summary.csv", "portfolio_summary.csv", "monthly_summary.csv", "events.csv", "manifest.json"],
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n")
    print(spacing[spacing.period.isin(["2026_to_2026-10-27", "all_history"])].to_string(index=False))
    print(pd.DataFrame(portfolio).query("period in ['all_history', '2026_to_2026-10-27']").to_string(index=False))


if __name__ == "__main__":
    run()

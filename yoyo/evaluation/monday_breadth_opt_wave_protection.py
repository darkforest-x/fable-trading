"""Evaluate causal individual-coin exits layered on the locked second-wave exit.

The locked event cohort and per-coin 144h VectorBT fills are frozen inputs. At
each event's locked 12h/24h checkpoint, a policy that is still open is closed
at the already-cached native fixed-horizon open-to-open return. A policy whose
recorded exit is strictly earlier keeps its original VectorBT fill. A fill at
the checkpoint timestamp is replaced by the checkpoint open, which is the
known-priority convention used by the fixed-horizon ledger.

No fills are re-simulated. Policy selection uses only epochs 0-2; epoch 3 is
reported as the 2026 locked evaluation. ALT is the equal-weight basket of the
eligible initial constituents in the frozen member bridge. All metrics use
events as observations, never constituent counts as independent samples.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_breadth_constituent_scope import exclusion_reason


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
OUT = EXPERIMENT / "exits"
LOCKED_EVENTS = OUT / "second_wave_requests/locked_events.parquet"
LOCKED_RULES = OUT / "second_wave_requests/locked_rules.csv"
POLICIES = OUT / "policies.csv"
FEATURE_MEMBERS = EXPERIMENT / "features/members.csv.gz"
FILLS = ROOT / "data/research/monday_breadth_optimization_20261108/exit_fills_v1"
FIXED = ROOT / "data/research/monday_breadth_optimization_20261108_v1/payoffs/constituents"
PAYOFFS = EXPERIMENT / "payoffs"
ASSETS = ("BTC", "ETH", "SOL", "ALT")
HORIZONS = (12, 24, 144)
PERIOD_LABELS = {
    0: "pre_2023",
    1: "2023_2024",
    2: "2025",
    3: "2026_locked",
}
SEED = 20261108


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def load_events() -> tuple[pd.DataFrame, pd.DataFrame]:
    events = pd.read_parquet(LOCKED_EVENTS)
    rules = pd.read_csv(LOCKED_RULES)
    rules = rules[["candidate_id", "minutes", "wait_min", "hold_hours", "policy_id"]]
    events = events.merge(rules, on="candidate_id", validate="many_to_one")
    events["known"] = _utc(events["known"])
    events["decision"] = _utc(events["decision"])
    if not (events.decision == events.known + pd.to_timedelta(events.wait_min, unit="m")).all():
        raise ValueError("Locked event decision does not match known + wait_min")
    if events.duplicated(["asset", "known"]).any():
        raise ValueError("Locked events must be unique within asset")
    events = events.reset_index(drop=True)
    events["event_i"] = np.arange(len(events), dtype=np.int32)
    return events, rules


def eligible_policies() -> pd.DataFrame:
    policy = pd.read_csv(POLICIES)
    # This is the complete six-day, non-partial universe. Entries with a full
    # take_fraction=1 target are allowed; partial fractions are not.
    keep = policy.hours.eq(144) & policy.take_fraction.isin([0.0, 1.0])
    result = policy.loc[keep].copy()
    result["policy_id"] = result.policy_id.astype(int)
    if result.policy_id.duplicated().any():
        raise ValueError("Duplicate policy_id in frozen policy table")
    return result.sort_values("policy_id").reset_index(drop=True)


def member_bridge(events: pd.DataFrame) -> pd.DataFrame:
    alt_events = events.loc[events.asset.eq("ALT"),
                            ["event_i", "asset", "minutes", "known", "decision", "exit_h"]].copy()
    source = pd.read_csv(FEATURE_MEMBERS, usecols=["minutes", "known", "symbol"],
                         parse_dates=["known"])
    source["known"] = _utc(source["known"])
    source = source[source.symbol.map(lambda s: not bool(exclusion_reason(s)))]
    alt = alt_events.merge(source, on=["minutes", "known"], how="left", validate="one_to_many")
    if alt.symbol.isna().any():
        raise ValueError("A locked ALT event has no eligible initial constituents")
    direct = events.loc[~events.asset.eq("ALT"),
                        ["event_i", "asset", "minutes", "known", "decision", "exit_h"]].copy()
    direct["symbol"] = direct.asset + "USDT"
    bridge = pd.concat([alt, direct], ignore_index=True)
    if bridge.duplicated(["event_i", "symbol"]).any():
        raise ValueError("Duplicate event constituent")
    counts = bridge.groupby("event_i").symbol.size()
    bridge["expected_members"] = bridge.event_i.map(counts).astype(np.int16)
    return bridge


def fixed_alt(symbol: str, minutes: int, knowns: pd.DatetimeIndex) -> pd.DataFrame:
    path = FIXED / f"minutes={minutes}" / f"symbol={symbol}" / "part-00000.parquet"
    columns = ["known", "wait_min", "hold_hours", "net_return_pct", "endpoint_available"]
    if not path.is_file():
        return pd.DataFrame(columns=["known", "fixed12", "fixed24", "fixed144"])
    frame = pd.read_parquet(path, columns=columns)
    frame["known"] = _utc(frame["known"])
    frame = frame[frame.known.isin(knowns) & frame.wait_min.eq(30) &
                  frame.hold_hours.isin(HORIZONS)].copy()
    frame["net_return_pct"] = frame.net_return_pct.where(frame.endpoint_available.astype(bool))
    if frame.duplicated(["known", "hold_hours"]).any():
        raise ValueError(f"Duplicate fixed-horizon rows for {symbol}")
    wide = frame.pivot(index="known", columns="hold_hours", values="net_return_pct")
    wide = wide.rename(columns={12: "fixed12", 24: "fixed24", 144: "fixed144"})
    return wide.reset_index()


def fixed_benchmarks(events: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for minutes, group in events.groupby("minutes", sort=True):
        path = PAYOFFS / f"event_outcomes_minutes{int(minutes)}_wait30.csv.gz"
        if not path.is_file():
            raise FileNotFoundError(path)
        frame = pd.read_csv(path, usecols=["known", "hold_hours", "btc_net_return_pct",
                                           "eth_net_return_pct", "sol_net_return_pct"],
                            parse_dates=["known"])
        frame["known"] = _utc(frame["known"])
        frame = frame[frame.known.isin(pd.DatetimeIndex(group.known.unique())) &
                      frame.hold_hours.isin(HORIZONS)]
        if frame.duplicated(["known", "hold_hours"]).any():
            raise ValueError(f"Duplicate benchmark fixed-horizon rows for W={minutes}")
        for asset in ("BTC", "ETH", "SOL"):
            z = frame[["known", "hold_hours", f"{asset.lower()}_net_return_pct"]].copy()
            z = z.rename(columns={f"{asset.lower()}_net_return_pct": "fixed_return"})
            z["asset"] = asset
            wide = z.pivot(index=["known", "asset"], columns="hold_hours", values="fixed_return")
            wide = wide.rename(columns={12: "fixed12", 24: "fixed24", 144: "fixed144"})
            parts.append(wide.reset_index())
    result = pd.concat(parts, ignore_index=True)
    if result.duplicated(["known", "asset"]).any():
        raise ValueError("Duplicate direct benchmark returns")
    return result


def _potential(native: np.ndarray, exit_time: pd.Series, decision: pd.Series,
               fixed_return: np.ndarray, hours: int) -> np.ndarray:
    deadline = decision + pd.to_timedelta(hours, unit="h")
    exited_before = (exit_time < deadline).to_numpy()
    return np.where(exited_before, native, fixed_return)


def build_policy_events(events: pd.DataFrame, bridge: pd.DataFrame,
                        policy_table: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    policy_ids = policy_table.policy_id.to_numpy(dtype=np.int32)
    id_to_col = {int(pid): i for i, pid in enumerate(policy_ids)}
    n_event = len(events)
    event_to_pos = {int(event_i): i for i, event_i in enumerate(events.event_i)}
    max_policy = len(policy_ids)
    # Event-level sums are accumulated by event and policy; constituent rows
    # never become independent observations.
    # slots 0=native, 1=locked wave, 2/3/4=h12/h24/h144 potentials
    # Native, actual, and three potentials are kept as separate arrays.
    sums = np.zeros((n_event, max_policy, 5), dtype=np.float64)
    counts = np.zeros((n_event, max_policy, 5), dtype=np.int16)
    direct_fixed = fixed_benchmarks(events)
    data_receipts: dict[str, dict] = {}

    for (asset, minutes, symbol), members in bridge.groupby(["asset", "minutes", "symbol"], sort=False):
        member = members[["event_i", "known", "decision", "exit_h", "expected_members"]].copy()
        if asset == "ALT":
            fixed = fixed_alt(symbol, int(minutes), pd.DatetimeIndex(member.known.unique()))
            member = member.merge(fixed, on="known", how="left", validate="many_to_one")
            fixed_path = FIXED / f"minutes={int(minutes)}" / f"symbol={symbol}" / "part-00000.parquet"
        else:
            fixed = direct_fixed[direct_fixed.asset.eq(asset)].drop(columns="asset")
            member = member.merge(fixed, on="known", how="left", validate="many_to_one")
            fixed_path = PAYOFFS / f"event_outcomes_minutes{int(minutes)}_wait30.csv.gz"

        fill_path = FILLS / f"{symbol}.parquet"
        need_ids = policy_ids.copy()
        # The two BTC-only policies are appended after all common policy ids.
        if asset != "BTC":
            btc_only = policy_table.loc[policy_table.kind.eq("btc_net_10000"), "policy_id"].to_numpy(dtype=np.int32)
            need_ids = policy_ids[~np.isin(policy_ids, btc_only)]
        if not fill_path.is_file():
            data_receipts[f"{asset}:{symbol}"] = {"fills_file": str(fill_path), "missing": True}
            continue
        fill_cols = ["entry_time", "policy_id", "exit_time", "net_pct", "exit_legs"]
        fill = pd.read_parquet(fill_path, columns=fill_cols)
        fill["entry_time"] = _utc(fill["entry_time"])
        fill["exit_time"] = _utc(fill["exit_time"])
        decisions = pd.DatetimeIndex(member.decision.unique())
        fill = fill[fill.entry_time.isin(decisions) & fill.policy_id.isin(need_ids) & fill.exit_legs.eq(1)].copy()
        if fill.duplicated(["entry_time", "policy_id"]).any():
            raise ValueError(f"Duplicate native fill for {symbol}")
        fill["policy_col"] = fill.policy_id.map(id_to_col).astype("int32")
        joined = member.merge(fill, left_on="decision", right_on="entry_time", how="left",
                              validate="one_to_many")
        joined["native"] = joined.net_pct
        for h, slot in ((12, 2), (24, 3), (144, 4)):
            joined[f"potential{h}"] = _potential(
                joined.native.to_numpy(dtype=float), joined.exit_time, joined.decision,
                joined[f"fixed{h}"].to_numpy(dtype=float), h)
        wave_slot = joined.exit_h.map({12: "potential12", 24: "potential24", 144: "potential144"})
        wave_vals = np.full(len(joined), np.nan, dtype=np.float64)
        for h in HORIZONS:
            mask = wave_slot.eq(f"potential{h}").to_numpy()
            wave_vals[mask] = joined.loc[mask, f"potential{h}"].to_numpy(dtype=float)
        joined["wave"] = wave_vals
        valid_policy = joined.policy_col.notna().to_numpy()
        rows = joined.loc[valid_policy]
        if len(rows):
            event_i = rows.event_i.map(event_to_pos).to_numpy(dtype=np.int64)
            col = rows.policy_col.to_numpy(dtype=np.int64)
            vals = np.column_stack([rows.native, rows.wave, rows.potential12,
                                    rows.potential24, rows.potential144]).astype(float)
            for j in range(5):
                finite = np.isfinite(vals[:, j])
                np.add.at(sums[:, :, j], (event_i[finite], col[finite]), vals[finite, j])
                np.add.at(counts[:, :, j], (event_i[finite], col[finite]), 1)
        data_receipts[f"{asset}:{symbol}"] = {
            "fills_file": str(fill_path), "fixed_file": str(fixed_path),
            "member_event_rows": int(len(member)), "native_fill_rows": int(len(fill)),
            "fixed_12_n": int(member.fixed12.notna().sum()),
            "fixed_24_n": int(member.fixed24.notna().sum()),
            "fixed_144_n": int(member.fixed144.notna().sum()),
        }

    # Materialize the complete asset-specific event x policy rectangle. Missing
    # fills remain visible with zero coverage instead of silently dropping events.
    pieces = []
    for asset, asset_events in events.groupby("asset", sort=False):
        pids = policy_ids.copy()
        if asset != "BTC":
            btc_only = policy_table.loc[policy_table.kind.eq("btc_net_10000"), "policy_id"].to_numpy(dtype=np.int32)
            pids = pids[~np.isin(pids, btc_only)]
        ix = asset_events.event_i.to_numpy(dtype=np.int32)
        grid = pd.MultiIndex.from_product([ix, pids], names=["event_i", "policy_id"]).to_frame(index=False)
        idx_ev = grid.event_i.map(event_to_pos).to_numpy(dtype=np.int64)
        idx_pid = np.array([id_to_col[int(p)] for p in grid.policy_id], dtype=np.int64)
        ev = events.set_index("event_i").loc[grid.event_i.to_numpy()].reset_index()
        grid = grid.join(ev.drop(columns=["event_i", "policy_id"], errors="ignore").reset_index(drop=True))
        expected = bridge.groupby("event_i").size()
        grid["expected_members"] = grid.event_i.map(expected).astype(np.int16)
        for j, name in enumerate(("native", "wave", "potential12", "potential24", "potential144")):
            n = counts[idx_ev, idx_pid, j]
            v = np.divide(sums[idx_ev, idx_pid, j], n, out=np.full(len(n), np.nan), where=n > 0)
            grid[f"{name}_n"] = n
            grid[f"{name}_pct"] = v
            grid[f"{name}_coverage_pct"] = 100 * n / grid.expected_members.to_numpy()
        grid["native_complete"] = grid.native_n.eq(grid.expected_members)
        grid["wave_complete"] = grid.wave_n.eq(grid.expected_members)
        grid["common_complete"] = grid.native_complete & grid.wave_complete
        grid["wave_difference_pct"] = grid.wave_pct - grid.native_pct
        grid["policy_key"] = grid.policy_id.map(policy_table.set_index("policy_id").key)
        pieces.append(grid)
    result = pd.concat(pieces, ignore_index=True)
    # Same event support across all exit policies per asset. This is the support
    # used for cross-policy selection and the four-period summaries.
    result["common_event"] = result.groupby(["asset", "event_i"]).common_complete.transform("all")
    policy5 = result[result.policy_id.eq(5)][["asset", "event_i", "wave_pct"]].rename(
        columns={"wave_pct": "locked_policy5_wave_pct"})
    result = result.merge(policy5, on=["asset", "event_i"], how="left", validate="many_to_one")
    return result, data_receipts


def _metrics(frame: pd.DataFrame) -> dict:
    z = frame.dropna(subset=["wave_pct", "native_pct"]).sort_values("decision")
    vals = z.wave_pct.to_numpy(dtype=float)
    native = z.native_pct.to_numpy(dtype=float)
    delta = vals - native
    if not len(vals):
        return {"n_events": 0, "wins": 0, "losses": 0, "win_pct": np.nan,
                "mean_net_pct": np.nan, "median_net_pct": np.nan, "mean_native_pct": np.nan,
                "mean_delta_pp": np.nan, "delta_positive_n": 0, "worst_event_pct": np.nan,
                "settlement_max_dd_pct": np.nan, "compounded_net_pct": np.nan,
                "mean_ex_best_month_pct": np.nan, "positive_baseline_upside_retention_pct": np.nan}
    cash = np.r_[1.0, np.cumprod(1.0 + vals / 100.0)]
    peaks = np.maximum.accumulate(cash)
    month = z.decision.dt.strftime("%Y-%m")
    month_sums = pd.Series(vals).groupby(month.to_numpy()).sum()
    best_month = month_sums.idxmax()
    exclude_best = month.ne(best_month).to_numpy()
    base_pos = native > 0
    denom = np.maximum(native[base_pos], 0).sum()
    retention = (100 * np.maximum(vals[base_pos], 0).sum() / denom) if denom > 0 else np.nan
    return {
        "n_events": int(len(vals)), "wins": int((vals > 0).sum()), "losses": int((vals <= 0).sum()),
        "win_pct": float(100 * np.mean(vals > 0)), "mean_net_pct": float(np.mean(vals)),
        "median_net_pct": float(np.median(vals)), "mean_native_pct": float(np.mean(native)),
        "mean_delta_pp": float(np.mean(delta)), "delta_positive_n": int((delta > 0).sum()),
        "worst_event_pct": float(np.min(vals)),
        "settlement_max_dd_pct": float(100 * np.max(1.0 - cash / peaks)),
        "compounded_net_pct": float(100 * (cash[-1] - 1.0)),
        "mean_ex_best_month_pct": float(vals[exclude_best].mean()) if exclude_best.any() else np.nan,
        # Among events where the same policy's unprotected exit made money, how
        # much positive PnL remained after wave protection? This may exceed 100%.
        "positive_baseline_upside_retention_pct": float(retention),
    }


def summarize(policy_events: pd.DataFrame, policies: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary = []
    common = policy_events[policy_events.common_event].copy()
    for (asset, policy_id, epoch), group in common.groupby(["asset", "policy_id", "epoch"], sort=False):
        summary.append({"asset": asset, "policy_id": int(policy_id),
                        "policy_key": group.policy_key.iloc[0], "epoch": int(epoch),
                        "period": PERIOD_LABELS[int(epoch)], **_metrics(group)})
    summary = pd.DataFrame(summary)
    pre = common[common.epoch.le(2)]
    pre_stats = []
    for (asset, policy_id), group in pre.groupby(["asset", "policy_id"], sort=False):
        epn = group.groupby("epoch").event_i.nunique()
        stats = _metrics(group)
        # The primary candidate must have adequate representation in all three
        # pre-2026 phases and retain positive baseline trade PnL.
        per_epoch_floor = min((int(epn.get(i, 0)) for i in (0, 1, 2)), default=0)
        stats.update(asset=asset, policy_id=int(policy_id), policy_key=group.policy_key.iloc[0],
                     pre_n_events=int(group.event_i.nunique()), pre_epoch_min_n=per_epoch_floor)
        pre_stats.append(stats)
    pre_stats = pd.DataFrame(pre_stats).merge(
        policies[["policy_id", "kind", "stop_pct", "activate_pct", "width", "take_pct", "take_fraction"]],
        on="policy_id", how="left", validate="many_to_one")
    # The baseline is the locked, six-day time policy (id 5) with the same wave
    # overlay; each row's native result remains the same-policy counterfactual.
    baseline_rows = [{"asset": asset, **_metrics(group)}
                     for asset, group in pre[pre.policy_id.eq(5)].groupby("asset", sort=False)]
    base = pd.DataFrame(baseline_rows)
    if not base.empty:
        base = base.rename(columns={"settlement_max_dd_pct": "base_dd", "mean_net_pct": "base_mean",
                                    "mean_ex_best_month_pct": "base_exbest"})[
                                        ["asset", "base_dd", "base_mean", "base_exbest"]]
        pre_stats = pre_stats.merge(base, on="asset", how="left", validate="many_to_one")
    else:
        pre_stats["base_dd"] = np.nan
    pre_stats["primary_eligible"] = (
        pre_stats.pre_epoch_min_n.ge(10) & pre_stats.mean_net_pct.gt(0) &
        pre_stats.mean_ex_best_month_pct.gt(0) & pre_stats.mean_delta_pp.gt(0) &
        pre_stats.positive_baseline_upside_retention_pct.ge(80) &
        pre_stats.settlement_max_dd_pct.le(pre_stats.base_dd)
    )
    pre_stats["risk_first_eligible"] = (
        pre_stats.pre_epoch_min_n.ge(10) & pre_stats.mean_net_pct.gt(0) &
        pre_stats.mean_ex_best_month_pct.gt(0) &
        (pre_stats.base_dd - pre_stats.settlement_max_dd_pct).ge(10) &
        pre_stats.positive_baseline_upside_retention_pct.ge(50)
    )
    selected = []
    for asset, group in pre_stats.groupby("asset", sort=False):
        primary = group[group.primary_eligible].sort_values(
            ["settlement_max_dd_pct", "mean_ex_best_month_pct", "mean_delta_pp",
             "positive_baseline_upside_retention_pct"], ascending=[True, False, False, False], kind="stable")
        risk = group[group.risk_first_eligible].sort_values(
            ["settlement_max_dd_pct", "mean_net_pct", "positive_baseline_upside_retention_pct"],
            ascending=[True, False, False], kind="stable")
        base_row = group[group.policy_id.eq(5)].iloc[0]
        primary_row = primary.iloc[0] if len(primary) else base_row
        risk_row = risk.iloc[0] if len(risk) else None
        for role, row in (("primary", primary_row), ("risk_first", risk_row)):
            if row is None:
                selected.append({"asset": asset, "role": role, "policy_id": np.nan,
                                 "policy_key": "none_supported", "selection_status": "none_supported"})
            else:
                selected.append({"asset": asset, "role": role, "policy_id": int(row.policy_id),
                                 "policy_key": row.policy_key,
                                 "selection_status": "selected_pre_2026" if role == "primary" and len(primary)
                                 else ("risk_threshold_met" if role == "risk_first" else "none_supported"),
                                 "selection_pre_n": int(row.pre_n_events),
                                 "pre_mean_net_pct": float(row.mean_net_pct),
                                 "pre_mean_delta_pp": float(row.mean_delta_pp),
                                 "pre_settlement_max_dd_pct": float(row.settlement_max_dd_pct),
                                 "pre_ex_best_month_mean_pct": float(row.mean_ex_best_month_pct),
                                 "pre_upside_retention_pct": float(row.positive_baseline_upside_retention_pct),
                                 "kind": row.kind, "stop_pct": row.stop_pct, "activate_pct": row.activate_pct,
                                 "width": row.width, "take_pct": row.take_pct, "take_fraction": row.take_fraction})
    candidates = pd.DataFrame(selected)
    return summary, candidates


def permutation_test(frame: pd.DataFrame, draws: int = 20_000) -> dict:
    """Conditional same-month horizon-label permutation for locked 2026 events."""
    z = frame.dropna(subset=["native_pct", "potential12_pct", "potential24_pct", "potential144_pct"]).copy()
    if z.empty:
        return {"permutation_n": 0, "p_two_sided": np.nan, "p_positive": np.nan,
                "observed_mean_delta_pp": np.nan, "permutation_draws": 0}
    z = z.sort_values("decision")
    native = z.native_pct.to_numpy(float)
    potentials = z[["potential12_pct", "potential24_pct", "potential144_pct"]].to_numpy(float)
    hours = z.exit_h.to_numpy(int)
    h_index = {12: 0, 24: 1, 144: 2}
    observed = potentials[np.arange(len(z)), np.array([h_index[h] for h in hours])] - native
    observed_mean = float(observed.mean())
    month_codes, month_values = pd.factorize(z.decision.dt.strftime("%Y-%m"), sort=True)
    blocks = [np.flatnonzero(month_codes == i) for i in range(len(month_values))]
    rng = np.random.default_rng(SEED + int(z.policy_id.iloc[0]))
    null = np.empty(draws, dtype=float)
    for b in range(draws):
        assigned = hours.copy()
        for idx in blocks:
            if len(idx) > 1:
                assigned[idx] = rng.permutation(assigned[idx])
        deltas = potentials[np.arange(len(z)), np.array([h_index[h] for h in assigned])] - native
        null[b] = deltas.mean()
    p_two = (1 + np.count_nonzero(np.abs(null) >= abs(observed_mean) - 1e-12)) / (draws + 1)
    p_positive = (1 + np.count_nonzero(null >= observed_mean - 1e-12)) / (draws + 1)
    # Exact number of distinct horizon assignments within month (bounded for audit).
    distinct = 1
    for idx in blocks:
        labels, counts = np.unique(hours[idx], return_counts=True)
        ways = 1
        remain = int(len(idx))
        from math import comb
        for n in counts[:-1]:
            ways *= comb(remain, int(n))
            remain -= int(n)
        distinct *= ways
        if distinct > 10**12:
            distinct = 10**12
            break
    return {"permutation_n": int(len(z)), "permutation_months": int(len(blocks)),
            "distinct_assignments_capped_1e12": int(distinct),
            "observed_mean_delta_pp": observed_mean, "null_mean_delta_pp": float(null.mean()),
            "p_two_sided": float(p_two), "p_positive": float(p_positive),
            "permutation_draws": int(draws)}


def run(draws: int = 20_000) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    events, _ = load_events()
    policies = eligible_policies()
    bridge = member_bridge(events)
    policy_events, receipts = build_policy_events(events, bridge, policies)

    # Exact baseline checks against the frozen locked member basket and the
    # previously materialized event-level wave accounting.
    check = policy_events[policy_events.policy_id.eq(5)].copy()
    complete = check[check.common_complete]
    actual_error = (complete.wave_pct - complete.net_pct).abs()
    baseline_error = (complete.native_pct - complete.baseline_pct).abs()
    if len(complete) != len(locked) or actual_error.max() > 1e-7 or baseline_error.max() > 1e-7:
        raise ValueError(f"Locked policy 5 check failed: n={len(complete)}/{len(locked)}, "
                         f"wave_err={actual_error.max()}, native_err={baseline_error.max()}")

    summary, candidates = summarize(policy_events, policies)
    policy_events.to_parquet(OUT / "wave_protection_policy_events.parquet", index=False)
    summary.to_csv(OUT / "wave_protection_policy_summary.csv", index=False)
    candidates.to_csv(OUT / "wave_protection_locked_candidates.csv", index=False)

    # Test only the final pre-2026 choices on the untouched 2026 epoch, with the
    # within-calendar-month count of each locked wave horizon held fixed.
    perm = []
    for row in candidates.itertuples():
        if not np.isfinite(row.policy_id):
            perm.append({"asset": row.asset, "role": row.role, "policy_id": np.nan,
                         "policy_key": row.policy_key, "test_period": "2026_locked",
                         "permutation_draws": 0, "p_two_sided": np.nan, "p_positive": np.nan})
            continue
        panel = policy_events[(policy_events.asset.eq(row.asset)) &
                              (policy_events.policy_id.eq(int(row.policy_id))) &
                              (policy_events.epoch.eq(3)) & policy_events.common_event].copy()
        result = permutation_test(panel, draws=draws)
        perm.append({"asset": row.asset, "role": row.role, "policy_id": int(row.policy_id),
                     "policy_key": row.policy_key, "test_period": "2026_locked", **result})
    perm = pd.DataFrame(perm)
    perm.to_csv(OUT / "wave_protection_permutation_2026.csv", index=False)

    counts = (policy_events[policy_events.common_event]
              .groupby(["asset", "epoch"], as_index=False).event_i.nunique()
              .rename(columns={"event_i": "common_event_n"}))
    expected_by_event = bridge.groupby(["event_i", "asset"], as_index=False).agg(
        expected_members=("symbol", "size"))
    audit = {
        "builder": "yoyo/evaluation/monday_breadth_opt_wave_protection.py",
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_locked_events": str(LOCKED_EVENTS.relative_to(ROOT)),
        "source_locked_rules": str(LOCKED_RULES.relative_to(ROOT)),
        "source_policy_fills": str(FILLS.relative_to(ROOT)),
        "source_fixed_alt_returns": str(FIXED.relative_to(ROOT)),
        "source_fixed_major_returns": str(PAYOFFS.relative_to(ROOT)),
        "event_n": int(len(events)), "locked_policy_event_rows_expected": int(len(events)),
        "locked_policy5_common_rows": int(len(complete)),
        "policy_filter": "hours=144; take_fraction in {0,1}; actual exit_legs=1; BTC-only policies applied to BTC",
        "eligible_policy_n": {asset: int(len(policies) if asset == "BTC" else len(policies) - policies.kind.eq("btc_net_10000").sum())
                               for asset in ASSETS},
        "epoch_labels": PERIOD_LABELS,
        "strict_early_exit_rule": "native exit_time < decision + wave exit_h; exact checkpoint timestamp uses checkpoint open",
        "fixed_return_fee_basis": "existing open-to-open native fixed-horizon net_return_pct (two 5bp taker legs)",
        "alt_weighting": "equal weight eligible initial members after constituent-scope exclusion; USTCUSDT excluded only from ALT return basket",
        "observation_unit": "locked event; constituents are aggregated within each event",
        "policy5_max_abs_wave_error_pp": float(actual_error.max()),
        "policy5_max_abs_native144_error_pp": float(baseline_error.max()),
        "common_event_n_by_asset_epoch": counts.to_dict("records"),
        "bridge_expected_members_min": int(expected_by_event.expected_members.min()),
        "bridge_expected_members_max": int(expected_by_event.expected_members.max()),
        "data_receipts": receipts,
        "primary_selection": "pre-2026 epochs 0-2; at least 10 events in each; positive mean and best-month-excluded mean; positive paired mean delta; at least 80% positive baseline upside retention; no worse same-policy settlement max drawdown; lexicographic minimum drawdown then best-month-excluded mean, paired delta, retention. If none qualifies, retain policy 5 wave baseline.",
        "risk_first_selection": "pre-2026 epochs 0-2; at least 10 events in each; positive mean and best-month-excluded mean; at least 10 percentage-point lower settlement max drawdown than policy 5 wave baseline; at least 50% positive baseline upside retention; minimize drawdown.",
        "settlement_dd": "compound one event at a time in decision order; drawdown on event settlement equity including initial 1.0",
        "best_month_removed": "mean event net after removing all events in the calendar month with the highest sum of event returns",
        "permutation": "2026 only; randomly permute locked 12h/24h/144h horizon labels within calendar month preserving each month's counts; 20k draws by default; event-level paired difference to same-policy native fill",
    }
    (OUT / "wave_protection_coverage_audit.json").write_text(json.dumps(audit, indent=2, default=str) + "\n")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--permutations", type=int, default=20_000)
    args = parser.parse_args()
    audit = run(draws=args.permutations)
    print(json.dumps({k: audit[k] for k in ("event_n", "eligible_policy_n", "locked_policy5_common_rows",
                                            "policy5_max_abs_wave_error_pp", "policy5_max_abs_native144_error_pp",
                                            "common_event_n_by_asset_epoch", "builder_commit")}, indent=2))


if __name__ == "__main__":
    main()

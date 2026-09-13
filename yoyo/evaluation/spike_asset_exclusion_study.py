"""Causal monthly asset exclusion on frozen V1/V8 outcome ledgers.

The sole intervention is an asset admission list.  At each UTC month boundary,
the list uses only original-ledger trades that had *already closed* in the
preceding 90 days.  It ranks assets after pooling venues, takes the bottom 20%
of every asset with at least ten observations, then excludes only selected
assets whose historical mean net R is negative.  The original complete ledger
remains the ranking shadow, so an earlier exclusion cannot manufacture later
history by removing a losing trade.

Input is a previously authenticated, unified CSV ledger.  Required columns
are ``system_source``, ``stream_key``, ``asset``, ``timeframe_min``, ``side``,
``signal_confirm_time``, ``exit_time``, ``net_r``, ``net_return``,
``mfe_r``, ``closed``, and ``scoring_closed``.  No OHLCV, signal construction,
execution, costs, stop, or exit logic is read or changed here.  V1 is frozen
long-only; V8 long-only and bidirectional views are separate fixed cohorts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


EXPERIMENT = Path("experiments/active/exp-spike-v1-v8-asset-ranking-20260914-v1")
START = pd.Timestamp("2024-09-10T00:00:00Z")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
LOOKBACK = pd.Timedelta(days=90)
MIN_TRADES = 10
BOTTOM_FRACTION = 0.20
RANDOM_SEEDS = tuple(range(100))
TIMEFRAMES = (30, 60, 240)
REQUIRED = {
    "system_source", "stream_key", "asset", "timeframe_min", "side", "signal_confirm_time",
    "exit_time", "net_r", "net_return", "mfe_r", "closed", "scoring_closed",
}


def sha256(path: Path) -> str:
    """Return a streaming SHA-256 for a frozen input or produced artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_bool(values: pd.Series, name: str) -> pd.Series:
    """Normalize one ledger boolean and reject malformed values."""
    mapping = {True: True, False: False, "True": True, "False": False, 1: True, 0: False}
    invalid = values.notna() & ~values.isin(mapping)
    if invalid.any():
        raise ValueError(f"{name} contains non-boolean values")
    return values.map(mapping).astype("boolean")


def _known_closed_outcome(frame: pd.DataFrame) -> pd.Series:
    """Identify a closed result that is usable without evaluating an open trade."""
    return (
        frame["closed"].eq(True)
        & frame["exit_time"].notna()
        & np.isfinite(frame[["net_r", "net_return"]]).all(axis=1)
    )


def load_ledger(path: Path) -> pd.DataFrame:
    """Load and normalize the prior authenticated unified ledger only.

    The feature clocks consumed are ``signal_confirm_time`` and ``exit_time``;
    no later bar value is used.  A trade's result becomes ranking history only
    once its exit timestamp is strictly before a subsequent decision boundary.
    """
    frame = pd.read_csv(path, low_memory=False)
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError("unified ledger missing columns: " + ", ".join(sorted(missing)))
    frame = frame.loc[:, sorted(REQUIRED)].copy()
    for column in ("signal_confirm_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in ("net_r", "net_return", "mfe_r"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timeframe_min"] = pd.to_numeric(frame["timeframe_min"], errors="coerce")
    frame["side"] = pd.to_numeric(frame["side"], errors="coerce")
    frame["closed"] = _as_bool(frame["closed"], "closed")
    frame["scoring_closed"] = _as_bool(frame["scoring_closed"], "scoring_closed")
    valid = (
        frame["system_source"].isin(["v1_common_execution_long", "v8"])
        & frame["stream_key"].notna()
        & frame["asset"].notna()
        & frame["timeframe_min"].isin(TIMEFRAMES)
        & frame["side"].isin([-1, 1])
        & frame["signal_confirm_time"].notna()
    )
    if not valid.all():
        raise ValueError(f"unified ledger has {int((~valid).sum())} invalid rows")
    if frame.duplicated(["system_source", "stream_key", "side", "signal_confirm_time", "exit_time"]).any():
        raise ValueError("unified ledger has duplicate normalized trade identities")
    frame["strategy"] = frame["system_source"].map({"v1_common_execution_long": "v1", "v8": "v8"})
    frame["known_closed_outcome"] = _known_closed_outcome(frame)
    return frame.sort_values(["strategy", "signal_confirm_time", "exit_time"], kind="mergesort").reset_index(drop=True)


def scopes(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return the fixed V1/V8 cohorts without inventing V1 short trades."""
    output = {
        "v1_long": frame.loc[frame.strategy.eq("v1") & frame.side.eq(1)].copy(),
        "v8_long": frame.loc[frame.strategy.eq("v8") & frame.side.eq(1)].copy(),
        "v8_both": frame.loc[frame.strategy.eq("v8")].copy(),
    }
    if (frame.strategy.eq("v1") & frame.side.eq(-1)).any():
        raise ValueError("unified V1 ledger must remain long-only")
    return output


def decision_times() -> list[pd.Timestamp]:
    """Return the initial no-history date followed by UTC calendar boundaries."""
    starts = [START]
    first_month = (START + pd.offsets.MonthBegin(1)).normalize()
    starts.extend(pd.date_range(first_month, END, freq="MS", tz="UTC").tolist())
    return [time for time in starts if time < END]


def _seed(seed: int, scope: str, decision_time: pd.Timestamp) -> int:
    """Derive one reproducible draw from a fixed public seed and month key."""
    text = f"{seed}|{scope}|{decision_time.isoformat()}".encode()
    return int.from_bytes(hashlib.sha256(text).digest()[:8], "big", signed=False)


def monthly_lists(frame: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Build causal asset lists from the unchanged original-ledger shadow.

    Each decision uses rows with ``exit_time`` in ``[month-90d, month)`` and
    both frozen closed flags true.  The initial partial research month is an
    explicit no-exclusion row; later months may use a partial lookback only if
    ten prior exits exist for that asset.
    """
    rows: list[dict[str, object]] = []
    # ``scoring_closed`` is an evaluation-split denominator, not a fact known
    # at the decision clock.  A trade closed before this decision is usable for
    # the 90-day shadow ranking even if it later falls across the global split.
    history = frame.loc[_known_closed_outcome(frame)].copy()
    for at in decision_times():
        if at == START:
            rows.append({
                "scope": scope, "decision_time": at, "asset": pd.NA,
                "history_trades": 0, "history_net_r_sum": 0.0, "history_mean_net_r": np.nan,
                "eligible": False, "bottom_rank": pd.NA, "bottom_pool_n": 0,
                "negative_after_bottom_rank": False, "causal_excluded": False,
                "reason": "initial_research_month_no_prior_history",
            })
            continue
        prior = history.loc[(history.exit_time >= at - LOOKBACK) & (history.exit_time < at)]
        ranking = prior.groupby("asset", sort=True).agg(
            history_trades=("net_r", "size"),
            history_net_r_sum=("net_r", "sum"),
            history_mean_net_r=("net_r", "mean"),
        ).reset_index()
        ranking["eligible"] = ranking.history_trades.ge(MIN_TRADES)
        eligible = ranking.loc[ranking.eligible].sort_values(
            ["history_mean_net_r", "asset"], kind="mergesort"
        ).reset_index(drop=True)
        take = int(np.ceil(BOTTOM_FRACTION * len(eligible)))
        eligible["bottom_rank"] = np.arange(1, len(eligible) + 1)
        ranking = ranking.merge(eligible.loc[:, ["asset", "bottom_rank"]], on="asset", how="left")
        ranking["bottom_pool_n"] = take
        ranking["negative_after_bottom_rank"] = (
            ranking.bottom_rank.le(take) & ranking.history_mean_net_r.lt(0)
        )
        ranking["causal_excluded"] = ranking.negative_after_bottom_rank
        ranking["scope"] = scope
        ranking["decision_time"] = at
        ranking["reason"] = np.where(ranking.eligible, "ranked_from_prior_closed_90d", "fewer_than_10_prior_closed_trades")
        rows.extend(ranking.to_dict("records"))
    return pd.DataFrame(rows)


def random_asset_lists(lists: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Draw 100 fixed-seed equal-asset-count causal controls per month.

    Each seed draws from the same n>=10 asset universe as the causal list and
    deletes exactly the causal list's realised asset count, rather than trying
    to equalize the number of affected trades.  No random selection observes
    a future outcome.
    """
    rows: list[dict[str, object]] = []
    for at, ranking in lists.loc[lists.asset.notna()].groupby("decision_time", sort=True):
        eligible = ranking.loc[ranking.eligible.eq(True), "asset"].to_numpy()
        causal_count = int(ranking.causal_excluded.sum())
        if causal_count > len(eligible):
            raise ValueError("causal asset deletion count exceeds rankable asset universe")
        for seed in RANDOM_SEEDS:
            if causal_count == 0:
                continue
            selected = np.random.default_rng(_seed(seed, scope, at)).choice(
                eligible, size=causal_count, replace=False
            )
            rows.extend({
                "scope": scope,
                "decision_time": at,
                "random_seed": seed,
                "asset": asset,
                "causal_excluded_assets_n": causal_count,
            } for asset in selected)
    return pd.DataFrame(rows, columns=["scope", "decision_time", "random_seed", "asset", "causal_excluded_assets_n"])


def attach_lists(frame: pd.DataFrame, lists: pd.DataFrame) -> pd.DataFrame:
    """Attach each trade's pre-existing month list by confirmation clock.

    A signal confirmed before a UTC boundary stays governed by the preceding
    list even when its next-open entry lands after the boundary.
    """
    decisions = lists.loc[lists.asset.notna(), ["decision_time", "asset", "causal_excluded"]].copy()
    frame = frame.copy()
    frame["decision_time"] = (
        pd.DatetimeIndex(frame.signal_confirm_time)
        .tz_localize(None)
        .to_period("M")
        .to_timestamp()
        .tz_localize("UTC")
    )
    frame.loc[frame.signal_confirm_time.lt(START + pd.offsets.MonthBegin(1)), "decision_time"] = START
    joined = frame.merge(decisions, on=["decision_time", "asset"], how="left", validate="many_to_one")
    joined["causal_excluded"] = joined.causal_excluded.eq(True)
    return joined


def attach_random_list(frame: pd.DataFrame, random_lists: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Mark only one fixed-seed random asset list on an already clocked ledger."""
    chosen = random_lists.loc[random_lists.random_seed.eq(seed), ["decision_time", "asset"]].copy()
    chosen["random_excluded"] = True
    joined = frame.merge(chosen, on=["decision_time", "asset"], how="left", validate="many_to_one")
    joined["random_excluded"] = joined.random_excluded.eq(True)
    return joined


def _pf(part: pd.DataFrame, column: str) -> float:
    gains = float(part.loc[part[column].gt(0), column].sum())
    losses = float(-part.loc[part[column].lt(0), column].sum())
    return np.inf if losses == 0 and gains > 0 else (gains / losses if losses else np.nan)


def _metrics(part: pd.DataFrame) -> dict[str, object]:
    if part.empty:
        return {
            "trades": 0, "net_r_sum": 0.0, "net_r_mean": np.nan,
            "win_rate": np.nan, "profit_factor_net_r": np.nan,
            "profit_factor_net_return": np.nan, "realized_10r": 0,
        }
    return {
        "trades": int(len(part)),
        "net_r_sum": float(part.net_r.sum()),
        "net_r_mean": float(part.net_r.mean()),
        "win_rate": float(part.net_r.gt(0).mean()),
        "profit_factor_net_r": float(_pf(part, "net_r")),
        "profit_factor_net_return": float(_pf(part, "net_return")),
        "realized_10r": int(part.net_r.ge(10).sum()),
    }


def _summary_rows(scored: pd.DataFrame, scope: str, policy: str, retained: pd.Series, random_seed: int | None) -> list[dict[str, object]]:
    """Calculate all fixed reporting levels for one admission policy."""
    levels: dict[str, list[str]] = {
        "period": ["period"],
        "timeframe_side": ["period", "timeframe_min", "side"],
        "month": ["period", "calendar_month"],
    }
    rows: list[dict[str, object]] = []
    for level, keys in levels.items():
        for values, baseline in scored.groupby(keys, dropna=False, sort=True):
            values = values if isinstance(values, tuple) else (values,)
            base_metrics = _metrics(baseline)
            part = baseline.loc[retained.loc[baseline.index]]
            row = {
                "scope": scope, "level": level, "policy": policy,
                "random_seed": random_seed, **dict(zip(keys, values)), **_metrics(part),
            }
            row["baseline_trades"] = base_metrics["trades"]
            row["entry_retention"] = float(len(part) / len(baseline)) if len(baseline) else np.nan
            row["baseline_realized_10r"] = base_metrics["realized_10r"]
            row["tail_10r_retention"] = (
                float(row["realized_10r"] / base_metrics["realized_10r"])
                if base_metrics["realized_10r"] else np.nan
            )
            rows.append(row)
    return rows


def summaries(frame: pd.DataFrame, scope: str, random_lists: pd.DataFrame) -> pd.DataFrame:
    """Compare fixed-ledger retention without replaying excluded transactions."""
    scored = frame.loc[
        frame.scoring_closed.eq(True)
        & _known_closed_outcome(frame)
        & frame.signal_confirm_time.ge(START)
        & frame.signal_confirm_time.lt(END)
    ].copy()
    scored["period"] = np.where(scored.signal_confirm_time.lt(SPLIT), "development", "validation")
    scored["calendar_month"] = scored.signal_confirm_time.dt.strftime("%Y-%m")
    rows = _summary_rows(scored, scope, "baseline", pd.Series(True, index=scored.index), None)
    rows.extend(_summary_rows(scored, scope, "causal_bottom20_negative", ~scored.causal_excluded, None))
    for seed in RANDOM_SEEDS:
        selected = attach_random_list(scored, random_lists, seed)
        rows.extend(_summary_rows(
            selected, scope, "random_equal_asset_count", ~selected.random_excluded, seed
        ))
    return pd.DataFrame(rows)


def asset_leaderboard(frame: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Return descriptive V1/V8 asset rankings for the requested frozen ledger."""
    scored = frame.loc[
        frame.scoring_closed.eq(True)
        & _known_closed_outcome(frame)
        & frame.signal_confirm_time.ge(START)
        & frame.signal_confirm_time.lt(END)
    ].copy()
    scored["period"] = np.where(scored.signal_confirm_time.lt(SPLIT), "development", "validation")
    rows: list[dict[str, object]] = []
    period_parts: list[tuple[str, pd.DataFrame]] = [("full", scored)]
    period_parts.extend((str(period), part) for period, part in scored.groupby("period", sort=True))
    for period, part in period_parts:
        for asset, asset_part in part.groupby("asset", sort=True):
            rows.append({"scope": scope, "period": period, "asset": asset, **_metrics(asset_part)})
    result = pd.DataFrame(rows)
    if not result.empty:
        result["rank_by_net_r_sum"] = result.groupby(["scope", "period"])["net_r_sum"].rank(method="first", ascending=False).astype(int)
    return result


def primary_rank_p(summary: pd.DataFrame) -> pd.DataFrame:
    """Report the predeclared one-tailed V8-both validation net-R rank p.

    This is exploratory context against 100 equal-*asset*-count controls.  The
    statistic is fixed at retained net-R sum: larger is favourable, and a tie
    counts as a random control at least as favourable as the causal exclusion.
    """
    selector = (
        summary.scope.eq("v8_both")
        & summary.level.eq("period")
        & summary.period.eq("validation")
    )
    causal = summary.loc[selector & summary.policy.eq("causal_bottom20_negative")]
    random = summary.loc[selector & summary.policy.eq("random_equal_asset_count")]
    if len(causal) != 1 or len(random) != len(RANDOM_SEEDS):
        raise ValueError("primary V8-both validation random distribution is incomplete")
    observed = float(causal.net_r_sum.iloc[0])
    exceed = int(random.net_r_sum.ge(observed).sum())
    return pd.DataFrame([{
        "scope": "v8_both",
        "period": "validation",
        "statistic": "retained_net_r_sum",
        "alternative": "causal_exclusion_greater_than_random_asset_controls",
        "causal_value": observed,
        "random_draws": int(len(random)),
        "random_at_least_causal": exceed,
        "one_tailed_rank_p": float((exceed + 1) / (len(random) + 1)),
        "formula": "(random_at_least_causal + 1) / (random_draws + 1)",
        "exploratory": True,
    }])


def accounting_attribution(primary: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Decompose one already-fixed V8-both validation result by removed asset.

    This is arithmetic on the existing causal admission outcome.  In
    particular, the ``excluding_USDC`` subtotal removes USDC from *both* the
    baseline and retained books; it is not a proposed alternative exclusion.
    """
    baseline = primary.copy()
    removed = baseline.loc[baseline.causal_excluded]
    retained = baseline.loc[~baseline.causal_excluded]
    improvement = float(retained.net_r.sum() - baseline.net_r.sum())
    by_asset = removed.groupby("asset", sort=True).agg(
        removed_trades=("net_r", "size"),
        removed_net_r_sum=("net_r", "sum"),
        removed_net_r_mean=("net_r", "mean"),
        removed_realized_10r=("net_r", lambda values: int(values.ge(10).sum())),
    ).reset_index()
    by_asset["improvement_contribution_net_r"] = -by_asset.removed_net_r_sum
    by_asset["improvement_share"] = (
        by_asset.improvement_contribution_net_r / improvement if improvement else np.nan
    )
    by_asset.insert(0, "record_type", "asset_removed_contribution")
    totals: list[dict[str, object]] = []
    for label, asset_filter in (("all_assets", pd.Series(True, index=baseline.index)), ("excluding_USDC_accounting_only", baseline.asset.ne("USDC"))):
        base = baseline.loc[asset_filter]
        keep = retained.loc[retained.asset.isin(base.asset.unique())]
        totals.append({
            "record_type": "accounting_subtotal",
            "asset": label,
            "baseline_trades": int(len(base)),
            "baseline_net_r_sum": float(base.net_r.sum()),
            "retained_trades": int(len(keep)),
            "retained_net_r_sum": float(keep.net_r.sum()),
            "improvement_contribution_net_r": float(keep.net_r.sum() - base.net_r.sum()),
            "note": "accounting decomposition; not a different exclusion policy",
        })
    monthly_rows: list[dict[str, object]] = []
    for month, base in baseline.groupby("calendar_month", sort=True):
        keep = retained.loc[retained.calendar_month.eq(month)]
        monthly_rows.append({
            "record_type": "monthly_stability",
            "asset": month,
            "baseline_trades": int(len(base)),
            "baseline_net_r_sum": float(base.net_r.sum()),
            "baseline_net_r_mean": float(base.net_r.mean()),
            "retained_trades": int(len(keep)),
            "retained_net_r_sum": float(keep.net_r.sum()),
            "retained_net_r_mean": float(keep.net_r.mean()),
            "net_r_sum_improved": bool(keep.net_r.sum() > base.net_r.sum()),
            "net_r_mean_improved": bool(keep.net_r.mean() > base.net_r.mean()),
        })
    attribution = pd.concat([by_asset, pd.DataFrame(totals), pd.DataFrame(monthly_rows)], ignore_index=True, sort=False)
    lost_10r = removed.loc[removed.net_r.ge(10)].groupby("asset", sort=True).agg(
        lost_10r_trades=("net_r", "size"),
        lost_10r_net_r_sum=("net_r", "sum"),
        largest_lost_10r=("net_r", "max"),
    ).reset_index().sort_values(["lost_10r_net_r_sum", "asset"], ascending=[False, True], kind="mergesort")
    monthly = pd.DataFrame(monthly_rows)
    return attribution, lost_10r, monthly


def finalize_attribution(ledger_path: Path, output: Path) -> None:
    """Add output-only accounting attribution to a completed exclusion folder.

    The existing ``monthly_asset_lists.csv`` is the only admission input.  This
    function deliberately does not call ``monthly_lists`` or generate any new
    random lists, so it cannot alter the frozen rule or rerun the experiment.
    """
    manifest_path = output / "manifest.json"
    lists_path = output / "monthly_asset_lists.csv"
    summary_path = output / "counterfactual_summary.csv"
    if not manifest_path.is_file() or not lists_path.is_file() or not summary_path.is_file():
        raise FileNotFoundError("completed exclusion output is required for attribution finalization")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("input", {}).get(str(ledger_path)) != sha256(ledger_path):
        raise ValueError("attribution ledger SHA does not match the completed exclusion manifest")
    lists = pd.read_csv(lists_path)
    lists["decision_time"] = pd.to_datetime(lists["decision_time"], utc=True, errors="coerce")
    if lists.decision_time.isna().any():
        raise ValueError("existing monthly asset lists have invalid decision clocks")
    ledger = load_ledger(ledger_path)
    v8 = scopes(ledger)["v8_both"]
    attached = attach_lists(v8, lists.loc[lists.scope.eq("v8_both")])
    primary = attached.loc[
        attached.scoring_closed.eq(True)
        & _known_closed_outcome(attached)
        & attached.signal_confirm_time.ge(SPLIT)
        & attached.signal_confirm_time.lt(END)
    ].copy()
    primary["calendar_month"] = primary.signal_confirm_time.dt.strftime("%Y-%m")
    summary = pd.read_csv(summary_path)
    expected = summary.loc[
        summary.scope.eq("v8_both")
        & summary.level.eq("period")
        & summary.period.eq("validation")
        & summary.policy.isin(["baseline", "causal_bottom20_negative"]),
        ["policy", "net_r_sum"],
    ].set_index("policy")["net_r_sum"]
    if not np.isclose(primary.net_r.sum(), expected["baseline"], atol=1e-9):
        raise ValueError("attribution baseline does not match completed summary")
    if not np.isclose(primary.loc[~primary.causal_excluded, "net_r"].sum(), expected["causal_bottom20_negative"], atol=1e-9):
        raise ValueError("attribution retained result does not match completed summary")
    attribution, lost_10r, monthly = accounting_attribution(primary)
    attribution_path = output / "attribution.csv"
    lost_path = output / "lost_10r_assets.csv"
    attribution.to_csv(attribution_path, index=False)
    lost_10r.to_csv(lost_path, index=False)
    monthly_summary = {
        "validation_months": int(len(monthly)),
        "net_r_sum_improved_months": int(monthly.net_r_sum_improved.sum()),
        "net_r_mean_improved_months": int(monthly.net_r_mean_improved.sum()),
        "method": "same-ledger accounting attribution only; no new admission list, random draw, or replay",
    }
    manifest["output_only_attribution"] = monthly_summary
    manifest["outputs"].update({attribution_path.name: sha256(attribution_path), lost_path.name: sha256(lost_path)})
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "receipt.json").write_text(json.dumps({path.name: sha256(path) for path in output.iterdir() if path.is_file() and path.name != "receipt.json"}, indent=2) + "\n")


def run(ledger_path: Path, output: Path) -> None:
    """Write one fixed-rule, non-replayed counterfactual from an empty folder."""
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    ledger = load_ledger(ledger_path)
    output.mkdir(parents=True, exist_ok=True)
    lists_parts, random_list_parts, summary_parts, leaderboard_parts = [], [], [], []
    for name, cohort in scopes(ledger).items():
        lists = monthly_lists(cohort, name)
        random_lists = random_asset_lists(lists, name)
        attached = attach_lists(cohort, lists)
        lists_parts.append(lists)
        random_list_parts.append(random_lists)
        summary_parts.append(summaries(attached, name, random_lists))
        leaderboard_parts.append(asset_leaderboard(cohort, name))
    summary = pd.concat(summary_parts, ignore_index=True)
    lists_path = output / "monthly_asset_lists.csv"
    random_lists_path = output / "random_asset_lists.csv.gz"
    summary_path = output / "counterfactual_summary.csv"
    random_distribution_path = output / "random_distribution.csv"
    rank_p_path = output / "exploratory_primary_rank_p.csv"
    leaderboard_path = output / "asset_leaderboard.csv"
    pd.concat(lists_parts, ignore_index=True).to_csv(lists_path, index=False)
    pd.concat(random_list_parts, ignore_index=True).to_csv(random_lists_path, index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(summary_path, index=False)
    summary.loc[summary.policy.eq("random_equal_asset_count")].to_csv(random_distribution_path, index=False)
    primary_rank_p(summary).to_csv(rank_p_path, index=False)
    pd.concat(leaderboard_parts, ignore_index=True).to_csv(leaderboard_path, index=False)
    manifest = {
        "complete": True,
        "official": True,
        "research_only": True,
        "single_variable": "causal asset admission exclusion only",
        "non_replayed_fixed_ledger_counterfactual": True,
        "ranking_shadow": "original full strategy ledger; exclusions never alter later history",
        "ranking": {
            "decision_clock": "UTC month boundary; signal confirmation governs admission",
            "history": "already closed original trades in [decision-90d, decision)",
            "min_trades": MIN_TRADES,
            "bottom_fraction": BOTTOM_FRACTION,
            "selection": "bottom ceil(20% of all n>=10 assets), then require mean net R < 0",
            "asset_pooling": "same asset across venues, timeframes, and sides within each fixed scope",
            "random_control": "100 fixed seeds 0..99; each draws the same actual causal asset deletion count, never equal trade count",
        },
        "primary": "v8_both validation",
        "diagnostic_scopes": ["v1_long", "v8_long"],
        "input": {
            str(ledger_path): sha256(ledger_path),
            "rows": int(len(ledger)),
            "known_closed_outcomes": int(ledger.known_closed_outcome.sum()),
            "unscored_or_open_rows_retained_for_audit": int((~ledger.known_closed_outcome).sum()),
        },
        "outputs": {name: sha256(path) for name, path in {
            lists_path.name: lists_path, random_lists_path.name: random_lists_path,
            summary_path.name: summary_path, random_distribution_path.name: random_distribution_path,
            rank_p_path.name: rank_p_path, leaderboard_path.name: leaderboard_path,
        }.items()},
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "receipt.json").write_text(json.dumps({path.name: sha256(path) for path in output.iterdir() if path.is_file()}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--finalize-attribution", action="store_true")
    args = parser.parse_args()
    if args.finalize_attribution:
        finalize_attribution(args.ledger, args.output)
    else:
        run(args.ledger, args.output)

"""Deterministic descriptive diagnostics from already-produced trade ledgers.

This helper never opens raw prices, computes a signal, changes a policy, or
selects a threshold. It consumes only named simulator result CSVs. Entry
features were observable at the decision; outcome, MFE/MAE and loss flags are
future-path labels and MUST NOT be presented as causal entry features.

Simulator ``net_return`` uses fixed original notional and completed exits;
``closed=False`` marks are excluded throughout. MFE is measured only on the
actually held path and conservatively records open/fill on intrabar barrier
bars. Therefore ``giveback`` describes recorded excursion versus realised
outcome, not the precise tick-level amount a trader could have banked.

Commit this helper before running it against actual experiment outcome files.
Tests use synthetic ledgers only. Fixed bin edges below are reporting
categories, not optimised strategy gates. All event sums are unweighted
diagnostics of possibly overlapping trades, never portfolio returns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import numpy as np
import pandas as pd


FIXED_BINS = {
    "body_ratio": [-np.inf, 0.5, 0.65, 0.8, 1.0, np.inf],
    "range_atr": [-np.inf, 0.65, 1.0, 1.5, 2.0, 3.0, np.inf],
    "risk_pct": [-np.inf, 0.002, 0.004, 0.008, 0.015, 0.03, np.inf],
    "fee_to_risk": [-np.inf, 0.1, 0.2, 0.4, 0.8, 1.0, np.inf],
    "extension_atr": [-np.inf, 0.0, 0.25, 0.5, 1.0, 1.5, np.inf],
    "cross_count24": [-np.inf, 1.0, 3.0, 6.0, 12.0, 24.0, np.inf],
    "efficiency24": [-np.inf, 0.1, 0.2, 0.4, 0.6, 0.8, np.inf],
    "volume_ratio": [-np.inf, 0.5, 1.0, 1.5, 2.0, 3.0, np.inf],
    "ma_slope_atr": [-np.inf, -0.1, 0.0, 0.05, 0.1, 0.2, np.inf],
}
LOSS_FLAGS = ["hard_stop", "colour_exit", "fees_flip", "never_positive", "giveback", "early_30min"]
REQUIRED = {"event_id", "entry_time", "closed", "outcome", "gross_return", "net_return"}
INTERPRETATION = (
    "Descriptive outcome/path associations, not proven causal failure mechanisms. "
    "Bins are fixed reporting categories and do not select strategy gates. "
    "Only finite completed trade returns enter outcome metrics. "
    "Event sums may overlap and are not portfolio returns."
)


def _numeric(frame: pd.DataFrame, key: str) -> pd.Series:
    values = pd.to_numeric(frame[key], errors="coerce") if key in frame else pd.Series(np.nan, index=frame.index)
    return values.where(np.isfinite(values))


def _closed(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _stats(frame: pd.DataFrame) -> Dict[str, Any]:
    net = _numeric(frame, "net_return").dropna().to_numpy(dtype=float)
    positive = float(net[net > 0].sum())
    negative = float(-net[net < 0].sum())
    return {
        "n": len(net), "mean_net_bp": float(net.mean() * 1e4) if len(net) else np.nan,
        "median_net_bp": float(np.median(net) * 1e4) if len(net) else np.nan,
        "sum_event_net_bp": float(net.sum() * 1e4),
        "profit_factor": positive / negative if negative > 0 else (np.inf if positive > 0 else np.nan),
        "profit_factor_infinite": bool(positive > 0 and negative == 0),
        "win_rate": float((net > 0).mean()) if len(net) else np.nan,
        "loss_n": int((net < 0).sum()), "flat_n": int((net == 0).sum()),
    }


def classify_trades(frame: pd.DataFrame, cost_fraction: float = 0.002) -> pd.DataFrame:
    """Add outcome flags and mutually exclusive loss taxonomy to a copy.

    Taxonomy applies to strictly net-negative closed trades. Precedence is
    hard stop, cost flip, recorded MFE >= 1R giveback, <=30-minute reversal,
    other loss. Flat and positive trades have separate labels. Flags can
    overlap; ``fees_flip`` intentionally includes exact zero net outcomes.
    Existing MA slope/extension columns are already direction-signed by the
    entry builder, so no second sign transformation is performed here.
    """
    missing = REQUIRED - set(frame)
    if missing:
        raise ValueError("Trade ledger missing columns: {}".format(sorted(missing)))
    if frame["event_id"].isna().any() or frame["event_id"].duplicated().any():
        raise ValueError("Trade event_id must be finite and unique within an arm")
    if not np.isfinite(cost_fraction) or cost_fraction < 0:
        raise ValueError("cost_fraction must be finite and nonnegative")
    result = frame.copy()
    result["entry_time"] = pd.to_datetime(result["entry_time"], utc=True, errors="coerce")
    result["closed"] = _closed(result["closed"])
    for key in ("gross_return", "net_return", "max_favourable_r", "max_adverse_r", "hold_minutes", "risk_pct"):
        result[key] = _numeric(result, key)
    result["diagnostic_eligible"] = result["closed"] & result["net_return"].notna() & result["gross_return"].notna()
    result["net_loser"] = result["diagnostic_eligible"] & result["net_return"].lt(0)
    result["hard_stop"] = result["outcome"].isin(["hard_stop", "hard_stop_gap"])
    result["colour_exit"] = result["outcome"].isin(["colour_exit", "slope_colour_exit", "transition_colour_exit"])
    result["fees_flip"] = result["diagnostic_eligible"] & result["gross_return"].gt(0) & result["net_return"].le(0)
    result["never_positive"] = result["diagnostic_eligible"] & result["max_favourable_r"].le(0)
    result["giveback"] = result["diagnostic_eligible"] & result["max_favourable_r"].ge(1) & result["net_return"].le(0)
    result["early_30min"] = result["diagnostic_eligible"] & result["hold_minutes"].le(30)
    result["fee_to_risk"] = cost_fraction / result["risk_pct"].where(result["risk_pct"].gt(0))
    result["observed_cost_fraction"] = result["gross_return"] - result["net_return"]
    reason = pd.Series("ineligible", index=result.index)
    reason.loc[result["diagnostic_eligible"] & result["net_return"].gt(0)] = "net_profit"
    reason.loc[result["diagnostic_eligible"] & result["net_return"].eq(0)] = "flat"
    losses = result["net_loser"]
    reason.loc[losses] = "other_loss"
    reason.loc[losses & result["early_30min"]] = "early_reversal"
    reason.loc[losses & result["giveback"]] = "giveback"
    reason.loc[losses & result["fees_flip"]] = "cost_flip"
    reason.loc[losses & result["hard_stop"]] = "hard_stop"
    result["primary_loss_reason"] = reason
    result["entry_day"] = result["entry_time"].dt.strftime("%Y-%m-%d")
    result["entry_month"] = result["entry_time"].dt.strftime("%Y-%m")

    def explanation(row: pd.Series) -> str:
        flags = ", ".join(flag for flag in LOSS_FLAGS if bool(row[flag])) or "none"
        return "{}; outcome={}; gross={:.2f}bp; net={:.2f}bp; held={:.0f}min; recorded MFE={:.3f}R; flags={}. Descriptive, not a proven causal mechanism.".format(
            row["primary_loss_reason"], row["outcome"], row["gross_return"] * 1e4,
            row["net_return"] * 1e4, row["hold_minutes"], row["max_favourable_r"], flags,
        )

    result["diagnostic_explanation"] = result.apply(explanation, axis=1) if len(result) else pd.Series(dtype=str)
    return result


def fixed_bin_table(classified: pd.DataFrame) -> pd.DataFrame:
    """Report preset bins using existing closed events; never search thresholds."""
    closed = classified.loc[classified["diagnostic_eligible"]].copy()
    rows = []
    for feature, edges in FIXED_BINS.items():
        if feature not in closed:
            continue
        values = _numeric(closed, feature)
        cuts = pd.cut(values, edges, right=False)
        for bucket in cuts.cat.categories:
            part = closed.loc[cuts.eq(bucket)]
            rows.append({"feature": feature, "bin": str(bucket), "lower": bucket.left, "upper": bucket.right, **_stats(part)})
        rows.append({"feature": feature, "bin": "missing", "lower": np.nan, "upper": np.nan, **_stats(closed.loc[values.isna()])})
    return pd.DataFrame(rows)


def group_table(classified: pd.DataFrame, columns: Tuple[str, ...]) -> pd.DataFrame:
    closed = classified.loc[classified["diagnostic_eligible"]]
    available = [column for column in columns if column in closed]
    if len(available) != len(columns):
        return pd.DataFrame()
    rows = []
    for values, part in closed.groupby(available, dropna=False, sort=True):
        values = values if isinstance(values, tuple) else (values,)
        rows.append({**dict(zip(available, values)), **_stats(part)})
    return pd.DataFrame(rows, columns=available + list(_stats(closed).keys()))


def paired_exit_comparison(arms: Mapping[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """All exit-arm pairs on identical event IDs closed in both arms.

    Every delta is candidate minus reference; no unmatched trade contributes.
    Source event definitions must be identical across exit arms. Entry time,
    direction, entry price and initial stop are checked whenever present.
    Missing/censored arms remain visible as attrition counts.
    """
    names = sorted(arms, key=lambda name: (name != "15m_first", name))
    summaries, event_rows = [], []
    for reference, candidate in combinations(names, 2):
        a, b = arms[reference], arms[candidate]
        both_ids = set(a["event_id"]) & set(b["event_id"])
        left = a.loc[a["diagnostic_eligible"]].set_index("event_id")
        right = b.loc[b["diagnostic_eligible"]].set_index("event_id")
        ids = sorted(set(left.index) & set(right.index))
        part = []
        for event_id in ids:
            old, new = left.loc[event_id], right.loc[event_id]
            for key in ("entry_time", "direction", "entry_price", "initial_stop"):
                if key in old and key in new and pd.notna(old[key]) and pd.notna(new[key]):
                    equal = bool(np.isclose(old[key], new[key], rtol=1e-12, atol=1e-12)) if key != "entry_time" else old[key] == new[key]
                    if not equal:
                        raise ValueError("Paired arms differ in entry contract: {} {}".format(event_id, key))
            record = {
                "reference": reference, "candidate": candidate, "event_id": event_id,
                "entry_time": old["entry_time"], "reference_outcome": old["outcome"], "candidate_outcome": new["outcome"],
                "reference_net_return": old["net_return"], "candidate_net_return": new["net_return"],
                "delta_net_bp": (new["net_return"] - old["net_return"]) * 1e4,
                "reference_hold_minutes": old["hold_minutes"], "candidate_hold_minutes": new["hold_minutes"],
            }
            part.append(record)
        paired = pd.DataFrame(part)
        delta = paired["delta_net_bp"] if len(paired) else pd.Series(dtype=float)
        summaries.append({
            "reference": reference, "candidate": candidate, "n_reference_closed": len(left), "n_candidate_closed": len(right),
            "n_shared_event_ids": len(both_ids), "n_same_event_closed": len(ids),
            "n_not_closed_in_both": len(both_ids) - len(ids),
            "mean_delta_net_bp": delta.mean(), "median_delta_net_bp": delta.median(),
            "n_improved": int(delta.gt(0).sum()), "n_worse": int(delta.lt(0).sum()), "n_tied": int(delta.eq(0).sum()),
            "reference_mean_net_bp_same_events": paired["reference_net_return"].mean() * 1e4 if len(paired) else np.nan,
            "candidate_mean_net_bp_same_events": paired["candidate_net_return"].mean() * 1e4 if len(paired) else np.nan,
            "n_loss_to_profit": int(((paired["reference_net_return"] <= 0) & (paired["candidate_net_return"] > 0)).sum()) if len(paired) else 0,
            "n_profit_to_nonpositive": int(((paired["reference_net_return"] > 0) & (paired["candidate_net_return"] <= 0)).sum()) if len(paired) else 0,
        })
        event_rows.extend(part)
    return pd.DataFrame(summaries), pd.DataFrame(event_rows)


def diagnose_frame(frame: pd.DataFrame, cost_fraction: float = 0.002) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, pd.DataFrame]]:
    classified = classify_trades(frame, cost_fraction)
    closed = classified.loc[classified["diagnostic_eligible"]]
    losses = closed.loc[closed["net_loser"]].copy()
    positive = closed.loc[closed["net_return"].gt(0)].sort_values(["net_return", "event_id"], ascending=[False, True])
    net_sum = closed["net_return"].sum()
    positive_sum = positive["net_return"].sum()
    top = positive.head(10).copy()
    top["share_of_positive_event_profits"] = top["net_return"] / positive_sum if positive_sum > 0 else np.nan
    top["share_of_total_event_net"] = top["net_return"] / net_sum if net_sum > 0 else np.nan
    top["cumulative_share_of_positive_profits"] = top["share_of_positive_event_profits"].cumsum()
    ordered = closed["net_return"].sort_values(ascending=False)
    rejected = classified["outcome"].astype(str).str.startswith("entry_")
    summary = {
        "interpretation": INTERPRETATION, "input_events": len(classified), "closed_finite_events": len(closed),
        "rejected_entries": int(rejected.sum()),
        "opened_censored": int((~classified["closed"] & ~rejected).sum()),
        "closed_nonfinite": int((classified["closed"] & ~classified["diagnostic_eligible"]).sum()),
        "invalid_entry_timestamp": int(classified["entry_time"].isna().sum()),
        "metrics": _stats(closed),
        "loss_taxonomy": losses["primary_loss_reason"].value_counts().sort_index().to_dict(),
        "loss_overlapping_flags": {flag: int(losses[flag].sum()) for flag in LOSS_FLAGS},
        "fee_flips_including_flat": int(closed["fees_flip"].sum()),
        "top1_share_of_positive_profits": float(top["share_of_positive_event_profits"].head(1).sum()) if len(top) else None,
        "top2_share_of_positive_profits": float(top["share_of_positive_event_profits"].head(2).sum()) if len(top) else None,
        "top2_share_of_total_net": float(top["net_return"].head(2).sum() / net_sum) if net_sum > 0 else None,
        "leave_top_one_mean_net_bp": float(ordered.iloc[1:].mean() * 1e4),
        "leave_top_two_mean_net_bp": float(ordered.iloc[2:].mean() * 1e4),
        "configured_roundtrip_cost_fraction": cost_fraction,
        "observed_cost_min": closed["observed_cost_fraction"].min(),
        "observed_cost_max": closed["observed_cost_fraction"].max(),
        "cost_contract_mismatch_n": int((~np.isclose(closed["observed_cost_fraction"], cost_fraction, rtol=1e-9, atol=1e-12)).sum()),
        "daily_monthly_basis": "Entry cohorts; sums of possibly overlapping event returns, not compounded portfolio returns.",
    }
    tables = {
        "outcome_direction_fold": group_table(classified, ("outcome", "direction", "fold")),
        "outcome": group_table(classified, ("outcome",)),
        "direction": group_table(classified, ("direction",)),
        "fold": group_table(classified, ("fold",)),
        "loss_taxonomy": group_table(classified.loc[classified["net_loser"]], ("primary_loss_reason",)),
        "fixed_feature_bins": fixed_bin_table(classified),
        "daily": group_table(classified, ("entry_day",)),
        "monthly": group_table(classified, ("entry_month",)),
        "top_winners": top, "losing_trades": losses,
    }
    return classified, summary, tables


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(path, index=False, compression=compression, float_format="%.12g", lineterminator="\n")


def build_diagnostics(results_dir: Path, output_dir: Path, cost_fraction: float = 0.002) -> Dict[str, Any]:
    """Consume named result ledgers only; write deterministic CSV/JSON diagnostics."""
    results_dir, output_dir = Path(results_dir).resolve(), Path(output_dir).resolve()
    if results_dir == output_dir:
        raise ValueError("Diagnostics output must be separate from source results")
    named = ["development_baseline", "development_candidate", "audit_baseline", "audit_candidate"]
    paths = {name: results_dir / (name + "_trades.csv.gz") for name in named}
    paths = {name: path for name, path in paths.items() if path.is_file()}
    exit_paths = sorted(results_dir.glob("development_exit_*_trades.csv.gz"))
    if not paths and not exit_paths:
        raise ValueError("No supported trade ledgers in results directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    bin_edges = {feature: [float(edge) if np.isfinite(edge) else ("-inf" if edge < 0 else "+inf") for edge in edges] for feature, edges in FIXED_BINS.items()}
    summary: Dict[str, Any] = {"interpretation": INTERPRETATION, "source_manifest": [], "datasets": {}, "fixed_bin_edges": bin_edges, "fixed_bin_interval": "left-closed, right-open"}
    for name, path in sorted(paths.items()):
        classified, info, tables = diagnose_frame(pd.read_csv(path), cost_fraction)
        summary["datasets"][name] = info
        summary["source_manifest"].append({"name": name, "filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        _write_csv(output_dir / (name + "_classified.csv.gz"), classified)
        for table_name, table in tables.items():
            suffix = ".csv.gz" if table_name == "losing_trades" else ".csv"
            _write_csv(output_dir / (name + "_" + table_name + suffix), table)
    arms = {}
    for path in exit_paths:
        name = path.name.removeprefix("development_exit_").removesuffix("_trades.csv.gz")
        arms[name] = classify_trades(pd.read_csv(path), cost_fraction)
        summary["source_manifest"].append({"name": "development_exit_" + name, "filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    paired, events = paired_exit_comparison(arms)
    _write_csv(output_dir / "paired_exit_summary.csv", paired)
    _write_csv(output_dir / "paired_exit_events.csv.gz", events)
    summary["paired_exits"] = paired.to_dict("records")
    summary["exit_arm_count"] = len(arms)
    summary = _clean(summary)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cost-fraction", type=float, default=0.002)
    args = parser.parse_args()
    summary = build_diagnostics(args.results_dir, args.output_dir, args.cost_fraction)
    print(json.dumps({"datasets": list(summary["datasets"]), "exit_arm_count": summary["exit_arm_count"], "output_dir": str(args.output_dir)}, sort_keys=True))


if __name__ == "__main__":
    main()

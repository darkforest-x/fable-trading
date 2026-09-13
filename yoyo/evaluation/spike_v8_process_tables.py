"""Build source-backed delivery tables for the frozen V8 process experiments.

Only already-produced experiment outcomes and pre-registered labels are read.
This module selects no thresholds or policies and creates no trading signals.
Entry comparisons are same-entry diagnostics; exit comparisons use the full
serial replay. Net-R sums across independent streams are not account returns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ENTRY = Path("experiments/active/exp-spike-v8-entry-process-20260913-v1/results/full_v1")
EXIT = Path("experiments/active/exp-spike-v8-early-exit-20260913-v1/results/full_v1")
OUTPUT = Path("experiments/active/exp-spike-v8-entry-process-20260913-v1/delivery")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    values = pd.to_numeric(frame.net_r).dropna()
    losses = -values.clip(upper=0).sum()
    return {"trades": len(values), "wins": int(values.gt(0).sum()),
            "losses": int(values.lt(0).sum()), "net_r": float(values.sum()),
            "mean_r": float(values.mean()), "win_rate": float(values.gt(0).mean()),
            "pf": float(values.clip(lower=0).sum() / losses) if losses > 0 else np.nan,
            "realized_10r": int(values.ge(10).sum())}


def build_entry(out: Path) -> list[Path]:
    source = ENTRY / "same_entry_evidence.csv.gz"
    events = pd.read_csv(source)
    closed = events.loc[events.scoring_closed].copy()
    rows, harm = [], []
    arms = {"baseline": None, "h1_max_removed": "h1_stale_no_progress",
            "h1_confirm_removed": "h1_confirmation_no_progress", "h2_setup": "h2_failed_break_reversal"}
    for period, whole in closed.groupby("period"):
        for name, flag in arms.items():
            part = whole if flag is None else whole.loc[whole[flag]]
            rows.append({"period": period, "cohort": name, **metrics(part)})
            if name.startswith("h1_"):
                kept = whole.loc[~whole[flag]]
                rows.append({"period": period, "cohort": name.replace("removed", "retained"), **metrics(kept)})
                base = metrics(whole)
                harm.append({"period": period, "rule": name, "removed_trades_pct": len(part) / len(whole),
                             "removed_losses": int(part.net_r.lt(0).sum()), "removed_wins": int(part.net_r.gt(0).sum()),
                             "removed_10r": int(part.net_r.ge(10).sum()),
                             "exact_10r_retention": float(kept.net_r.ge(10).sum() / base["realized_10r"]),
                             "removed_net_r": float(part.net_r.sum()), "retained_mean_r": float(kept.net_r.mean()),
                             "baseline_mean_r": base["mean_r"]})
    pd.DataFrame(rows).to_csv(out / "entry_period_summary.csv", index=False)
    pd.DataFrame(harm).to_csv(out / "entry_filter_tradeoffs.csv", index=False)
    closed["month"] = pd.to_datetime(closed.signal_confirm_time, utc=True).dt.strftime("%Y-%m")
    stability = []
    for keys, whole in closed.groupby(["period", "month", "timeframe_min", "side"]):
        for name, flag in arms.items():
            if flag is None:
                continue
            tagged, other = whole.loc[whole[flag]], whole.loc[~whole[flag]]
            stability.append({"period": keys[0], "month": keys[1], "timeframe_min": keys[2], "side": keys[3],
                              "cohort": name, "tagged_trades": len(tagged), "other_trades": len(other),
                              "tagged_mean_r": tagged.net_r.mean(), "other_mean_r": other.net_r.mean(),
                              "tagged_minus_other_r": tagged.net_r.mean() - other.net_r.mean()})
    pd.DataFrame(stability).to_csv(out / "entry_monthly_comparison.csv", index=False)

    pairs = pd.read_csv(ENTRY / "same_stream_month_pairs.csv")
    matched = pairs.loc[pairs.matched].copy()
    pair_rows = []
    for key, part in matched.groupby(["cohort", "period"]):
        pair_rows.append({"cohort": key[0], "period": key[1], "matched_pairs": len(part),
                          "target_mean_r": float(part.target_net_r.mean()), "near_time_control_mean_r": float(part.control_net_r.mean()),
                          "mean_difference_r": float(part.net_r_difference.mean()),
                          "limitation": "descriptive same-stream/period/month/side nearest-time pairs; no volatility match or causal p claim"})
    pd.DataFrame(pair_rows).to_csv(out / "entry_near_time_comparator.csv", index=False)
    controls = pd.read_csv(ENTRY / "existing_random_controls.csv")
    labels = {"baseline": None, "h1_max_removed": "h1_stale_no_progress",
              "h1_confirm_removed": "h1_confirmation_no_progress", "h2_setup": "h2_failed_break_reversal"}
    control_rows = []
    for period, whole in controls.loc[controls.matched.eq(True)].groupby("period"):
        for name, flag in labels.items():
            part = whole if flag is None else whole.loc[whole[flag].eq(True)]
            control_rows.append({"period": period, "cohort": name, "matched_random_pairs": len(part),
                                 "target_mean_r": part.target_net_r.mean(), "random_mean_r": part.control_net_r.mean(),
                                 "mean_excess_r": part.net_r_difference.mean(),
                                 "use": "descriptive only: control exit clock unavailable, no development selection"})
    pd.DataFrame(control_rows).to_csv(out / "entry_existing_market_controls.csv", index=False)
    missing = {"signals": len(events), "executed": int(events.executed.sum()), "scoring_closed": len(closed),
               "cross_split_purged": int(events.development_cross_split_purged.sum()),
               "h1_missing_anchors": int((~events.h1_anchor_available).sum()),
               "h2_rejected_discontinuous_candidates": int(events.h2_discontinuous_candidate_windows.sum())}
    (out / "entry_coverage.json").write_text(json.dumps(missing, indent=2))
    return [source, ENTRY / "manifest.json", ENTRY / "same_stream_month_pairs.csv", ENTRY / "existing_random_controls.csv"]


def build_exit(out: Path) -> list[Path]:
    source = EXIT / "trades.csv.gz"
    trades = pd.read_csv(source)
    # Development must use the frozen prefix, never late outcomes of positions
    # entered before the split. Full replay is retained for later-year paths.
    prefix_source = EXIT / "development_trades.csv.gz"
    prefix = pd.read_csv(prefix_source)
    later = pd.to_datetime(trades.entry_time, utc=True) >= pd.Timestamp("2025-09-10T00:00:00Z")
    trades = pd.concat([prefix, trades.loc[later]], ignore_index=True)
    trades = trades.loc[~trades.censored].copy()
    trades["period"] = np.where(pd.to_datetime(trades.entry_time, utc=True) < pd.Timestamp("2025-09-10T00:00:00Z"), "development", "validation_reused_history")
    rows = []
    for keys, part in trades.groupby(["period", "policy"]):
        rows.append({"period": keys[0], "policy": keys[1], **metrics(part)})
    pd.DataFrame(rows).to_csv(out / "exit_scoring_period_summary.csv", index=False)
    return [source, prefix_source, EXIT / "manifest.json", EXIT / "selected_rule.json"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-exits", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    inputs = build_entry(OUTPUT)
    if args.with_exits:
        inputs += build_exit(OUTPUT)
    manifest = {"source": {str(Path(__file__)): digest(Path(__file__))}, "inputs": {str(p): digest(p) for p in inputs},
                "outputs": {p.name: digest(p) for p in OUTPUT.iterdir() if p.suffix in (".csv", ".json") and p.name != "tables_manifest.json"},
                "same_entry_only_for_h1_h2": True, "no_policy_selection": True}
    (OUTPUT / "tables_manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build frozen diagnostics for the altcoin 1D K1->K2 experiments.

All inputs are committed selection, audit, development, and one-shot
confirmation ledgers.  No source candle file is opened here, so this report
builder cannot cross the repository holdout boundary.  Signal-score AUC,
feature correlations, horizon MFE, and cohort comparisons are explicitly
postmortem diagnostics and never feed parameter selection.

Inputs used at signal time by the research engines are completed daily OHLCV,
ATR14, EMA/SMA values, rolling 20/120-bar release statistics, and the current
neutral episode.  Their causal windows are documented in the two experiment
configs and engine module docstrings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
V1 = (
    ROOT
    / "experiments/active/exp-altcoin-1d-k1k2-episode-runner-preholdout-20260905-v1"
)
V2 = (
    ROOT
    / "experiments/active/exp-altcoin-1d-k1k2-close-stop-bank-repair-preholdout-20260905-v2"
)
OUTPUT = ROOT / "analysis/output/altcoin_1d_k1k2_20260905_v1_v2"

PHASES = (
    {
        "label": "v1_selection",
        "experiment": V1,
        "prefix": "selection",
        "start": "2022-01-01T00:00:00Z",
        "end": "2024-01-01T00:00:00Z",
        "attempt": "selection_final_attempts.csv.gz",
        "pair": "selection_final_pairs.csv.gz",
        "setup": "selection_final_setups.csv.gz",
        "trade": "selection_final_trades.csv.gz",
    },
    {
        "label": "v1_audit",
        "experiment": V1,
        "prefix": "audit",
        "start": "2024-01-01T00:00:00Z",
        "end": "2025-07-01T00:00:00Z",
        "attempt": "audit_candidate_attempts.csv.gz",
        "pair": "audit_candidate_pairs.csv.gz",
        "setup": "audit_candidate_setups.csv.gz",
        "trade": "audit_candidate_trades.csv.gz",
    },
    {
        "label": "v2_development",
        "experiment": V2,
        "prefix": "development",
        "start": "2022-07-01T00:00:00Z",
        "end": "2025-07-01T00:00:00Z",
        "attempt": "development_signal_attempts.csv.gz",
        "pair": "development_signal_pairs.csv.gz",
        "setup": "development_signal_setups.csv.gz",
        "trade": "development_candidate_trades.csv.gz",
    },
    {
        "label": "v2_confirmation",
        "experiment": V2,
        "prefix": "confirmation",
        "start": "2025-07-01T00:00:00Z",
        "end": "2026-05-01T00:00:00Z",
        "attempt": "confirmation_signal_attempts.csv.gz",
        "pair": "confirmation_signal_pairs.csv.gz",
        "setup": "confirmation_signal_setups.csv.gz",
        "trade": "confirmation_candidate_trades.csv.gz",
    },
)

FEATURES = (
    "signal_score",
    "k1_body_atr",
    "k1_range_atr",
    "k1_close_location",
    "k1_signed_slow_side_atr",
    "k1_range_release",
    "k1_volume_release",
    "neutral_to_k1_bars",
    "k1_k2_gap",
    "k2_touch_depth_atr",
    "k2_body_side_atr",
    "k2_close_side_atr",
    "k2_wick_share",
    "k2_body_ratio",
    "k2_signed_spread_atr",
    "k2_signed_fast_slope3_atr",
    "transition_votes",
    "risk_atr",
)


def read_json(path: Path) -> dict[str, Any]:
    """Read a committed JSON receipt."""

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """Write stable UTF-8 JSON for registry hashing."""

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a deterministic analysis table."""

    frame.to_csv(path, index=False, lineterminator="\n")


def _receipt(experiment: Path, prefix: str) -> dict[str, Any]:
    return read_json(experiment / "results" / f"{prefix}_receipt.json")


def _rank_auc(outcome: pd.Series, score: pd.Series) -> float:
    """Return tie-aware binary ROC AUC without adding an ML dependency."""

    y = outcome.astype(bool).to_numpy()
    n_positive = int(y.sum())
    n_negative = int((~y).sum())
    if n_positive == 0 or n_negative == 0:
        return float("nan")
    ranks = score.astype(float).rank(method="average").to_numpy()
    rank_sum = float(ranks[y].sum())
    return (
        rank_sum - n_positive * (n_positive + 1) / 2
    ) / (n_positive * n_negative)


def build_phase_summary() -> pd.DataFrame:
    """Collect registered metrics without recomputing any selected outcome."""

    rows: list[dict[str, Any]] = []
    for spec in PHASES:
        receipt = _receipt(spec["experiment"], spec["prefix"])
        for policy in ("baseline", "candidate"):
            metrics = receipt[policy]
            matched = receipt.get("matched_random", {}) if policy == "candidate" else {}
            portfolio = receipt.get("portfolio", {}) if policy == "candidate" else {}
            rows.append(
                {
                    "phase": spec["label"],
                    "policy": policy,
                    "start_inclusive": spec["start"],
                    "end_exclusive": spec["end"],
                    "events": int(metrics["events"]),
                    "symbols": int(metrics["symbols"]),
                    "mean_gross_bp": float(metrics["mean_gross_bp"]),
                    "mean_net_bp": float(metrics["mean_net_bp"]),
                    "median_net_bp": float(metrics["median_net_bp"]),
                    "profit_factor": float(metrics["profit_factor"]),
                    "win_rate": float(metrics["win_rate"]),
                    "positive_folds": int(metrics["positive_folds"]),
                    "total_folds": int(metrics["total_folds"]),
                    "positive_symbol_share": float(metrics["positive_symbol_share"]),
                    "runner_armed_share": float(metrics["runner_armed_share"]),
                    "banked_any_share": float(metrics["banked_any_share"]),
                    "top_score_decile_events": int(
                        metrics["top_score_decile_events"]
                    ),
                    "top_score_decile_mean_net_bp": float(
                        metrics["top_score_decile_mean_net_bp"]
                    ),
                    "week_cluster_signflip_p": float(
                        metrics["week_cluster_signflip_p"]
                    ),
                    "matched_events": matched.get("matched_events"),
                    "matched_control_mean_net_bp": matched.get(
                        "control_mean_net_bp"
                    ),
                    "matched_excess_bp": matched.get("excess_bp"),
                    "matched_signflip_p": matched.get("week_cluster_signflip_p"),
                    "portfolio_total_return": portfolio.get("total_return"),
                    "portfolio_closed_equity_max_drawdown": portfolio.get(
                        "closed_equity_max_drawdown"
                    ),
                    "repository_holdout_rows_read": int(
                        receipt["repository_holdout_rows_read"]
                    ),
                    "registered_result": True,
                }
            )
    return pd.DataFrame(rows)


def build_signal_funnel() -> pd.DataFrame:
    """Count episode attempts, K2 pairs, accepted setups, and executable trades."""

    rows: list[dict[str, Any]] = []
    configured_symbols = 52
    for spec in PHASES:
        result_dir = spec["experiment"] / "results"
        attempts = pd.read_csv(result_dir / spec["attempt"])
        pairs = pd.read_csv(result_dir / spec["pair"])
        setups = pd.read_csv(result_dir / spec["setup"])
        trades = pd.read_csv(result_dir / spec["trade"])
        start = pd.Timestamp(spec["start"])
        end = pd.Timestamp(spec["end"])
        attempts["k1_time"] = pd.to_datetime(attempts["k1_time"], utc=True)
        pairs["signal_time"] = pd.to_datetime(pairs["signal_time"], utc=True)
        setups["signal_time"] = pd.to_datetime(setups["signal_time"], utc=True)
        attempts = attempts[
            attempts["k1_time"].ge(start) & attempts["k1_time"].lt(end)
        ]
        pairs = pairs[
            pairs["signal_time"].ge(start) & pairs["signal_time"].lt(end)
        ]
        setups = setups[
            setups["signal_time"].ge(start) & setups["signal_time"].lt(end)
        ]
        years = (end - start).days / 365.25
        statuses = attempts["attempt_status"].value_counts()
        pair_statuses = pairs["attempt_status"].value_counts()
        rows.append(
            {
                "phase": spec["label"],
                "configured_symbols": configured_symbols,
                "calendar_years": years,
                "k1_episode_attempts": len(attempts),
                "expired_without_k2": int(statuses.get("expired_without_k2", 0)),
                "invalidated_wrong_side_close": int(
                    statuses.get("invalidated_wrong_side_close", 0)
                ),
                "k2_vote_rejected": int(statuses.get("k2_vote_rejected", 0)),
                "k2_accepted_attempts": int(statuses.get("k2_accepted", 0)),
                "evaluated_k2_pairs": len(pairs),
                "pair_vote_rejections": int(
                    pair_statuses.get("k2_vote_rejected", 0)
                ),
                "accepted_setups": len(setups),
                "executable_trades": len(trades),
                "setups_per_100_symbol_years": len(setups)
                / (configured_symbols * years)
                * 100.0,
                "evidence_role": "registered_signal_funnel",
            }
        )
    return pd.DataFrame(rows)


def build_score_diagnostics() -> pd.DataFrame:
    """Diagnose whether the fixed causal score ranks profitable trades."""

    rows: list[dict[str, Any]] = []
    for spec in PHASES:
        trades = pd.read_csv(spec["experiment"] / "results" / spec["trade"])
        receipt = _receipt(spec["experiment"], spec["prefix"])
        metrics = receipt["candidate"]
        rows.append(
            {
                "phase": spec["label"],
                "events": len(trades),
                "profitable_events": int(trades["net_return"].gt(0.0).sum()),
                "signal_score_profit_auc": _rank_auc(
                    trades["net_return"].gt(0.0), trades["signal_score"]
                ),
                "top_score_decile_events": int(
                    metrics["top_score_decile_events"]
                ),
                "top_score_decile_mean_net_bp": float(
                    metrics["top_score_decile_mean_net_bp"]
                ),
                "week_cluster_signflip_p": float(
                    metrics["week_cluster_signflip_p"]
                ),
                "diagnostic_only": True,
            }
        )
    return pd.DataFrame(rows)


def build_feature_diagnostics() -> pd.DataFrame:
    """Compare causal feature levels across regimes after all tests are frozen."""

    development = pd.read_csv(V2 / "results/development_candidate_trades.csv.gz")
    confirmation = pd.read_csv(V2 / "results/confirmation_candidate_trades.csv.gz")
    development["entry_time"] = pd.to_datetime(development["entry_time"], utc=True)
    cohorts = {
        "development_2022H2_2023": development[
            development["entry_time"].lt(pd.Timestamp("2024-01-01T00:00:00Z"))
        ],
        "development_2024_2025H1": development[
            development["entry_time"].ge(pd.Timestamp("2024-01-01T00:00:00Z"))
        ],
        "confirmation_2025H2_2026P1": confirmation,
    }
    rows: list[dict[str, Any]] = []
    for feature in FEATURES:
        record: dict[str, Any] = {
            "feature": feature,
            "evidence_role": "postmortem_association_not_threshold_selection",
        }
        for cohort, frame in cohorts.items():
            record[f"{cohort}_events"] = len(frame)
            record[f"{cohort}_median"] = float(frame[feature].median())
            record[f"{cohort}_spearman_net_r"] = float(
                frame[[feature, "net_return_r"]]
                .corr(method="spearman")
                .iloc[0, 1]
            )
        rows.append(record)
    return pd.DataFrame(rows)


def build_followthrough() -> pd.DataFrame:
    """Measure live-path and post-exit MFE reach rates in fixed v2 cohorts."""

    development = pd.read_csv(V2 / "results/development_candidate_trades.csv.gz")
    confirmation = pd.read_csv(V2 / "results/confirmation_candidate_trades.csv.gz")
    development["entry_time"] = pd.to_datetime(development["entry_time"], utc=True)
    cohorts = {
        "2022H2-2023": development[
            development["entry_time"].lt(pd.Timestamp("2024-01-01T00:00:00Z"))
        ],
        "2024-2025H1": development[
            development["entry_time"].ge(pd.Timestamp("2024-01-01T00:00:00Z"))
        ],
        "2025H2-2026P1 confirmation": confirmation,
    }
    rows: list[dict[str, Any]] = []
    for cohort, frame in cohorts.items():
        for threshold in (0.5, 1.0, 1.5, 2.0, 3.0, 6.0):
            rows.append(
                {
                    "cohort": cohort,
                    "events": len(frame),
                    "threshold_r": threshold,
                    "live_path_reach_share": float(
                        frame["mfe_at_exit_r"].ge(threshold).mean()
                    ),
                    "full_horizon_reach_share": float(
                        frame["horizon_mfe_r"].ge(threshold).mean()
                    ),
                    "evidence_role": "postmortem_path_diagnostic",
                }
            )
    return pd.DataFrame(rows)


def build_confirmation_trade_audit() -> pd.DataFrame:
    """Emit a compact per-trade confirmation failure ledger."""

    trades = pd.read_csv(V2 / "results/confirmation_failure_detail.csv.gz")
    columns = [
        "setup_id",
        "symbol",
        "direction",
        "entry_time",
        "outcome",
        "failure_mode",
        "hold_bars",
        "net_return",
        "net_return_r",
        "mfe_at_exit_r",
        "horizon_mfe_r",
        "mae_at_exit_r",
        "signal_score",
        "transition_votes",
        "neutral_to_k1_bars",
        "k1_k2_gap",
        "k1_range_release",
        "k1_volume_release",
        "k2_signed_spread_atr",
        "k2_signed_fast_slope3_atr",
    ]
    output = trades[columns].copy()
    output["direction"] = output["direction"].map({1: "LONG", -1: "SHORT"})
    output["net_bp"] = output["net_return"] * 1e4
    return output.drop(columns="net_return").sort_values("entry_time")


def build_matched_pair_audit() -> pd.DataFrame:
    """Return exactly matched confirmation pairs used by the registered test."""

    pairs = pd.read_csv(V2 / "results/confirmation_matched_pairs.csv")
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    matched["candidate_net_bp"] = matched["candidate_net_return"] * 1e4
    matched["control_mean_net_bp"] = matched["control_mean_net_return"] * 1e4
    matched["paired_excess_bp"] = matched["paired_excess_return"] * 1e4
    return matched.sort_values("paired_excess_bp").reset_index(drop=True)

def render_figure(
    followthrough: pd.DataFrame,
    matched_pairs: pd.DataFrame,
) -> Path:
    """Render a four-panel technical overview from frozen artifacts."""

    development_candidate = pd.read_csv(
        V2 / "results/development_candidate_folds.csv"
    )
    development_baseline = pd.read_csv(V2 / "results/development_baseline_folds.csv")
    confirmation_candidate = pd.read_csv(
        V2 / "results/confirmation_candidate_folds.csv"
    )
    grid = pd.read_csv(V2 / "results/development_coordinate_grid.csv")

    ink = "#252a34"
    blue = "#3568a8"
    orange = "#d08a25"
    open_blue = "#a9c3df"
    grey = "#888f99"
    fig, axes = plt.subplots(2, 2, figsize=(14.2, 9.5), constrained_layout=True)

    ax = axes[0, 0]
    folds = development_candidate["fold"].tolist()
    x = np.arange(len(folds), dtype=float)
    ax.bar(
        x - 0.18,
        development_baseline["mean_net_bp"],
        width=0.34,
        color=open_blue,
        edgecolor=blue,
        label="V1 wick stop",
    )
    ax.bar(
        x + 0.18,
        development_candidate["mean_net_bp"],
        width=0.34,
        color=orange,
        label="V2 two-close exit",
    )
    ax.axhline(0.0, color=ink, linewidth=0.8)
    ax.axvline(2.5, color=grey, linewidth=0.9, linestyle="--")
    ax.set_xticks(x, folds)
    ax.set_ylabel("Mean net return (bp/trade)")
    ax.set_title("Half-year return by frozen execution")
    ax.legend(frameon=False, fontsize=9)

    ax = axes[0, 1]
    subset = grid[
        grid["factor"].isin(("stop_policy", "bank_schedule"))
    ].copy()
    labels = []
    for row in subset.itertuples():
        prefix = "stop" if row.factor == "stop_policy" else "bank"
        labels.append(f"{prefix}: {row.value}")
    positions = np.arange(len(subset), dtype=float)
    colors = [orange if bool(value) else blue for value in subset["stage_selected"]]
    ax.barh(positions, subset["robust_score_r"], color=colors)
    ax.axvline(0.0, color=ink, linewidth=0.8)
    ax.set_yticks(positions, labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Robust score (R; higher is better)")
    ax.set_title("Single-variable stop and bank search")

    ax = axes[1, 0]
    marker_map = {"2022H2-2023": "o", "2024-2025H1": "s", "2025H2-2026P1 confirmation": "^"}
    color_map = {
        "2022H2-2023": blue,
        "2024-2025H1": grey,
        "2025H2-2026P1 confirmation": orange,
    }
    live = followthrough[followthrough["threshold_r"].le(3.0)]
    for cohort, frame in live.groupby("cohort", sort=False):
        ax.plot(
            frame["threshold_r"],
            frame["live_path_reach_share"] * 100.0,
            marker=marker_map[cohort],
            linewidth=1.8,
            color=color_map[cohort],
            label=cohort,
        )
    ax.set_xticks([0.5, 1.0, 1.5, 2.0, 3.0])
    ax.set_xlabel("Favourable excursion reached before exit (R)")
    ax.set_ylabel("Share of trades (%)")
    ax.set_ylim(0.0, 100.0)
    ax.set_title("Live-path follow-through collapses out of sample")
    ax.legend(frameon=False, fontsize=8)

    ax = axes[1, 1]
    values = matched_pairs["paired_excess_bp"].to_numpy()
    positions = np.arange(len(values), dtype=float)
    colors = [open_blue if value >= 0.0 else orange for value in values]
    ax.bar(positions, values, color=colors, edgecolor=blue, linewidth=0.5)
    ax.axhline(0.0, color=ink, linewidth=0.8)
    ax.axhline(
        float(values.mean()),
        color=ink,
        linewidth=1.1,
        linestyle="--",
        label=f"Mean {values.mean():.0f} bp",
    )
    ax.set_xticks(positions, [str(i + 1) for i in positions], fontsize=8)
    ax.set_xlabel("Exactly matched confirmation event (sorted)")
    ax.set_ylabel("Candidate minus matched random (bp)")
    ax.set_title("Confirmation signal underperforms matched random")
    ax.legend(frameon=False, fontsize=9)

    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.18, linewidth=0.6)
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(colors=ink)
    path = OUTPUT / "altcoin_1d_k1k2_diagnosis.png"
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def main() -> None:
    """Write the frozen report evidence bundle."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    phase_summary = build_phase_summary()
    funnel = build_signal_funnel()
    score = build_score_diagnostics()
    features = build_feature_diagnostics()
    followthrough = build_followthrough()
    confirmation_trades = build_confirmation_trade_audit()
    matched_pairs = build_matched_pair_audit()

    write_csv(phase_summary, OUTPUT / "phase_summary.csv")
    write_csv(funnel, OUTPUT / "signal_funnel.csv")
    write_csv(score, OUTPUT / "signal_score_diagnostics.csv")
    write_csv(features, OUTPUT / "postmortem_feature_diagnostics.csv")
    write_csv(followthrough, OUTPUT / "followthrough_reach.csv")
    write_csv(confirmation_trades, OUTPUT / "confirmation_trade_audit.csv")
    write_csv(matched_pairs, OUTPUT / "confirmation_matched_pair_audit.csv")
    figure = render_figure(followthrough, matched_pairs)

    v1_audit = _receipt(V1, "audit")
    v2_development = _receipt(V2, "development")
    v2_confirmation = _receipt(V2, "confirmation")
    write_json(
        OUTPUT / "summary.json",
        {
            "status": "v1_and_v2_rejected_before_repository_holdout",
            "registered_experiments": [V1.name, V2.name],
            "v1_audit": {
                "candidate": v1_audit["candidate"],
                "matched_random": v1_audit["matched_random"],
                "portfolio": v1_audit["portfolio"],
            },
            "v2_development": {
                "selected_params": v2_development["selected_params"],
                "baseline": v2_development["baseline"],
                "candidate": v2_development["candidate"],
                "portfolio": v2_development["portfolio"],
            },
            "v2_confirmation": {
                "selected_params": v2_confirmation["selected_params"],
                "baseline": v2_confirmation["baseline"],
                "candidate": v2_confirmation["candidate"],
                "matched_random": v2_confirmation["matched_random"],
                "portfolio": v2_confirmation["portfolio"],
            },
            "diagnostic_table_rows": {
                "phase_summary": len(phase_summary),
                "signal_funnel": len(funnel),
                "score": len(score),
                "feature": len(features),
                "followthrough": len(followthrough),
                "confirmation_trades": len(confirmation_trades),
                "matched_pairs": len(matched_pairs),
            },
            "chart_map": [
                {
                    "section": "execution transport",
                    "question": "Did the close-based exit repair transport by half-year?",
                    "family": "comparison",
                    "type": "grouped bar",
                    "claim": "Improvement was concentrated before 2024 and did not create stable positive folds.",
                },
                {
                    "section": "single-variable search",
                    "question": "Which stop/bank candidates changed robust development score?",
                    "family": "ranking",
                    "type": "horizontal bar",
                    "claim": "Two-close exits improved the frozen baseline, while larger banking missed the preregistered improvement gate.",
                },
                {
                    "section": "entry follow-through",
                    "question": "How often did entries achieve favourable excursion before exit?",
                    "family": "ordered comparison",
                    "type": "line",
                    "claim": "Confirmation entries never reached the 1.5R bank/runner threshold before exit.",
                },
                {
                    "section": "matched random",
                    "question": "Did the confirmation pattern beat same-symbol, same-regime random entries?",
                    "family": "distribution",
                    "type": "signed event bars",
                    "claim": "Mean paired excess was materially negative.",
                },
            ],
            "repository_holdout_start": "2026-05-04T00:00:00Z",
            "bounded_end_exclusive": "2026-05-01T00:00:00Z",
            "repository_holdout_rows_read": 0,
            "figure": figure.relative_to(ROOT).as_posix(),
            "production_or_live_changed": False,
            "diagnostic_warning": "Feature correlations and horizon MFE are postmortem only and cannot select another threshold on the spent confirmation period.",
        },
    )


if __name__ == "__main__":
    main()

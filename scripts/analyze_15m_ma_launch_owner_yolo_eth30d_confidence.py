#!/usr/bin/env python3
"""Describe confidence on the frozen ETHUSDT.P 30-day YOLO episode ledger.

Sources are the immutable ``accepted_candidates.csv`` and ``episodes.csv``
inside the committed lossless delivery ZIP.  The analysis uses only detector
outputs available at or after each already-recorded decision window.  It does
not read future returns, Owner labels, trade outcomes, or new market data.
Threshold cuts are descriptive sensitivity checks on the fixed candidate
ledger, not tuning recommendations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import zipfile
from pathlib import Path
from typing import Any, Sequence

import cv2
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from scripts.scan_15m_ma_launch_owner_yolo_eth30d import cluster_month_episodes


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-eth30d-20260828-v1/results"
    / "ethusdt_p_30d_all_signal_charts.zip"
)
DEFAULT_SCAN_RECEIPT = DEFAULT_ARCHIVE.parent / "scan_receipt.json"
DEFAULT_OUT = DEFAULT_ARCHIVE.parent / "confidence_analysis"
THRESHOLDS = (0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.75, 0.80, 0.90, 0.95)
BIN_EDGES = (0.25, 0.35, 0.50, 0.75, 0.90, 1.000001)
BIN_LABELS = ("0.25-<0.35", "0.35-<0.50", "0.50-<0.75", "0.75-<0.90", "0.90-1.00")
SEED = 20260828
PERMUTATIONS = 10_000


class ConfidenceAnalysisError(RuntimeError):
    """Fail closed when the frozen confidence evidence drifts."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_source_tables(
    archive: Path = DEFAULT_ARCHIVE,
    scan_receipt: Path = DEFAULT_SCAN_RECEIPT,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Read the two frozen CSV ledgers and validate their committed identity."""

    receipt = read_json(scan_receipt)
    expected = str(receipt["archive"]["sha256"])
    actual = sha256_file(archive)
    if actual != expected:
        raise ConfidenceAnalysisError(f"archive SHA drifted: {actual} != {expected}")
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        required = {"accepted_candidates.csv", "episodes.csv"}
        if not required.issubset(names):
            raise ConfidenceAnalysisError(f"archive missing {sorted(required - names)}")
        with package.open("accepted_candidates.csv") as handle:
            candidates = pd.read_csv(handle)
        with package.open("episodes.csv") as handle:
            episodes = pd.read_csv(handle)
    validate_source_tables(episodes, candidates, receipt)
    return episodes, candidates, receipt


def validate_source_tables(
    episodes: pd.DataFrame,
    candidates: pd.DataFrame,
    receipt: dict[str, Any],
) -> None:
    expected_episodes = int(receipt["overlap_episodes"])
    expected_candidates = int(receipt["accepted_candidates"])
    if len(episodes) != expected_episodes or len(candidates) != expected_candidates:
        raise ConfidenceAnalysisError("source row counts drifted")
    if episodes["episode_id"].nunique() != len(episodes):
        raise ConfidenceAnalysisError("episode identities are not unique")
    required_episode = {
        "episode_id",
        "episode_sequence",
        "class_name",
        "confidence",
        "episode_max_confidence",
        "episode_candidate_count",
        "window_end_i",
        "window_end_time",
    }
    required_candidate = {
        "episode_id",
        "class_name",
        "confidence",
        "window_end_i",
        "window_end_time",
    }
    if missing := required_episode - set(episodes.columns):
        raise ConfidenceAnalysisError(f"episode columns missing: {sorted(missing)}")
    if missing := required_candidate - set(candidates.columns):
        raise ConfidenceAnalysisError(f"candidate columns missing: {sorted(missing)}")
    if candidates["episode_id"].isna().any():
        raise ConfidenceAnalysisError("candidate without episode identity")
    if not candidates["confidence"].between(0.25, 1.0).all():
        raise ConfidenceAnalysisError("candidate confidence outside frozen threshold range")
    grouped = candidates.groupby("episode_id").agg(
        observed_count=("confidence", "size"),
        observed_max=("confidence", "max"),
    )
    check = episodes.set_index("episode_id").join(grouped, how="left")
    if not np.array_equal(
        check["episode_candidate_count"].to_numpy(dtype=int),
        check["observed_count"].to_numpy(dtype=int),
    ):
        raise ConfidenceAnalysisError("episode candidate counts drifted")
    if not np.allclose(
        check["episode_max_confidence"].to_numpy(dtype=float),
        check["observed_max"].to_numpy(dtype=float),
        atol=1e-12,
        rtol=0.0,
    ):
        raise ConfidenceAnalysisError("episode maximum confidence drifted")


def enrich_episodes(episodes: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Add maximum-score timing and stable owner-facing fields to episodes."""

    order = candidates.sort_values(
        ["episode_id", "confidence", "window_end_i", "window_len"],
        ascending=[True, False, True, True],
        kind="mergesort",
    )
    max_rows = order.drop_duplicates("episode_id").loc[
        :, ["episode_id", "confidence", "window_end_i", "window_end_time", "window_len"]
    ]
    max_rows = max_rows.rename(
        columns={
            "confidence": "recomputed_max_confidence",
            "window_end_i": "max_confidence_window_end_i",
            "window_end_time": "max_confidence_time_utc",
            "window_len": "max_confidence_window_len",
        }
    )
    enriched = episodes.merge(max_rows, on="episode_id", how="left", validate="one_to_one")
    enriched["tg_order"] = enriched["episode_sequence"].astype(int)
    enriched["direction"] = enriched["class_name"].map(
        {"dense_long": "LONG", "dense_short": "SHORT"}
    )
    enriched["representative_confidence"] = enriched["confidence"].astype(float)
    enriched["max_confidence"] = enriched["episode_max_confidence"].astype(float)
    enriched["confidence_uplift"] = (
        enriched["max_confidence"] - enriched["representative_confidence"]
    )
    enriched["max_confidence_delay_bars"] = (
        enriched["max_confidence_window_end_i"].astype(int)
        - enriched["window_end_i"].astype(int)
    )
    enriched["max_confidence_delay_minutes"] = enriched["max_confidence_delay_bars"] * 15
    detected = pd.to_datetime(enriched["window_end_time"], utc=True)
    enriched["detection_time_utc"] = detected.dt.strftime("%Y-%m-%d %H:%M")
    enriched["detection_time_cst"] = detected.dt.tz_convert("Asia/Shanghai").dt.strftime(
        "%Y-%m-%d %H:%M"
    )
    return enriched.sort_values("tg_order").reset_index(drop=True)


def build_bin_table(enriched: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for field, label in (
        ("representative_confidence", "earliest_visible"),
        ("max_confidence", "episode_max"),
    ):
        cut = pd.cut(
            enriched[field],
            bins=BIN_EDGES,
            labels=BIN_LABELS,
            right=False,
            include_lowest=True,
        )
        counts = cut.value_counts(sort=False)
        for confidence_bin, count in counts.items():
            rows.append(
                {
                    "score_type": label,
                    "confidence_bin": str(confidence_bin),
                    "episodes": int(count),
                    "share": float(count / len(enriched)),
                }
            )
    return pd.DataFrame(rows)


def build_threshold_table(
    enriched: pd.DataFrame,
    candidates: pd.DataFrame,
    thresholds: Sequence[float] = THRESHOLDS,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    bases = enriched.set_index("episode_id")["window_end_i"].astype(int)
    for threshold in thresholds:
        filtered = candidates[candidates["confidence"] >= threshold].copy()
        _, clustered = cluster_month_episodes(filtered.to_dict("records"))
        classes = pd.Series([row["class_name"] for row in clustered]).value_counts()
        detection_days = len(
            {
                pd.Timestamp(row["window_end_time"]).tz_convert("UTC").date()
                for row in clustered
            }
        )
        first = (
            filtered.sort_values(
                ["episode_id", "window_end_i", "confidence"],
                ascending=[True, True, False],
                kind="mergesort",
            )
            .drop_duplicates("episode_id")
            .set_index("episode_id")
        )
        retained_ids = bases.index.intersection(first.index)
        delays = (
            first.loc[retained_ids, "window_end_i"].astype(int)
            - bases.loc[retained_ids].astype(int)
        )
        rows.append(
            {
                "threshold": float(threshold),
                "candidate_boxes": int(len(filtered)),
                "episodes": int(len(clustered)),
                "long_episodes": int(classes.get("dense_long", 0)),
                "short_episodes": int(classes.get("dense_short", 0)),
                "detection_days": int(detection_days),
                "episodes_dropped_vs_025": int(len(enriched) - len(retained_ids)),
                "same_time_as_025": int((delays == 0).sum()),
                "median_extra_delay_bars": float(delays.median()) if len(delays) else math.nan,
                "p90_extra_delay_bars": float(delays.quantile(0.90)) if len(delays) else math.nan,
                "max_extra_delay_bars": int(delays.max()) if len(delays) else 0,
                "median_extra_delay_minutes": float(delays.median() * 15)
                if len(delays)
                else math.nan,
                "p90_extra_delay_minutes": float(delays.quantile(0.90) * 15)
                if len(delays)
                else math.nan,
            }
        )
    return pd.DataFrame(rows)


def ranked_spearman(x: pd.Series, y: pd.Series) -> float:
    left = x.rank(method="average").to_numpy(dtype=float)
    right = y.rank(method="average").to_numpy(dtype=float)
    return float(np.corrcoef(left, right)[0, 1])


def permutation_spearman(
    score: pd.Series,
    candidate_count: pd.Series,
    *,
    permutations: int = PERMUTATIONS,
    seed: int = SEED,
) -> dict[str, float | int]:
    """Two-sided rank-permutation control for confidence/repeat association."""

    score_rank = score.rank(method="average").to_numpy(dtype=float)
    count_rank = candidate_count.rank(method="average").to_numpy(dtype=float)
    score_rank = (score_rank - score_rank.mean()) / score_rank.std(ddof=0)
    count_rank = (count_rank - count_rank.mean()) / count_rank.std(ddof=0)
    observed = float(np.mean(score_rank * count_rank))
    rng = np.random.default_rng(seed)
    exceed = 0
    null_values = np.empty(permutations, dtype=float)
    for index in range(permutations):
        value = float(np.mean(rng.permutation(score_rank) * count_rank))
        null_values[index] = value
        exceed += int(abs(value) >= abs(observed))
    return {
        "spearman_rho": observed,
        "permutations": int(permutations),
        "seed": int(seed),
        "two_sided_p": float((exceed + 1) / (permutations + 1)),
        "null_p025": float(np.quantile(null_values, 0.025)),
        "null_median": float(np.quantile(null_values, 0.50)),
        "null_p975": float(np.quantile(null_values, 0.975)),
    }


def summary_payload(
    enriched: pd.DataFrame,
    candidates: pd.DataFrame,
    threshold_table: pd.DataFrame,
    archive: Path,
) -> dict[str, Any]:
    by_class: dict[str, Any] = {}
    for direction, group in enriched.groupby("direction", sort=True):
        by_class[str(direction)] = {
            "episodes": int(len(group)),
            "representative_mean": float(group["representative_confidence"].mean()),
            "representative_median": float(group["representative_confidence"].median()),
            "episode_max_mean": float(group["max_confidence"].mean()),
            "episode_max_median": float(group["max_confidence"].median()),
            "candidate_count_median": float(group["episode_candidate_count"].median()),
        }
    rep_control = permutation_spearman(
        enriched["representative_confidence"], enriched["episode_candidate_count"]
    )
    max_control = permutation_spearman(
        enriched["max_confidence"], enriched["episode_candidate_count"]
    )
    return {
        "analysis_id": "exp-15m-ma-launch-owner-yolo-eth30d-20260828-v1-confidence-v1",
        "source_archive": str(archive.relative_to(ROOT)),
        "source_archive_sha256": sha256_file(archive),
        "episodes": int(len(enriched)),
        "accepted_candidates": int(len(candidates)),
        "representative_confidence": {
            "mean": float(enriched["representative_confidence"].mean()),
            "median": float(enriched["representative_confidence"].median()),
            "q25": float(enriched["representative_confidence"].quantile(0.25)),
            "q75": float(enriched["representative_confidence"].quantile(0.75)),
            "minimum": float(enriched["representative_confidence"].min()),
            "maximum": float(enriched["representative_confidence"].max()),
            "at_least_050": int((enriched["representative_confidence"] >= 0.50).sum()),
            "at_least_075": int((enriched["representative_confidence"] >= 0.75).sum()),
            "at_least_090": int((enriched["representative_confidence"] >= 0.90).sum()),
        },
        "episode_max_confidence": {
            "mean": float(enriched["max_confidence"].mean()),
            "median": float(enriched["max_confidence"].median()),
            "minimum": float(enriched["max_confidence"].min()),
            "maximum": float(enriched["max_confidence"].max()),
            "at_least_050": int((enriched["max_confidence"] >= 0.50).sum()),
            "at_least_075": int((enriched["max_confidence"] >= 0.75).sum()),
            "at_least_090": int((enriched["max_confidence"] >= 0.90).sum()),
        },
        "confidence_uplift": {
            "mean": float(enriched["confidence_uplift"].mean()),
            "median": float(enriched["confidence_uplift"].median()),
            "max_at_first_detection": int((enriched["max_confidence_delay_bars"] == 0).sum()),
            "max_after_first_detection": int((enriched["max_confidence_delay_bars"] > 0).sum()),
            "max_delay_bars": int(enriched["max_confidence_delay_bars"].max()),
        },
        "by_direction": by_class,
        "repeat_association_null_control": {
            "representative_confidence": rep_control,
            "episode_max_confidence": max_control,
        },
        "threshold_sensitivity": threshold_table.to_dict("records"),
        "claim_scope": "descriptive_confidence_not_correctness_or_profitability",
        "new_holdout_consumption": False,
        "training_or_tuning": False,
        "production_eligible": False,
    }


def render_figure(
    enriched: pd.DataFrame,
    bins: pd.DataFrame,
    thresholds: pd.DataFrame,
    output: Path,
) -> None:
    """Render one four-panel evidence figure for the technical report."""

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#4b5563",
            "axes.labelcolor": "#263238",
            "xtick.color": "#4b5563",
            "ytick.color": "#4b5563",
            "text.color": "#263238",
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(16, 10.5), dpi=150)
    fig.patch.set_facecolor("#fafafa")
    for axis in axes.flat:
        axis.set_facecolor("#fafafa")
        axis.grid(axis="y", color="#d9dee5", linewidth=0.8, alpha=0.8)
        axis.set_axisbelow(True)

    pivot = bins.pivot(index="confidence_bin", columns="score_type", values="episodes").loc[
        list(BIN_LABELS)
    ]
    x = np.arange(len(pivot))
    width = 0.36
    axes[0, 0].bar(
        x - width / 2,
        pivot["earliest_visible"],
        width,
        color="#2f6fbb",
        label="Earliest visible",
    )
    axes[0, 0].bar(
        x + width / 2,
        pivot["episode_max"],
        width,
        color="#d88927",
        label="Episode maximum",
    )
    axes[0, 0].set_xticks(x, pivot.index, rotation=18, ha="right")
    axes[0, 0].set_ylim(0, max(pivot.max()) + 3)
    axes[0, 0].set_ylabel("Episodes")
    axes[0, 0].set_title("Confidence distribution", loc="left", fontweight="bold")
    axes[0, 0].legend(frameon=False)

    axes[0, 1].plot(
        thresholds["threshold"],
        thresholds["episodes"],
        marker="o",
        linewidth=2.5,
        color="#2f6fbb",
    )
    for row in thresholds.itertuples(index=False):
        axes[0, 1].annotate(
            str(int(row.episodes)),
            (row.threshold, row.episodes),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8,
        )
    axes[0, 1].set_ylim(0, 46)
    axes[0, 1].set_xlabel("Candidate confidence cut")
    axes[0, 1].set_ylabel("Re-clustered episodes")
    axes[0, 1].set_title("Episode count under confidence cuts", loc="left", fontweight="bold")

    palette = {"LONG": "#2f6fbb", "SHORT": "#d88927"}
    markers = {"LONG": "o", "SHORT": "^"}
    for direction, group in enriched.groupby("direction"):
        axes[1, 0].scatter(
            group["episode_candidate_count"],
            group["max_confidence"],
            s=55,
            marker=markers[direction],
            color=palette[direction],
            edgecolor="#263238",
            linewidth=0.5,
            alpha=0.88,
            label=direction,
        )
    axes[1, 0].set_xlim(0, 102)
    axes[1, 0].set_ylim(0.23, 1.02)
    axes[1, 0].set_xlabel("Raw candidates merged into episode")
    axes[1, 0].set_ylabel("Episode maximum confidence")
    axes[1, 0].set_title("Maximum confidence vs repeated windows", loc="left", fontweight="bold")
    axes[1, 0].legend(frameon=False)

    selected = thresholds[thresholds["threshold"].isin([0.35, 0.50, 0.60, 0.75, 0.80, 0.90, 0.95])]
    x = np.arange(len(selected))
    width = 0.38
    axes[1, 1].bar(
        x - width / 2,
        selected["median_extra_delay_minutes"],
        width,
        color="#2f6fbb",
        label="Median",
    )
    axes[1, 1].bar(
        x + width / 2,
        selected["p90_extra_delay_minutes"],
        width,
        color="#d88927",
        label="P90",
    )
    axes[1, 1].set_xticks(x, [f"{value:.2f}" for value in selected["threshold"]])
    axes[1, 1].set_xlabel("Candidate confidence cut")
    axes[1, 1].set_ylabel("Extra delay vs 0.25 detection (minutes)")
    axes[1, 1].set_title("Higher cuts often wait for later windows", loc="left", fontweight="bold")
    axes[1, 1].legend(frameon=False)

    fig.suptitle(
        "ETHUSDT.P 15m | 41 overlap episodes | confidence is not calibrated correctness",
        x=0.05,
        y=0.995,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    fig.text(
        0.05,
        0.012,
        "Source: frozen 2026-07-29..08-27 episode/candidate ledgers. Threshold panels are descriptive holdout sensitivity, not tuning advice.",
        fontsize=9,
        color="#5f6b76",
    )
    fig.tight_layout(rect=(0.03, 0.04, 0.99, 0.965), h_pad=2.2, w_pad=2.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    image = cv2.imread(str(output), cv2.IMREAD_COLOR)
    if image is None or image.shape[1] < 1800 or image.shape[0] < 1100:
        raise ConfidenceAnalysisError("confidence figure is missing or undersized")


def build(
    *,
    archive: Path = DEFAULT_ARCHIVE,
    scan_receipt: Path = DEFAULT_SCAN_RECEIPT,
    out: Path = DEFAULT_OUT,
) -> dict[str, Any]:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to overwrite confidence analysis: {out}")
    out.mkdir(parents=True, exist_ok=True)
    episodes, candidates, _receipt = read_source_tables(archive, scan_receipt)
    enriched = enrich_episodes(episodes, candidates)
    bins = build_bin_table(enriched)
    thresholds = build_threshold_table(enriched, candidates)
    episode_columns = [
        "tg_order",
        "episode_id",
        "direction",
        "detection_time_utc",
        "detection_time_cst",
        "representative_confidence",
        "max_confidence",
        "confidence_uplift",
        "episode_candidate_count",
        "max_confidence_delay_bars",
        "max_confidence_delay_minutes",
        "window_len",
        "confirmation_bars",
    ]
    enriched.loc[:, episode_columns].to_csv(out / "episode_confidence.csv", index=False)
    bins.to_csv(out / "confidence_bins.csv", index=False)
    thresholds.to_csv(out / "threshold_sensitivity.csv", index=False)
    figure = out / "confidence_analysis.png"
    render_figure(enriched, bins, thresholds, figure)
    payload = summary_payload(enriched, candidates, thresholds, archive)
    payload["outputs"] = {
        "episode_confidence": str((out / "episode_confidence.csv").relative_to(ROOT)),
        "confidence_bins": str((out / "confidence_bins.csv").relative_to(ROOT)),
        "threshold_sensitivity": str((out / "threshold_sensitivity.csv").relative_to(ROOT)),
        "figure": str(figure.relative_to(ROOT)),
    }
    (out / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--scan-receipt", type=Path, default=DEFAULT_SCAN_RECEIPT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    build(
        archive=args.archive.resolve(),
        scan_receipt=args.scan_receipt.resolve(),
        out=args.out.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

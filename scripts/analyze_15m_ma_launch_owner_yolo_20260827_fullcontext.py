#!/usr/bin/env python3
"""Describe the frozen 2026-08-27 Owner-YOLO events without retuning them.

Inputs are the immutable raw-box event/candidate/episode ledgers, their frozen
scan statistics, the verified full-context manifest, and the pre-holdout 10k
positive training manifest.  The analysis reads no network data, performs no
model inference, and does not define a trading entry or return label.  Daily
direction agreement is explicitly post-hoc because the Top20 universe itself
was selected after each UTC day closed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.render_15m_ma_launch_owner_yolo_20260827_fullcontext import (
    DEFAULT_RESULTS,
    EXPECTED_CONTEXT_BARS,
    EXPECTED_EVENTS,
    ROOT,
    read_json,
    sha256_file,
)


DAY = pd.Timestamp("2026-08-27T00:00:00Z")
EVENTS = ROOT / "analysis/output/ma_launch_owner_yolo_recent5d_rawbox_v2/legacy_events.csv"
CANDIDATES = ROOT / "analysis/output/ma_launch_owner_yolo_recent5d_rawbox_v2/accepted_candidates.csv"
EPISODES = ROOT / "analysis/output/ma_launch_owner_yolo_recent5d_rawbox_v2/episodes.csv"
SCAN_STATS = ROOT / "analysis/output/ma_launch_owner_yolo_recent5d_rawbox_v2/scan_stats.csv"
TRAIN_MANIFEST = ROOT / "datasets/ma_launch_owner_autofill10000_yolo_neg30000_v2/manifest.jsonl"


class DetailedAnalysisError(RuntimeError):
    """Fail closed when frozen source identity or expected totals drift."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def on_day(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[pd.to_datetime(frame["day"], utc=True) == DAY].copy()


def qstats(series: pd.Series) -> dict[str, float]:
    values = pd.to_numeric(series, errors="raise")
    return {
        "min": float(values.min()),
        "p05": float(values.quantile(0.05)),
        "p25": float(values.quantile(0.25)),
        "median": float(values.median()),
        "mean": float(values.mean()),
        "p75": float(values.quantile(0.75)),
        "p95": float(values.quantile(0.95)),
        "max": float(values.max()),
    }


def display_path(path: Path) -> str:
    """Use a repo-relative path when possible, otherwise preserve a repro path."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def training_boxes() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with TRAIN_MANIFEST.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["sample_kind"] != "positive":
                continue
            rows.append(
                {
                    **row["box"],
                    "class_id": int(row["class_id"]),
                    "window_len": int(row["window_end_i"]) - int(row["window_start_i"]) + 1,
                }
            )
    result = pd.DataFrame(rows)
    if len(result) != 10_000:
        raise DetailedAnalysisError(f"expected 10,000 positive training boxes, found {len(result)}")
    return result


def build_plot(
    *,
    event_frame: pd.DataFrame,
    per_symbol: pd.DataFrame,
    training: pd.DataFrame,
    funnel: dict[str, int],
    output: Path,
) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 3, figsize=(12, 8.75), dpi=160)
    fig.suptitle("2026-08-27 Owner-YOLO: full-context diagnostic", fontsize=17, fontweight="bold")

    ax = axes[0, 0]
    funnel_labels = ["windows", "boxed windows", "raw boxes", "structural", "5-bar events", "episodes"]
    funnel_values = [funnel[key] for key in funnel_labels]
    ax.barh(funnel_labels[::-1], funnel_values[::-1], color="#3976b9")
    ax.set_xscale("log")
    ax.set_title("Detection funnel (log scale)")
    for idx, value in enumerate(funnel_values[::-1]):
        ax.text(value * 1.06, idx, f"{value:,}", va="center", fontsize=8)

    ax = axes[0, 1]
    ordered = per_symbol.sort_values(["events", "rank"], ascending=[True, False])
    colors = np.where(ordered["daily_return"] >= 0, "#1b9e77", "#d95f02")
    ax.barh(ordered["short_symbol"], ordered["events"], color=colors)
    ax.set_xlim(0, 4.5)
    ax.set_xticks([0, 1, 2, 3, 4])
    ax.set_title("Repeated 5-bar events per detected symbol")
    ax.set_xlabel("events")

    ax = axes[0, 2]
    for class_id, label, color in [(0, "LONG", "#1b9e77"), (1, "SHORT", "#d62728")]:
        subset = event_frame.loc[event_frame["class_id"] == class_id]
        hour = (pd.to_datetime(subset["window_end_time"], utc=True) - DAY).dt.total_seconds() / 3600
        ax.scatter(
            hour,
            subset["rank"],
            s=25 + subset["confidence"] * 65,
            c=color,
            alpha=0.8,
            label=label,
            edgecolors="white",
            linewidths=0.5,
        )
    ax.axvline(24, color="#333333", linestyle="--", linewidth=1)
    ax.set_xlim(0, 25.5)
    ax.invert_yaxis()
    ax.set_xlabel("completion hour (UTC-day offset)")
    ax.set_ylabel("Top20 rank")
    ax.set_title("When the detector actually knew")
    ax.legend(loc="lower right", frameon=True)

    ax = axes[1, 0]
    positions = [1, 2, 4, 5]
    box_data = [
        training["w_norm"],
        event_frame["prediction_w_norm"],
        training["h_norm"],
        event_frame["prediction_h_norm"],
    ]
    bp = ax.boxplot(box_data, positions=positions, widths=0.62, showfliers=False, patch_artist=True)
    for patch, color in zip(bp["boxes"], ["#aec7e8", "#1f77b4", "#ffbb78", "#ff7f0e"]):
        patch.set_facecolor(color)
    ax.set_xticks(positions, ["train w", "08-27 w", "train h", "08-27 h"])
    ax.set_ylim(0, 0.75)
    ax.set_ylabel("normalized box size")
    ax.set_title("Prediction geometry vs 10k positive labels")

    ax = axes[1, 1]
    bins = np.linspace(0.25, 1.0, 11)
    aligned = event_frame.loc[event_frame["aligned_daily_direction"]]
    opposite = event_frame.loc[~event_frame["aligned_daily_direction"]]
    ax.hist(aligned["confidence"], bins=bins, alpha=0.8, label=f"same final-day sign ({len(aligned)})", color="#4c78a8")
    ax.hist(opposite["confidence"], bins=bins, alpha=0.8, label=f"opposite ({len(opposite)})", color="#e45756")
    ax.set_xlabel("confidence")
    ax.set_ylabel("events")
    ax.set_title("High confidence does not remove contradictions")
    ax.legend(fontsize=8)

    ax = axes[1, 2]
    by_symbol = per_symbol.sort_values(["events", "rank"], ascending=[False, True]).head(12)
    x = np.arange(len(by_symbol))
    ax.bar(x - 0.18, by_symbol["events"], width=0.36, label="5-bar events", color="#4c78a8")
    ax.bar(x + 0.18, by_symbol["episodes"], width=0.36, label="overlap episodes", color="#f58518")
    ax.set_xticks(x, by_symbol["short_symbol"], rotation=55, ha="right")
    ax.set_ylim(0, 4.5)
    ax.set_title("9 redundant events across 9 episodes")
    ax.legend(fontsize=8)

    fig.text(
        0.01,
        0.012,
        "Exploratory display audit only. Top20 and daily-return sign are post-close information; no trading return is estimated.",
        fontsize=8,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.035, 1, 0.965))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor="white")
    plt.close(fig)


def analyze(*, results: Path = DEFAULT_RESULTS) -> dict[str, Any]:
    render_receipt = read_json(results / "render_receipt.json")
    qa_receipt = read_json(results / "qa_receipt.json")
    if qa_receipt.get("passed") is not True or int(qa_receipt.get("exact_pixel_rerenders", -1)) != EXPECTED_EVENTS:
        raise DetailedAnalysisError("full-context QA must pass before analysis")
    if int(render_receipt.get("holdout_consumption_number_for_this_configuration", -1)) != 3:
        raise DetailedAnalysisError("holdout-use identity drifted")

    events = on_day(pd.read_csv(EVENTS))
    candidates = on_day(pd.read_csv(CANDIDATES))
    episodes = on_day(pd.read_csv(EPISODES))
    scan_stats = on_day(pd.read_csv(SCAN_STATS))
    full_manifest = pd.DataFrame(read_jsonl(results / "manifest.jsonl"))
    if (len(events), len(candidates), len(episodes), len(scan_stats), len(full_manifest)) != (43, 1019, 34, 20, 43):
        raise DetailedAnalysisError("frozen 08-27 totals drifted")

    event_keys = ["symbol", "rank", "class_id", "window_start_i", "window_end_i"]
    geometry_cols = event_keys + [
        "event_order",
        "global_x0_bar",
        "global_x1_bar",
        "context_start_i",
        "raw_x0_px",
        "raw_x1_px",
        "raw_y0_px",
        "raw_y1_px",
    ]
    event_frame = events.merge(full_manifest[geometry_cols], on=event_keys, validate="one_to_one")
    candidate_identity = [
        "symbol",
        "class_id",
        "window_start_i",
        "window_end_i",
        "prediction_cx_norm",
        "prediction_cy_norm",
        "prediction_w_norm",
        "prediction_h_norm",
    ]
    event_frame = event_frame.merge(
        candidates[candidate_identity + ["episode_id"]],
        on=candidate_identity,
        validate="one_to_one",
    )
    if event_frame["episode_id"].isna().any() or event_frame["episode_id"].nunique() != 34:
        raise DetailedAnalysisError("event-to-episode mapping drifted")

    event_frame["direction"] = np.where(event_frame["class_id"] == 0, "LONG", "SHORT")
    event_frame["aligned_daily_direction"] = np.where(
        event_frame["daily_return"] >= 0,
        event_frame["class_id"] == 0,
        event_frame["class_id"] == 1,
    )
    event_frame["predicted_width_bars"] = event_frame["global_x1_bar"] - event_frame["global_x0_bar"]
    event_frame["box_to_detection_gap_bars"] = event_frame["window_end_i"] - event_frame["global_x1_bar"]
    event_frame["box_left_minus_core_start_bars"] = event_frame["global_x0_bar"] - event_frame["core_start_i"]
    event_frame["box_right_minus_core_end_bars"] = event_frame["global_x1_bar"] - event_frame["core_end_i"]
    event_frame["naive_fullchart_center_bar"] = (
        event_frame["context_start_i"] + event_frame["prediction_cx_norm"] * (EXPECTED_CONTEXT_BARS - 1)
    )
    event_frame["correct_box_center_bar"] = (
        event_frame["global_x0_bar"] + event_frame["global_x1_bar"]
    ) / 2.0
    event_frame["naive_projection_error_bars"] = (
        event_frame["naive_fullchart_center_bar"] - event_frame["correct_box_center_bar"]
    )
    event_frame = event_frame.sort_values("event_order").reset_index(drop=True)

    event_episode_counts = event_frame.groupby(["symbol", "episode_id"]).size()
    duplicate_events = int((event_episode_counts - 1).clip(lower=0).sum())
    duplicate_episodes = int((event_episode_counts > 1).sum())
    if (duplicate_events, duplicate_episodes) != (9, 9):
        raise DetailedAnalysisError("expected nine duplicated legacy events across nine episodes")

    per_symbol = (
        event_frame.groupby(["rank", "symbol", "daily_return"], as_index=False)
        .agg(
            events=("event_order", "size"),
            episodes=("episode_id", "nunique"),
            long_events=("class_id", lambda values: int((values == 0).sum())),
            short_events=("class_id", lambda values: int((values == 1).sum())),
            mean_confidence=("confidence", "mean"),
            max_confidence=("confidence", "max"),
            aligned_events=("aligned_daily_direction", "sum"),
            first_detection=("window_end_time", "min"),
            last_detection=("window_end_time", "max"),
        )
        .sort_values("rank")
    )
    per_symbol["short_symbol"] = per_symbol["symbol"].str.replace("_USDT_SWAP", "", regex=False)
    per_symbol["duplicate_events_inside_episode"] = per_symbol["events"] - per_symbol["episodes"]

    training = training_boxes()
    sums = {column: int(scan_stats[column].fillna(0).sum()) for column in scan_stats.columns if column not in {"day", "symbol", "daily_return"}}
    funnel = {
        "windows": sums["windows_scored"],
        "boxed windows": sums["windows_with_any_box"],
        "raw boxes": sums["raw_boxes"],
        "structural": sums["accepted_structural_boxes"],
        "5-bar events": sums["deduplicated_events"],
        "episodes": len(episodes),
    }
    if funnel != {
        "windows": 16_320,
        "boxed windows": 1_116,
        "raw boxes": 1_321,
        "structural": 1_019,
        "5-bar events": 43,
        "episodes": 34,
    }:
        raise DetailedAnalysisError(f"08-27 funnel drifted: {funnel}")

    event_counts = per_symbol["events"].value_counts().sort_index()
    aligned = event_frame["aligned_daily_direction"]
    detect_times = pd.to_datetime(event_frame["window_end_time"], utc=True)
    telegram_path = results / "telegram_delivery_receipt.json"
    telegram_documents = 0
    telegram_complete = False
    if telegram_path.is_file():
        telegram_receipt = read_json(telegram_path)
        telegram_documents = int(len(telegram_receipt.get("document_actions", [])))
        telegram_complete = bool(telegram_receipt.get("delivery_complete", False))
    train_geometry = {name: qstats(training[name]) for name in ("cx_norm", "cy_norm", "w_norm", "h_norm")}
    prediction_geometry = {
        name: qstats(event_frame[f"prediction_{name}"])
        for name in ("cx_norm", "cy_norm", "w_norm", "h_norm")
    }
    payload: dict[str, Any] = {
        "scope": {
            "board_day_utc": DAY.isoformat(),
            "holdout_consumption_number_for_this_configuration": 3,
            "exploratory_not_preregistered_performance_test": True,
            "network_reads": 0,
            "new_model_inference": False,
            "threshold_or_weight_changed": False,
            "training_or_tuning": False,
        },
        "coverage": {
            "top20_symbol_days": 20,
            "symbols_with_events": int(event_frame["symbol"].nunique()),
            "symbols_without_events": ["ONT_USDT_SWAP"],
            "events": len(event_frame),
            "long_events": int((event_frame["class_id"] == 0).sum()),
            "short_events": int((event_frame["class_id"] == 1).sum()),
            "events_per_detected_symbol_mean": float(len(event_frame) / event_frame["symbol"].nunique()),
            "event_count_histogram": {str(int(key)): int(value) for key, value in event_counts.items()},
        },
        "funnel": {
            **funnel,
            "boxed_window_rate": funnel["boxed windows"] / funnel["windows"],
            "structural_acceptance_from_raw_boxes": funnel["structural"] / funnel["raw boxes"],
            "five_bar_dedup_removal_rate": sums["dedup_removed"] / funnel["structural"],
        },
        "episode_deduplication": {
            "five_bar_events": len(event_frame),
            "overlap_episodes": int(event_frame["episode_id"].nunique()),
            "events_redundant_inside_existing_episode": duplicate_events,
            "episodes_with_two_legacy_events": duplicate_episodes,
            "one_per_symbol_day_review_surfaces": int(event_frame["symbol"].nunique()),
        },
        "direction": {
            "daily_final_sign_aligned_events": int(aligned.sum()),
            "daily_final_sign_opposite_events": int((~aligned).sum()),
            "daily_final_sign_alignment_rate": float(aligned.mean()),
            "posthoc_warning": "Top20 membership and daily final sign are known only after UTC close; this is not prediction accuracy.",
        },
        "confidence": {
            "distribution": qstats(event_frame["confidence"]),
            "at_or_above_0_50": int((event_frame["confidence"] >= 0.50).sum()),
            "at_or_above_0_70": int((event_frame["confidence"] >= 0.70).sum()),
            "at_or_above_0_80": int((event_frame["confidence"] >= 0.80).sum()),
            "at_or_above_0_90": int((event_frame["confidence"] >= 0.90).sum()),
            "aligned_mean": float(event_frame.loc[aligned, "confidence"].mean()),
            "opposite_mean": float(event_frame.loc[~aligned, "confidence"].mean()),
        },
        "timing": {
            "confirmation_bars": {str(int(k)): int(v) for k, v in event_frame["confirmation_bars"].value_counts().sort_index().items()},
            "confirmation_delay_minutes_min": int(event_frame["confirmation_bars"].min() * 15),
            "confirmation_delay_minutes_median": int(event_frame["confirmation_bars"].median() * 15),
            "confirmation_delay_minutes_max": int(event_frame["confirmation_bars"].max() * 15),
            "detections_before_12_utc": int((detect_times < DAY + pd.Timedelta(hours=12)).sum()),
            "detections_12_to_18_utc": int(((detect_times >= DAY + pd.Timedelta(hours=12)) & (detect_times < DAY + pd.Timedelta(hours=18))).sum()),
            "detections_after_18_utc_including_next_day": int((detect_times >= DAY + pd.Timedelta(hours=18)).sum()),
            "detections_after_board_midnight": int((detect_times >= DAY + pd.Timedelta(days=1)).sum()),
        },
        "geometry": {
            "core_length_bars": {str(int(k)): int(v) for k, v in event_frame["core_length_bars"].value_counts().sort_index().items()},
            "predicted_width_bars": qstats(event_frame["predicted_width_bars"]),
            "predicted_box_right_to_detection_gap_bars": qstats(event_frame["box_to_detection_gap_bars"]),
            "predicted_box_visual_width_of_110_bar_chart_median": float(event_frame["predicted_width_bars"].median() / (EXPECTED_CONTEXT_BARS - 1)),
            "prediction_normalized": prediction_geometry,
            "training_positive_normalized": train_geometry,
            "prediction_vs_training_median_height_change": (
                prediction_geometry["h_norm"]["median"] / train_geometry["h_norm"]["median"] - 1.0
            ),
            "prediction_vs_training_median_width_change": (
                prediction_geometry["w_norm"]["median"] / train_geometry["w_norm"]["median"] - 1.0
            ),
            "construction_warning": "Mapped core_start/core_end are derived from prediction x geometry, so close agreement is not an independent semantic-label validation.",
        },
        "projection_negative_control": {
            "wrong_method": "Apply raw small-window normalized x directly to the 110-bar full chart.",
            "median_absolute_error_bars": float(event_frame["naive_projection_error_bars"].abs().median()),
            "median_absolute_error_minutes": float(event_frame["naive_projection_error_bars"].abs().median() * 15),
            "correct_method": "Invert model pixels through the exact W18-25 ChartTransform, recover absolute fractional bar/price, then reproject through the 110-bar ChartTransform.",
            "correct_roundtrip_matches": EXPECTED_EVENTS,
        },
        "parity": {
            "exact_event_identity_matches": int(qa_receipt["exact_event_identity_matches"]),
            "exact_pixel_rerenders": int(qa_receipt["exact_pixel_rerenders"]),
            "exact_model_input_pixel_matches": int(qa_receipt["exact_model_input_pixel_matches"]),
            "documents_with_exactly_one_box": int(qa_receipt["documents_with_exactly_one_box"]),
            "telegram_documents_sent": telegram_documents,
            "telegram_delivery_complete": telegram_complete,
        },
        "safety": {
            "training_eligible": False,
            "production_eligible": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
        },
    }

    event_output = results / "detailed_event_analysis.csv"
    symbol_output = results / "detailed_symbol_analysis.csv"
    summary_output = results / "detailed_analysis.json"
    plot_output = results / "detailed_analysis_overview.png"
    event_frame.to_csv(event_output, index=False)
    per_symbol.to_csv(symbol_output, index=False)
    summary_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    build_plot(
        event_frame=event_frame,
        per_symbol=per_symbol,
        training=training,
        funnel=funnel,
        output=plot_output,
    )
    payload["artifacts"] = {
        "event_csv": {"path": display_path(event_output), "sha256": sha256_file(event_output)},
        "symbol_csv": {"path": display_path(symbol_output), "sha256": sha256_file(symbol_output)},
        "summary_json": {"path": display_path(summary_output), "sha256": sha256_file(summary_output)},
        "overview_png": {"path": display_path(plot_output), "sha256": sha256_file(plot_output)},
    }
    (results / "detailed_analysis_receipt.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"detailed analysis complete: events={len(event_frame)} episodes={event_frame['episode_id'].nunique()} "
        f"symbols={event_frame['symbol'].nunique()} duplicate_events={duplicate_events}",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    analyze(results=args.results.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

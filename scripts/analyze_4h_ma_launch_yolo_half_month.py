#!/usr/bin/env python3
"""Summarize the frozen 15-day all-symbol 4h YOLO research scan.

Inputs are only the completed scan's summary, signals, and already-rendered
event charts.  Event onset uses ``first_available_at`` (bar close), converted to
Asia/Shanghai.  No market data, model inference, thresholding, or event
selection occurs in this post-processing step.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1"
LONG_COLOR = "#16a085"
SHORT_COLOR = "#e74c3c"


def latest_tail_parity(out: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """Compare the unchanged latest-six-endpoint arm with its source scan."""

    source = ROOT / str(summary["source_snapshot"])
    baseline = pd.read_csv(source / "accepted_candidates.csv")
    expanded = pd.read_csv(out / "accepted_candidates.csv")
    row_counts = {
        str(audit["symbol"]): int(audit["rows"]) for audit in summary["fetch_audits"]
    }
    tail = expanded[
        expanded.apply(
            lambda row: int(row["window_end_i"])
            >= row_counts[str(row["symbol"])] - base.LOOKBACK_ENDPOINTS,
            axis=1,
        )
    ].copy()
    sort_columns = [
        "symbol",
        "window_end_i",
        "window_len",
        "class_id",
        "core_start_i",
        "core_end_i",
    ]
    baseline = baseline.sort_values(sort_columns).reset_index(drop=True)
    tail = tail.sort_values(sort_columns).reset_index(drop=True)
    identity_columns = sort_columns + [
        "window_start_i",
        "window_end_time",
        "input_pixel_sha256",
        "core_length_bars",
        "confirmation_bars",
        "class_name",
        "core_start_time",
        "core_end_time",
    ]
    counts_equal = len(baseline) == len(tail)
    identity_equal = counts_equal and baseline[identity_columns].equals(tail[identity_columns])
    float_columns = [
        "prediction_cx_norm",
        "prediction_cy_norm",
        "prediction_w_norm",
        "prediction_h_norm",
        "confidence",
    ]
    max_abs_diff: dict[str, float | None] = {}
    for column in float_columns:
        max_abs_diff[column] = (
            float(np.max(np.abs(baseline[column].to_numpy() - tail[column].to_numpy())))
            if counts_equal and len(tail)
            else None
        )
    exact_float_equal = counts_equal and all(
        np.array_equal(baseline[column].to_numpy(), tail[column].to_numpy())
        for column in float_columns
    )
    return {
        "baseline_candidates": len(baseline),
        "expanded_latest_six_candidates": len(tail),
        "candidate_count_equal": counts_equal,
        "identity_and_input_hashes_equal": identity_equal,
        "prediction_floats_bit_equal": exact_float_equal,
        "max_abs_prediction_differences": max_abs_diff,
        "verdict": "PASS" if identity_equal and exact_float_equal else "FAIL",
    }


def write_summary_figure(signals: pd.DataFrame, summary: dict[str, Any], path: Path) -> None:
    """Render daily frequency, symbol concentration, confidence, and scan funnel."""

    start = pd.Timestamp(summary["scan_first_endpoint_available_at"]).tz_convert("Asia/Shanghai")
    end = pd.Timestamp(summary["scan_last_endpoint_available_at"]).tz_convert("Asia/Shanghai")
    days = pd.date_range(start.normalize(), end.normalize(), freq="D", tz="Asia/Shanghai")
    daily = (
        signals.groupby(["onset_day", "side"]).size().unstack(fill_value=0)
        .reindex(days, fill_value=0)
    )
    for side in ("LONG", "SHORT"):
        if side not in daily:
            daily[side] = 0

    symbol_side = signals.groupby(["symbol_label", "side"]).size().unstack(fill_value=0)
    for side in ("LONG", "SHORT"):
        if side not in symbol_side:
            symbol_side[side] = 0
    symbol_side["total"] = symbol_side["LONG"] + symbol_side["SHORT"]
    top = symbol_side.sort_values(["total", "LONG", "SHORT"], ascending=False).head(18)

    plt.style.use("seaborn-v0_8-whitegrid")
    fig = plt.figure(figsize=(17, 11), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=(1.05, 1.0))

    ax_daily = fig.add_subplot(grid[0, :])
    ax_daily.bar(days, daily["LONG"], color=LONG_COLOR, label="LONG")
    ax_daily.bar(days, daily["SHORT"], bottom=daily["LONG"], color=SHORT_COLOR, label="SHORT")
    totals = daily["LONG"] + daily["SHORT"]
    for day, total in zip(days, totals):
        if int(total):
            ax_daily.text(day, float(total) + 0.25, str(int(total)), ha="center", va="bottom", fontsize=8)
    ax_daily.set_title("4h YOLO event onsets by confirmed availability day (CST)", loc="left", weight="bold")
    ax_daily.set_ylabel("deduplicated events")
    ax_daily.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax_daily.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=days.tz))
    ax_daily.legend(frameon=False, ncol=2)

    ax_symbols = fig.add_subplot(grid[1, 0])
    positions = np.arange(len(top))
    ax_symbols.barh(positions, top["LONG"], color=LONG_COLOR, label="LONG")
    ax_symbols.barh(positions, top["SHORT"], left=top["LONG"], color=SHORT_COLOR, label="SHORT")
    ax_symbols.set_yticks(positions, top.index)
    ax_symbols.invert_yaxis()
    ax_symbols.set_xlabel("events")
    ax_symbols.set_title("Most frequently detected symbols", loc="left", weight="bold")
    ax_symbols.legend(frameon=False, ncol=2)

    ax_conf = fig.add_subplot(grid[1, 1])
    for side, color in (("LONG", LONG_COLOR), ("SHORT", SHORT_COLOR)):
        subset = signals[signals["side"] == side]
        ax_conf.scatter(
            subset["first_available_cst"],
            subset["confidence"],
            s=26 + 34 * subset["candidate_count"].clip(upper=8) / 8,
            alpha=0.72,
            color=color,
            label=side,
            edgecolor="white",
            linewidth=0.35,
        )
    ax_conf.axhline(base.CONFIDENCE, color="#555", linestyle="--", linewidth=1.1, label="threshold 0.25")
    ax_conf.set_ylim(0.20, 1.02)
    ax_conf.set_ylabel("last-detection confidence")
    ax_conf.set_title("Event confidence at its last detected endpoint", loc="left", weight="bold")
    ax_conf.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax_conf.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=days.tz))
    ax_conf.legend(frameon=False, ncol=3, fontsize=8)

    fig.suptitle(
        "ALL ELIGIBLE OKX USDT SWAPS | 15-DAY 4H YOLO OOD RESEARCH\n"
        f"{summary['usable_symbols']}/{summary['universe_symbols']} symbols | "
        f"{summary['windows_scored']:,} windows | {summary['deduplicated_events']} events | "
        f"{summary['current_latest_bar_events']} current",
        fontsize=16,
        weight="bold",
    )
    fig.text(
        0.5,
        -0.012,
        "15m-trained checkpoint applied unchanged to 4h. Detector confidence is not win probability or trade authorization.",
        ha="center",
        color="#b8322a",
        fontsize=10,
        weight="bold",
    )
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = args.output.resolve()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    signals = pd.read_csv(out / "signals.csv")
    if len(signals) != int(summary["deduplicated_events"]):
        raise ValueError("signals.csv count differs from summary")

    signals["first_available_cst"] = pd.to_datetime(
        signals["first_available_at"], utc=True
    ).dt.tz_convert("Asia/Shanghai")
    signals["onset_day"] = signals["first_available_cst"].dt.normalize()
    signals["side"] = np.where(signals["class_id"].astype(int) == 0, "LONG", "SHORT")
    signals["symbol_label"] = signals["symbol"].str.replace("_USDT_SWAP", "", regex=False)

    figure_path = out / "halfmonth_summary.png"
    write_summary_figure(signals, summary, figure_path)

    declared_events = list(summary["signals"])
    current = sorted(
        (dict(event) for event in declared_events if bool(event["is_current_latest_bar"])),
        key=lambda event: float(event["confidence"]),
        reverse=True,
    )
    top_conf = sorted(
        (dict(event) for event in declared_events),
        key=lambda event: float(event["confidence"]),
        reverse=True,
    )[:12]
    if current:
        current_dir = out / "current"
        current_dir.mkdir(exist_ok=True)
        base.build_overview(
            [out / str(event["chart"]) for event in current],
            current,
            current_dir,
        )
    top_dir = out / "top_confidence"
    top_dir.mkdir(exist_ok=True)
    base.build_overview(
        [out / str(event["chart"]) for event in top_conf],
        top_conf,
        top_dir,
    )

    by_symbol = Counter(str(value) for value in signals["symbol_label"])
    event_count_distribution = Counter(by_symbol.values())
    daily_counts = signals.groupby("onset_day").size().sort_values(ascending=False)
    top_three_days = list(daily_counts.head(3).index)
    top_three = signals[signals["onset_day"].isin(top_three_days)]
    availability_span_bars = (
        (
            pd.to_datetime(signals["last_available_at"], utc=True)
            - pd.to_datetime(signals["first_available_at"], utc=True)
        )
        / base.BAR_DELTA
        + 1
    ).astype(int)
    mixed_class_events = sum(
        len(set(event.get("classes_observed") or [])) > 1 for event in declared_events
    )
    scan_start = pd.Timestamp(summary["scan_first_endpoint_available_at"])
    left_censored_events = sum(
        pd.Timestamp(event["first_available_at"]) == scan_start for event in declared_events
    )
    candidate_memberships = int(signals["candidate_count"].sum())
    unique_candidates = int(summary["accepted_structural_boxes"])
    analysis = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_summary_sha256": base.sha256_file(out / "summary.json"),
        "events": len(signals),
        "symbols_with_events": len(by_symbol),
        "current_events": len(current),
        "left_censored_events": left_censored_events,
        "mixed_class_events": mixed_class_events,
        "event_candidate_count_sum": candidate_memberships,
        "unique_accepted_candidates": unique_candidates,
        "overlapping_candidate_memberships": candidate_memberships - unique_candidates,
        "events_below_confidence_0_30": int((signals["confidence"] < 0.30).sum()),
        "mean_events_per_calendar_day": float(len(signals) / 15.0),
        "event_count_per_symbol_distribution": {
            str(events): int(symbols)
            for events, symbols in sorted(event_count_distribution.items())
        },
        "symbols_with_exactly_one_event": int(event_count_distribution[1]),
        "top_three_onset_days": [str(day.date()) for day in top_three_days],
        "top_three_onset_day_events": int(len(top_three)),
        "top_three_onset_day_event_share": float(len(top_three) / len(signals)),
        "top_three_onset_day_long_events": int((top_three["side"] == "LONG").sum()),
        "top_three_onset_day_short_events": int((top_three["side"] == "SHORT").sum()),
        "outside_top_three_long_events": int(
            ((signals["side"] == "LONG") & ~signals["onset_day"].isin(top_three_days)).sum()
        ),
        "outside_top_three_short_events": int(
            ((signals["side"] == "SHORT") & ~signals["onset_day"].isin(top_three_days)).sum()
        ),
        "event_availability_span_bars_quantiles": {
            str(key): float(value)
            for key, value in availability_span_bars.quantile(
                [0.0, 0.25, 0.5, 0.75, 0.9, 1.0]
            ).items()
        },
        "single_endpoint_events": int((availability_span_bars == 1).sum()),
        "confidence_quantiles": {
            str(key): float(value)
            for key, value in signals["confidence"].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).items()
        },
        "top_symbols": dict(by_symbol.most_common(20)),
        "latest_six_endpoint_parity": latest_tail_parity(out, summary),
        "summary_figure": "halfmonth_summary.png",
        "summary_figure_sha256": base.sha256_file(figure_path),
        "current_overview": "current/overview.png" if current else None,
        "current_overview_sha256": (
            base.sha256_file(out / "current/overview.png") if current else None
        ),
        "top_confidence_overview": "top_confidence/overview.png",
        "top_confidence_overview_sha256": base.sha256_file(
            out / "top_confidence/overview.png"
        ),
        "network_reads": 0,
        "model_inference": 0,
    }
    base.write_json(out / "analysis_summary.json", analysis)
    print(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

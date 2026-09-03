#!/usr/bin/env python3
"""Evaluate frozen SMA40 retest profiles on historical and fresh pre-holdout data.

This runner imports the causal feature/outcome primitives from the committed V1
study, but changes the primary reference to SMA40(HL2). Profile thresholds live
only in the V2 config. They are committed before this script requests the fresh
2026-03-01..2026-05-03 window from OKX. API requests set ``after`` to the
exclusive safe boundary and page backwards; rows at or after the repository's
2026-05-04 holdout are rejected before any feature or outcome is computed.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_two_key_candle_ma_retest_1h import (
    add_features,
    attach_event_outcomes,
    direction_columns,
    extended_summary,
    half_label,
    make_pair_rows,
    process_symbol,
    select_independent_events,
    sha256_file,
    symbol_from_path,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-two-key-candle-ma-retest-sma40-state-v2"
CONFIG_PATH = EXPERIMENT / "config.json"
OUT = EXPERIMENT / "results"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def expanded_conditions(config: dict[str, Any], profile: str, stack: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if profile in stack:
        raise ValueError(f"profile reference cycle: {stack + (profile,)}")
    raw = config["profiles"][profile]
    conditions: list[dict[str, Any]] = []
    for condition in raw:
        operation = str(condition["op"])
        if operation == "include":
            conditions.extend(
                expanded_conditions(config, str(condition["value"]), stack + (profile,))
            )
        elif operation == "between_override":
            column = str(condition["column"])
            conditions = [item for item in conditions if str(item["column"]) != column]
            conditions.append(
                {"column": column, "op": "between", "low": condition["low"], "high": condition["high"]}
            )
        else:
            conditions.append(dict(condition))
    return conditions


def profile_mask(frame: pd.DataFrame, conditions: list[dict[str, Any]]) -> pd.Series:
    mask = pd.Series(True, index=frame.index, dtype=bool)
    for condition in conditions:
        column = str(condition["column"])
        operation = str(condition["op"])
        if column not in frame.columns:
            raise ValueError(f"profile references missing column {column!r}")
        values = frame[column]
        if operation == "ge":
            current = values.ge(float(condition["value"]))
        elif operation == "le":
            current = values.le(float(condition["value"]))
        elif operation == "eq":
            current = values.eq(condition["value"])
        elif operation == "between":
            current = values.between(float(condition["low"]), float(condition["high"]))
        elif operation == "abs_le":
            current = values.abs().le(float(condition["value"]))
        elif operation == "true":
            current = values.astype(bool)
        elif operation == "false":
            current = ~values.astype(bool)
        else:
            raise ValueError(f"unsupported profile operation {operation!r}")
        mask &= current.fillna(False).astype(bool)
    return mask


def add_anchor_score(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the preregistered 0–100 owner-anchor similarity score.

    The four equal groups are K1 displacement, K2 rejection, K1→K2 path and
    indicator state. Thresholds are geometric anchors, not fitted weights.
    """

    out = frame.copy()

    def unit(values: pd.Series, low: float, high: float) -> pd.Series:
        return ((values - low) / (high - low)).clip(0.0, 1.0)

    k1 = pd.DataFrame(
        {
            "body": unit(out["k1_body_ratio"], 0.50, 0.90),
            "range": unit(out["k1_range_atr"], 1.00, 2.00),
            "close": unit(out["k1_close_location"], 0.75, 0.95),
            "volume": unit(out["k1_volume_ratio"], 1.00, 2.00),
            "cross": unit(out["k1_sma40_cross_depth_atr"], -0.05, 0.35),
        }
    ).mean(axis=1)
    touch = np.exp(-((out["k2_sma40_touch_depth_atr"] - 0.40) / 0.45) ** 2)
    risk = out["stop_distance_atr_24"].between(0.25, 2.00).astype(float)
    k2 = pd.DataFrame(
        {
            "wick": unit(out["k2_wick_share"], 0.45, 0.90),
            "small_body": unit(0.50 - out["k2_body_ratio"], 0.0, 0.40),
            "rejection": unit(out["k2_rejection_close_location"], 0.65, 0.95),
            "touch": touch.clip(0.0, 1.0),
            "close_side": unit(out["k2_sma40_close_side_atr"], 0.0, 0.75),
            "risk": risk,
        }
    ).mean(axis=1)
    gap = np.select(
        [out["gap_bars"].between(3, 6), out["gap_bars"].isin([2, 7, 8])],
        [1.0, 0.65],
        default=0.0,
    )
    volume_relation = np.exp(-np.abs(np.log(out["k2_to_k1_volume_ratio"].clip(lower=1e-6))) / np.log(3.0))
    path = pd.DataFrame(
        {
            "gap": gap,
            "limited_extension": (1.0 - out["pre_retest_extension_atr"] / 1.5).clip(0.0, 1.0),
            "no_wrong_close": out["wrong_sma40_close_count"].eq(0).astype(float),
            "continuous_ma_colour": out["intermediate_ma_colour_share"].clip(0.0, 1.0),
            "close_distance": np.exp(-out["close_distance_atr"].abs() / 0.75),
            "volume_relation": volume_relation.clip(0.0, 1.0),
        }
    ).mean(axis=1)
    state = pd.DataFrame(
        {
            "k1_ma_colour": out["k1_ma_colour_aligned"].astype(float),
            "k2_ma_colour": out["k2_ma_colour_aligned"].astype(float),
            "k1_accelerates": out["k1_osc_accel_aligned"].astype(float),
            "k2_sign": out["k2_osc_sign_aligned"].astype(float),
            "k2_cools": (~out["k2_osc_accel_aligned"].astype(bool)).astype(float),
            "structure": out["k2_structure_aligned"].astype(float),
        }
    ).mean(axis=1)
    out["anchor_k1_score"] = k1 * 100.0
    out["anchor_k2_score"] = k2 * 100.0
    out["anchor_path_score"] = path * 100.0
    out["anchor_state_score"] = state * 100.0
    out["anchor_score"] = (k1 + k2 + path + state) * 25.0
    return out


def evaluate_profiles(
    pairs: pd.DataFrame,
    config: dict[str, Any],
    *,
    segment_prefix: str,
    time_start: pd.Timestamp,
    time_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    source = pairs[pairs["k2_time"].ge(time_start) & pairs["k2_time"].lt(time_end)].copy()
    source = add_anchor_score(source)
    rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    events_by_profile: dict[str, pd.DataFrame] = {}
    for index, profile in enumerate(config["profiles"]):
        conditions = expanded_conditions(config, profile)
        events = select_independent_events(
            source,
            profile_mask(source, conditions),
            int(config["event_cooldown_bars"]),
        )
        events["profile"] = profile
        events["segment"] = segment_prefix
        events_by_profile[profile] = events
        summary = extended_summary(events, f"{segment_prefix}:{profile}", 2026090500 + index)
        rows.append({"profile": profile, "segment": segment_prefix, **summary})
        if len(events):
            local = events.assign(fold=half_label(events["k2_time"]))
            for fold, group in local.groupby("fold"):
                fold_rows.append(
                    {
                        "profile": profile,
                        "segment": segment_prefix,
                        "fold": fold,
                        "n": int(len(group)),
                        "net_bp": float(group["net_return_24"].mean() * 1e4),
                        "control_net_bp": float(group["control_net_return_24"].mean() * 1e4),
                        "paired_excess_bp": float(group["paired_excess_24"].mean() * 1e4),
                    }
                )
    return pd.DataFrame(rows), pd.DataFrame(fold_rows), events_by_profile


def distance_table(pairs: pd.DataFrame, label: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for gap, group in pairs.groupby("gap_bars"):
        rows.append(
            {
                "segment": label,
                "gap_bars": int(gap),
                "n_pairs": int(len(group)),
                "net_bp": float(group["net_return_24"].mean() * 1e4),
                "control_net_bp": float(group["control_net_return_24"].mean() * 1e4),
                "paired_excess_bp": float(group["paired_excess_24"].mean() * 1e4),
            }
        )
    return pd.DataFrame(rows)


def plot_profiles(summary: pd.DataFrame, output: Path) -> None:
    if summary.empty:
        return
    profiles = list(dict.fromkeys(summary["profile"].tolist()))
    segments = list(dict.fromkeys(summary["segment"].tolist()))
    x = np.arange(len(profiles))
    width = 0.8 / max(1, len(segments))
    palette = ["#315A7D", "#D18B36", "#82734D"]
    fig, axis = plt.subplots(figsize=(12, 6), constrained_layout=True)
    for index, segment in enumerate(segments):
        current = summary[summary["segment"].eq(segment)].set_index("profile").reindex(profiles)
        axis.bar(
            x + (index - (len(segments) - 1) / 2) * width,
            current["paired_excess_bp"],
            width,
            label=segment,
            color=palette[index % len(palette)],
        )
    axis.axhline(0.0, color="#333333", linewidth=1)
    axis.set_xticks(x, profiles, rotation=20, ha="right")
    axis.set_ylabel("paired excess (bp/event)")
    axis.set_title("Frozen SMA40 profiles: historical versus fresh pre-holdout")
    axis.legend(frameon=False)
    axis.grid(axis="y", color="#D9D9D9", linewidth=0.7)
    axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=170, facecolor="white")
    plt.close(fig)


def plot_distance(data: pd.DataFrame, output: Path) -> None:
    if data.empty:
        return
    fig, axis = plt.subplots(figsize=(10, 5.5), constrained_layout=True)
    palette = {"historical": "#315A7D", "fresh": "#D18B36"}
    for segment, group in data.groupby("segment"):
        group = group.sort_values("gap_bars")
        axis.plot(
            group["gap_bars"], group["paired_excess_bp"], marker="o", linewidth=2,
            label=segment, color=palette.get(str(segment), "#777777"),
        )
    axis.axhline(0.0, color="#333333", linewidth=1)
    axis.set_xlabel("K1→K2 gap bars (1h)")
    axis.set_ylabel("paired excess (bp/pair)")
    axis.set_title("Exact K1→K2 distance on the SMA40 candidate surface")
    axis.legend(frameon=False)
    axis.grid(axis="y", color="#D9D9D9", linewidth=0.7)
    axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=170, facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    config = load_config()
    safe_end = pd.Timestamp(config["safe_end_exclusive"])
    holdout = pd.Timestamp(config["holdout_start"])
    if safe_end != holdout:
        raise RuntimeError("V2 safe end must equal the exclusive holdout boundary")
    paths = sorted(PROJECT.glob(config["data_glob"]))
    symbols = [symbol_from_path(path) for path in paths]
    if not paths:
        raise FileNotFoundError(config["data_glob"])
    OUT.mkdir(parents=True, exist_ok=True)

    pair_parts: list[pd.DataFrame] = []
    control_parts: list[pd.DataFrame] = []
    quality_rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        jobs = {executor.submit(process_symbol, path, config): path for path in paths}
        for position, future in enumerate(as_completed(jobs), start=1):
            pairs, controls, quality = future.result()
            pair_parts.append(pairs)
            control_parts.append(controls)
            quality_rows.append(quality)
            print(f"source [{position:02d}/{len(paths):02d}] {quality['symbol']}: {len(pairs)} pairs")
    all_pairs = pd.concat(pair_parts, ignore_index=True)
    all_pairs["k2_time"] = pd.to_datetime(all_pairs["k2_time"], utc=True)
    all_pairs["k1_time"] = pd.to_datetime(all_pairs["k1_time"], utc=True)
    all_pairs = all_pairs.sort_values(["k2_time", "symbol", "side", "k1_i"]).reset_index(drop=True)
    all_controls = pd.concat(control_parts, ignore_index=True)
    all_controls["candidate_k2_time"] = pd.to_datetime(all_controls["candidate_k2_time"], utc=True)
    all_controls["control_signal_time"] = pd.to_datetime(all_controls["control_signal_time"], utc=True)
    historical_end = pd.Timestamp(config["historical_end_exclusive"])
    fresh_start = pd.Timestamp(config["fresh_test_start"])
    historical = all_pairs[all_pairs["k2_time"].lt(historical_end)].copy()
    fresh = all_pairs[all_pairs["k2_time"].ge(fresh_start) & all_pairs["k2_time"].lt(safe_end)].copy()
    fresh_excluded = set(map(str, config.get("fresh_primary_excluded_symbols", [])))
    fresh = fresh[~fresh["symbol"].isin(fresh_excluded)].copy()
    historical_controls = all_controls[all_controls["candidate_k2_time"].lt(historical_end)].copy()
    fresh_controls = all_controls[
        all_controls["candidate_k2_time"].ge(fresh_start)
        & all_controls["candidate_k2_time"].lt(safe_end)
        & ~all_controls["symbol"].isin(fresh_excluded)
    ].copy()
    if fresh.empty or fresh["k2_time"].max() >= holdout:
        raise RuntimeError("fresh sealed-window candidates are empty or reach holdout")

    historical_summary, historical_folds, historical_events = evaluate_profiles(
        historical,
        config,
        segment_prefix="historical",
        time_start=pd.Timestamp(config["historical_start"]),
        time_end=pd.Timestamp(config["historical_end_exclusive"]),
    )
    fresh_summary, fresh_folds, fresh_events = evaluate_profiles(
        fresh,
        config,
        segment_prefix="fresh_preholdout",
        time_start=pd.Timestamp(config["fresh_test_start"]),
        time_end=safe_end,
    )
    profile_summary = pd.concat([historical_summary, fresh_summary], ignore_index=True)
    fold_summary = pd.concat([historical_folds, fresh_folds], ignore_index=True)
    selected_events = pd.concat(
        [events for mapping in (historical_events, fresh_events) for events in mapping.values()],
        ignore_index=True,
    )
    distances = pd.concat(
        [
            distance_table(
                historical[
                    historical["k2_time"].ge(pd.Timestamp(config["historical_start"]))
                    & historical["k2_time"].lt(pd.Timestamp(config["historical_end_exclusive"]))
                ],
                "historical",
            ),
            distance_table(
                fresh[
                    fresh["k2_time"].ge(pd.Timestamp(config["fresh_test_start"]))
                    & fresh["k2_time"].lt(safe_end)
                ],
                "fresh",
            ),
        ],
        ignore_index=True,
    )

    pd.DataFrame(quality_rows).sort_values("symbol").to_csv(OUT / "source_data_quality.csv", index=False)
    profile_summary.to_csv(OUT / "profile_summary.csv", index=False)
    fold_summary.to_csv(OUT / "profile_halfyear_summary.csv", index=False)
    distances.to_csv(OUT / "distance_response.csv", index=False)
    selected_events.to_csv(OUT / "profile_events.csv.gz", index=False, compression="gzip")
    historical_controls.to_csv(OUT / "historical_matched_controls.csv.gz", index=False, compression="gzip")
    fresh_controls.to_csv(OUT / "fresh_matched_controls.csv.gz", index=False, compression="gzip")
    plot_profiles(profile_summary, OUT / "profile_comparison.png")
    plot_distance(distances, OUT / "distance_response.png")

    primary = str(config["primary_profile"])
    primary_fresh = profile_summary[
        profile_summary["profile"].eq(primary) & profile_summary["segment"].eq("fresh_preholdout")
    ]
    if len(primary_fresh) != 1:
        raise RuntimeError("primary fresh summary missing or duplicated")
    row = primary_fresh.iloc[0]
    gates = {
        "fresh_net_positive": bool(float(row["mean_net_bp"]) > 0.0),
        "fresh_beats_matched_control": bool(float(row["paired_excess_bp"]) > 0.0),
        "fresh_profit_factor_above_one": bool(float(row["profit_factor"]) > 1.0),
        "fresh_paired_p_below_0_01": bool(float(row["paired_signflip_p"]) < 0.01),
        "fresh_ci_low_positive": bool(float(row["paired_excess_ci95_low_bp"]) > 0.0),
    }
    payload = {
        "experiment_id": config["experiment_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "fresh_preholdout_evaluated",
        "holdout_consumed": False,
        "training_eligible": False,
        "production_eligible": False,
        "primary_profile": primary,
        "script": {"path": str(Path(__file__).relative_to(PROJECT)), "sha256": sha256_file(Path(__file__))},
        "config": {"path": str(CONFIG_PATH.relative_to(PROJECT)), "sha256": sha256_file(CONFIG_PATH)},
        "source": {
            "symbols": len(symbols),
            "fresh_primary_symbols": len(set(fresh["symbol"])),
            "fresh_excluded_symbols": sorted(fresh_excluded),
            "historical_pairs": int(len(historical)),
            "fresh_pairs": int(len(fresh)),
            "fresh_min_time": fresh["k2_time"].min().isoformat(),
            "fresh_max_time": fresh["k2_time"].max().isoformat(),
            "safe_end_exclusive": config["safe_end_exclusive"],
            "holdout_start": config["holdout_start"],
        },
        "profile_summary": profile_summary.to_dict(orient="records"),
        "primary_fresh_gates": gates,
        "primary_pass": bool(all(gates.values())),
        "caveats": [
            "V2 architecture was motivated by the V1 failure and two owner anchors; only the March-May window is fresh for V2.",
            "Historical and fresh windows both aggregate four cached OKX 15m candles into each UTC hour.",
            "The current 54-symbol universe has survivorship bias and is not a historical listing snapshot.",
            "Market Break is reproduced from public confirmed-pivot semantics, not proprietary internal state.",
            "Passing a pre-holdout profile would still require owner-approved holdout and fresh-forward confirmation before production.",
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps({"primary": primary, "gates": gates, "summary": profile_summary.to_dict(orient="records")}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

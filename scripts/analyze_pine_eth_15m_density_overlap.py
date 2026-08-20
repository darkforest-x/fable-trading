#!/usr/bin/env python3
"""Audit whether frozen Pine V9 entries are actually project-density events.

The user supplied strategy was motivated by moving-average density, but its
entry rule is an SMA10/SMA60 crossover plus EMA100/EMA200 trend and oscillator
filters.  This audit maps every continuously replayed V9 signal bar to the
repository's already-frozen strict and expanded long/short density masks.  It
does not change thresholds, fit a model, select a gate, or read the holdout.

All indicator inputs use OHLCV at the signal bar and earlier.  Outcomes are
reported only as descriptive columns; the primary overlap null circularly
shifts the complete V9 long/short signal pattern inside each chronological
split and compares the observed overlap count with every possible shift.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    RESULTS,
    SPLITS,
    Variant,
    build_feature_frame,
    current_commit,
    load_config,
    load_research_frame,
    simulate_period,
)
from yoyo.data.indicators import (
    EXPANDED_THRESHOLDS,
    STRICT_THRESHOLDS,
    short_mask,
    strict_mask,
)


DETAIL_OUTPUT = RESULTS / "density_overlap_trades.csv"
SUMMARY_OUTPUT = RESULTS / "density_overlap_audit.json"


def component_passes(
    featured: pd.DataFrame, *, side: str, thresholds: dict[str, float]
) -> pd.DataFrame:
    """Return existing density-rule component booleans for one trade side."""

    if side not in {"long", "short"}:
        raise ValueError(f"side must be long|short, got {side!r}")
    range24 = featured["drawdown24"] if side == "long" else featured["runup24"]
    extension = featured["ext_up"] if side == "long" else featured["ext_down"]
    order = featured["order_score"] if side == "long" else featured["down_order_score"]
    return pd.DataFrame(
        {
            "fast_spread": featured["fast_spread"].le(thresholds["fast_spread_max"]),
            "full_spread": featured["full_spread"].le(thresholds["full_spread_max"]),
            "fast_slow_gap": featured["fast_slow_gap"].le(
                thresholds["fast_slow_gap_max"]
            ),
            "full_ratio_min48": featured["full_ratio_min48"].le(
                thresholds["full_ratio_min48_max"]
            ),
            "pre_range48": featured["pre_range48"].le(thresholds["pre_range48_max"]),
            "pre_range168": featured["pre_range168"].le(
                thresholds["pre_range168_max"]
            ),
            "side_range24": range24.le(thresholds["drawdown24_max"]),
            "side_extension": extension.between(
                thresholds["ext_up_min"], thresholds["ext_up_max"]
            ),
            "side_order": order.ge(thresholds["order_score_min"]),
            "slow_slope_abs": featured["slow_slope_abs"].le(
                thresholds["slow_slope_abs_max"]
            ),
            "zero_volume96": featured["zero_volume96"].le(
                thresholds["zero_volume96_max"]
            ),
            "volume_ratio": featured["volume_ratio"].ge(
                thresholds["volume_ratio_min"]
            ),
        }
    ).fillna(False)


def circular_overlap_null(
    signal_long: np.ndarray,
    signal_short: np.ndarray,
    eligible_long: np.ndarray,
    eligible_short: np.ndarray,
) -> dict[str, Any]:
    """Enumerate all common circular time shifts of the two-side signal path."""

    arrays = tuple(
        np.asarray(values, dtype=np.int8)
        for values in (signal_long, signal_short, eligible_long, eligible_short)
    )
    if len({len(values) for values in arrays}) != 1:
        raise ValueError("all circular-overlap arrays must have the same length")
    n = len(arrays[0])
    if n == 0:
        raise ValueError("circular-overlap arrays cannot be empty")

    sig_long, sig_short, mask_long, mask_short = arrays
    # Circular cross-correlation.  Orientation does not affect the complete
    # shift distribution; shift zero is explicitly recomputed below.
    counts = np.rint(
        np.fft.ifft(
            np.conj(np.fft.fft(sig_long)) * np.fft.fft(mask_long)
            + np.conj(np.fft.fft(sig_short)) * np.fft.fft(mask_short)
        ).real
    ).astype(int)
    observed = int(sig_long @ mask_long + sig_short @ mask_short)
    if int(counts[0]) != observed:
        raise RuntimeError("circular overlap shift-zero contract failed")
    return {
        "bars": n,
        "signals": int(sig_long.sum() + sig_short.sum()),
        "observed_overlap": observed,
        "observed_rate": float(observed / max(1, sig_long.sum() + sig_short.sum())),
        "null_mean_overlap": float(counts.mean()),
        "null_q05_overlap": float(np.quantile(counts, 0.05)),
        "null_q95_overlap": float(np.quantile(counts, 0.95)),
        "exact_circular_shift_p_enrichment": float(np.mean(counts >= observed)),
        "exact_shifts": n,
    }


def _trade_rows(
    featured: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    split: str,
    masks: dict[str, dict[str, pd.Series]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    strict_components = {
        side: component_passes(featured, side=side, thresholds=STRICT_THRESHOLDS)
        for side in ("long", "short")
    }
    for trade in trades.itertuples(index=False):
        i = int(trade.signal_i)
        side = str(trade.direction)
        components = strict_components[side].iloc[i]
        rows.append(
            {
                "split": split,
                "trade_id": trade.trade_id,
                "side": side,
                "signal_i": i,
                "signal_time": trade.signal_time,
                "project_net_return": float(trade.project_net_return),
                "ma_spread_bp": float(featured.iloc[i]["ma_spread_pct"] * 10_000.0),
                "full_spread_bp": float(featured.iloc[i]["full_spread"] * 10_000.0),
                "strict_density_eligible": bool(masks["strict"][side].iloc[i]),
                "expanded_density_eligible": bool(masks["expanded"][side].iloc[i]),
                "strict_components_passed": int(components.sum()),
                **{
                    f"strict_pass_{name}": bool(value)
                    for name, value in components.items()
                },
            }
        )
    return pd.DataFrame(rows)


def _split_summary(rows: pd.DataFrame) -> dict[str, Any]:
    n = len(rows)
    strict = rows["strict_density_eligible"]
    expanded = rows["expanded_density_eligible"]
    return {
        "trades": n,
        "long_trades": int(rows["side"].eq("long").sum()),
        "short_trades": int(rows["side"].eq("short").sum()),
        "strict_overlap": int(strict.sum()),
        "strict_overlap_rate": float(strict.mean()),
        "expanded_overlap": int(expanded.sum()),
        "expanded_overlap_rate": float(expanded.mean()),
        "ma_spread_bp_q10": float(rows["ma_spread_bp"].quantile(0.1)),
        "ma_spread_bp_median": float(rows["ma_spread_bp"].median()),
        "ma_spread_bp_q90": float(rows["ma_spread_bp"].quantile(0.9)),
        "net_bp_all": float(rows["project_net_return"].mean() * 10_000.0),
        "net_bp_strict_overlap": (
            None
            if not strict.any()
            else float(rows.loc[strict, "project_net_return"].mean() * 10_000.0)
        ),
        "net_bp_not_strict": (
            None
            if strict.all()
            else float(rows.loc[~strict, "project_net_return"].mean() * 10_000.0)
        ),
    }


def main() -> None:
    config = load_config()
    raw, quality = load_research_frame(config)
    featured = build_feature_frame(raw)
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("density overlap audit crossed the holdout boundary")

    masks = {
        mode: {
            "long": strict_mask(featured, mode=mode).fillna(False),
            "short": short_mask(featured, mode=mode).fillna(False),
        }
        for mode in ("strict", "expanded")
    }
    variants = Variant("v9_density_mapping", "v9_long", "v9_short")
    details = []
    split_results: dict[str, Any] = {}
    for period in SPLITS:
        trades, _summary = simulate_period(featured, variants, period)
        rows = _trade_rows(
            featured, trades, split=period.name, masks=masks
        )
        details.append(rows)

        times = pd.to_datetime(featured["open_time"], utc=True)
        in_period = times.ge(period.start) & times.lt(period.end)
        local_positions = np.flatnonzero(in_period.to_numpy())
        offset = int(local_positions[0])
        signal_long = np.zeros(len(local_positions), dtype=np.int8)
        signal_short = np.zeros(len(local_positions), dtype=np.int8)
        for trade in trades.itertuples(index=False):
            target = signal_long if trade.direction == "long" else signal_short
            target[int(trade.signal_i) - offset] = 1

        nulls = {}
        for mode in ("strict", "expanded"):
            eligible_long = masks[mode]["long"].iloc[local_positions].to_numpy(dtype=np.int8)
            eligible_short = masks[mode]["short"].iloc[local_positions].to_numpy(dtype=np.int8)
            nulls[mode] = circular_overlap_null(
                signal_long, signal_short, eligible_long, eligible_short
            )
        split_results[period.name] = {
            **_split_summary(rows),
            "circular_shift_null": nulls,
        }

    detail = pd.concat(details, ignore_index=True)
    component_columns = [
        column for column in detail.columns if column.startswith("strict_pass_")
    ]
    overall = _split_summary(detail)
    overall["strict_component_pass_rates"] = {
        column.removeprefix("strict_pass_"): float(detail[column].mean())
        for column in component_columns
    }
    overall["strict_fast_spread_threshold_bp"] = float(
        STRICT_THRESHOLDS["fast_spread_max"] * 10_000.0
    )
    overall["expanded_fast_spread_threshold_bp"] = float(
        EXPANDED_THRESHOLDS["fast_spread_max"] * 10_000.0
    )

    artifact = {
        "artifact": "Pine V9 versus project moving-average density overlap",
        "generated_from_commit": current_commit(),
        "status": "descriptive semantic audit; no gate selected",
        "data_quality": quality,
        "threshold_source": "yoyo.data.indicators existing owner-frozen strict/expanded presets",
        "primary_question": (
            "Does the SMA10/SMA60 crossover strategy actually fire on the project's "
            "EMA8/13/21/34/55 plus EMA144/200 density definition?"
        ),
        "overall": overall,
        "splits": split_results,
        "null_hypothesis": (
            "Within each split, circularly shift the complete long/short V9 signal path "
            "against the side-specific density masks; enumerate every possible shift."
        ),
        "interpretation_guardrail": (
            "Overlap and outcome subgroup means are diagnostic only. They do not authorize "
            "adding a density gate, changing thresholds, training L2, or replacing V9."
        ),
        "holdout_consumed": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    DETAIL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(DETAIL_OUTPUT, index=False)
    SUMMARY_OUTPUT.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()

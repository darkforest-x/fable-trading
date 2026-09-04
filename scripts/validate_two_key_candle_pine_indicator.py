#!/usr/bin/env python3
"""Validate the Pine V1 source and the two owner-provided morphology anchors.

The validator is non-directional implementation QA, not a trading evaluation.
It reads the saved official OKX 1h OHLCV series only to reconstruct the two
timestamps explicitly supplied by the owner. Features use current/past OHLCV,
ATR14, SMA40(HL2), SMA/EMA 20/60/120, the MA Shift oscillator (1000-bar
percentile, lag 15, HMA10), and confirmed 10/10 pivots. The only later datum is
the immediately following bar's open, which is the causal entry-time price.
No return, target hit, model, parameter fit, or holdout aggregate is computed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.replay_owner_two_key_candle_anchors import ANCHORS, pair_row
from scripts.research_two_key_candle_ma_retest_1h import (
    add_features,
    broad_masks,
    direction_columns,
    sha256_file,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-two-key-candle-feature-atlas-v3"
RESULTS = EXPERIMENT / "results"
PINE = EXPERIMENT / "pine/fable_two_key_candle_sma40_retest_v1.pine"
RAW = RESULTS / "owner_anchor_okx_1h.csv.gz"
EXPECTED = RESULTS / "owner_anchor_pairs.csv"
COMPILE_RECEIPT = RESULTS / "pine_compile_receipt.json"
OUTPUT = RESULTS / "pine_validation.json"
V2_CONFIG = (
    PROJECT
    / "experiments/active/exp-two-key-candle-ma-retest-sma40-state-v2/config.json"
)


def _non_comment_source(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )


def _best_gap_at_anchor(
    featured: pd.DataFrame,
    *,
    direction: int,
    k2_time: pd.Timestamp,
    config: dict[str, Any],
) -> tuple[int | None, list[int]]:
    """Return the broad-profile K1-quality winner and every eligible gap."""

    side = direction_columns(featured, direction)
    side.attrs["direction"] = direction
    k1_mask, k2_mask = broad_masks(side, featured, config)
    matches = featured.index[featured["open_time"].eq(k2_time)].tolist()
    if len(matches) != 1:
        raise AssertionError(f"expected one K2 row for {k2_time}, got {matches}")
    k2_i = int(matches[0])
    if not bool(k2_mask.iloc[k2_i]):
        return None, []

    candidates: list[tuple[float, int]] = []
    for gap in range(2, 9):
        k1_i = k2_i - gap
        if k1_i < 0 or not bool(k1_mask.iloc[k1_i]):
            continue
        open_ = float(featured.loc[k1_i, "open"])
        close = float(featured.loc[k1_i, "close"])
        body_low = min(open_, close)
        body_high = max(open_, close)
        rope_low = float(featured.loc[k1_i, "rope_low"])
        rope_high = float(featured.loc[k1_i, "rope_high"])
        rope_mid = float(featured.loc[k1_i, "rope_mid"])
        atr = float(featured.loc[k1_i, "atr"])
        overlap = max(0.0, min(body_high, rope_high) - max(body_low, rope_low))
        rope_width = rope_high - rope_low
        coverage = (
            min(1.0, overlap / rope_width)
            if rope_width > 0.0
            else float(body_low <= rope_mid <= body_high)
        )
        if direction > 0:
            entry_depth = (rope_low - open_) / atr
            exit_depth = (close - rope_high) / atr
        else:
            entry_depth = (open_ - rope_high) / atr
            exit_depth = (rope_low - close) / atr
        rope_cross_depth = min(entry_depth, exit_depth)
        body_ratio = float(side.loc[k1_i, "k1_body_ratio"])
        range_atr = float(side.loc[k1_i, "k1_range_atr"])
        quality = float(
            np.mean(
                [
                    min(1.0, coverage),
                    min(1.0, max(0.0, body_ratio)),
                    min(1.0, max(0.0, range_atr / 2.0)),
                    min(1.0, max(0.0, (rope_cross_depth + 0.15) / 0.50)),
                ]
            )
        )
        candidates.append((quality, gap))
    if not candidates:
        return None, []
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return int(candidates[0][1]), sorted(gap for _, gap in candidates)


def validate() -> dict[str, Any]:
    source = PINE.read_text(encoding="utf-8")
    code = _non_comment_source(source)
    config = json.loads(V2_CONFIG.read_text(encoding="utf-8"))
    receipt = json.loads(COMPILE_RECEIPT.read_text(encoding="utf-8"))
    expected = pd.read_csv(EXPECTED)
    raw = pd.read_csv(RAW, parse_dates=["open_time"])
    featured = add_features(raw)

    static_checks = {
        "pine_v6": source.startswith("//@version=6\n"),
        "indicator_not_strategy": "indicator(" in code and "strategy(" not in code,
        "no_external_data_request": "request." not in code,
        "no_negative_history_offset": re.search(r"\[\s*-\s*\d+", code) is None,
        "k2_requires_confirmed_bar": "barstate.isconfirmed and longK2Found" in code,
        "entry_uses_previous_k2": "longK2Found[1]" in code
        and "shortK2Found[1]" in code,
        "entry_runs_on_new_bar": "if barstate.isnew" in code,
        "exact_k2_extreme_stop": "longEntryEvent ? low[1]" in code
        and "shortEntryEvent ? high[1]" in code,
        "dynamic_and_selectable_alerts": "alert(" in code
        and "alertcondition(" in code,
        "risk_reward_drawings": "box.new(" in code and "line.new(" in code,
        "frozen_broad_gap": "broadProfile ? 2" in code
        and "broadProfile ? 8" in code,
        "visual_score_disclaimed": "形态≠盈利概率" in source,
    }

    source_hash = sha256_file(PINE)
    compile_checks = {
        "official_compiler_run": receipt["official_pine_compiler_run"] is True,
        "official_compiler_zero_errors": int(receipt["pine_compile_error_count"]) == 0,
        "compiled_source_hash_matches": receipt["source_sha256"] == source_hash,
        "not_saved_or_published": receipt["script_saved_to_tradingview"] is False
        and receipt["script_published"] is False,
    }

    expected_by_name = expected.set_index("name")
    anchor_rows: list[dict[str, Any]] = []
    reverse_matches = 0
    for anchor in ANCHORS:
        name = str(anchor["name"])
        direction = int(anchor["direction"])
        k2_time = pd.Timestamp(anchor["k2_time"])
        best_gap, all_gaps = _best_gap_at_anchor(
            featured,
            direction=direction,
            k2_time=k2_time,
            config=config,
        )
        reverse_gap, reverse_all = _best_gap_at_anchor(
            featured,
            direction=-direction,
            k2_time=k2_time,
            config=config,
        )
        if reverse_gap is not None:
            reverse_matches += 1
        actual = pair_row(featured, anchor)
        expected_row = expected_by_name.loc[name]
        anchor_rows.append(
            {
                "name": name,
                "direction": "long" if direction > 0 else "short",
                "k1_time": str(actual["k1_time"]),
                "k2_time": str(actual["k2_time"]),
                "eligible_gaps": all_gaps,
                "selected_gap": best_gap,
                "expected_gap": int(expected_row["gap_bars"]),
                "causal_entry": float(actual["entry_price"]),
                "expected_entry": float(expected_row["entry_price"]),
                "exact_stop": float(actual["stop_price"]),
                "expected_stop": float(expected_row["stop_price"]),
                "stop_distance_atr": float(actual["stop_distance_atr_24"]),
                "morphology_score": float(actual["anchor_score"]),
                "expected_score": float(expected_row["anchor_score"]),
                "reverse_direction_gaps": reverse_all,
                "passes": bool(
                    best_gap == int(expected_row["gap_bars"])
                    and np.isclose(
                        float(actual["entry_price"]),
                        float(expected_row["entry_price"]),
                        atol=1e-9,
                    )
                    and np.isclose(
                        float(actual["stop_price"]),
                        float(expected_row["stop_price"]),
                        atol=1e-9,
                    )
                    and np.isclose(
                        float(actual["anchor_score"]),
                        float(expected_row["anchor_score"]),
                        atol=1e-9,
                    )
                    and reverse_gap is None
                ),
            }
        )

    parity_checks = {
        "owner_anchor_matches": sum(int(row["passes"]) for row in anchor_rows),
        "owner_anchor_total": len(anchor_rows),
        "all_owner_anchors_match": all(row["passes"] for row in anchor_rows),
        "reverse_direction_null_matches": reverse_matches,
        "reverse_direction_null_total": len(anchor_rows),
        "reverse_direction_null_passes": reverse_matches == 0,
    }
    checks = {**static_checks, **compile_checks}
    passed = all(checks.values()) and parity_checks["all_owner_anchors_match"] and parity_checks[
        "reverse_direction_null_passes"
    ]
    return {
        "status": "pass" if passed else "fail",
        "purpose": "Pine source/static/owner-anchor parity; no trading evaluation",
        "pine_source": str(PINE.relative_to(PROJECT)),
        "pine_source_sha256": source_hash,
        "static_checks": static_checks,
        "compile_checks": compile_checks,
        "anchor_parity": anchor_rows,
        "null_control": {
            "definition": "invert each owner anchor's direction at the same K2 timestamp",
            "result": parity_checks,
        },
        "holdout_statement": (
            "Only the two owner-specified 2026-09 morphology timestamps and their "
            "causal next opens were reconstructed; no post-K2 return, outcome, "
            "aggregate signal scan, score selection, or parameter tuning was performed."
        ),
        "checks": checks,
    }


def main() -> int:
    payload = validate()
    OUTPUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

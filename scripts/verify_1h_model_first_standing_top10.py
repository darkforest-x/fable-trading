#!/usr/bin/env python3
"""Independently verify the frozen OKX 1h model-first Top-10 artifacts.

This verifier performs no network request and no model inference.  It hashes
every frozen candle file, independently recomputes all six trailing moving
averages and every actual/flipped standing decision, reconstructs the declared
Top-10 ordering without any future fields, recalculates review-only outcomes,
replays selected model-input pixels, and validates all ten global charts.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-1h-okx-model-first-standing-top10-20260904-v1"
EXP_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG = EXP_DIR / "preregistration.json"
RESULTS = EXP_DIR / "results"
FETCH_ROWS = 396
FUTURE_BARS = 96
TOP_K = 10
MA_COLS = ("sma20", "sma60", "sma120", "ema20", "ema60", "ema120")


class TopTenVerificationError(RuntimeError):
    """Raised when a delivered artifact differs from the frozen contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def delivered_bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    if text not in {"true", "false"}:
        raise TopTenVerificationError(f"not a delivered boolean: {value}")
    return text == "true"


def assert_close(actual: object, expected: float, label: str, atol: float = 1e-10) -> None:
    if not bool(np.isclose(float(actual), float(expected), rtol=0.0, atol=atol)):
        raise TopTenVerificationError(f"{label} differs: {actual} != {expected}")


def independent_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute the six close-based trailing averages without importing L1 data."""

    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="raise")
    for period in (20, 60, 120):
        out[f"sma{period}"] = close.rolling(period).mean()
        out[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    return out


def manual_gate(frame: pd.DataFrame, index: int, direction: str) -> dict[str, Any]:
    """Evaluate the declared one-row gate independently."""

    row = frame.iloc[index]
    close = float(row["close"])
    mas = row.loc[list(MA_COLS)].to_numpy(dtype=float)
    if not bool(np.isfinite(np.concatenate(([close], mas))).all()):
        raise TopTenVerificationError(f"non-finite gate input at {index}")
    if direction == "LONG":
        edge = float(mas.max())
        passed = close > edge
    elif direction == "SHORT":
        edge = float(mas.min())
        passed = close < edge
    else:
        raise TopTenVerificationError(f"unsupported direction: {direction}")
    return {"passed": bool(passed), "close": close, "edge": edge}


def expected_outcomes(row: Mapping[str, Any], frame: pd.DataFrame) -> dict[str, float]:
    """Recalculate the four-day review fields after selection."""

    end = int(row["window_end_i"])
    reference = float(frame.iloc[end]["close"])
    side = str(row["direction"])
    sign = 1.0 if side == "LONG" else -1.0
    values: dict[str, float] = {"review_reference_close": reference}
    for horizon in (24, 48, 96):
        close = float(frame.iloc[end + horizon]["close"])
        values[f"review_directional_move_{horizon}h_pct"] = (
            sign * (close / reference - 1.0) * 100.0
        )
    future = frame.iloc[end + 1 : end + FUTURE_BARS + 1]
    if side == "LONG":
        mfe = float(future["high"].max() / reference - 1.0)
        mae = float(future["low"].min() / reference - 1.0)
    else:
        mfe = float(1.0 - future["low"].min() / reference)
        mae = float(1.0 - future["high"].max() / reference)
    values["review_mfe_96h_pct"] = mfe * 100.0
    values["review_mae_96h_pct"] = mae * 100.0
    return values


def main() -> int:
    output = RESULTS / "verification.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise TopTenVerificationError("preregistration identity drifted")
    if int(summary.get("holdout_consumption_number_for_checkpoint", -1)) != 20:
        raise TopTenVerificationError("holdout accounting drifted")
    if summary["recovery"] != {
        **summary["recovery"],
        "used": True,
        "new_market_reads": 0,
        "semantic_or_selection_change": False,
    }:
        raise TopTenVerificationError("offline recovery disclosure drifted")
    if int(summary["causal_scan"]["future_review_bars_physically_removed_before_inference"]) != FUTURE_BARS:
        raise TopTenVerificationError("future reserve drifted")
    if bool(summary["selection"]["future_or_outcome_fields_used"]):
        raise TopTenVerificationError("summary claims outcome-based selection")

    fetch = json.loads((RESULTS / "fetch_audit.json").read_text(encoding="utf-8"))
    frames: dict[str, pd.DataFrame] = {}
    candle_hashes = 0
    for audit in fetch["usable"]:
        symbol = str(audit["symbol"])
        path = RESULTS / "candles" / f"{symbol}.csv"
        if sha256_file(path) != str(audit["sha256"]):
            raise TopTenVerificationError(f"candle hash drift: {symbol}")
        frame = pd.read_csv(path)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        if len(frame) != FETCH_ROWS:
            raise TopTenVerificationError(f"candle length drift: {symbol}")
        if not bool((frame["open_time"].diff().iloc[1:] == pd.Timedelta(hours=1)).all()):
            raise TopTenVerificationError(f"candle gap: {symbol}")
        frames[symbol] = frame
        candle_hashes += 1
    if len(frames) != int(summary["source"]["usable_instruments"]):
        raise TopTenVerificationError("usable-symbol count drifted")

    decisions = pd.read_csv(RESULTS / "standing_decisions.csv")
    events = pd.read_csv(RESULTS / "deduplicated_events.csv")
    selected = pd.read_csv(RESULTS / "selected_top10.csv")
    if len(selected) != TOP_K:
        raise TopTenVerificationError(f"expected {TOP_K} selected rows, got {len(selected)}")
    forbidden = ("future", "return", "mfe", "mae", "profit", "outcome", "review_")
    contaminated_event_columns = [
        column
        for column in events.columns
        if any(fragment in column.lower() for fragment in forbidden)
    ]
    if contaminated_event_columns:
        raise TopTenVerificationError(
            f"pre-selection event ledger contains future fields: {contaminated_event_columns}"
        )

    ma_cache = {
        symbol: independent_mas(frame.iloc[:-FUTURE_BARS].copy())
        for symbol, frame in frames.items()
    }
    actual_passes = 0
    flipped_passes = 0
    for _, row in decisions.iterrows():
        symbol = str(row["symbol"])
        direction = str(row["direction"])
        end = int(row["window_end_i"])
        actual = manual_gate(ma_cache[symbol], end, direction)
        flipped = manual_gate(
            ma_cache[symbol], end, "SHORT" if direction == "LONG" else "LONG"
        )
        if delivered_bool(row["standing_gate_pass"]) != actual["passed"]:
            raise TopTenVerificationError(f"actual gate drift: {row['candidate_id']}")
        if delivered_bool(row["flipped_standing_gate_pass"]) != flipped["passed"]:
            raise TopTenVerificationError(f"flipped gate drift: {row['candidate_id']}")
        assert_close(
            row["standing_current_close"], actual["close"], f"{row['candidate_id']}:close"
        )
        assert_close(
            row["standing_bundle_edge"], actual["edge"], f"{row['candidate_id']}:edge"
        )
        actual_passes += int(actual["passed"])
        flipped_passes += int(flipped["passed"])

    ranked = events.copy()
    ranked["_available_ns"] = pd.to_datetime(
        ranked["first_available_at"], utc=True
    ).astype("int64")
    ranked = ranked.sort_values(
        ["event_peak_confidence", "_available_ns", "symbol", "class_id"],
        ascending=[False, False, True, True],
        kind="mergesort",
    ).head(TOP_K)
    if list(ranked["event_id"].astype(str)) != list(selected["event_id"].astype(str)):
        raise TopTenVerificationError("Top-10 identities/order do not match causal event ranking")
    if list(selected["review_rank"].astype(int)) != list(range(1, TOP_K + 1)):
        raise TopTenVerificationError("review ranks are not 1..10")

    from yoyo.layers.l1_detection.render import render_chart

    pixel_replays = 0
    future_mutations = 0
    chart_hashes: dict[str, str] = {}
    positive_96h = 0
    for _, row in selected.iterrows():
        symbol = str(row["symbol"])
        frame = frames[symbol]
        enriched = ma_cache[symbol]
        start = int(row["window_start_i"])
        end = int(row["window_end_i"])
        replay, _ = render_chart(enriched.iloc[start : end + 1], out_path=None)
        digest = hashlib.sha256(np.ascontiguousarray(replay).tobytes()).hexdigest()
        if digest != str(row["input_pixel_sha256"]):
            raise TopTenVerificationError(f"model pixel drift: {row['event_id']}")
        pixel_replays += 1

        original = manual_gate(enriched, end, str(row["direction"]))
        mutated = frame.copy()
        mask = mutated.index > end
        multipliers = np.linspace(11.0, 110.0, int(mask.sum()))
        for column in ("open", "high", "low", "close", "volume"):
            mutated.loc[mask, column] = (
                mutated.loc[mask, column].to_numpy(dtype=float) * multipliers
            )
        if manual_gate(independent_mas(mutated), end, str(row["direction"])) != original:
            raise TopTenVerificationError(f"future mutation changed {row['event_id']}")
        future_mutations += 1

        for key, expected in expected_outcomes(row, frame).items():
            assert_close(row[key], expected, f"{row['event_id']}:{key}", atol=1e-8)
        if float(row["review_directional_move_96h_pct"]) > 0:
            positive_96h += 1

        chart = RESULTS / "review" / "charts" / str(row["chart_filename"])
        decoded = cv2.imread(str(chart), cv2.IMREAD_COLOR)
        if decoded is None or tuple(decoded.shape) != (1400, 1920, 3):
            raise TopTenVerificationError(f"global chart missing/shape drift: {chart.name}")
        model_input = RESULTS / "model_inputs" / str(row["chart_filename"]).replace(
            "_global", "_model_input"
        )
        model_decoded = cv2.imread(str(model_input), cv2.IMREAD_COLOR)
        if model_decoded is None or tuple(model_decoded.shape) != (742, 1280, 3):
            raise TopTenVerificationError(
                f"model input chart missing/shape drift: {model_input.name}"
            )
        chart_hashes[chart.name] = sha256_file(chart)

    causal = summary["causal_scan"]
    expected_counts = {
        "structurally_accepted_boxes": len(decisions),
        "standing_gate_pass_boxes": actual_passes,
        "direction_flipped_gate_pass_boxes": flipped_passes,
        "deduplicated_events": len(events),
    }
    for key, value in expected_counts.items():
        if int(causal[key]) != value:
            raise TopTenVerificationError(f"summary {key} drifted")
    if int(summary["post_selection_review"]["positive_directional_move_at_96h"]) != positive_96h:
        raise TopTenVerificationError("summary 96h sign count drifted")

    receipt: Mapping[str, Any] = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "network_reads": 0,
        "model_inference_calls": 0,
        "candle_hashes_verified": candle_hashes,
        "proposal_gates_recomputed": len(decisions),
        "actual_gate_passes": actual_passes,
        "flipped_gate_passes": flipped_passes,
        "deduplicated_events": len(events),
        "top10_order_reconstructed": True,
        "selected_pixel_replays": pixel_replays,
        "selected_future_mutation_passes": future_mutations,
        "review_outcomes_recomputed": len(selected),
        "positive_directional_move_at_96h": positive_96h,
        "non_positive_directional_move_at_96h": len(selected) - positive_96h,
        "global_chart_hashes": chart_hashes,
    }
    output.write_text(
        json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

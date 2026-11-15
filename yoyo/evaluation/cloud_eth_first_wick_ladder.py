"""Strict ETH first-wick morphology audit on the frozen peak10r candles.

For each NY week and each 1.618 rail, locate the first strictly crossing
complete H1 candle after the Monday range locks. Inspect only that H1 candle
and its UTC-aligned 4H/12H mothers. No trades or future outcomes are read.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.monitor.monday_fib import week_window


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "exp-cloud-top20-peak10r-20261108-v1"
SOURCE = ROOT / "experiments/active" / SOURCE_ID
INPUT = SOURCE / "inputs/ETH"
HOUR_MS = 3_600_000
STEP_4H_MS = 4 * HOUR_MS
STEP_12H_MS = 12 * HOUR_MS
OHLC = ["open", "high", "low", "close"]
TF = {"1H": (HOUR_MS, 0), "4H": (STEP_4H_MS, 1), "12H": (STEP_12H_MS, 2)}


def _load_bars(path: Path, step_ms: int, *, with_source: bool = False) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col="time", float_precision="round_trip")
    if not set(OHLC).issubset(frame.columns):
        raise ValueError(f"Frozen OHLC fields missing: {path}")
    times = pd.to_numeric(pd.Index(frame.index), errors="raise")
    if times.isna().any() or (times % 1 != 0).any() or (times % step_ms != 0).any():
        raise ValueError(f"Frozen UTC candle timestamps are invalid or misaligned: {path}")
    frame.index = times.astype("int64")
    frame.index.name = "time_ms"
    cols = OHLC + (["source"] if with_source and "source" in frame else [])
    frame = frame.loc[:, cols].sort_index()
    if frame.empty or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Frozen bars must be nonempty, unique, and ordered: {path}")
    values = frame[OHLC].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError(f"Frozen OHLC contains missing, non-finite, or non-positive values: {path}")
    if ((frame.low > frame[["open", "close"]].min(axis=1))
            | (frame.high < frame[["open", "close"]].max(axis=1))).any():
        raise ValueError(f"Frozen OHLC range is invalid: {path}")
    return frame


def _aggregate_4h(hourly: pd.DataFrame) -> pd.DataFrame:
    """Build only UTC 4H candles with their exact four complete H1 children."""
    rows = []
    for start, group in hourly.groupby((hourly.index // STEP_4H_MS) * STEP_4H_MS, sort=True):
        expected = [int(start) + i * HOUR_MS for i in range(4)]
        if group.index.tolist() != expected:
            continue
        rows.append((int(start), float(group.open.iloc[0]), float(group.high.max()),
                     float(group.low.min()), float(group.close.iloc[-1])))
    return pd.DataFrame(rows, columns=["time_ms", *OHLC]).set_index("time_ms")


def _ema12_state(bars12: pd.DataFrame) -> dict[int, dict[str, float | int]]:
    """Close-only native-12H EMA20/50; reset after gaps, matching cloud_signals."""
    out: dict[int, dict[str, float | int]] = {}
    previous = None
    fast = slow = 0.0
    count = 0
    for stamp, row in bars12.iterrows():
        stamp = int(stamp)
        close = float(row.close)
        if previous is None or stamp != previous + STEP_12H_MS:
            fast = slow = close
            count = 0
        fast += (close - fast) * (2.0 / 21.0)
        slow += (close - slow) * (2.0 / 51.0)
        count += 1
        out[stamp] = {"ema20": fast, "ema50": slow, "count": count}
        previous = stamp
    return out


def _anchor(hourly: pd.DataFrame, window: dict[str, Any]) -> dict[str, Any]:
    expected = list(range(int(window["start"]), int(window["end"]), HOUR_MS))
    monday = hourly.reindex(expected)
    complete = len(expected) == 24 and monday[OHLC].notna().all().all()
    if not complete:
        return {**window, "state": "missing_monday_hours",
                "observed_monday_hours": int(monday[OHLC].notna().all(axis=1).sum())}
    high, low = float(monday.high.max()), float(monday.low.min())
    if not math.isfinite(high - low) or high <= low:
        return {**window, "state": "invalid_monday_range", "high": high, "low": low}
    width = high - low
    return {**window, "state": "ready", "high": high, "low": low,
            "lower": low - 0.618 * width, "upper": high + 0.618 * width,
            "observed_monday_hours": 24}


def _boundary_equalities(bar: pd.Series, lower: float, upper: float) -> list[str]:
    """Exact OHLC-to-rail equalities are retained for explicit boundary review."""
    return [name for name, value, rail in (
        ("open_lower", bar.open, lower), ("open_upper", bar.open, upper),
        ("high_lower", bar.high, lower), ("high_upper", bar.high, upper),
        ("low_lower", bar.low, lower), ("low_upper", bar.low, upper),
        ("close_lower", bar.close, lower), ("close_upper", bar.close, upper))
        if float(value) == rail]


def _morphology(bar: pd.Series, side: str, lower: float, upper: float) -> tuple[bool, str]:
    """Strict wick outside the rail with the whole candle body inside the band.

    An open may equal either rail; the close must be strictly between rails.
    The opposite-rail body bound is enforced so "entire body inside" is literal.
    """
    o, h, low, c = map(float, (bar.open, bar.high, bar.low, bar.close))
    if not lower < c < upper:
        return False, "close_not_strictly_inside"
    if not lower <= o <= upper:
        return False, "open_outside_body_band"
    if side == "upper" and h <= upper:
        return False, "upper_wick_not_strictly_outside"
    if side == "lower" and low >= lower:
        return False, "lower_wick_not_strictly_outside"
    return True, "pass"


def _trend_at_close(stamp: int, close: float, ema_state: dict[int, dict[str, float | int]],
                    side: str) -> dict[str, Any]:
    """Project each decision close from the previous completed native 12H EMA."""
    current_12h = ((stamp - 1) // STEP_12H_MS) * STEP_12H_MS
    previous = ema_state.get(current_12h - STEP_12H_MS)
    if previous is None:
        return {"trend_status": "missing_previous_native_12h", "trend_match": False}
    # The decision close is the 50th eligible sample when the prior count is 49.
    if int(previous["count"]) + 1 < 50:
        return {"trend_status": "50_bar_warmup", "trend_match": False,
                "trend_prior_count": int(previous["count"])}
    fast = float(previous["ema20"]) + (close - float(previous["ema20"])) * (2.0 / 21.0)
    slow = float(previous["ema50"]) + (close - float(previous["ema50"])) * (2.0 / 51.0)
    direction = 1 if fast > slow else (-1 if fast < slow else 0)
    expected = -1 if side == "upper" else 1
    return {"trend_status": "aligned" if direction == expected else
            ("flat" if direction == 0 else "direction_mismatch"),
            "trend_match": direction == expected, "trend_ema20": fast,
            "trend_ema50": slow, "trend_direction": direction,
            "trend_prior_count": int(previous["count"])}


def _first_cross(hourly: pd.DataFrame, anchor: dict[str, Any], side: str,
                 cutoff_ms: int) -> dict[str, Any]:
    """Find first H1 strict crossing; a missing earlier hour censors the claim."""
    if anchor["state"] != "ready":
        return {"first_cross_status": "anchor_unavailable"}
    end, expires = int(anchor["end"]), int(anchor["expires"])
    scan_end = min(expires, cutoff_ms)
    for stamp in range(end, scan_end, HOUR_MS):
        if stamp + HOUR_MS > cutoff_ms:
            return {"first_cross_status": "right_censored_at_cutoff"}
        if stamp not in hourly.index:
            return {"first_cross_status": "gap_before_first_cross",
                    "first_gap_open_ms": stamp}
        bar = hourly.loc[stamp]
        crossed = float(bar.high) > float(anchor["upper"]) if side == "upper" else (
            float(bar.low) < float(anchor["lower"]))
        if crossed:
            return {"first_cross_status": "found", "first_cross_open_ms": stamp,
                    "first_cross_close_ms": stamp + HOUR_MS,
                    **{f"first_h1_{name}": float(bar[name]) for name in OHLC},
                    "first_cross_equalities": ";".join(_boundary_equalities(
                        bar, float(anchor["lower"]), float(anchor["upper"])))}
    if expires <= cutoff_ms:
        return {"first_cross_status": "no_cross_full_week"}
    return {"first_cross_status": "right_censored_at_cutoff"}


def _candidate_rows(week: str, side: str, hit: dict[str, Any], anchor: dict[str, Any],
                    hourly: pd.DataFrame, bars4: pd.DataFrame, bars12: pd.DataFrame,
                    ema_state: dict[int, dict[str, float | int]]) -> list[dict[str, Any]]:
    if hit.get("first_cross_status") != "found":
        return []
    hit_ms = int(hit["first_cross_open_ms"])
    lower, upper = float(anchor["lower"]), float(anchor["upper"])
    specs = [("1H", hit_ms, hourly),
             ("4H", (hit_ms // STEP_4H_MS) * STEP_4H_MS, bars4),
             ("12H", (hit_ms // STEP_12H_MS) * STEP_12H_MS, bars12)]
    rows = []
    for timeframe, start, frame in specs:
        step, priority = TF[timeframe]
        exists = start in frame.index
        time_eligible = int(anchor["end"]) <= start and start + step <= int(anchor["expires"])
        if exists:
            bar = frame.loc[start]
            passed, reason = _morphology(bar, side, lower, upper)
            decision_close = int(start + step)
            trend = _trend_at_close(decision_close, float(bar.close), ema_state, side)
            equality = _boundary_equalities(bar, lower, upper)
            source = str(bar.get("source", "complete_1H_aggregation" if timeframe == "4H"
                                 else "native_H1"))
            row = {f"bar_{name}": float(bar[name]) for name in OHLC}
        else:
            passed, reason, decision_close, trend, equality, source = (
                False, "mother_bar_unavailable", start + step,
                {"trend_status": "not_evaluated", "trend_match": False}, [], "missing")
            row = {}
        rows.append({"candidate_id": f"{week}|{side}|{timeframe}|{start}",
            "week": week, "side": side, "trade_direction": -1 if side == "upper" else 1,
            "first_cross_open_ms": hit_ms, "first_cross_close_ms": int(hit["first_cross_close_ms"]),
            "timeframe": timeframe, "timeframe_priority": priority,
            "bar_open_ms": int(start), "decision_close_ms": int(decision_close),
            "bar_source": source, "bar_exists": bool(exists),
            "signal_bar_inside_anchor_window": bool(time_eligible),
            "morphology_pass": bool(passed), "morphology_status": reason,
            "boundary_equalities": ";".join(equality),
            "open_eq_lower": "open_lower" in equality, "open_eq_upper": "open_upper" in equality,
            "high_eq_upper": "high_upper" in equality, "low_eq_lower": "low_lower" in equality,
            "close_eq_lower": "close_lower" in equality, "close_eq_upper": "close_upper" in equality,
            **row, **trend})
    return rows


def _first_side_candidate(rows: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    eligible = [r for r in rows if r["signal_bar_inside_anchor_window"]
                and r["bar_exists"] and r["morphology_pass"] and predicate(r)]
    return min(eligible, key=lambda r: (r["decision_close_ms"], r["timeframe_priority"])) if eligible else None


def _weekly_pick(candidates: list[dict[str, Any]], status: str) -> dict[str, Any]:
    if not candidates:
        return {"selection_status": status}
    candidates = sorted(candidates, key=lambda r: (r["decision_close_ms"],
                                                   r["timeframe_priority"], r["side"]))
    first = candidates[0]
    tied = [r for r in candidates if (r["decision_close_ms"], r["timeframe_priority"])
            == (first["decision_close_ms"], first["timeframe_priority"])]
    if len({r["side"] for r in tied}) > 1:
        return {"selection_status": "ambiguous_same_time_opposite_sides",
                "tie_candidate_ids": ";".join(r["candidate_id"] for r in tied)}
    return {"selection_status": "selected", **first}


def _iso(ms: int | None) -> str | None:
    return None if ms is None else pd.Timestamp(ms, unit="ms", tz="UTC").isoformat()


def main(output_dir: Path | str) -> dict[str, Any]:
    """Write ledgers to ``output_dir`` and return the compact summary."""
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
    cutoff_ms = int(pd.Timestamp(config["cutoff_utc"]).timestamp() * 1000)
    hourly = _load_bars(INPUT / "bars1h.csv.gz", HOUR_MS)
    bars12 = _load_bars(INPUT / "bars12h.csv.gz", STEP_12H_MS, with_source=True)
    hourly = hourly.loc[hourly.index + HOUR_MS <= cutoff_ms]
    bars12 = bars12.loc[bars12.index + STEP_12H_MS <= cutoff_ms]
    bars4 = _aggregate_4h(hourly)
    ema_state = _ema12_state(bars12)
    last_observed_close = min(cutoff_ms, int(hourly.index[-1]) + HOUR_MS)

    first_monday = week_window(int(hourly.index[0]))["start"]
    last_monday = week_window(int(hourly.index[-1]))["start"]
    week_starts = []
    monday = first_monday
    while monday <= last_monday:
        week_starts.append(monday)
        monday = week_window(monday)["expires"]
    side_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    weekly_rows: list[dict[str, Any]] = []
    for monday_ms in week_starts:
        window = week_window(int(monday_ms))
        week = str(window["week"])
        anchor = _anchor(hourly, window)
        week_sides: dict[str, list[dict[str, Any]]] = {"upper": [], "lower": []}
        cross_results = {}
        for side in ("upper", "lower"):
            hit = _first_cross(hourly, anchor, side, cutoff_ms)
            cross_results[side] = hit
            side_row = {"week": week, "side": side, "anchor_state": anchor["state"],
                "anchor_start_ms": int(anchor["start"]), "anchor_end_ms": int(anchor["end"]),
                "anchor_expires_ms": int(anchor["expires"]),
                "anchor_high": anchor.get("high"), "anchor_low": anchor.get("low"),
                "lower_1618": anchor.get("lower"), "upper_1618": anchor.get("upper"),
                "week_complete_at_cutoff": bool(int(anchor["expires"]) <= cutoff_ms),
                **hit}
            if "first_cross_open_ms" in hit:
                side_row["first_cross_open_utc"] = _iso(int(hit["first_cross_open_ms"]))
                side_row["first_cross_close_utc"] = _iso(int(hit["first_cross_close_ms"]))
                week_sides[side] = _candidate_rows(week, side, hit, anchor, hourly, bars4,
                                                   bars12, ema_state)
                candidates.extend(week_sides[side])
            side_rows.append(side_row)

        all_rows = week_sides["upper"] + week_sides["lower"]
        morph_by_side = [_first_side_candidate(week_sides[s], lambda r: True)
                         for s in ("upper", "lower")]
        # Once a side shows its first valid wick, use that close to check trend.
        strict_by_side = [r for r in morph_by_side if r and r["trend_match"]]
        # Alternate semantics allow a higher TF after a lower-TF morphology passed
        # but its direction did not align; retained only to count the rule effect.
        any_tf_by_side = [_first_side_candidate(week_sides[s], lambda r: r["trend_match"])
                          for s in ("upper", "lower")]
        morph_pick = _weekly_pick([r for r in morph_by_side if r], "no_strict_morphology")
        trend_pick = _weekly_pick([r for r in strict_by_side if r],
            "direction_mismatch_no_strict_signal" if any(
                (m := _first_side_candidate(week_sides[s], lambda r: True)) is not None
                for s in ("upper", "lower")) else "no_strict_morphology")
        any_pick = _weekly_pick([r for r in any_tf_by_side if r],
            "no_aligned_morphology_candidate")
        first_cross_times = [cross_results[s].get("first_cross_open_ms") for s in ("upper", "lower")]
        found = [t for t in first_cross_times if t is not None]
        weekly_rows.append({"week": week, "anchor_state": anchor["state"],
            "week_complete_at_cutoff": bool(int(anchor["expires"]) <= cutoff_ms),
            "both_sides_first_cross_found": len(found) == 2,
            "both_sides_same_first_hour": len(found) == 2 and found[0] == found[1],
            "pure_morphology_status": morph_pick["selection_status"],
            "pure_morphology_candidate_id": morph_pick.get("candidate_id"),
            "pure_morphology_side": morph_pick.get("side"),
            "pure_morphology_timeframe": morph_pick.get("timeframe"),
            "pure_morphology_decision_close_ms": morph_pick.get("decision_close_ms"),
            "morphology_trend_strict_status": trend_pick["selection_status"],
            "morphology_trend_strict_candidate_id": trend_pick.get("candidate_id"),
            "morphology_trend_strict_side": trend_pick.get("side"),
            "morphology_trend_strict_timeframe": trend_pick.get("timeframe"),
            "morphology_trend_strict_decision_close_ms": trend_pick.get("decision_close_ms"),
            "morphology_trend_any_tf_status": any_pick["selection_status"],
            "morphology_trend_any_tf_candidate_id": any_pick.get("candidate_id"),
            "morphology_trend_any_tf_side": any_pick.get("side"),
            "morphology_trend_any_tf_timeframe": any_pick.get("timeframe"),
            "morphology_trend_any_tf_decision_close_ms": any_pick.get("decision_close_ms"),
            "strict_vs_any_tf_changed": trend_pick.get("candidate_id") != any_pick.get("candidate_id"),
            "strict_direction_blocked_sides": ";".join(
                s for s in ("upper", "lower")
                if (m := _first_side_candidate(week_sides[s], lambda r: True)) is not None
                and not m["trend_match"]),
            "upper_first_cross_status": cross_results["upper"]["first_cross_status"],
            "lower_first_cross_status": cross_results["lower"]["first_cross_status"]})

    side_frame = pd.DataFrame(side_rows)
    candidate_frame = pd.DataFrame(candidates)
    weekly_frame = pd.DataFrame(weekly_rows)
    side_frame.to_csv(output_dir / "side_first_crosses.csv", index=False)
    candidate_frame.to_csv(output_dir / "mother_candidates.csv", index=False)
    weekly_frame.to_csv(output_dir / "weekly_selections.csv", index=False)

    candidate_equalities = {}
    if not candidate_frame.empty:
        for name in ("open_eq_lower", "open_eq_upper", "high_eq_upper", "low_eq_lower",
                     "close_eq_lower", "close_eq_upper"):
            candidate_equalities[name] = int(candidate_frame[name].sum())
    valid_morph = candidate_frame[candidate_frame.morphology_pass & candidate_frame.signal_bar_inside_anchor_window] if not candidate_frame.empty else candidate_frame
    summary = {
        "experiment_id": "exp-cloud-eth-first-wick-ladder-20261116-v1",
        "source_experiment": SOURCE_ID,
        "symbol": "ETH-USDT-SWAP",
        "cutoff_utc": config["cutoff_utc"],
        "source_paths": {"h1": str((INPUT / "bars1h.csv.gz").relative_to(ROOT)),
                         "h12": str((INPUT / "bars12h.csv.gz").relative_to(ROOT))},
        "source_rows": {"h1": len(hourly), "h4_complete_derived": len(bars4),
                        "h12": len(bars12), "h12_by_source": bars12.source.value_counts().to_dict()
                        if "source" in bars12 else {"unspecified": len(bars12)}},
        "observed": {"first_h1_open_utc": _iso(int(hourly.index[0])),
                     "last_h1_close_utc": _iso(int(hourly.index[-1]) + HOUR_MS),
                     "effective_observed_close_utc": _iso(last_observed_close),
                     "last_native_12h_close_utc": _iso(int(bars12.index[-1]) + STEP_12H_MS)},
        "calendar": {"weeks": len(weekly_frame),
                     "complete_at_cutoff": int(weekly_frame.week_complete_at_cutoff.sum()),
                     "right_truncated": int((~weekly_frame.week_complete_at_cutoff).sum()),
                     "ready_monday_anchors": int((side_frame.anchor_state == "ready").sum() // 2)},
        "first_crosses": {"upper_found": int((side_frame.loc[side_frame.side == "upper", "first_cross_status"] == "found").sum()),
                           "lower_found": int((side_frame.loc[side_frame.side == "lower", "first_cross_status"] == "found").sum()),
                           "both_sides_found_same_week": int(weekly_frame.both_sides_first_cross_found.sum()),
                           "same_first_h1": int(weekly_frame.both_sides_same_first_hour.sum()),
                           "statuses": side_frame.first_cross_status.value_counts().to_dict()},
        "candidate_morphology": {"mother_rows": len(candidate_frame),
                                 "time_eligible_rows": int(candidate_frame.signal_bar_inside_anchor_window.sum()) if not candidate_frame.empty else 0,
                                 "morphology_pass_rows": len(valid_morph),
                                 "by_timeframe": valid_morph.timeframe.value_counts().to_dict() if not valid_morph.empty else {},
                                 "trend_aligned_rows": int(valid_morph.trend_match.sum()) if not valid_morph.empty else 0,
                                 "direction_mismatch_rows": int((valid_morph.trend_status == "direction_mismatch").sum()) if not valid_morph.empty else 0,
                                 "boundary_equalities": candidate_equalities},
        "selection_counts": {"pure_morphology_selected": int((weekly_frame.pure_morphology_status == "selected").sum()),
                             "pure_morphology_ambiguous": int((weekly_frame.pure_morphology_status == "ambiguous_same_time_opposite_sides").sum()),
                             "morphology_plus_trend_strict_selected": int((weekly_frame.morphology_trend_strict_status == "selected").sum()),
                             "morphology_plus_trend_strict_no_aligned_signal": int((weekly_frame.morphology_trend_strict_status == "direction_mismatch_no_strict_signal").sum()),
                             "morphology_plus_trend_any_tf_selected": int((weekly_frame.morphology_trend_any_tf_status == "selected").sum()),
                             "strict_vs_any_tf_changed_weeks": int(weekly_frame.strict_vs_any_tf_changed.sum()),
                             "weeks_with_direction_blocked_sides": int((weekly_frame.strict_direction_blocked_sides != "").sum())},
        "interpretation": {
            "range": "Complete NY Monday 00:00-24:00 H1 range; 1.618 rails low-0.618*width and high+0.618*width.",
            "first_cross": "Each side independently stops at its first complete H1 high>upper or low<lower. Equality is not a cross; any earlier H1 gap censors first-cross knowledge.",
            "mothers": "Only the containing H1, UTC-aligned 4H built from exact four H1 children, and frozen UTC 12H candle are inspected.",
            "morphology": "Open may equal a rail; close is strictly inside; whole body is bounded by both rails; target-side wick is strictly beyond its rail.",
            "trend": "At each candidate close, project EMA20/50 from the prior contiguous completed frozen 12H EMA state plus that candidate close; require 50 samples. Lower wick requires EMA20>EMA50, upper wick requires EMA20<EMA50.",
            "selection": "One earliest qualifying side per NY week; order by decision close, then 1H, 4H, 12H. Opposite sides tied at the same close and timeframe are left ambiguous.",
            "strict_vs_alternative": "Primary trend arm stops at the side's first morphology-qualified timeframe even if trend direction mismatches. Any-TF arm is diagnostic and may promote to a higher timeframe; changed-week count is reported.",
            "scope": "Signal morphology only. No trading, fills, PnL, or original-ledger overlap reconstruction."},
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                                          allow_nan=False, default=str) + "\n",
                                                encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(main(args.output_dir), ensure_ascii=False, indent=2, default=str))

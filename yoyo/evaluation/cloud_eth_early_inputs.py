"""Frozen inputs for the ETH early-versus-confirmed timing comparison.

No signals are regenerated and no trades are simulated here. All times are
UTC epoch milliseconds. Bar rows expose ``time_ms, open, high, low, close``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EARLY_EXP = "exp-cloud-eth-hourly-early-signal-20261115-v1"
SOURCE_EXP = "exp-cloud-top20-peak10r-20261108-v1"
HOUR_MS = 3_600_000
STEP_12H_MS = 12 * HOUR_MS
STOP_PCT = 0.015
OHLC = ["open", "high", "low", "close"]


@dataclass(frozen=True)
class InputBundle:
    opportunities: list[dict[str, Any]]
    bars1h: list[dict[str, Any]]
    bars12h: list[dict[str, Any]]
    cloud_crosses: list[dict[str, Any]]


def _bars(path: Path, step_ms: int) -> pd.DataFrame:
    frame = pd.read_csv(path, float_precision="round_trip")
    if not {"time", *OHLC}.issubset(frame.columns):
        raise ValueError(f"Invalid frozen OHLC columns: {path}")
    times = pd.to_numeric(frame["time"], errors="raise")
    if times.isna().any() or (times % 1 != 0).any() or (times % step_ms != 0).any():
        raise ValueError(f"Misaligned frozen bar timestamps: {path}")
    frame["time"] = times.astype("int64")
    frame = frame.set_index("time").loc[:, OHLC]
    if frame.empty or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Frozen bars must be nonempty, unique, and ordered: {path}")
    if frame.isna().any().any() or not all(math.isfinite(float(v)) for v in frame.to_numpy().ravel()):
        raise ValueError(f"Missing or non-finite OHLC: {path}")
    if (frame[OHLC] <= 0).any().any():
        raise ValueError(f"Non-positive OHLC: {path}")
    if (frame.low > frame[["open", "close"]].min(axis=1)).any() or (
            frame.high < frame[["open", "close"]].max(axis=1)).any():
        raise ValueError(f"Invalid OHLC range: {path}")
    return frame


def _maybe_int(value: Any) -> int | None:
    if pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"Expected an integer millisecond value, got {value!r}")
    return int(number)


def _maybe_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Expected a finite numeric value, got {value!r}")
    return number


def _side(value: Any) -> int:
    side = _maybe_int(value)
    if side not in (-1, 1):
        raise ValueError(f"Side must be -1 or 1, got {value!r}")
    return side


def _timestamp_ms(value: Any) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError(f"Timezone required for frozen signal timestamp: {value!r}")
    return int(stamp.tz_convert("UTC").value // 1_000_000)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [dict(time_ms=int(t), **{name: float(row[name]) for name in OHLC})
            for t, row in frame.iterrows()]


def _cross_index(bars12h: pd.DataFrame) -> list[dict[str, Any]]:
    """For each 12H start, solve the EMA cross from the prior closed EMA state."""
    times = bars12h.index.to_series()
    groups = times.diff().ne(STEP_12H_MS).cumsum()
    closes = bars12h.close
    ema20 = closes.groupby(groups).transform(lambda s: s.ewm(span=20, adjust=False).mean())
    ema50 = closes.groupby(groups).transform(lambda s: s.ewm(span=50, adjust=False).mean())
    count = closes.groupby(groups).cumcount() + 1

    a20, a50 = 2.0 / 21.0, 2.0 / 51.0
    rows = []
    for i, start_ms in enumerate(bars12h.index):
        cross = prev_spread = None
        if i and start_ms == int(bars12h.index[i - 1]) + STEP_12H_MS and count.iloc[i - 1] >= 50:
            fast, slow = float(ema20.iloc[i - 1]), float(ema50.iloc[i - 1])
            cross = ((1.0 - a50) * slow - (1.0 - a20) * fast) / (a20 - a50)
            prev_spread = fast - slow
            if not (math.isfinite(cross) and math.isfinite(prev_spread)):
                raise ValueError(f"Non-finite causal EMA cross at {start_ms}")
        rows.append({"start_ms": int(start_ms), "cross": cross, "prev_spread": prev_spread})
    return rows


def load_inputs(root: Path | str = ROOT) -> InputBundle:
    """Load the two cohorts and reconcile them against their frozen sources.

    An early-only week has no original confirmed frame, so ``peak_lower_r`` is
    ``None``. When a confirmed signal exists in the same week, its original
    value is carried on both records; ``matched`` denotes an exact key+side
    match for downstream use.
    """
    root = Path(root).expanduser().resolve()
    early_dir = root / "experiments/active" / EARLY_EXP
    source_dir = root / "experiments/active" / SOURCE_EXP
    with (early_dir / "summary.json").open(encoding="utf-8") as f:
        summary = json.load(f)
    weeks = pd.read_csv(early_dir / "weeks.csv", float_precision="round_trip")
    signals = pd.read_csv(source_dir / "signals.csv", float_precision="round_trip")
    signals = signals.loc[signals.symbol == "ETH"]
    bars1h = _bars(source_dir / "inputs/ETH/bars1h.csv.gz", HOUR_MS)
    bars12h = _bars(source_dir / "inputs/ETH/bars12h.csv.gz", STEP_12H_MS)
    if weeks.week.duplicated().any() or signals.week.duplicated().any():
        raise ValueError("Expected at most one early and one original signal per week")

    signal_by_week = {}
    for row in signals.to_dict("records"):
        week = row["week"]
        side = _side(row["side"])
        key_ms, confirmed_ms = _timestamp_ms(row["key_open_bjt"]), _timestamp_ms(row["confirmed_bjt"])
        entry, stop = float(row["reference_entry"]), float(row["reference_stop"])
        stop_pct = float(row["stop_pct"])
        if (key_ms not in bars12h.index or confirmed_ms != key_ms + STEP_12H_MS
                or not math.isclose(stop_pct, STOP_PCT, abs_tol=1e-12, rel_tol=0.0)
                or not math.isclose(stop, entry * (1.0 - side * STOP_PCT), rel_tol=1e-10, abs_tol=1e-8)):
            raise ValueError(f"Original ETH signal failed key/1.5% stop checks: {week}")
        signal_by_week[week] = {
            "side": side, "key_ms": key_ms, "confirmed_ms": confirmed_ms,
            "stop": stop, "peak_lower_r": float(row["peak_lower_r"]),
        }
    if len(signals) != int(summary["confirmed"]):
        raise ValueError("Original ETH signal count disagrees with study summary")

    opportunities: list[dict[str, Any]] = []
    matched_count = early_count = confirmed_count = unmatched_early_count = 0

    def add(week: str, cohort: str, side: int, key_ms: int, entry_ms: int,
            entry_price: float, stop: float, fail_at_ms: int | None,
            matched: bool, peak_lower_r: float | None) -> None:
        if key_ms not in bars12h.index or entry_ms not in bars1h.index:
            raise ValueError(f"No frozen OHLC for {cohort} opportunity in {week}")
        if not math.isclose(entry_price, float(bars1h.at[entry_ms, "open"]), rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f"Next-H1 open disagrees with frozen bar for {cohort} in {week}")
        if fail_at_ms is not None and (fail_at_ms != key_ms + STEP_12H_MS or fail_at_ms not in bars1h.index):
            raise ValueError(f"Invalid early failure boundary in {week}")
        opportunities.append({
            "opportunity_id": f"ETH:{week}:{cohort}:{key_ms}:{entry_ms}",
            "week": week, "cohort": cohort, "side": side, "entry_ms": entry_ms,
            "entry_price": entry_price, "stop": stop, "fail_at_ms": fail_at_ms,
            "key_ms": key_ms, "matched": matched, "peak_lower_r": peak_lower_r,
        })

    for row in weeks.to_dict("records"):
        week = row["week"]
        key_ms, confirmed_ms = _maybe_int(row["key_ms"]), _maybe_int(row["confirmed_ms"])
        side = None if pd.isna(row["side"]) else _side(row["side"])
        confirmed_open = _maybe_float(row["confirmed_next_open"])
        early_key_ms, early_at_ms = _maybe_int(row["early_key_ms"]), _maybe_int(row["early_at_ms"])
        early_side = None if pd.isna(row["early_side"]) else _side(row["early_side"])
        early_open = _maybe_float(row["early_next_open"])
        early_stop = _maybe_float(row["reference_stop"])
        matched = bool(row["matched"])
        source = signal_by_week.get(week)

        has_confirmed = confirmed_ms is not None
        if has_confirmed:
            if key_ms is None or side is None or confirmed_open is None or source is None:
                raise ValueError(f"Incomplete confirmed row in {week}")
            if (source["key_ms"], source["confirmed_ms"], source["side"]) != (key_ms, confirmed_ms, side):
                raise ValueError(f"Confirmed row disagrees with original ETH signal in {week}")
        elif any(value is not None for value in (key_ms, side, confirmed_open)):
            raise ValueError(f"Partial confirmed row in {week}")

        has_early = early_at_ms is not None
        if has_early:
            if early_key_ms is None or early_side is None or early_open is None or early_stop is None:
                raise ValueError(f"Incomplete early row in {week}")
            if not early_key_ms < early_at_ms <= early_key_ms + 11 * HOUR_MS:
                raise ValueError(f"Early decision is outside the first 11 completed H1 bars in {week}")
        elif any(value is not None for value in (early_key_ms, early_side, early_open, early_stop)):
            raise ValueError(f"Partial early row in {week}")

        exact_match = bool(has_early and has_confirmed and early_key_ms == key_ms and early_side == side)
        if matched != exact_match:
            raise ValueError(f"Matched flag disagrees with exact key/side in {week}")
        if exact_match and not math.isclose(early_stop, source["stop"], rel_tol=1e-10, abs_tol=1e-8):
            raise ValueError(f"Matched early stop disagrees with original reference rail in {week}")

        if has_confirmed:
            assert key_ms is not None and confirmed_ms is not None and side is not None
            assert confirmed_open is not None and source is not None
            if confirmed_ms != key_ms + STEP_12H_MS:
                raise ValueError(f"Confirmed entry is not the next 12H boundary in {week}")
            add(week, "confirmed", side, key_ms, confirmed_ms, confirmed_open,
                source["stop"], None, matched, source["peak_lower_r"])
            confirmed_count += 1
        if has_early:
            assert early_key_ms is not None and early_at_ms is not None and early_side is not None
            assert early_open is not None and early_stop is not None
            fail_at_ms = None if exact_match else early_key_ms + STEP_12H_MS
            add(week, "early", early_side, early_key_ms, early_at_ms, early_open,
                early_stop, fail_at_ms, matched,
                None if source is None else source["peak_lower_r"])
            early_count += 1
            unmatched_early_count += int(not exact_match)
        matched_count += int(exact_match)

    for key, observed in (("confirmed", confirmed_count), ("early", early_count),
                          ("matched", matched_count), ("unmatched_early", unmatched_early_count)):
        if observed != int(summary[key]):
            raise ValueError(f"{key} count {observed} disagrees with summary ({summary[key]})")
    if confirmed_count != len(signal_by_week) or len(opportunities) != early_count + confirmed_count:
        raise ValueError("Opportunity ledger is incomplete or duplicated")
    ids = [row["opportunity_id"] for row in opportunities]
    if len(ids) != len(set(ids)):
        raise ValueError("Opportunity ids are not unique")

    return InputBundle(opportunities, _records(bars1h), _records(bars12h), _cross_index(bars12h))

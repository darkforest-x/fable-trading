"""Freeze explanatory H1 EMA-cloud chart inputs from completed study ledgers.

This module only prepares chart data.  It loads each symbol once, computes the
EMA20/50 over the full continuous 5m source history before cutting display
windows, and never recalculates trade outcomes or creates synthetic bars.
"""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from yoyo.evaluation.h1_ema_cloud_study import cloud_features
from yoyo.evaluation.h1_outside_glyph_signals import _aggregate_h1
from yoyo.evaluation.monday_15m_volume_rejection import load


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-h1-ema-cloud-20261102-v1"
BASE = ROOT / "experiments/active/exp-h1-outside-glyph-stops-20261102-v1"
CHARTS = EXP / "charts_white_v1"
INPUTS = CHARTS / "inputs"
FIVE_MINUTES = pd.Timedelta(minutes=5)
ONE_HOUR = pd.Timedelta(hours=1)
NEW_YORK = "America/New_York"

_DEFAULT_ROLES = {
    1: "fixed_lpt_screenshot",
    2: "baseline_win_cloud_loss",
    3: "baseline_loss_cloud_win",
    4: "both_loss_btc_long",
    5: "target_before_flip_cancel",
    6: "invalid_geometry_cancel",
}


def _file_stamp(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _identity_inputs(identity_path: Path) -> list[dict[str, object]]:
    identity = json.loads(identity_path.read_text())
    rows = identity.get("inputs")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"No source inputs registered in {identity_path}")
    return rows


def _verify_source_receipts(symbol: str) -> tuple[list[str], dict[str, object]]:
    old_dir = BASE / "symbols" / symbol
    new_dir = EXP / "symbols" / symbol
    old_identity_path = old_dir / "identity.json"
    new_identity_path = new_dir / "identity.json"
    old_inputs = _identity_inputs(old_identity_path)
    new_inputs = _identity_inputs(new_identity_path)
    if old_inputs != new_inputs:
        raise RuntimeError(f"Frozen source receipt differs between old and cloud identities: {symbol}")

    current = []
    for receipt in old_inputs:
        path = Path(receipt["path"])
        actual = _file_stamp(path)
        if actual != receipt:
            raise RuntimeError(f"Source stat changed since baseline study for {symbol}: {path}")
        current.append(actual)

    return [str(row["path"]) for row in old_inputs], {
        "old_identity": _file_stamp(old_identity_path),
        "cloud_identity": _file_stamp(new_identity_path),
        "registered_inputs": old_inputs,
        "current_inputs": current,
        "matches_old_identity": current == old_inputs,
        "matches_cloud_identity": current == new_inputs,
    }


def _json_value(value: object) -> object:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _row_dict(row: pd.Series | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {str(key): _json_value(value) for key, value in row.items()}


def _single_row(frame: pd.DataFrame, *, pair_id: str, stop_id: str,
                population: str, label: str) -> pd.Series | None:
    selected = frame[
        frame.pair_id.eq(pair_id)
        & frame.stop_id.eq(stop_id)
        & frame.population.eq(population)
    ]
    if len(selected) > 1:
        raise ValueError(f"Expected at most one {label} row for {pair_id}; got {len(selected)}")
    return None if selected.empty else selected.iloc[0]


def _gap_edges(index: pd.DatetimeIndex, step: pd.Timedelta) -> list[dict[str, object]]:
    if len(index) < 2:
        return []
    ns = index.as_unit("ns").asi8
    gaps = np.flatnonzero(np.diff(ns) != step.value)
    result = []
    for left in gaps:
        elapsed = int(ns[left + 1] - ns[left])
        result.append({
            "previous_bar_open": index[left].isoformat(),
            "next_bar_open": index[left + 1].isoformat(),
            "missing_intervals": max(0, elapsed // step.value - 1),
            "elapsed": str(pd.Timedelta(elapsed, unit="ns")),
        })
    return result


def _marker(row: pd.Series | None, bars: pd.DataFrame) -> dict[str, object] | None:
    if row is None:
        return None
    entry_i = int(row.entry_i)
    exit_i = int(row.exit_i)
    if not (0 <= entry_i < len(bars) and 0 <= exit_i < len(bars)):
        raise IndexError(f"Ledger marker index out of range for {row.pair_id}")
    entry_bar = bars.index[entry_i]
    exit_bar = bars.index[exit_i]
    return {
        "entry_bar_open": entry_bar.isoformat(),
        "entry_price_raw": float(row.entry),
        "exit_bar_open": exit_bar.isoformat(),
        "exit_bar_close_time": (exit_bar + FIVE_MINUTES).isoformat(),
        "exit_ledger_time": str(row.exit_time),
        "exit_price_raw": float(row.raw_exit_price),
        "exit_time_semantics": "exit_i identifies this 5m bar; exit_time is the bar close, not a second-accurate fill timestamp",
    }


def _monday_context(signal_time: pd.Timestamp, old: pd.Series) -> dict[str, object]:
    local = signal_time.tz_convert(NEW_YORK)
    monday = local.date() - timedelta(days=local.dayofweek)
    monday_start = pd.Timestamp(monday).tz_localize(NEW_YORK).tz_convert("UTC")
    monday_end = pd.Timestamp(monday + timedelta(days=1)).tz_localize(NEW_YORK).tz_convert("UTC")
    next_monday = pd.Timestamp(monday + timedelta(days=7)).tz_localize(NEW_YORK).tz_convert("UTC")
    monday_high = float(old.monday_high)
    monday_low = float(old.monday_low)
    width = monday_high - monday_low
    upper = monday_low + 1.618 * width
    lower = monday_high - 1.618 * width
    side = int(old.side)
    return {
        "monday_date_new_york": monday.isoformat(),
        "monday_start_utc": monday_start.isoformat(),
        "levels_available_from_utc": monday_end.isoformat(),
        "next_monday_start_utc": next_monday.isoformat(),
        "monday_high": monday_high,
        "monday_low": monday_low,
        "range": width,
        "level1618": {
            "upper": upper,
            "lower": lower,
            "active_outside_level": lower if side == 1 else upper,
            "target_edge": monday_low if side == 1 else monday_high,
            "formula": "upper = monday_low + 1.618 * (monday_high - monday_low); lower = monday_high - 1.618 * (monday_high - monday_low)",
        },
    }


def _first_target_touch(bars: pd.DataFrame, start_i: int, side: int,
                        target: float, armed_at: pd.Timestamp,
                        wait_hours: int) -> dict[str, object] | None:
    """Locate the first target-touch bar descriptively, without simulating a fill."""
    if start_i < 0 or start_i >= len(bars):
        return None
    deadline = armed_at + pd.Timedelta(hours=wait_hours)
    deadline_last = int(bars.index.searchsorted(deadline, side="left")) - 1
    ns = bars.index.as_unit("ns").asi8
    gaps = np.flatnonzero(np.diff(ns) != FIVE_MINUTES.value)
    after_start = gaps[gaps >= start_i]
    boundary = int(after_start[0]) if len(after_start) else len(bars) - 1
    last = min(len(bars) - 1, deadline_last, boundary)
    if last < start_i:
        return None
    section = bars.iloc[start_i:last + 1]
    touched = section.high.ge(target) if side == 1 else section.low.le(target)
    if not touched.any():
        return None
    touch_i = start_i + int(np.flatnonzero(touched.to_numpy())[0])
    bar_open = bars.index[touch_i]
    return {
        "kind": "first_target_touch_before_cloud_entry",
        "bar_open": bar_open.isoformat(),
        "bar_close": (bar_open + FIVE_MINUTES).isoformat(),
        "target_price": float(target),
        "bar_high": float(bars.high.iloc[touch_i]),
        "bar_low": float(bars.low.iloc[touch_i]),
        "descriptive_only_not_a_recomputed_fill": True,
    }


def _case_windows(signal_time: pd.Timestamp, old_marker: dict[str, object],
                  new_marker: dict[str, object] | None, status: str,
                  candidate_time: pd.Timestamp | None,
                  cancel_reference: dict[str, object] | None) -> dict[str, object]:
    signal_start = signal_time
    h1_events = [pd.Timestamp(old_marker["exit_bar_open"])]
    if new_marker is not None:
        h1_events.append(pd.Timestamp(new_marker["exit_bar_open"]))
    if status == "invalid_geometry" and candidate_time is not None:
        h1_events.append(candidate_time)
    h1_end_event = max(h1_events)
    h1_start = signal_start - pd.Timedelta(hours=72)
    h1_end = h1_end_event + pd.Timedelta(hours=12)

    if new_marker is not None:
        detail_event = pd.Timestamp(new_marker["entry_bar_open"])
        detail_source = "cloud_entry"
    elif status == "invalid_geometry" and candidate_time is not None:
        detail_event = candidate_time
        detail_source = "invalid_geometry_candidate"
    elif status == "target_before_flip" and cancel_reference is not None:
        detail_event = pd.Timestamp(cancel_reference["bar_open"])
        detail_source = "first_target_touch"
    else:
        detail_event = signal_start
        detail_source = "signal_time_fallback"
    m5_start = signal_start - pd.Timedelta(hours=6)
    m5_end = detail_event + pd.Timedelta(hours=6)
    return {
        "h1": {"requested_start": h1_start.isoformat(), "requested_end": h1_end.isoformat(),
               "end_anchor": h1_end_event.isoformat(), "before_hours": 72, "after_hours": 12},
        "m5": {"requested_start": m5_start.isoformat(), "requested_end": m5_end.isoformat(),
               "end_anchor": detail_event.isoformat(), "end_anchor_kind": detail_source,
               "before_hours": 6, "after_hours": 6},
    }


def _price_inside_bar(bars: pd.DataFrame, row: pd.Series | None,
                      index_name: str = "exit_i") -> dict[str, object] | None:
    if row is None:
        return None
    index = int(row[index_name])
    if not 0 <= index < len(bars):
        return {"valid_index": False, "bar_index": index}
    price = float(row.raw_exit_price)
    low = float(bars.low.iloc[index])
    high = float(bars.high.iloc[index])
    tolerance = max(1e-8, abs(price) * 1e-10)
    return {
        "valid_index": True,
        "bar_open": bars.index[index].isoformat(),
        "bar_low": low,
        "bar_high": high,
        "raw_exit_price": price,
        "tolerance": tolerance,
        "inside_ohlc_with_tolerance": low - tolerance <= price <= high + tolerance,
    }


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=True, index_label="timestamp", date_format="%Y-%m-%dT%H:%M:%S%z")


def build_case(case: dict[str, object], *, bars: pd.DataFrame,
               cloud: pd.DataFrame, h1: pd.DataFrame,
               source_receipt: dict[str, object], old_ledger_path: Path,
               new_ledger_path: Path, schedule_path: Path,
               old_ledger: pd.DataFrame, new_ledger: pd.DataFrame,
               schedules: pd.DataFrame, new_config: dict[str, object]) -> dict[str, object]:
    number = int(case["number"])
    pair_id = str(case["pair_id"])
    symbol = pair_id.split(":", 1)[0]
    stop_id = "fixed20"
    old = _single_row(old_ledger, pair_id=pair_id, stop_id=stop_id,
                      population="signal", label="old signal")
    if old is None:
        raise ValueError(f"Missing old fixed20 signal row: {pair_id}")
    new = _single_row(new_ledger, pair_id=pair_id, stop_id=stop_id,
                      population="signal", label="cloud signal")
    schedule_row = schedules[schedules.pair_id.eq(pair_id)]
    if len(schedule_row) != 1:
        raise ValueError(f"Expected one schedule row for {pair_id}; got {len(schedule_row)}")
    schedule_series = schedule_row.iloc[0]
    status = str(schedule_series.status)
    if status == "entered" and new is None:
        raise ValueError(f"Schedule entered but cloud fixed20 ledger row is missing: {pair_id}")
    if status != "entered" and new is not None:
        raise ValueError(f"Cloud trade row exists for a non-entered schedule: {pair_id} ({status})")

    signal_time = pd.Timestamp(old.signal_time)
    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")
    else:
        signal_time = signal_time.tz_convert("UTC")
    signal_candle_time = signal_time - ONE_HOUR
    monday = _monday_context(signal_time, old)

    old_marker = _marker(old, bars)
    new_marker = _marker(new, bars)
    candidate_time = None
    candidate = None
    if status == "invalid_geometry":
        candidate_i = int(schedule_series.entry_i)
        if 0 <= candidate_i < len(bars):
            candidate_time = bars.index[candidate_i]
            candidate_price = float(bars.open.iloc[candidate_i])
            side = int(old.side)
            extreme = float(old.signal_low if side == 1 else old.signal_high)
            candidate_stop = extreme - side * candidate_price * float(new_config["wick_buffer"])
            candidate = {
                "kind": "invalid_geometry_candidate_not_filled",
                "entry_bar_open": candidate_time.isoformat(),
                "entry_price_open": candidate_price,
                "wait_minutes": _json_value(schedule_series.wait_minutes),
                "confirm_bar_open": bars.index[int(schedule_series.confirm_i)].isoformat(),
                "confirm_close_time": (bars.index[int(schedule_series.confirm_i)] + FIVE_MINUTES).isoformat(),
                "wick_stop_candidate": candidate_stop,
                "target_price": float(old.target_price),
                "target_geometry_valid": side * (float(old.target_price) - candidate_price) > 0,
                "wick_stop_geometry_valid": side * (candidate_price - candidate_stop) > 0,
                "not_a_filled_entry": True,
            }

    cancel_reference = None
    if status == "target_before_flip":
        cancel_reference = _first_target_touch(
            bars, int(old.entry_i), int(old.side), float(old.target_price),
            signal_time, int(new_config["wait_hours"]),
        )

    windows = _case_windows(signal_time, old_marker, new_marker, status,
                            candidate_time, cancel_reference)
    h1_start = pd.Timestamp(windows["h1"]["requested_start"])
    h1_end = pd.Timestamp(windows["h1"]["requested_end"])
    m5_start = pd.Timestamp(windows["m5"]["requested_start"])
    m5_end = pd.Timestamp(windows["m5"]["requested_end"])
    h1_slice = h1.loc[(h1.index >= h1_start) & (h1.index <= h1_end)].copy()
    m5 = bars.join(cloud.loc[:, ["ema_fast", "ema_slow", "flip_long", "flip_short"]])
    m5["close_at"] = m5.index + FIVE_MINUTES
    m5_slice = m5.loc[(m5.index >= m5_start) & (m5.index <= m5_end),
                      ["open", "high", "low", "close", "volume", "ema_fast", "ema_slow",
                       "flip_long", "flip_short", "close_at"]].copy()

    # Show the event week's 1.618 range only after its New York Monday has closed.
    level_visible = (h1_slice.index >= pd.Timestamp(monday["levels_available_from_utc"]))
    level_visible &= h1_slice.index < pd.Timestamp(monday["next_monday_start_utc"])
    h1_slice["case_signal_candle"] = h1_slice.index == signal_candle_time
    for column in ("monday_high", "monday_low", "upper_1618", "lower_1618"):
        h1_slice[column] = np.nan
    h1_slice.loc[level_visible, "monday_high"] = monday["monday_high"]
    h1_slice.loc[level_visible, "monday_low"] = monday["monday_low"]
    h1_slice.loc[level_visible, "upper_1618"] = monday["level1618"]["upper"]
    h1_slice.loc[level_visible, "lower_1618"] = monday["level1618"]["lower"]

    input_prefix = f"{number:02d}"
    h1_path = INPUTS / f"{input_prefix}_h1.csv"
    m5_path = INPUTS / f"{input_prefix}_5m.csv"
    _write_csv(h1_slice, h1_path)
    _write_csv(m5_slice, m5_path)

    h1_signal = h1.loc[signal_candle_time] if signal_candle_time in h1.index else None
    validation: dict[str, object] = {"issues": []}
    issues = validation["issues"]
    validation["source_stat_matches"] = {
        "old_identity": bool(source_receipt["matches_old_identity"]),
        "cloud_identity": bool(source_receipt["matches_cloud_identity"]),
    }
    validation["h1_signal_bar_found"] = h1_signal is not None
    if h1_signal is None:
        issues.append("signal candle is absent from complete H1 aggregation")
    else:
        checks = {
            "high_matches_old_signal": bool(np.isclose(float(h1_signal.high), float(old.signal_high), rtol=0, atol=1e-10)),
            "low_matches_old_signal": bool(np.isclose(float(h1_signal.low), float(old.signal_low), rtol=0, atol=1e-10)),
            "close_matches_old_signal": bool(np.isclose(float(h1_signal.close), float(old.signal_close), rtol=0, atol=1e-10)),
        }
        validation["h1_signal_ohlc_matches_old_ledger"] = checks
        # The old ledger persists signal high/low/close, but no signal open.
        validation["signal_open_comparison_available"] = False
        validation["signal_open_comparison_note"] = "old fixed20 ledger has no signal_open field"
        validation["signal_bar_open_source"] = float(h1_signal.open)
        for label, passed in checks.items():
            if not passed:
                issues.append(f"H1 signal OHLC mismatch: {label}")

    def entry_open_check(row: pd.Series | None) -> dict[str, object] | None:
        if row is None:
            return None
        index = int(row.entry_i)
        actual = float(bars.open.iloc[index])
        expected = float(row.entry)
        passed = np.isclose(actual, expected, rtol=0, atol=max(1e-10, abs(expected) * 1e-12))
        if not passed:
            issues.append(f"{row.pair_id}: ledger raw entry differs from source open")
        return {"bar_open": bars.index[index].isoformat(), "source_open": actual,
                "ledger_entry": expected, "matches": bool(passed)}

    validation["old_entry_matches_source_open"] = entry_open_check(old)
    validation["new_entry_matches_source_open"] = entry_open_check(new)
    target_expected = float(old.monday_low if int(old.side) == 1 else old.monday_high)
    old_target_ok = np.isclose(float(old.target_price), target_expected, rtol=0, atol=1e-10)
    validation["old_target_matches_original_monday_edge"] = bool(old_target_ok)
    if not old_target_ok:
        issues.append("old target differs from original Monday edge")
    if new is not None:
        target_same = np.isclose(float(new.target_price), float(old.target_price), rtol=0, atol=1e-10)
        validation["new_target_matches_old_target"] = bool(target_same)
        if not target_same:
            issues.append("new target differs from old pair target")
    else:
        validation["new_target_matches_old_target"] = None
    validation["old_raw_exit_in_exit_bar_ohlc"] = _price_inside_bar(bars, old)
    validation["new_raw_exit_in_exit_bar_ohlc"] = _price_inside_bar(bars, new)
    for label in ("old_raw_exit_in_exit_bar_ohlc", "new_raw_exit_in_exit_bar_ohlc"):
        detail = validation[label]
        if detail is not None and not detail.get("inside_ohlc_with_tolerance", False):
            issues.append(f"{label} failed; raw price retained unchanged")
    if cancel_reference is None and status == "target_before_flip":
        issues.append("schedule says target-before-flip but no descriptive first-touch bar was found")

    actual_h1_window = {
        "first_bar_open": h1_slice.index[0].isoformat() if len(h1_slice) else None,
        "last_bar_open": h1_slice.index[-1].isoformat() if len(h1_slice) else None,
        "bars": len(h1_slice),
        "gaps": _gap_edges(h1_slice.index, ONE_HOUR),
        "synthetic_rows_added": 0,
    }
    actual_m5_window = {
        "first_bar_open": m5_slice.index[0].isoformat() if len(m5_slice) else None,
        "last_bar_open": m5_slice.index[-1].isoformat() if len(m5_slice) else None,
        "bars": len(m5_slice),
        "gaps": _gap_edges(m5_slice.index, FIVE_MINUTES),
        "synthetic_rows_added": 0,
    }

    role = str(case.get("role", _DEFAULT_ROLES.get(number, "illustrative_case")))
    provenance = {
        "source_inputs": source_receipt,
        "old_ledger": _file_stamp(old_ledger_path),
        "cloud_ledger": _file_stamp(new_ledger_path),
        "schedule": _file_stamp(schedule_path),
        "full_history_ema_before_window_slice": True,
        "source_hashes": "not computed per owner instruction",
    }
    return {
        "number": number,
        "role": role,
        "title": str(case["title"]),
        "caption": str(case["caption"]),
        "symbol": symbol,
        "side": int(old.side),
        "pair_id": pair_id,
        "stop_id": stop_id,
        "status": status,
        "signal_time": signal_time.isoformat(),
        "signal_candle_time": signal_candle_time.isoformat(),
        "level1618": monday,
        "old": _row_dict(old),
        "new": _row_dict(new),
        "schedule": _row_dict(schedule_series),
        "old_markers": old_marker,
        "new_markers": new_marker,
        "cancellation_reference": candidate if candidate is not None else cancel_reference,
        "windows": windows,
        "actual_windows": {"h1": actual_h1_window, "m5": actual_m5_window},
        "inputs": {"h1_csv": str(h1_path.relative_to(ROOT)),
                   "m5_csv": str(m5_path.relative_to(ROOT))},
        "provenance": provenance,
        "validation": validation,
    }


def main() -> None:
    """Build six frozen candle windows and a single source-backed manifest."""
    config_path = CHARTS / "config.json"
    chart_config = json.loads(config_path.read_text())
    new_config = json.loads((EXP / "config.json").read_text())
    cases = chart_config["cases"]
    symbols = sorted({str(case["pair_id"]).split(":", 1)[0] for case in cases})

    source_receipts: dict[str, dict[str, object]] = {}
    paths_by_symbol: dict[str, list[str]] = {}
    old_rows: dict[str, pd.DataFrame] = {}
    new_rows: dict[str, pd.DataFrame] = {}
    schedules: dict[str, pd.DataFrame] = {}
    frames: dict[str, pd.DataFrame] = {}
    clouds: dict[str, pd.DataFrame] = {}
    h1_frames: dict[str, pd.DataFrame] = {}
    result_cases = []

    for symbol in symbols:
        paths, source_receipts[symbol] = _verify_source_receipts(symbol)
        paths_by_symbol[symbol] = paths
        old_dir = BASE / "symbols" / symbol
        new_dir = EXP / "symbols" / symbol
        old_rows[symbol] = pd.read_csv(old_dir / "trades.csv.gz")
        new_rows[symbol] = pd.read_csv(new_dir / "trades.csv.gz")
        schedules[symbol] = pd.read_csv(new_dir / "schedule.csv.gz")
        # Load each symbol once. EMA features are computed on this complete
        # history before any per-case chart window is selected.
        frames[symbol] = load(paths)
        clouds[symbol] = cloud_features(
            frames[symbol], fast=int(new_config["ema_fast"]), slow=int(new_config["ema_slow"])
        )
        h1_frames[symbol] = _aggregate_h1(frames[symbol])

    INPUTS.mkdir(parents=True, exist_ok=True)
    for case in cases:
        symbol = str(case["pair_id"]).split(":", 1)[0]
        result_cases.append(build_case(
            case,
            bars=frames[symbol],
            cloud=clouds[symbol],
            h1=h1_frames[symbol],
            source_receipt=source_receipts[symbol],
            old_ledger_path=BASE / "symbols" / symbol / "trades.csv.gz",
            new_ledger_path=EXP / "symbols" / symbol / "trades.csv.gz",
            schedule_path=EXP / "symbols" / symbol / "schedule.csv.gz",
            old_ledger=old_rows[symbol],
            new_ledger=new_rows[symbol],
            schedules=schedules[symbol],
            new_config=new_config,
        ))

    manifest = {
        "source_experiment": chart_config.get("source_experiment"),
        "baseline_experiment": chart_config.get("baseline_experiment"),
        "stop_id": chart_config.get("stop_id"),
        "timezone": chart_config.get("timezone"),
        "background": chart_config.get("background"),
        "sample_selection": chart_config.get("sample_selection"),
        "panels": chart_config.get("panels"),
        "data_policy": chart_config.get("data_policy"),
        "cases": result_cases,
    }
    manifest_path = CHARTS / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n")
    issues = [
        f"{item['pair_id']}: {issue}"
        for item in result_cases
        for issue in item["validation"]["issues"]
    ]
    print(f"Wrote {len(result_cases)} chart input cases to {CHARTS}")
    if issues:
        print("Validation issues (values were not changed):", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)


if __name__ == "__main__":
    main()

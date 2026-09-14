"""Paired native SPIKE V1 0.5R MFE-to-entry protection replay.

This is a retrospective, non-blind holdout use (#1 for this configuration),
not an execution model.  It accepts only the 6,185 frozen V1 events linked to
their original OHLC sources.  Every source byte hash, original V1 code hash,
and immutable baseline-ledger hash must match before the paired replay starts.

The baseline preserves V1's contract exactly: long-only signal-close initial
protection, an actual following-open entry, stop-first intrabar handling, and
0.2% round-trip cost.  The paired arm observes MFE causally.  Once a completed
bar first reaches 0.5 actual entry R, it raises next-bar protection to entry;
it never lowers a tighter existing protection.  No MFE or exit outcome is an
input to signal selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256, features, path_reference, replay
from yoyo.evaluation.spike_v1_twoyear_allmarkets import (
    END,
    PINE_SHA,
    START,
    _entry_risk_fraction,
    _read_utc_source,
    aggregate,
    digest,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXP = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1"
SOURCE_RESULTS = SOURCE_EXP / "results"
IMMUTABLE_LEDGER = SOURCE_RESULTS / "immutable_replay_ledger" / (
    "covered_trade_ledger.b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578.csv.gz"
)
LINK_LEDGER = SOURCE_RESULTS / "replay_ledger_link_receipt.csv.gz"
EXPECTED_LEDGER_SHA256 = "b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578"
EXPECTED_REPLAY_SHA256 = "18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2"
DEFAULT_OUTPUT = ROOT / "experiments/active/exp-spike-v1-v8-be05-20260914-v1/native_v1"
CACHE_ROOT = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3/streams"
ROUND_TRIP_COST = 0.002
BE_TRIGGER_R = 0.5
METHOD_VERSION = "native-v1-causal-mfe-0.5r-next-bar-be-v1"
MIDPOINT = pd.Timestamp("2025-09-10T00:00:00Z")
NUMERIC_PARITY_FIELDS = (
    "entry_price", "exit_price", "gross_return", "net_return", "net_r",
    "risk_fraction_at_entry", "mfe_return",
)
IDENTITY_PARITY_FIELDS = ("entry_time", "exit_time", "exit_reason", "censored")


def sha256(path: Path) -> str:
    """Return the byte digest used by the frozen source receipts."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Write an owned result atomically enough to preserve failure evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _utc(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") if not isinstance(value, pd.Timestamp) else value.tz_convert("UTC") if value.tzinfo else value.tz_localize("UTC")


def _native_exit(
    bars: pd.DataFrame,
    replayed: pd.DataFrame,
    entry_position: int,
    initial_stop: float,
    *,
    use_be: bool,
) -> dict[str, Any]:
    """Replay one already-selected V1 long event with an optional causal BE arm.

    ``bars`` contributes OHLC only after the V1 signal; ``replayed`` contributes
    only V1's already-available close protections.  Each bar first checks the
    currently active stop.  A high that reaches 0.5 actual entry R is known only
    at that bar's close, so the entry-level stop can affect the following bar.
    The initial-R denominator is ``entry - initial_stop`` at actual entry.
    """
    entry = float(bars.open.iloc[entry_position])
    step = bars.index[1] - bars.index[0]
    protection = float(initial_stop)
    exit_position: int | None = None
    exit_price = np.nan
    reason = "censored"
    max_high = entry
    moved_to_entry = False
    move_bar_open: pd.Timestamp | None = None
    if entry <= protection:
        exit_position, exit_price, reason = entry_position, entry, "entry_gap_through_stop"
    else:
        actual_r = entry - float(initial_stop)
        for position in range(entry_position, len(bars)):
            bar = bars.iloc[position]
            # The presently active stop, including a prior bar's BE update,
            # has priority over the current bar's high and close-derived state.
            if float(bar.low) <= protection:
                exit_position = position
                exit_price = min(float(bar.open), protection)
                reason = "protective_stop"
                break
            max_high = max(max_high, float(bar.high))
            if bool(replayed.trend_side.iloc[position]) and np.isfinite(float(replayed.protection.iloc[position])):
                protection = float(replayed.protection.iloc[position])
            if use_be and not moved_to_entry and (max_high - entry) >= BE_TRIGGER_R * actual_r:
                moved_to_entry = True
                move_bar_open = bars.index[position]
            if use_be and moved_to_entry:
                # Every later native close reference remains subject to the
                # ratchet.  Applying max only on the trigger bar would allow a
                # later lower V1 reference to withdraw price protection.
                protection = max(protection, entry)
    if exit_position is None:
        exit_position, exit_price = len(bars) - 1, float(bars.close.iloc[-1])
        exit_time = min(END, bars.index[-1] + step)
    else:
        exit_time = bars.index[entry_position] if reason == "entry_gap_through_stop" else bars.index[exit_position] + step
    # As in V1, high/low from a touched-stop bar are unknowable after the stop.
    held_end = exit_position if reason == "protective_stop" else exit_position + 1
    held = bars.iloc[entry_position:held_end]
    highs = held.high.to_numpy(float) if len(held) else np.array([entry])
    lows = held.low.to_numpy(float) if len(held) else np.array([entry])
    risk_fraction = _entry_risk_fraction(entry, float(initial_stop))
    gross = float(exit_price / entry - 1)
    net = gross - ROUND_TRIP_COST
    return {
        "entry_time": bars.index[entry_position], "entry_price": entry,
        "exit_time": exit_time, "exit_price": float(exit_price), "exit_reason": reason,
        "gross_return": gross, "net_return": net,
        "risk_fraction_at_entry": risk_fraction,
        "net_r": net / risk_fraction if np.isfinite(risk_fraction) else np.nan,
        "mfe_return": float(highs.max() / entry - 1),
        "mae_return": float(lows.min() / entry - 1),
        "censored": reason == "censored", "be_triggered": moved_to_entry,
        "be_trigger_bar_open": move_bar_open,
        "initial_stop": float(initial_stop), "initial_risk": entry - float(initial_stop),
    }


def _replay_stream(
    bars: pd.DataFrame, tick: float, expected: pd.DataFrame,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Regenerate only expected native V1 events for one receipt-bound stream."""
    if len(bars) < 341:
        return {}, ["stream_has_less_than_341_bars"]
    replayed = replay(features(bars), tick)
    wanted = set(expected.ledger_event_id)
    outcomes: dict[str, dict[str, Any]] = {}
    for signal_time, signal in replayed.loc[replayed.burst].iterrows():
        position = bars.index.get_loc(signal_time)
        if position + 1 >= len(bars):
            continue
        entry_position = position + 1
        entry_time = bars.index[entry_position]
        if not (START <= entry_time < END):
            continue
        event_id = digest(f"{expected.venue.iloc[0]}|{expected.symbol.iloc[0]}|{int(expected.timeframe_min.iloc[0])}|{signal_time.isoformat()}".encode())
        if event_id not in wanted:
            continue
        baseline = _native_exit(bars, replayed, entry_position, float(signal.initial_stop), use_be=False)
        be = _native_exit(bars, replayed, entry_position, float(signal.initial_stop), use_be=True)
        outcomes[event_id] = {"baseline": baseline, "be05": be, "signal_bar_open": signal_time,
                              "signal_close": float(bars.close.iloc[position]), "reference_signal_risk": float(signal.risk)}
    return outcomes, []


def _cache_index() -> dict[tuple[str, str, int, str], list[Path]]:
    """Index receipt-validated featured-bars caches without trusting filenames."""
    if not CACHE_ROOT.is_dir():
        return {}
    index: dict[tuple[str, str, int, str], list[Path]] = {}
    for receipt_path in CACHE_ROOT.glob("*/control_cache.receipt.json"):
        folder = receipt_path.parent
        stream_receipt = folder / "receipt.csv"
        cache_path = folder / "control_cache.pkl.gz"
        if not stream_receipt.is_file() or not cache_path.is_file() or sha256(cache_path) != json.loads(receipt_path.read_text())["cache_sha256"]:
            continue
        row = pd.read_csv(stream_receipt).iloc[0]
        cache_receipt = json.loads(receipt_path.read_text())
        if str(row.source_sha256) != str(cache_receipt.get("source_sha256")) or float(row.tick) != float(cache_receipt.get("tick")):
            continue
        key = (str(row.venue), str(row.symbol), int(row.minutes), str(row.source_sha256))
        index.setdefault(key, []).append(cache_path)
    return index


def _frozen_stop(signal_close: float, reference_risk: float, tick: float) -> float:
    """Recover the original tick-grid stop from CSV signal-close/risk fields.

    The immutable ledger stores decimal values through CSV.  Its subtraction
    can land a few ULPs below the original exchange-tick stop, turning a true
    equality touch into a false non-touch.  This is representation recovery,
    not a new risk rule: V1 originally rounded protection to this same grid.
    """
    return round((signal_close - reference_risk) / tick) * tick


def _native_exit_from_cache(
    bars: pd.DataFrame, signal_time: pd.Timestamp, signal_close: float, signal_risk: float,
    initial_stop: float, tick: float, *, use_be: bool,
) -> dict[str, Any]:
    """Reconstruct native V1 protection from its causal path-reference seam.

    Featured cache bars contain only a 120-bar pre-window prefix and the frozen
    evaluation window.  For each fixed ledger event, V1's path state needs only
    signal-close entry/risk/stop plus later OHLC/ATR and tick.  This avoids
    rescanning V1 admission features while preserving the V1 exit contract.
    """
    position = bars.index.get_loc(signal_time)
    entry_position = position + 1
    if entry_position >= len(bars):
        raise ValueError("event lacks its following-open entry bar in cache")
    entry = float(bars.open.iloc[entry_position])
    step = bars.index[1] - bars.index[0]
    native_protection, native_peak, native_armed = float(initial_stop), 0.0, False
    peak_high, moved_to_entry, move_bar_open = entry, False, None
    exit_position: int | None = None
    exit_price, reason = np.nan, "censored"
    if entry <= native_protection:
        exit_position, exit_price, reason = entry_position, entry, "entry_gap_through_stop"
    else:
        actual_r = entry - float(initial_stop)
        for current in range(entry_position, len(bars)):
            bar = bars.iloc[current]
            active_protection = max(native_protection, entry) if use_be and moved_to_entry else native_protection
            if float(bar.low) <= active_protection:
                exit_position, exit_price, reason = current, min(float(bar.open), active_protection), "protective_stop"
                break
            peak_high = max(peak_high, float(bar.high))
            native_path = path_reference(1, signal_close, signal_risk, native_protection, native_peak, native_armed,
                                         float(bar.open), float(bar.high), float(bar.low), float(bar.close), float(bar.atr), tick=tick)
            if not native_path.alive:
                raise AssertionError("native path disagrees with stop-first active protection")
            native_protection, native_peak, native_armed = native_path.protection, native_path.peak_r, native_path.armed
            if use_be and not moved_to_entry and (peak_high - entry) >= BE_TRIGGER_R * actual_r:
                moved_to_entry, move_bar_open = True, bars.index[current]
    if exit_position is None:
        exit_position, exit_price = len(bars) - 1, float(bars.close.iloc[-1])
        exit_time = min(END, bars.index[-1] + step)
    else:
        exit_time = bars.index[entry_position] if reason == "entry_gap_through_stop" else bars.index[exit_position] + step
    held_end = exit_position if reason == "protective_stop" else exit_position + 1
    held = bars.iloc[entry_position:held_end]
    highs = held.high.to_numpy(float) if len(held) else np.array([entry])
    lows = held.low.to_numpy(float) if len(held) else np.array([entry])
    risk_fraction = _entry_risk_fraction(entry, float(initial_stop))
    gross, net = float(exit_price / entry - 1), float(exit_price / entry - 1 - ROUND_TRIP_COST)
    return {"entry_time": bars.index[entry_position], "entry_price": entry, "exit_time": exit_time,
            "exit_price": float(exit_price), "exit_reason": reason, "gross_return": gross, "net_return": net,
            "risk_fraction_at_entry": risk_fraction, "net_r": net / risk_fraction if np.isfinite(risk_fraction) else np.nan,
            "mfe_return": float(highs.max() / entry - 1), "mae_return": float(lows.min() / entry - 1),
            "censored": reason == "censored", "be_triggered": moved_to_entry,
            "be_trigger_bar_open": move_bar_open, "initial_stop": float(initial_stop), "initial_risk": entry - float(initial_stop)}


def _cached_outcomes(expected: pd.DataFrame, cache_paths: list[Path], tick: float) -> dict[str, dict[str, Any]]:
    """Replay cache-covered fixed events; mismatches are left for raw fallback."""
    outcomes: dict[str, dict[str, Any]] = {}
    for cache_path in cache_paths:
        cache = pd.read_pickle(cache_path, compression="gzip")
        if float(cache["tick"]) != float(tick):
            raise ValueError(f"cache tick differs from frozen catalog: {cache_path}")
        bars = cache["bars"]
        if not {"open", "high", "low", "close", "atr"}.issubset(bars.columns):
            raise ValueError(f"cache lacks native path inputs: {cache_path}")
        for row in expected.itertuples(index=False):
            event_id = str(row.ledger_event_id)
            signal_time = pd.Timestamp(int(row.signal_bar_open_ms), unit="ms", tz="UTC")
            if event_id in outcomes or signal_time not in bars.index:
                continue
            reference = float(row.reference_signal_risk)
            signal_close = float(row.signal_close)
            stop = _frozen_stop(signal_close, reference, tick)
            base = _native_exit_from_cache(bars, signal_time, signal_close, reference, stop, tick, use_be=False)
            be = _native_exit_from_cache(bars, signal_time, signal_close, reference, stop, tick, use_be=True)
            outcomes[event_id] = {"baseline": base, "be05": be, "signal_bar_open": signal_time,
                                  "signal_close": signal_close, "reference_signal_risk": reference,
                                  "cache_file": str(cache_path)}
    return outcomes


def _frame_to_utc(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.DataFrame:
    for name in names:
        frame[name] = pd.to_datetime(frame[name], utc=True)
    return frame


def _assert_source_contract(link: pd.DataFrame, ledger: pd.DataFrame) -> dict[str, Any]:
    """Fail closed if the claimed immutable V1 baseline or linkage has drifted."""
    if sha256(IMMUTABLE_LEDGER) != EXPECTED_LEDGER_SHA256:
        raise ValueError("immutable baseline ledger SHA differs from its frozen receipt")
    if SOURCE_SHA256 != EXPECTED_REPLAY_SHA256 or PINE_SHA != EXPECTED_REPLAY_SHA256:
        raise ValueError("native V1 replay/Pine SHA differs from frozen ledger receipt")
    if link.ledger_event_id.duplicated().any() or len(link) != 6185:
        raise ValueError("expected 6,185 unique linked V1 events")
    if not link.ledger_event_id.isin(ledger.event_id).all():
        raise ValueError("link receipt points outside immutable V1 ledger")
    if not link.source_sha256.eq(EXPECTED_REPLAY_SHA256).all() or not link.pine_sha256.eq(EXPECTED_REPLAY_SHA256).all():
        raise ValueError("link replay/Pine SHA differs from frozen V1 contract")
    return {"immutable_ledger_sha256": EXPECTED_LEDGER_SHA256, "linked_events": int(len(link)),
            "linked_closed": int(link.link_status.eq("realized").sum()), "linked_censored": int(link.link_status.eq("censored").sum())}


def _summary(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    """Describe paired closed outcomes; event cumulative R is not an account curve."""
    rows: list[dict[str, Any]] = []
    for key, part in frame.groupby(groups, dropna=False):
        for arm in ("baseline", "be05"):
            net_r = pd.to_numeric(part[f"{arm}_net_r"], errors="coerce")
            closed = ~part[f"{arm}_censored"].astype(bool)
            ordered = part.loc[closed].sort_values([f"{arm}_exit_time", "entry_time", "event_id"], kind="mergesort")
            values = pd.to_numeric(ordered[f"{arm}_net_r"], errors="coerce").dropna()
            positive, negative = values.loc[values > 0].sum(), values.loc[values < 0].sum()
            cumulative = values.cumsum().to_numpy(float)
            curve = np.r_[0.0, cumulative]
            event_drawdown = float(np.min(curve - np.maximum.accumulate(curve))) if len(curve) else np.nan
            base = dict(zip(groups, key if isinstance(key, tuple) else (key,)))
            rows.append(dict(base, arm=arm, signal_rows=len(part), closed=int(closed.sum()), censored=int((~closed).sum()),
                             net_wins=int((values > 0).sum()), net_win_rate=float((values > 0).mean()) if len(values) else np.nan,
                             pf_r=float(positive / abs(negative)) if negative < 0 else np.nan,
                             net_mean_r=float(values.mean()) if len(values) else np.nan, total_r=float(values.sum()),
                             mfe_ge_10r=int((pd.to_numeric(part[f"{arm}_mfe_r"], errors="coerce") >= 10).sum()),
                             net_ge_10r=int((values >= 10).sum()), event_cumulative_r_drawdown=event_drawdown))
    return pd.DataFrame(rows)


def _paired_summary(pairs: pd.DataFrame) -> pd.DataFrame:
    """Summarize a completed paired ledger using its two fixed UTC year blocks."""
    pairs = pairs.copy()
    for name in ("entry_time", "baseline_exit_time", "be05_exit_time"):
        pairs[name] = pd.to_datetime(pairs[name], utc=True)
    overall = pairs.assign(scope="all")
    yearly = pairs.assign(entry_period=np.where(
        pairs.entry_time < MIDPOINT, "2024-09-10..2025-09-10", "2025-09-10..2026-09-10"))
    return pd.concat([_summary(overall, ["scope"]), _summary(pairs, ["timeframe_min"]),
                      _summary(yearly, ["entry_period"])], ignore_index=True)


def summarize_existing(output: Path) -> dict[str, Any]:
    """Repair only aggregate presentation from a complete, hash-pinned paired table."""
    receipt_path, pairs_path = output / "receipt.json", output / "paired_trade_ledger.csv.gz"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != "complete" or receipt.get("files", {}).get("paired_trade_ledger.csv.gz") != sha256(pairs_path):
        raise ValueError("paired ledger must be complete and match its receipt before summary-only rebuild")
    pairs = pd.read_csv(pairs_path)
    if len(pairs) != int(receipt.get("paired_events", -1)):
        raise ValueError("paired ledger row count differs from complete receipt")
    _paired_summary(pairs).to_csv(output / "summary.csv", index=False)
    receipt["files"]["summary.csv"] = sha256(output / "summary.csv")
    receipt["summary_rebuilt_from_paired_ledger"] = True
    receipt["summary_periods"] = ["2024-09-10..2025-09-10", "2025-09-10..2026-09-10"]
    write_json(receipt_path, receipt)
    return receipt


def rave_diagnostic_existing(output: Path) -> dict[str, Any]:
    """Measure RAVE concentration from completed paired outcomes, without reselection.

    Asset identity comes only from the immutable native V1 ledger keyed by
    event ID.  Exchange symbols are deliberately not parsed, so contract
    aliases cannot be mistaken for the RAVE underlying.
    """
    receipt_path, pairs_path = output / "receipt.json", output / "paired_trade_ledger.csv.gz"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != "complete" or receipt.get("files", {}).get("paired_trade_ledger.csv.gz") != sha256(pairs_path):
        raise ValueError("paired ledger must be complete and match receipt before concentration diagnostic")
    if sha256(IMMUTABLE_LEDGER) != EXPECTED_LEDGER_SHA256:
        raise ValueError("immutable baseline ledger SHA differs from frozen receipt")
    pairs = pd.read_csv(pairs_path)
    assets = pd.read_csv(IMMUTABLE_LEDGER, usecols=["event_id", "asset"])
    joined = pairs.merge(assets, on="event_id", how="left", validate="one_to_one")
    if joined.asset.isna().any() or len(joined) != len(pairs):
        raise ValueError("paired events do not map one-to-one to immutable native asset identity")
    rows: list[dict[str, Any]] = []
    original_closed = joined.loc[~joined.baseline_censored.astype(bool)]
    scopes = (("all", joined), ("asset_RAVE", joined.loc[joined.asset.eq("RAVE")]),
              ("excluding_asset_RAVE", joined.loc[~joined.asset.eq("RAVE")]),
              ("original_closed_footprint", original_closed),
              ("original_closed_asset_RAVE", original_closed.loc[original_closed.asset.eq("RAVE")]),
              ("original_closed_excluding_RAVE", original_closed.loc[~original_closed.asset.eq("RAVE")]))
    for scope, part in scopes:
        for arm in ("baseline", "be05"):
            closed = ~part[f"{arm}_censored"].astype(bool)
            values = pd.to_numeric(part.loc[closed, f"{arm}_net_r"], errors="coerce").dropna()
            rows.append({"scope": scope, "arm": arm, "signal_rows": len(part), "closed": len(values),
                         "censored": int((~closed).sum()), "realized_wins": int((values > 0).sum()),
                         "total_net_r": float(values.sum()), "mean_net_r": float(values.mean()) if len(values) else np.nan,
                         "rave_asset_literal": "RAVE"})
    diagnostic = pd.DataFrame(rows)
    diagnostic.to_csv(output / "rave_concentration.csv", index=False)
    receipt["files"]["rave_concentration.csv"] = sha256(output / "rave_concentration.csv")
    receipt["rave_concentration_diagnostic"] = "post-hoc concentration only; asset joined from immutable ledger by event_id; not an admission rule"
    write_json(receipt_path, receipt)
    return receipt


def run(output: Path, *, max_streams: int | None = None) -> dict[str, Any]:
    """Generate paired evidence, retaining a failure receipt if a source drifts."""
    output.mkdir(parents=True, exist_ok=True)
    link = pd.read_csv(LINK_LEDGER)
    ledger = pd.read_csv(IMMUTABLE_LEDGER)
    contract = _assert_source_contract(link, ledger)
    link = _frame_to_utc(link, ())
    ledger = _frame_to_utc(ledger, ("signal_bar_open", "entry_time", "exit_time"))
    baseline = ledger.set_index("event_id").loc[link.ledger_event_id].reset_index()
    catalog = pd.read_json(SOURCE_EXP / "data/catalog.json")
    ticks = catalog.set_index(["venue", "symbol"]).tick.astype(float).to_dict()
    cache_index = _cache_index()
    stream_keys = ["venue", "symbol", "timeframe_min", "frozen_ohlc_file", "frozen_ohlc_sha256", "frozen_ohlc_timeframe_min"]
    groups = list(link.groupby(stream_keys, sort=True, dropna=False))
    if max_streams is not None:
        groups = groups[:max_streams]
    run_receipt: dict[str, Any] = {"method_version": METHOD_VERSION, "config": {"window_start": START.isoformat(), "window_end_exclusive": END.isoformat(), "direction": "long_only", "entry": "following_open", "round_trip_cost": ROUND_TRIP_COST, "mfe_be_trigger_r": BE_TRIGGER_R, "be_effective": "following_bar", "holdout_consumption": 1, "sample_classification": "non_blind_historical"},
                                   "contract": contract, "requested_streams": len(groups), "stream_failures": [],
                                   "cache_root": str(CACHE_ROOT), "cache_indexed_streams": int(sum(len(paths) for paths in cache_index.values())),
                                   "cache_replayed_events": 0, "raw_fallback_events": 0}
    records: list[dict[str, Any]] = []
    try:
        for number, (key, expected) in enumerate(groups, 1):
            venue, symbol, minutes, raw_file, raw_sha, raw_minutes = key
            source_path = Path(raw_file)
            if not source_path.exists():
                raise FileNotFoundError(f"source missing: {source_path}")
            actual_source_sha = sha256(source_path)
            if actual_source_sha != raw_sha:
                raise ValueError(f"source SHA differs for {venue}/{symbol}/{minutes}: {actual_source_sha}")
            tick = ticks.get((venue, symbol))
            if tick is None or not np.isfinite(tick) or tick <= 0:
                raise ValueError(f"missing valid frozen catalog tick for {venue}/{symbol}")
            expected = expected.merge(baseline[["event_id", "signal_close", "reference_signal_risk"]],
                                      left_on="ledger_event_id", right_on="event_id", how="left", validate="one_to_one")
            cache_paths = cache_index.get((str(venue), str(symbol), int(minutes), actual_source_sha), [])
            outcomes = _cached_outcomes(expected, cache_paths, float(tick))
            run_receipt["cache_replayed_events"] += len(outcomes)
            unmatched = expected.loc[~expected.ledger_event_id.isin(outcomes)].copy()
            if len(unmatched):
                # A missing cache row is never silently discarded.  The frozen
                # source remains the fallback, with the same full V1 replay.
                source = _read_utc_source(source_path)
                bars = source if int(raw_minutes) == int(minutes) else aggregate(source, int(minutes))
                fallback, errors = _replay_stream(bars, float(tick), unmatched)
                if errors:
                    raise ValueError(f"{venue}/{symbol}/{minutes}: {';'.join(errors)}")
                outcomes.update(fallback)
                run_receipt["raw_fallback_events"] += len(fallback)
            missing = set(expected.ledger_event_id).difference(outcomes)
            if missing:
                raise ValueError(f"{venue}/{symbol}/{minutes}: {len(missing)} expected events absent from native replay")
            reference = baseline.loc[baseline.event_id.isin(expected.ledger_event_id)].set_index("event_id")
            for event_id, replayed_outcome in outcomes.items():
                row = reference.loc[event_id]
                base = replayed_outcome["baseline"]
                for field in IDENTITY_PARITY_FIELDS:
                    if str(base[field]) != str(row[field]):
                        raise ValueError(f"baseline parity failed {event_id} {field}: {base[field]!r} != {row[field]!r}")
                for field in NUMERIC_PARITY_FIELDS:
                    if not np.isclose(float(base[field]), float(row[field]), rtol=0, atol=1e-10, equal_nan=True):
                        raise ValueError(f"baseline parity failed {event_id} {field}: {base[field]!r} != {row[field]!r}")
                for field, actual, expected_value in (("signal_close", replayed_outcome["signal_close"], row.signal_close),
                                                      ("reference_signal_risk", replayed_outcome["reference_signal_risk"], row.reference_signal_risk)):
                    if not np.isclose(float(actual), float(expected_value), rtol=0, atol=1e-10, equal_nan=True):
                        raise ValueError(f"baseline parity failed {event_id} {field}: {actual!r} != {expected_value!r}")
                be = replayed_outcome["be05"]
                initial_r = float(be["initial_risk"])
                records.append({"event_id": event_id, "venue": venue, "symbol": symbol, "timeframe_min": int(minutes),
                                "signal_bar_open": replayed_outcome["signal_bar_open"], "entry_time": base["entry_time"], "entry_price": base["entry_price"],
                                "initial_stop": base["initial_stop"], "initial_risk": initial_r,
                                "baseline_exit_time": base["exit_time"], "baseline_exit_price": base["exit_price"], "baseline_exit_reason": base["exit_reason"], "baseline_net_r": base["net_r"], "baseline_net_return": base["net_return"], "baseline_mfe_r": base["mfe_return"] / (initial_r / base["entry_price"]), "baseline_censored": base["censored"],
                                "be05_exit_time": be["exit_time"], "be05_exit_price": be["exit_price"], "be05_exit_reason": be["exit_reason"], "be05_net_r": be["net_r"], "be05_net_return": be["net_return"], "be05_mfe_r": be["mfe_return"] / (initial_r / be["entry_price"]), "be05_censored": be["censored"], "be05_triggered": be["be_triggered"], "be05_trigger_bar_open": be["be_trigger_bar_open"],
                                "source_file": str(source_path), "source_sha256": actual_source_sha,
                                "replay_path": "featured_cache" if "cache_file" in replayed_outcome else "raw_features_fallback",
                                "featured_cache_file": replayed_outcome.get("cache_file")})
            if number % 50 == 0 or number == len(groups):
                print(f"replayed {number}/{len(groups)} streams; {len(records)} linked events", flush=True)
        pairs = pd.DataFrame(records).sort_values(["entry_time", "event_id"], kind="mergesort")
        if len(pairs) != len(link) and max_streams is None:
            raise ValueError(f"linked event count mismatch: {len(pairs)} != {len(link)}")
        if max_streams is None:
            summary = _paired_summary(pairs)
        else:
            summary = _summary(pairs, ["timeframe_min"])
        pairs.to_csv(output / "paired_trade_ledger.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        summary.to_csv(output / "summary.csv", index=False)
        run_receipt.update({"status": "complete", "completed_streams": len(groups), "paired_events": len(pairs), "source_files": int(pairs.source_file.nunique()),
                            "files": {"paired_trade_ledger.csv.gz": sha256(output / "paired_trade_ledger.csv.gz"), "summary.csv": sha256(output / "summary.csv")}})
        write_json(output / "receipt.json", run_receipt)
        return run_receipt
    except Exception as error:
        run_receipt.update({"status": "failed", "completed_events_before_failure": len(records), "error_type": type(error).__name__, "error": str(error)})
        write_json(output / "failed_run.json", run_receipt)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-streams", type=int, help="Bounded source timing/parity probe; never an all-universe result.")
    parser.add_argument("--summarize-existing", action="store_true", help="Rebuild summary only from a complete hash-pinned paired ledger.")
    parser.add_argument("--rave-diagnostic-existing", action="store_true", help="Run only the literal-asset RAVE concentration diagnostic from paired output.")
    args = parser.parse_args()
    if args.summarize_existing or args.rave_diagnostic_existing:
        if args.max_streams is not None:
            raise ValueError("existing-output postprocessors cannot combine with --max-streams")
        if args.summarize_existing and args.rave_diagnostic_existing:
            raise ValueError("choose one existing-output postprocessor per invocation")
        result = summarize_existing(args.output) if args.summarize_existing else rave_diagnostic_existing(args.output)
    else:
        result = run(args.output, max_streams=args.max_streams)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

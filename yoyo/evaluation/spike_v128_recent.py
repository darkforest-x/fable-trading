"""Receipt-bound recent-window SPIKE V12.8 replay.

This runner consumes only the registered five-minute archive.  It rebuilds
complete chart/HTF buckets, evaluates facts at each chart close, and starts a
fixed V9 exit at the following open.  It deliberately keeps V9's both-side
ledger separate from the V12.6-derived long joint ledger.  ``frames`` and
``hints`` are delegated to the reference renderer and are diagnostic only.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v7_fast import _data_gap, _detect_confirmed_fast, _initial_position_fast
from yoyo.evaluation.spike_v10_4 import reference_long_exits
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v126_engine import line_events, pair_events
from yoyo.evaluation.spike_v126_htf_recheck import (
    _legacy,
    bb_admission,
    complete_bars,
    pine_facts,
    structure_parents,
)
from yoyo.evaluation.spike_v8_six_filters import _committed


EXP = Path("experiments/active/exp-spike-v128-recent-20260923-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
MANIFEST = Path("data/research/spike_v128_recent_20260923/manifest.json")
TEST = Path("tests/evaluation/test_spike_v128_recent.py")
PINE = Path("yoyo/evaluation/pine/spike_burst_v12_6.pine")
PINE_V128 = Path("yoyo/evaluation/pine/spike_burst_v12_8.pine")
TABLES = ("decisions", "trades", "statuses", "controls", "frames", "hints")
ARMS = ("v9_both", "joint")
VERSION = "spike-v128-recent-20260923-v1"


def digest(path: Path) -> str:
    """Return the stable content hash used for source, input, and receipt checks."""
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dump(path: Path, value: object) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n")


def _config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    required = {"experiment_id", "start", "split", "end", "warmup_start", "timeframes", "data_dir", "round_trip_cost", "control_seed", "vol_bins"}
    missing = required - set(cfg)
    if missing or cfg["experiment_id"] != EXP.name or cfg["timeframes"] != [15, 60]:
        raise ValueError(f"invalid frozen V12.8 config; missing={sorted(missing)}")
    if float(cfg["round_trip_cost"]) != .002:
        raise ValueError("the fixed 20bp cost contract changed")
    return cfg


def _window(index: pd.DatetimeIndex, minutes: int, cfg: dict) -> np.ndarray:
    close = index + pd.Timedelta(minutes=minutes)
    return np.asarray((close >= pd.Timestamp(cfg["start"])) & (close < pd.Timestamp(cfg["end"])))


def _generic_facts(bars: pd.DataFrame, asset: str, tick: float, minutes: int) -> dict:
    """Port V12.6 close facts for a chart without the 15m-only H1 SMA gate."""
    frame = features(bars)
    gap = _data_gap(frame, minutes).to_numpy(bool)
    ages = np.arange(len(frame)) - np.maximum.accumulate(np.where(gap, np.arange(len(frame)), 0))
    frame["ready"] &= ages >= 12
    segments = pd.Series(gap.astype(int).cumsum(), index=frame.index)
    med = frame.volume.groupby(segments).transform(lambda v: v.rolling(20, min_periods=20).median())
    frame["rv"] = (frame.volume / med.shift()).where(med.shift().gt(0))
    advance = (frame.close - frame.close.shift(3)) / frame.atr.shift(3)
    volume_ratio = (frame.volume.rolling(3).sum() / (3 * med.shift(3))).where(med.shift(3).gt(0))
    detected: dict[str, np.ndarray] = {}
    for side, name in ((1, "long"), (-1, "short")):
        legacy, hi, lo = _legacy(frame, gap, advance, volume_ratio, side)
        supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
        supplied["legacy_confirmed"], supplied["legacy_parent_high"], supplied["legacy_parent_low"] = legacy, hi, lo
        supplied["advance3"], supplied["volume_ratio3"] = advance, volume_ratio
        supplied["data_gap"], supplied["confirmed"] = gap, True
        detected[name] = _detect_confirmed_fast(supplied, side)
        if side == 1:
            known = (frame.ready.to_numpy(bool) & ~gap & frame.atr.gt(0).to_numpy()
                     & np.isfinite(frame[["md", "sb", "atr", "ropeHigh", "middle"]]).all(axis=1).to_numpy())
            parent_high, parent_low = structure_parents(frame.close.to_numpy(), known, legacy, hi, lo, detected[name])
    if (detected["long"] & detected["short"]).any():
        raise ValueError("ambiguous raw direction")
    side = np.where(detected["long"], 1, np.where(detected["short"], -1, 0))
    close, atr = frame.close.to_numpy(float), frame.atr.to_numpy(float)
    edge = np.where(side == 1, frame.ropeHigh, frame.ropeLow)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = side * (close - edge) / np.where(atr > 0, atr, np.nan)
    bb = bb_admission(frame.close, ages + 1)
    clock = frame.index + pd.Timedelta(minutes=minutes)
    rv = frame.rv.to_numpy(float)
    bundle = ((asset not in ("", "USDC")) & np.isfinite(rv) & (rv >= 0) & (rv <= 50)
              & np.asarray(clock.dayofweek != 6))
    final = (side != 0) & bb & bundle & (distance <= 3)
    state: dict = {}
    exits = reference_long_exits(frame.high, frame.low, frame.close, frame.atr, ready=frame.ready, gap=gap,
                                 raw_side=side, signal_side=np.where(final, side, 0), tick=tick, state=state)
    return {"frame": frame, "gap": gap, "side": side, "v9": final, "v9_long": final & (side == 1),
            "parent_high": parent_high, "parent_low": parent_low, "ref_long_exit": exits,
            "box": state,
            "ready": frame.ready.to_numpy(bool), "can_run": ~gap & np.isfinite(atr) & (atr > 0),
            "long_alive": frame.ready.to_numpy(bool) & (atr > 0) & (close > frame.ropeHigh.to_numpy()) & (side != -1)}


def facts_for(bars: pd.DataFrame, base5m: pd.DataFrame, asset: str, tick: float, minutes: int) -> dict:
    """Use byte-compatible V12.6 15m facts; 1h retains every other V12.6 gate."""
    if minutes == 15:
        return pine_facts(bars, base5m, asset, tick)
    if minutes == 60:
        return _generic_facts(bars, asset, tick, minutes)
    raise ValueError("V12.8 only permits 15m and 60m")


def _map_htf(higher: pd.DataFrame, chart: pd.DataFrame, events: list[dict], chart_minutes: int, higher_minutes: int) -> list[dict]:
    """Make a closed HTF break visible only at its following chart bucket."""
    clock = chart.index.asi8 // 60_000_000_000
    hclock = higher.index.asi8 // 60_000_000_000
    mapped: list[dict] = []
    for event in events:
        break_i = int(event.get("i", event["break_i"]))
        visible = int(hclock[break_i]) + higher_minutes
        i = int(np.searchsorted(clock, visible))
        if i >= len(clock) or clock[i] - visible >= chart_minutes:
            continue
        mapped.append({**event, "visible_i": i,
                       **{f"{field}_t": int(hclock[int(event[field])]) for field in ("ax", "bx", "cx")}})
    return mapped


def _mfe_fields(result: dict, prepared: fixed.PreparedArm) -> dict:
    """Separate fully observed excursion from a stop bar's unknowable wick order."""
    side, entry, risk = int(result["side"]), float(result["entry_price"]), float(result["initial_risk"])
    entry_i, exit_i = int(result["entry_i"]), int(result["exit_i"])
    censored, reason = bool(result["censored"]), str(result["exit_reason"])
    if not (risk > 0 and math.isfinite(entry)):
        return {"mfe_known_r": math.nan, "mfe_upper_r": math.nan, "close_peak_r": math.nan, "stop_bar_excursion_ambiguous": False}
    terminal_stop = (not censored) and reason in {"initial_stop", "trailing_stop"}
    terminal_gap = ("_gap" in reason) or reason == "data_gap_censored"
    last_full = exit_i if censored and reason == "boundary_mark" else exit_i - 1
    known, closes = 0.0, []
    for i in range(entry_i, max(entry_i, last_full + 1)):
        if i >= len(prepared.frame) or prepared.gap[i]:
            break
        known = max(known, side * ((float(prepared.high[i]) if side == 1 else float(prepared.low[i])) - entry) / risk)
        closes.append(side * (float(prepared.close[i]) - entry) / risk)
    if not terminal_gap and 0 <= exit_i < len(prepared.frame) and not prepared.gap[exit_i]:
        known = max(known, side * (float(prepared.open[exit_i]) - entry) / risk)
    upper = known
    if terminal_stop and not terminal_gap and 0 <= exit_i < len(prepared.frame) and not prepared.gap[exit_i]:
        wick = float(prepared.high[exit_i]) if side == 1 else float(prepared.low[exit_i])
        upper = max(upper, side * (wick - entry) / risk)
    return {"mfe_known_r": known, "mfe_upper_r": upper,
            "close_peak_r": max([0.0, *closes]), "stop_bar_excursion_ambiguous": bool(terminal_stop and upper > known)}


def attempt(prepared: fixed.PreparedArm, i: int, side: int) -> tuple[str, dict | None]:
    """Evaluate exactly one next-open fixed entry and retain unavailable/censored outcomes."""
    if i + 1 >= len(prepared.frame):
        return "no_next_bar", None
    if prepared.gap[i + 1]:
        return "next_bar_is_gap", None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low, prepared.close,
                                 prepared.atr, prepared.gap, i, side, prepared.spec)
    if row is None:
        return "risk_invalid", None
    result = fixed.replay_fixed_entry(prepared.context, pd.Series(row), arm="v8", enable_be=False, prepared=prepared)
    result.update(_mfe_fields(result, prepared))
    return ("closed" if not bool(result["censored"]) else
            "censored_boundary" if result["exit_reason"] == "boundary_mark" else "censored_gap"), result


def serial(prepared: fixed.PreparedArm, candidates: list[dict], arm: str, key: str) -> tuple[list[dict], list[dict]]:
    """Warm through the whole archive: a data gap censors only its old position."""
    trades, statuses, flat_from, holder = [], [], -1, None
    for event in candidates:
        i, side = int(event["signal_i"]), int(event["side"])
        common = {**event, "arm": arm}
        if i < flat_from:
            if event["in_window"]:
                statuses.append({**common, "status": "skipped_in_position", "blocking_trade": holder})
            continue
        status, result = attempt(prepared, i, side)
        if event["in_window"]:
            statuses.append({**common, "status": status, "blocking_trade": None})
        if result is None:
            continue
        trade_key = f"{key}:{arm}:{pd.Timestamp(event['signal_close']).isoformat()}"
        row = {**common, **{name: result.get(name) for name in source.TRADE_KEEP}, "status": status,
               "trade_key": trade_key, "trail_armed": bool(result["close_peak_r"] >= 2.0),
               "mfe_known_r": result["mfe_known_r"], "mfe_upper_r": result["mfe_upper_r"],
               "close_peak_r": result["close_peak_r"], "stop_bar_excursion_ambiguous": result["stop_bar_excursion_ambiguous"]}
        if event["in_window"]:
            trades.append(row)
        holder = trade_key
        # An exit at i's close/open permits a new signal discovered at that same close;
        # a later segment after a gap is likewise free once its old position is censored.
        flat_from = len(prepared.frame) + 1 if status == "censored_boundary" else int(result["exit_i"])
    return trades, statuses


def matched_controls(prepared: fixed.PreparedArm, trades: list[dict], cfg: dict) -> pd.DataFrame:
    """Draw exactly once from same symbol/side/UTC-week/fold/causal volatility strata."""
    frame, minutes = prepared.frame, prepared.context.minutes
    close = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(close.strftime("%G-W%V")); fold = np.where(close < pd.Timestamp(cfg["split"]), "earlier", "later")
    bins = np.searchsorted(np.asarray(cfg["vol_bins"], float), prepared.atr / prepared.close, side="left")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    valid = (ready & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close) & (prepared.close > 0)
             & ~prepared.gap & (close >= pd.Timestamp(cfg["start"])) & (close < pd.Timestamp(cfg["end"])))
    pools: dict[tuple, np.ndarray] = {}
    cache: dict[tuple[int, int], tuple[str, dict | None]] = {}
    rows: list[dict] = []
    for trade in trades:
        i, side = int(trade["signal_i"]), int(trade["side"])
        stratum = (side, week[i], fold[i], int(bins[i]))
        if stratum not in pools:
            pools[stratum] = np.flatnonzero(valid & (week == stratum[1]) & (fold == stratum[2]) & (bins == stratum[3]))
        choices = pools[stratum][pools[stratum] != i]
        chosen = None if not len(choices) else int(choices[int(hashlib.sha256(f"{cfg['control_seed']}|{trade['trade_key']}".encode()).hexdigest(), 16) % len(choices)])
        status, control = "empty_stratum", None
        if chosen is not None:
            status, control = cache.setdefault((chosen, side), attempt(prepared, chosen, side))
        matched = control is not None and status == "closed" and not bool(trade["censored"])
        rows.append({"trade_key": trade["trade_key"], "arm": trade["arm"], "symbol": trade["symbol"],
                     "timeframe_min": minutes, "side": side, "utc_week": stratum[1], "fold": stratum[2], "vol_bin": stratum[3],
                     "matched": bool(matched), "reason": "target_censored" if bool(trade["censored"]) else ("matched" if matched else status),
                     "target_net_return": trade["net_return"], "control_sig": chosen,
                     "control_signal_close": None if chosen is None else close[chosen],
                     "control_exit_time": None if control is None else control["exit_time"],
                     "control_exit_reason": None if control is None else control["exit_reason"],
                     "control_censored": True if control is None else bool(control["censored"]),
                     "control_net_r": math.nan if not matched else control["net_r"],
                     "control_net_return": math.nan if not matched else control["net_return"]})
    return pd.DataFrame(rows)


def _empty() -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame() for name in TABLES}


def run_stream(base5m: pd.DataFrame, symbol: str, meta: dict, minutes: int, cfg: dict) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build one symbol/timeframe event ledger, then evaluate independent serial arms."""
    tick, asset = float(meta["tick"]), str(meta["asset"])
    since = pd.Timestamp(cfg["warmup_start"])
    bars, partial = complete_bars(base5m.loc[base5m.index >= since], minutes)
    higher_minutes = 60 if minutes == 15 else 240
    higher, hpartial = complete_bars(base5m.loc[base5m.index >= since], higher_minutes)
    if len(bars) < 2:
        expected = pd.date_range(pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), freq=f"{minutes}min", inclusive="left")
        actual = bars.index[_window(bars.index, minutes, cfg)] if len(bars) else bars.index
        return _empty(), {"symbol": symbol, "minutes": minutes, "chart_bars_total": len(bars), "higher_bars": len(higher),
                          "window_bars_expected": len(expected), "window_bars_actual": len(actual),
                          "window_gap_count": int(len(expected) - len(actual)), "first": None if not len(bars) else bars.index[0],
                          "last": None if not len(bars) else bars.index[-1], "valid_ready_window_bars": 0,
                          "candidates": 0, "v9_candidates": 0, "joint_candidates": 0,
                          "partial_chart_buckets": partial, "partial_higher_buckets": hpartial, "chart_gaps": 0}
    facts = facts_for(bars, base5m, asset, tick, minutes)
    frame = facts["frame"]
    local = line_events(frame.open, frame.high, frame.low, frame.close, frame.atr, can_run=facts["can_run"], gap=facts["gap"], tick=tick,
                        confirmed_long=facts["v9_long"], parent_high=facts["parent_high"], parent_low=facts["parent_low"],
                        raw_side=facts["side"], long_alive=facts["long_alive"], ref_long_exit=facts["ref_long_exit"])
    hf = features(higher)
    hgap = _data_gap(hf, higher_minutes).to_numpy(bool)
    htf = line_events(hf.open, hf.high, hf.low, hf.close, hf.atr, can_run=~hgap & np.isfinite(hf.atr) & hf.atr.gt(0), gap=hgap,
                      tick=tick, htf=True)
    mapped = _map_htf(hf, frame, htf.winner_events, minutes, higher_minutes)
    chart_clock = frame.index.asi8 // 60_000_000_000
    joints = pair_events(frame.close.to_numpy(), bar_times=chart_clock, chart_breaks=local.events, htf_breaks=mapped,
                         box_id=facts["box"]["box_entry"], confirmed_long=facts["v9_long"], gap=facts["gap"])
    key = f"binance_um:{symbol}:{minutes}m"
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe_min": minutes}
    prepared = source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    in_window = _window(frame.index, minutes, cfg)
    all_events = []
    for i in np.flatnonzero(facts["v9"]):
        signal_close = frame.index[i] + pd.Timedelta(minutes=minutes)
        if signal_close >= pd.Timestamp(cfg["end"]):
            continue
        all_events.append({**identity, "candidate_family": "v9", "signal_i": int(i), "side": int(facts["side"][i]),
                           "signal_bar_open": frame.index[i], "signal_close": signal_close, "joint": False,
                           "in_window": bool(in_window[i])})
    joint_events_rows = []
    for event in joints:
        i = int(event["joint_i"])
        signal_close = frame.index[i] + pd.Timedelta(minutes=minutes)
        if signal_close >= pd.Timestamp(cfg["end"]):
            continue
        joint_events_rows.append({**identity, **event, "candidate_family": "joint", "signal_i": i, "side": 1,
                                  "signal_bar_open": frame.index[i], "signal_close": signal_close, "joint": True,
                                  "in_window": bool(in_window[i])})
    all_events.extend(joint_events_rows)
    all_events.sort(key=lambda x: (x["signal_i"], x["candidate_family"]))
    v9_candidates = [x for x in all_events if x["candidate_family"] == "v9"]
    trades, statuses = [], []
    for arm, events in (("v9_both", v9_candidates), ("joint", joint_events_rows)):
        t, s = serial(prepared, events, arm, key)
        trades.extend(t); statuses.extend(s)
    for row in statuses:
        row["symbol"], row["timeframe_min"] = symbol, minutes
    trade_table = pd.DataFrame(trades)
    if len(trade_table):
        trade_table["censored"] = trade_table["censored"].astype(bool)
        closed = trade_table.loc[~trade_table.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
    controls = matched_controls(prepared, trades, cfg) if trades else pd.DataFrame()
    # Delay the optional renderer import so its independently-owned module need not be available during unit testing.
    from yoyo.evaluation.spike_v128_hint_replay import replay_reference_and_hints
    frames, hints = replay_reference_and_hints(facts, base5m, tick, minutes, pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]))
    for table in (frames, hints):
        if len(table):
            table.insert(0, "symbol", symbol)
            table.insert(1, "timeframe_min", minutes)
            table["frame_key"] = f"{symbol}:{minutes}m:" + table["frame_key"].astype(str)
    decisions = [event for event in all_events if event["in_window"]]
    for event in decisions:
        event["arm"] = "v9_both" if event["candidate_family"] == "v9" else "joint"
    expected_window = pd.date_range(pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), freq=f"{minutes}min", inclusive="left")
    actual_window = frame.index[in_window]
    return {"decisions": pd.DataFrame(decisions), "trades": trade_table, "statuses": pd.DataFrame(statuses),
            "controls": controls, "frames": frames, "hints": hints}, {
                "symbol": symbol, "minutes": minutes, "chart_bars_total": len(frame), "higher_bars": len(hf),
                "window_bars_expected": len(expected_window), "window_bars_actual": len(actual_window),
                "window_gap_count": int(len(expected_window) - len(actual_window)),
                "first": None if not len(frame) else frame.index[0], "last": None if not len(frame) else frame.index[-1],
                "valid_ready_window_bars": int((facts["ready"] & ~facts["gap"] & in_window).sum()),
                "candidates": len(decisions), "v9_candidates": sum(event["in_window"] for event in v9_candidates),
                "joint_candidates": sum(event["in_window"] for event in joint_events_rows), "partial_chart_buckets": partial,
                "partial_higher_buckets": hpartial, "chart_gaps": int(facts["gap"].sum())}


def _receipt_dir(output: Path, symbol: str, minutes: int) -> Path:
    return output / "streams" / f"{symbol}_{minutes}m"


def _validate_receipt(folder: Path, identity: str, input_sha: str) -> dict:
    receipt = json.loads((folder / "receipt.json").read_text())
    if receipt.get("status") != "complete" or receipt.get("run_identity") != identity or receipt.get("input_sha256") != input_sha:
        raise ValueError(f"receipt identity mismatch: {folder}")
    if set(receipt.get("files", {})) != {f"{name}.csv.gz" for name in TABLES}:
        raise ValueError(f"receipt inventory mismatch: {folder}")
    for name, sha in receipt["files"].items():
        if digest(folder / name) != sha:
            raise ValueError(f"receipt artifact changed: {folder / name}")
    return receipt


def worker(args: tuple) -> dict:
    symbol, path, meta, output_s, minutes, cfg, run_identity, input_sha = args
    output, final = Path(output_s), _receipt_dir(Path(output_s), symbol, minutes)
    if (final / "receipt.json").exists():
        return _validate_receipt(final, run_identity, input_sha)
    staging = final.with_name("." + final.name + ".staging")
    if staging.exists():
        raise ValueError(f"stale incomplete staging is evidence: {staging}")
    if digest(Path(path)) != input_sha:
        raise ValueError("registered input changed before replay")
    started = time.perf_counter()
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    if not index.is_monotonic_increasing or not index.is_unique or (index.asi8 % pd.Timedelta(minutes=5).value != 0).any():
        raise ValueError(f"invalid 5m clock: {path}")
    base = pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float), index=index, columns=["open", "high", "low", "close", "volume"])
    base = base.loc[(base.index >= pd.Timestamp(cfg["warmup_start"])) & (base.index + pd.Timedelta(minutes=5) <= pd.Timestamp(cfg["end"]))]
    tables, summary = run_stream(base, symbol, meta, minutes, cfg)
    summary["recent_rows"] = int(((base.index >= pd.Timestamp(cfg["start"])) & (base.index < pd.Timestamp(cfg["end"]))).sum())
    staging.mkdir(parents=True)
    for name in TABLES:
        table = tables[name]
        table.to_csv(staging / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {"status": "complete", "symbol": symbol, "minutes": minutes, "run_identity": run_identity, "input_sha256": input_sha,
               "summary": summary, "files": {f"{name}.csv.gz": digest(staging / f"{name}.csv.gz") for name in TABLES},
               "wall_seconds": time.perf_counter() - started}
    dump(staging / "receipt.json", receipt)
    staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 4, symbols: list[str] | None = None,
        input_manifest: Path = MANIFEST) -> None:
    """Run process-isolated symbol/timeframe work only after all replay builders are committed."""
    cfg = _config()
    roots = (Path(__file__), Path(source.__file__), Path(fixed.__file__), Path("yoyo/evaluation/spike_v126_engine.py"), Path("yoyo/evaluation/spike_v126_htf_recheck.py"), Path("yoyo/evaluation/spike_v128_hint_replay.py"))
    code = _local_transitive_python(roots)
    declared = tuple(dict.fromkeys((Path(__file__), TEST, CONFIG, PLAN, PINE, PINE_V128, *code)))
    if not _committed(declared):
        raise ValueError("commit runner, tests, config, plan, Pine, and transitive source before replay")
    input_manifest = Path(input_manifest)
    manifest = json.loads(input_manifest.read_text())
    streams = manifest.get("streams")
    if not isinstance(streams, list):
        raise ValueError("input manifest lacks streams")
    if manifest.get("failed"):
        raise ValueError("input manifest records failed streams")
    by_symbol = {str(item["symbol"]): item for item in streams}
    if len(by_symbol) != len(streams):
        raise ValueError("input manifest has duplicate symbols")
    requested = sorted(by_symbol if symbols is None else symbols)
    if not requested or any(s not in by_symbol for s in requested):
        raise ValueError("invalid --symbols selection")
    declared_requested = manifest.get("requested")
    if symbols is None and declared_requested is not None:
        if isinstance(declared_requested, int) and declared_requested != len(requested):
            raise ValueError("input manifest does not cover its requested universe")
        if isinstance(declared_requested, list) and set(map(str, declared_requested)) != set(requested):
            raise ValueError("input manifest requested symbols differ from streams")
    meta = source.symbol_meta()
    input_sha = {symbol: str(by_symbol[symbol]["sha256"]) for symbol in requested}
    for symbol in requested:
        if symbol not in meta or digest(Path(by_symbol[symbol]["path"])) != input_sha[symbol]:
            raise ValueError(f"metadata/input identity failed for {symbol}")
    identity = {"version": VERSION, "config": cfg, "config_sha256": digest(CONFIG),
                "input_manifest": str(input_manifest), "input_manifest_sha256": digest(input_manifest),
                "inputs": input_sha, "code": {str(path): digest(path) for path in declared},
                "symbol_meta_source": {"path": str(source.EXCHANGE_INFO), "sha256": digest(source.EXCHANGE_INFO)}, "symbols": requested,
                "timeframes": cfg["timeframes"], "subset": symbols is not None}
    run_identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    identity_path = output / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("output identity differs; select a fresh output")
    dump(identity_path, identity)
    (output / "streams").mkdir(exist_ok=True)
    tasks = [(symbol, str(by_symbol[symbol]["path"]), meta[symbol], str(output), minutes, cfg, run_identity, input_sha[symbol])
             for symbol in requested for minutes in cfg["timeframes"]]
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        pending = {pool.submit(worker, task): (task[0], task[4]) for task in tasks}
        for done, future in enumerate(as_completed(pending), 1):
            symbol, minutes = pending[future]
            try:
                receipts.append(future.result())
            except Exception as exc:
                errors.append({"symbol": symbol, "minutes": minutes, "error": repr(exc)})
            if done == 1 or done % 20 == 0 or done == len(tasks):
                print(json.dumps({"done": done, "total": len(tasks), "errors": len(errors),
                                  "latest_stream": f"{symbol}_{minutes}m"}), flush=True)
    keys = [f"{r['symbol']}_{r['minutes']}m" for r in receipts]
    dump(output / "manifest.json", {"complete": not errors and symbols is None and len(receipts) == len(tasks), "run_identity": run_identity,
                                     "stream_keys": sorted(keys), "receipts": {f"{r['symbol']}_{r['minutes']}m": digest(_receipt_dir(output, r["symbol"], r["minutes"]) / "receipt.json") for r in receipts},
                                     "errors": errors})
    if errors:
        raise RuntimeError(f"{len(errors)} stream workers failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--input-manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    run(args.output, workers=args.workers, symbols=args.symbols, input_manifest=args.input_manifest)

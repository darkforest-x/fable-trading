"""Receipt-bound BTC regime gates for the frozen SPIKE V11.2 box ledger.

BTC features use only complete Binance USD-M five-minute archive buckets.  At a
chart decision close the lookup selects the latest BTC candle already closed;
an incomplete, gapped, stale, or insufficient SMA window is ``unknown`` and
fails closed.  This module changes admission only: price protection, fills,
costs, and both exit engines remain the published implementations.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_joint_rsi_exit as rsi
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python, _validate_completion
from yoyo.evaluation.spike_v112_support_report import compare
from yoyo.evaluation.spike_v10_4_increment_report import PARITY
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-joint-btc-gate-20260922-v1")
CONFIG, PLAN = EXP / "config.json", EXP / "PROJECT_PLAN.md"
SOURCE = Path("experiments/active/exp-spike-v112-support-20260919-v1/results/run_v1")
GATES = ("none", "same_sma60", "same_sma120", "same_sma240", "h1_sma60", "h1_sma120", "h1_sma240")
EXITS = ("price", "rsi7")
TABLES = ("trades", "controls", "statuses", "decisions")
_BTC_REFERENCE_CACHE: dict[tuple[str, str], dict[int, pd.DataFrame]] = {}

TRADE_COLUMNS = [*study.TRADE_KEEP, "status", "arm", "exit_rule", "gate", "trade_key", "symbol", "asset", "timeframe", "source", "box_entry_i", "bars_after_v9"]
STATUS_COLUMNS = ["arm", "exit_rule", "gate", "trade_key", "signal_i", "signal_bar_open", "status", "blocking_trade", "symbol", "asset", "timeframe", "gate_known", "gate_reason"]
CONTROL_COLUMNS = ["exit_rule", "gate", "trade_key", "arm", "matched", "reason", "control_signal_i", "control_signal_bar_open", "control_net_r", "control_net_return", "control_exit_time", "month", "vol_bin", "fold"]
DECISION_COLUMNS = ["symbol", "timeframe", "trade_key", "signal_i", "signal_bar_open", "signal_close", "gate", "gate_pass", "gate_known", "gate_reason", "btc_timeframe", "btc_source_bar_open", "btc_source_close_time", "btc_source_close", "btc_sma", "btc_staleness_seconds"]


def _empty() -> dict[str, pd.DataFrame]:
    return {"trades": pd.DataFrame(columns=TRADE_COLUMNS), "controls": pd.DataFrame(columns=CONTROL_COLUMNS),
            "statuses": pd.DataFrame(columns=STATUS_COLUMNS), "decisions": pd.DataFrame(columns=DECISION_COLUMNS)}


def _complete_buckets(base: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate only full epoch buckets, retaining missing buckets as NaN.

    A rolling SMA is computed on this dense index, so a maintenance hole cannot
    be bridged by a later row.  All columns are derived from 5m bars at or
    before the named bucket close.
    """
    if minutes not in (15, 60):
        raise ValueError("BTC reference supports 15m and 1h only")
    if base.empty:
        return pd.DataFrame(columns=["close", "complete"], index=pd.DatetimeIndex([], tz="UTC"))
    expected = minutes // 5
    grouped = base.resample(f"{minutes}min", origin="epoch", label="left", closed="left")
    frame = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    count = grouped.close.count()
    frame["complete"] = count.eq(expected)
    frame.loc[~frame.complete, ["open", "high", "low", "close", "volume"]] = np.nan
    return frame


def btc_reference(base: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Return dense closed BTC 15m/1h tables with contiguous-window SMAs."""
    result: dict[int, pd.DataFrame] = {}
    for minutes in (15, 60):
        frame = _complete_buckets(base, minutes)
        for length in (60, 120, 240):
            frame[f"sma{length}"] = frame.close.rolling(length, min_periods=length).mean()
        result[minutes] = frame
    return result


def gate_decision(reference: dict[int, pd.DataFrame], signal_close: pd.Timestamp, timeframe: str, gate: str) -> dict:
    """Evaluate a strictly-as-of BTC permission with explicit unknown causes."""
    if gate not in GATES or timeframe not in ("15m", "1h"):
        raise ValueError("unknown gate or timeframe")
    if gate == "none":
        return {"gate_pass": True, "gate_known": True, "gate_reason": "not_applicable", "btc_timeframe": None,
                "btc_source_bar_open": pd.NaT, "btc_source_close_time": pd.NaT, "btc_source_close": math.nan,
                "btc_sma": math.nan, "btc_staleness_seconds": math.nan}
    family, raw_length = gate.split("_sma")
    minutes = {"same": {"15m": 15, "1h": 60}[timeframe], "h1": 60}[family]
    length = int(raw_length)
    frame = reference[minutes]
    close_time = pd.Timestamp(signal_close)
    cutoff = close_time - pd.Timedelta(minutes=minutes)
    i = int(frame.index.searchsorted(cutoff, side="right") - 1)
    common = {"btc_timeframe": f"{minutes}m", "btc_source_bar_open": pd.NaT, "btc_source_close_time": pd.NaT,
              "btc_source_close": math.nan, "btc_sma": math.nan, "btc_staleness_seconds": math.nan}
    if i < 0:
        return {**common, "gate_pass": False, "gate_known": False, "gate_reason": "btc_unavailable"}
    row = frame.iloc[i]
    source_open = frame.index[i]
    source_close = source_open + pd.Timedelta(minutes=minutes)
    stale = (close_time - source_close).total_seconds()
    common.update(btc_source_bar_open=source_open, btc_source_close_time=source_close, btc_staleness_seconds=stale)
    if source_close > close_time or stale < 0 or stale >= minutes * 60:
        return {**common, "gate_pass": False, "gate_known": False, "gate_reason": "btc_stale"}
    value, sma = float(row.close), float(row[f"sma{length}"])
    common.update(btc_source_close=value, btc_sma=sma)
    if not bool(row.complete) or not math.isfinite(value) or not math.isfinite(sma):
        return {**common, "gate_pass": False, "gate_known": False, "gate_reason": "btc_warmup_or_gap"}
    return {**common, "gate_pass": bool(value > sma), "gate_known": True,
            "gate_reason": "above_sma" if value > sma else "below_sma"}


def gate_mask(reference: dict[int, pd.DataFrame], signal_closes: pd.DatetimeIndex, timeframe: str, gate: str) -> np.ndarray:
    """Return permission booleans for a full timeline without per-bar pandas work."""
    if gate == "none":
        return np.ones(len(signal_closes), dtype=bool)
    family, raw_length = gate.split("_sma")
    minutes = {"same": {"15m": 15, "1h": 60}[timeframe], "h1": 60}[family]
    frame, length = reference[minutes], int(raw_length)
    interval_ns = minutes * 60 * 1_000_000_000
    positions = np.searchsorted(frame.index.asi8, signal_closes.asi8 - interval_ns, side="right") - 1
    valid = positions >= 0
    safe = np.maximum(positions, 0)
    source_close_ns = frame.index.asi8[safe] + interval_ns
    stale = signal_closes.asi8 - source_close_ns
    complete = frame.complete.to_numpy(bool)[safe]
    close = frame.close.to_numpy(float)[safe]
    sma = frame[f"sma{length}"].to_numpy(float)[safe]
    return valid & (stale >= 0) & (stale < interval_ns) & complete & np.isfinite(close) & np.isfinite(sma) & (close > sma)


def _cached_btc_reference(path: Path, input_sha: str, earliest: pd.Timestamp) -> dict[int, pd.DataFrame]:
    """Load immutable BTC once per worker, with the receipt hash as cache key."""
    key = (str(path.resolve()), input_sha)
    if key not in _BTC_REFERENCE_CACHE:
        if study.digest(path) != input_sha:
            raise ValueError("BTC archive changed after preflight")
        _BTC_REFERENCE_CACHE[key] = btc_reference(inc.guarded_5m(path, earliest))
    return _BTC_REFERENCE_CACHE[key]


def serial_candidates(events: list[dict], exit_rule: str, gate: str, decisions: dict[int, dict], evaluate, n: int) -> tuple[list[dict], list[dict]]:
    """Replay every parent candidate using arm-local occupancy and gate admission."""
    arm, trades, statuses, flat_from, holder = f"{exit_rule}__{gate}", [], [], -1, None
    for event in events:
        decision = decisions[int(event["signal_i"])]
        common = {**event, "arm": arm, "exit_rule": exit_rule, "gate": gate,
                  "gate_known": decision["gate_known"], "gate_reason": decision["gate_reason"]}
        if not decision["gate_pass"]:
            status = "rejected_btc_unknown" if not decision["gate_known"] else "rejected_btc_gate"
            statuses.append({**common, "status": status, "blocking_trade": None})
            continue
        if int(event["signal_i"]) < flat_from:
            statuses.append({**common, "status": "skipped_in_position", "blocking_trade": holder})
            continue
        status, result = evaluate(exit_rule, int(event["signal_i"]))
        statuses.append({**common, "status": status, "blocking_trade": None})
        if result is None:
            continue
        trades.append({**common, **{k: result.get(k) for k in study.TRADE_KEEP}, "status": status})
        holder = event["trade_key"]
        flat_from = n + 1 if status == "censored_boundary" else int(result["exit_i"])
    return trades, statuses


def matched_controls(prepared, trades: list[dict], ready: np.ndarray, minutes: int, gate_pass: np.ndarray, evaluate) -> list[dict]:
    """Use the published seed/strata, adding the arm's BTC permission to pools."""
    frame = prepared.frame
    with np.errstate(invalid="ignore", divide="ignore"):
        vol = np.searchsorted(study.VOL_BINS, prepared.atr / prepared.close, side="left")
    months = np.asarray(frame.index.strftime("%Y-%m"))
    fold = np.where(np.asarray(frame.index + pd.Timedelta(minutes=minutes) < study.SPLIT), "earlier", "later")
    eligible = (ready & gate_pass & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close)
                & (prepared.close > 0) & study.in_window(frame.index, minutes))
    pools, rows = {}, []
    for trade in trades:
        i, key = int(trade["signal_i"]), (months[int(trade["signal_i"])], int(vol[int(trade["signal_i"])]), fold[int(trade["signal_i"])])
        if key not in pools:
            pools[key] = np.flatnonzero(eligible & (months == key[0]) & (vol == key[1]) & (fold == key[2]))
        choices = pools[key][pools[key] != i]
        chosen, result, reason = None, None, "empty_stratum"
        if len(choices):
            seed = int(hashlib.sha256(f"{study.CONTROL_SEED}|{trade['trade_key']}".encode()).hexdigest(), 16)
            chosen = int(choices[seed % len(choices)])
            status, result = evaluate(trade["exit_rule"], chosen)
            reason = status if result is None else "censored" if result["censored"] else "matched"
        matched = reason == "matched" and not bool(trade["censored"])
        rows.append({"exit_rule": trade["exit_rule"], "gate": trade["gate"], "trade_key": trade["trade_key"], "arm": trade["arm"],
                     "matched": matched, "reason": "target_censored" if trade["censored"] else reason,
                     "control_signal_i": chosen, "control_signal_bar_open": None if chosen is None else frame.index[chosen],
                     "control_net_r": result["net_r"] if matched else math.nan,
                     "control_net_return": result["net_return"] if matched else math.nan,
                     "control_exit_time": None if result is None else result["exit_time"], "month": key[0], "vol_bin": key[1], "fold": key[2]})
    return rows


def _control_parity(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """Compare full baseline control ledgers, preserving thin empty CSV evidence."""
    if old.empty or new.empty:
        passed = old.empty and new.empty
        return {"comparison": "controls", "left": len(old), "right": len(new), "shared": 0,
                "empty_evidence": True, "passed": passed,
                "reason": "both_empty" if passed else "one_control_ledger_is_nonempty"}
    from yoyo.evaluation.spike_v112_execution_report import control_contract
    return {"comparison": "controls", "left": len(old), "right": len(new), **control_contract(old, new)}


def run_pair(symbol: str, base: pd.DataFrame, meta: dict, timeframe: str, source_decisions: pd.DataFrame, reference: dict[int, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Rebuild one stream and replay every specified admission/exit arm."""
    out = _empty()
    if source_decisions.empty:
        return out
    minutes, tick, asset = {"15m": 15, "1h": 60}[timeframe], float(meta["tick"]), meta["asset"]
    bars = v11.bars_for(base, minutes)
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    key = f"binance_um:{symbol}:{timeframe}"
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], key,
        {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": timeframe, "timeframe_min": minutes}, minutes, tick)
    events = []
    for row in source_decisions.sort_values("signal_i").itertuples(index=False):
        i, parent = int(row.signal_i), int(row.box_entry_i)
        if not (0 <= parent <= i < len(frame)) or frame.index[i] != pd.Timestamp(row.signal_bar_open) or not bool(facts["v9_long"][parent]):
            raise ValueError(f"parent decision mismatch {symbol}:{timeframe}:{i}")
        events.append({"signal_i": i, "signal_bar_open": frame.index[i], "symbol": symbol, "asset": asset, "timeframe": timeframe,
                       "source": row.source, "box_entry_i": parent, "bars_after_v9": i - parent, "trade_key": f"{key}:box_any:{i}"})
    features = rsi.chartprime_strong_side(frame, gap=facts["gap"], minutes=minutes)
    cache: dict[tuple[str, int], tuple[str, dict | None]] = {}
    def evaluate(exit_rule: str, i: int):
        k = (exit_rule, i)
        if k not in cache:
            if exit_rule == "price":
                cache[k] = inc.attempt(prepared, i)
            else:
                entry_i = i + 1
                mask = np.zeros(len(frame), dtype=bool) if entry_i >= len(frame) else rsi.entry_rsi_exit_mask(rsi.entry_strong_diamond_counts(features, entry_i), features)
                cache[k] = rsi.attempt_with_rsi_exit(prepared, i, mask)
        return cache[k]
    decisions_by_gate, gate_masks, decision_rows, mask_cache = {}, {}, [], {}
    chart_closes = frame.index + pd.Timedelta(minutes=minutes)
    for gate in GATES:
        if gate == "none":
            gate_masks[gate] = np.ones(len(frame), dtype=bool)
        else:
            family, raw_length = gate.split("_sma")
            key = ({"same": {"15m": 15, "1h": 60}[timeframe], "h1": 60}[family], int(raw_length))
            if key not in mask_cache:
                mask_cache[key] = gate_mask(reference, chart_closes, timeframe, gate)
            gate_masks[gate] = mask_cache[key]
        values = {}
        for event in events:
            close = event["signal_bar_open"] + pd.Timedelta(minutes=minutes)
            value = gate_decision(reference, close, timeframe, gate)
            values[event["signal_i"]] = value
            decision_rows.append({"symbol": symbol, "timeframe": timeframe, "trade_key": event["trade_key"], "signal_i": event["signal_i"], "signal_bar_open": event["signal_bar_open"], "signal_close": close, "gate": gate, **value})
        decisions_by_gate[gate] = values
    trades, statuses, controls = [], [], []
    for exit_rule in EXITS:
        for gate in GATES:
            t, s = serial_candidates(events, exit_rule, gate, decisions_by_gate[gate], evaluate, len(frame))
            trades.extend(t); statuses.extend(s)
            if exit_rule == "price" and gate == "none":
                # This branch deliberately calls the parent implementation so
                # baseline receipt parity covers its original reason taxonomy
                # as well as the sampled bar and its outcome.
                baseline = pd.DataFrame(t, columns=TRADE_COLUMNS).copy()
                baseline["arm"] = "box_any"
                original = study.controls(prepared, baseline, minutes, facts["ready"])
                original["exit_rule"], original["gate"], original["arm"] = "price", "none", "price__none"
                controls.extend(original.reindex(columns=CONTROL_COLUMNS).to_dict("records"))
            else:
                controls.extend(matched_controls(prepared, t, facts["ready"], minutes, gate_masks[gate], evaluate))
    out["trades"] = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    out["statuses"] = pd.DataFrame(statuses, columns=STATUS_COLUMNS)
    out["controls"] = pd.DataFrame(controls, columns=CONTROL_COLUMNS)
    out["decisions"] = pd.DataFrame(decision_rows, columns=DECISION_COLUMNS)
    return out


def _validate_config(config: dict) -> None:
    expected = {"experiment_id": EXP.name, "schema": "spike-joint-btc-gate-v1", "expected_symbols": 638,
                "timeframes": {"15m": 15, "1h": 60}, "gates": list(GATES), "exit_rules": list(EXITS),
                "btc_symbol": "BTCUSDT", "btc_venue": "binance_um", "roundtrip_cost": .002, "arm_r": 2.0,
                "trail_atr": 4.0, "control_seed": study.CONTROL_SEED, "training_eligible": False, "production_eligible": False,
                "source": str(SOURCE), "start": study.START.isoformat().replace("+00:00", "Z"),
                "split": study.SPLIT.isoformat().replace("+00:00", "Z"),
                "end_exclusive": inc.DATA_END.isoformat().replace("+00:00", "Z")}
    for key, value in expected.items():
        if config.get(key) != value: raise ValueError(f"frozen config mismatch {key}")
    spec = study.ExecutionSpec()
    if spec.round_trip_cost != config["roundtrip_cost"] or spec.arm_r != config["arm_r"] or spec.trail_atr != config["trail_atr"]:
        raise ValueError("published execution specification drift")


def _validate_parent(files: dict[str, Path], expected: int) -> tuple[dict, dict, dict[str, str]]:
    manifest, identity = json.loads((SOURCE / "manifest.json").read_text()), json.loads((SOURCE / "identity.json").read_text())
    if not manifest.get("complete") or manifest.get("symbols") != expected or manifest.get("failures"):
        raise ValueError("parent source is not complete")
    parent_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if manifest.get("run_identity") != parent_hash or set(identity.get("inputs", {})) != set(files):
        raise ValueError("parent source identity mismatch")
    receipts = {}
    for symbol in sorted(files):
        _validate_completion(SOURCE / "streams" / symbol, parent_hash, identity["inputs"][symbol])
        receipts[symbol] = study.digest(SOURCE / "streams" / symbol / "completion.json")
    return manifest, identity, receipts


def _identity(files: dict[str, Path], config: dict, source_identity: dict, source_receipts: dict[str, str], btc_path: Path) -> tuple[dict, str]:
    code = _local_transitive_python((Path(__file__), Path(rsi.__file__)))
    declared = (Path(__file__), Path("yoyo/evaluation/spike_joint_btc_study.py"), Path(rsi.__file__),
                Path("tests/evaluation/test_spike_joint_btc_gate.py"), Path("tests/evaluation/test_spike_joint_btc_study.py"), CONFIG, PLAN, *code)
    inputs = {s: study.digest(p) for s, p in sorted(files.items())}
    if any(inputs[s] != source_identity["inputs"].get(s) for s in inputs):
        raise ValueError("requested archive hash differs from the parent identity")
    btc_sha = study.digest(btc_path)
    if btc_sha != source_identity["inputs"].get("BTCUSDT"):
        raise ValueError("BTC archive hash differs from the parent identity")
    exchange_sha = study.digest(study.EXCHANGE_INFO)
    if exchange_sha != source_identity.get("exchange_info", {}).get("sha256"):
        raise ValueError("exchange metadata differs from the parent identity")
    payload = {"schema": config["schema"], "declared": {str(p): study.digest(p) for p in declared},
               "code": {str(p): study.digest(p) for p in code}, "inputs": inputs,
               "btc_input": {"symbol": "BTCUSDT", "path": str(btc_path), "sha256": btc_sha},
               "exchange_info": {"path": str(study.EXCHANGE_INFO), "sha256": exchange_sha},
               "source_identity_sha256": study.digest(SOURCE / "identity.json"), "source_manifest_sha256": study.digest(SOURCE / "manifest.json"),
               "source_identity": source_identity, "source_receipts": source_receipts, "expected_symbols": config["expected_symbols"]}
    return payload, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def validate_receipt(directory: Path, identity_hash: str, input_sha: str) -> dict:
    receipt = json.loads((directory / "completion.json").read_text())
    if receipt.get("status") != "complete" or receipt.get("run_identity") != identity_hash or receipt.get("input_sha256") != input_sha:
        raise ValueError(f"receipt identity mismatch: {directory}")
    if not receipt.get("baseline_parity", {}).get("passed") or not receipt.get("control_parity", {}).get("passed") or not receipt.get("candidate_status_parity"):
        raise ValueError(f"receipt parity failed: {directory}")
    if set(receipt.get("files", {})) != {f"{x}.csv.gz" for x in TABLES}:
        raise ValueError(f"receipt inventory mismatch: {directory}")
    for name, sha in receipt["files"].items():
        if study.digest(directory / name) != sha: raise ValueError(f"receipt file drift: {directory / name}")
    return receipt


def run_symbol(args) -> dict:
    symbol, path_text, meta, output_text, identity_hash, input_sha, btc_path_text, btc_sha = args
    output, final = Path(output_text), Path(output_text) / "streams" / symbol
    if (final / "completion.json").is_file(): return validate_receipt(final, identity_hash, input_sha)
    if final.exists(): raise ValueError(f"incomplete symbol output: {final}")
    if study.digest(Path(path_text)) != input_sha:
        raise ValueError("input archive changed after preflight")
    started = time.perf_counter(); earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = inc.guarded_5m(Path(path_text), earliest)
    reference = _cached_btc_reference(Path(btc_path_text), btc_sha, earliest); source = SOURCE / "streams" / symbol
    source_decisions = pd.read_csv(source / "decisions.csv.gz")
    parts = _empty()
    for tf in ("15m", "1h"):
        got = run_pair(symbol, base, meta, tf, source_decisions.loc[source_decisions.timeframe.eq(tf)], reference)
        for name in TABLES: parts[name] = pd.concat([parts[name], got[name]], ignore_index=True)
    original = pd.read_csv(source / "trades.csv.gz").query("arm == 'box_any'")
    actual = parts["trades"].query("exit_rule == 'price' and gate == 'none'")
    checks = compare(original, actual, [*PARITY, "initial_risk_frac", "gross_return", "net_return", "status"], "baseline")
    old_controls = pd.read_csv(source / "controls.csv.gz").query("arm == 'box_any'")
    new_controls = parts["controls"].query("exit_rule == 'price' and gate == 'none'")
    checks_control = _control_parity(old_controls, new_controls)
    expected = {(row.timeframe, int(row.signal_i)): row.box_any_status for row in source_decisions.itertuples()}
    statuses = parts["statuses"].query("exit_rule == 'price' and gate == 'none'")
    got = {(row.timeframe, int(row.signal_i)): row.status for row in statuses.itertuples()}
    if not checks.get("passed"): raise ValueError(f"baseline trade parity failed: {checks}")
    if not checks_control.get("passed"): raise ValueError(f"baseline control parity failed: {checks_control}")
    if got != expected: raise ValueError("baseline candidate-status parity failed")
    staging = output / "streams" / f".{symbol}.staging"; staging.mkdir(parents=True, exist_ok=True)
    for name, table in parts.items(): table.to_csv(staging / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash, "input_sha256": input_sha,
               "baseline_parity": checks, "control_parity": checks_control, "candidate_status_parity": True,
               "files": {f"{name}.csv.gz": study.digest(staging / f"{name}.csv.gz") for name in TABLES}, "wall_seconds": round(time.perf_counter() - started, 2)}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n"); staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 6, symbols: list[str] | None = None, allow_uncommitted: bool = False) -> None:
    """Run every archived symbol, or an explicit non-reportable smoke subset."""
    config = json.loads(CONFIG.read_text()); _validate_config(config)
    if allow_uncommitted and symbols is None: raise ValueError("--allow-uncommitted needs explicit --symbols")
    files = study.series_files()
    if len(files) != config["expected_symbols"] or "BTCUSDT" not in files: raise ValueError("archive universe changed")
    keys = sorted(files) if symbols is None else list(symbols)
    if set(keys) - set(files): raise ValueError("unknown requested symbol")
    parent_manifest, parent_identity, receipts = _validate_parent(files, config["expected_symbols"])
    btc_path = files["BTCUSDT"]
    identity, identity_hash = _identity({s: files[s] for s in keys}, config, parent_identity, receipts, btc_path)
    if not allow_uncommitted and not _committed(tuple(Path(p) for p in identity["declared"])):
        raise ValueError("commit runner, focused tests, config, plan and imported dependencies before replay")
    output.mkdir(parents=True, exist_ok=True); (output / "streams").mkdir(exist_ok=True)
    identity_path = output / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity: raise ValueError("output identity mismatch")
    if not identity_path.exists(): identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    started_path = output / "evaluation_started.json"
    if not started_path.exists(): started_path.write_text(json.dumps({"run_identity": identity_hash, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "started_at_unix": time.time()}, indent=2) + "\n")
    meta, completed, errors, started = study.symbol_meta(), [], {}, time.perf_counter()
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(run_symbol, (s, str(files[s]), meta[s], str(output), identity_hash, identity["inputs"][s], str(btc_path), identity["btc_input"]["sha256"])): s for s in keys}
        for number, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try: completed.append(future.result())
            except Exception as exc: errors[symbol] = f"{type(exc).__name__}: {exc}"
            if number == 1 or number % 50 == 0 or number == len(keys): print(json.dumps({"completed": number, "target": len(keys), "errors": len(errors)}), flush=True)
    manifest = {"complete": not errors and symbols is None and not allow_uncommitted and len(completed) == config["expected_symbols"], "symbols": len(keys), "completed": len(completed), "errors": errors, "run_identity": identity_hash, "source_manifest_sha256": study.digest(SOURCE / "manifest.json"), "wall_seconds": round(time.perf_counter() - started, 2)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if errors: raise RuntimeError(f"{len(errors)} streams failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--symbols", nargs="*"); parser.add_argument("--allow-uncommitted", action="store_true")
    args = parser.parse_args(); run(args.output, workers=args.workers, symbols=args.symbols, allow_uncommitted=args.allow_uncommitted)

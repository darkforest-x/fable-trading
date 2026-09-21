"""Build the receipt-bound, independent-outcome SPIKE V9-long 10R data set.

This offline builder replays every original V9 long admission independently.
It deliberately does not create a serial portfolio: a signal suppressed by the
serial position state remains a candidate here, and overlapping outcomes must
never be read as portfolio returns.  Entries, initial stops, raw opposite
exits, 2R-close/4ATR trailing protection, censorship, and the fixed 20bp
round-trip cost are delegated to the frozen V9 replay engine.

Per-stream ``features.csv.gz``, ``outcomes.csv.gz``, and ``controls.csv.gz``
are hash-receipted and resumable.  Successful complete builds additionally
write ``candidates.csv.gz`` (one feature/outcome row per event),
``controls.csv.gz``, ``manifest.json``, and ``receipt.json``.  Candidate keys
use ``stream_key:original_signal_ordinal:1``; cache-local indices are retained
only to address authenticated source bars.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation import spike_v9_full_replay as source_replay
from yoyo.evaluation.spike_10r_features import FEATURE_COLUMNS, candidate_features
from yoyo.evaluation.spike_v8_six_filters import _committed, assert_baseline_parity


EXP = Path("experiments/active/exp-spike-10r-discovery-20260921-v3")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST_FEATURES = Path("tests/evaluation/test_spike_10r_features.py")
TEST_DATASET = Path("tests/evaluation/test_spike_10r_dataset.py")
SOURCE_EXP = Path("experiments/active/exp-spike-v9-full-backtest-20260915-v1")
SOURCE_RESULTS = SOURCE_EXP / "results/full_v1"
SOURCE_STATS_RECEIPT = SOURCE_EXP / "statistics/full_v1/statistics_receipt.json"
RAW = base.SOURCE_STREAMS
DEPENDENCIES = (
    Path(__file__), Path("yoyo/evaluation/spike_10r_features.py"), TEST_FEATURES, TEST_DATASET,
    CONFIG, PLAN, Path(source_replay.__file__), Path(engine.__file__), Path(base.__file__),
    Path("yoyo/evaluation/spike_v9.py"), Path("yoyo/evaluation/spike_v7_fast.py"),
    Path("yoyo/evaluation/spike_burst_replay.py"), Path("yoyo/evaluation/spike_v8_replay.py"),
    Path("yoyo/evaluation/spike_v6_wvf_study.py"),
)
OUTCOME_COLUMNS = (
    "event_key", "stream_key", "signal_i", "local_i", "signal_bar_open", "available_at", "entry_i",
    "entry_time", "side", "valid_entry", "censored", "label_gt10", "entry_price", "initial_stop",
    "initial_risk", "initial_risk_frac", "exit_i", "exit_time", "exit_price", "exit_reason",
    "gross_return", "net_return", "gross_r", "net_r", "mfe_r", "exit_time_precision",
)


def digest(path: Path) -> str:
    """Return a SHA-256 content identity without trusting timestamps."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gzip_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})


def _read_config() -> dict[str, Any]:
    config = json.loads(CONFIG.read_text())
    required = {"experiment_id", "expected_streams", "expected_long_candidates", "statistics_receipt_sha256",
                "start", "split", "end", "round_trip_cost", "feature_count"}
    missing = required - set(config)
    if missing:
        raise ValueError(f"10R config missing keys: {sorted(missing)}")
    if float(config["round_trip_cost"]) != 0.002:
        raise ValueError("the frozen V9 engine requires 0.002 round-trip cost")
    if int(config["feature_count"]) != len(FEATURE_COLUMNS):
        raise ValueError("feature schema/config count mismatch")
    if tuple(pd.Timestamp(config[name]) for name in ("start", "split", "end")) != (base.START, base.SPLIT, base.END):
        raise ValueError("10R dates must match the frozen V9 source window")
    spec = base.ExecutionSpec()
    if (base.ENTRY_COST != .001 or base.EXIT_COST != .001 or
            any(getattr(spec, k) != v for k, v in dict(stop_bars=5, stop_buffer_atr=.2,
                risk_floor_atr=2., arm_r=2., trail_atr=4., round_trip_cost=.002).items())):
        raise ValueError("frozen execution/cost contract drift")
    return config


@lru_cache(maxsize=1)
def _source_receipt_pins() -> dict[str, str]:
    """Read the statistics receipt's per-stream source-completion pins once."""
    receipt = json.loads(SOURCE_STATS_RECEIPT.read_text())
    pins = {str(item["key"]): str(item["receipt_sha256"]) for item in receipt.get("source_receipts", [])}
    if len(pins) != int(receipt.get("streams", 0)) or len(pins) != 3531:
        raise ValueError("statistics receipt lacks a complete unique source receipt chain")
    return pins


def _validated_source(key: str, expected_completion_sha256: str) -> dict[str, Any]:
    """Authenticate the archived V9 output before using its parity ledger."""
    folder = SOURCE_RESULTS / "streams" / key
    receipt_path = folder / "completion.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"missing V9 source receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text())
    if digest(receipt_path) != expected_completion_sha256:
        raise ValueError(f"statistics source-receipt pin mismatch: {receipt_path}")
    if receipt.get("status") != "complete" or receipt.get("key") != key:
        raise ValueError(f"invalid V9 source receipt: {receipt_path}")
    for name, expected in receipt.get("files", {}).items():
        path = folder / name
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"V9 source output drift: {path}")
    if "v9.trades.csv.gz" not in receipt.get("files", {}) or "decisions.csv.gz" not in receipt.get("files", {}):
        raise ValueError(f"V9 source is missing required long-ledger evidence: {key}")
    return receipt


def _candidate_table(prepared: engine.PreparedArm) -> tuple[engine.PreparedArm, pd.DataFrame]:
    """Return every in-window original V9 long candidate, never serial-filtered."""
    v9, decisions = source_replay.prepare_v9(prepared)
    rows = decisions.loc[decisions.v9.astype(bool) & decisions.side.eq(1)].copy()
    if rows.signal_i.duplicated().any() or rows.local_i.duplicated().any():
        raise ValueError("duplicate V9 long candidate ordinal")
    rows["available_at"] = pd.to_datetime(rows.signal_bar_open, utc=True) + pd.Timedelta(minutes=v9.context.minutes)
    rows["event_key"] = rows.stream_key.astype(str) + ":" + rows.signal_i.astype(int).astype(str) + ":1"
    if not rows.available_at.ge(base.START).all() or not rows.available_at.lt(base.END).all():
        raise ValueError("candidate escaped frozen entry window")
    return v9, rows


def _assert_candidate_eventset(candidates: pd.DataFrame, archived_decisions: pd.DataFrame) -> None:
    """Bind the rebuilt all-candidate mask to the authenticated V9 decisions."""
    required = {"signal_i", "side", "v9"}
    if not required.issubset(archived_decisions):
        raise ValueError("source decisions lack V9 candidate columns")
    expected = archived_decisions.loc[archived_decisions.v9.astype(bool) & archived_decisions.side.eq(1), "signal_i"].astype(int)
    actual = candidates.signal_i.astype(int)
    if expected.duplicated().any() or actual.duplicated().any() or set(expected) != set(actual):
        raise AssertionError("rebuilt V9-long candidate event set differs from source decisions")


def _invalid_outcome(row: pd.Series, reason: str) -> dict[str, object]:
    result = {name: math.nan for name in OUTCOME_COLUMNS}
    result.update(event_key=row.event_key, stream_key=row.stream_key, signal_i=int(row.signal_i), local_i=int(row.local_i),
                  signal_bar_open=row.signal_bar_open, available_at=row.available_at, entry_i=int(row.signal_i) + 1,
                  entry_time=pd.NaT, side=int(row.side), valid_entry=False, censored=True, label_gt10=pd.NA,
                  exit_reason=reason, exit_time_precision="unavailable")
    return result


def _outcomes(context: base.StreamContext, prepared: engine.PreparedArm, candidates: pd.DataFrame) -> pd.DataFrame:
    """Independently replay each candidate with original ordinal translation."""
    result: list[dict[str, object]] = []
    for candidate in candidates.itertuples(index=False):
        row = pd.Series(candidate._asdict())
        local_i, side = int(row.local_i), int(row.side)
        initial = base._initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                              prepared.close, prepared.atr, prepared.gap, local_i, side, prepared.spec)
        if initial is None:
            result.append(_invalid_outcome(row, "initial_position_unavailable"))
            continue
        entry_time = pd.Timestamp(initial["entry_time"])
        if not (base.START <= entry_time < base.END):
            result.append(_invalid_outcome(row, "entry_outside_frozen_window"))
            continue
        # The engine receives local index coordinates, then publishes source
        # ordinals so its returned exit_i agrees with the frozen serial ledger.
        initial["signal_i"] = int(row.signal_i)
        initial["entry_i"] = int(row.signal_i) + 1
        initial["initial_risk_frac"] = float(initial["initial_risk"]) / float(initial["entry_price"])
        replayed = engine.replay_fixed_entry(context, pd.Series(initial), arm="v8", enable_be=False, prepared=prepared)
        censored = bool(replayed["censored"])
        item = {name: replayed.get(name, math.nan) for name in OUTCOME_COLUMNS}
        item.update(event_key=row.event_key, stream_key=row.stream_key, signal_i=int(row.signal_i), local_i=local_i,
                    signal_bar_open=row.signal_bar_open, available_at=row.available_at, side=side,
                    valid_entry=True, censored=censored,
                    label_gt10=(pd.NA if censored else bool(float(replayed["net_r"]) > 10.0)))
        result.append(item)
    return pd.DataFrame(result, columns=OUTCOME_COLUMNS)


def _assert_common_event_parity(outcomes: pd.DataFrame, archived: pd.DataFrame) -> None:
    """Demand exact economics and exits for independent paths shared with serial V9."""
    source = archived.loc[archived.side.eq(1)].copy()
    if source.signal_i.duplicated().any():
        raise ValueError("ambiguous serial V9 source event")
    fixed = outcomes.loc[outcomes.valid_entry.astype(bool)]
    missing = set(source.signal_i.astype(int)) - set(fixed.signal_i.astype(int))
    if missing:
        raise AssertionError(f"serial V9 events omitted from independent candidates: {len(missing)}")
    common = fixed.merge(source, on="signal_i", suffixes=("_fixed", "_serial"), how="inner", validate="one_to_one")
    if len(common) != len(source):
        raise AssertionError("not every serial V9 long event received parity validation")
    for name in ("entry_i", "side", "censored", "entry_price", "initial_stop", "initial_risk", "exit_i", "exit_price",
                 "gross_return", "net_return", "gross_r", "net_r", "mfe_r"):
        left, right = common[f"{name}_fixed"], common[f"{name}_serial"]
        if pd.api.types.is_bool_dtype(left) or name == "censored":
            equal = left.astype(bool).eq(right.astype(bool))
        else:
            equal = np.isclose(pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce"), rtol=0., atol=1e-10, equal_nan=True)
        if not bool(np.all(equal)):
            raise AssertionError(f"independent/serial V9 parity failed for {name}")
    for name in ("signal_bar_open", "entry_time", "exit_time"):
        left, right = pd.to_datetime(common[f"{name}_fixed"], utc=True), pd.to_datetime(common[f"{name}_serial"], utc=True)
        if not left.equals(right):
            raise AssertionError(f"independent/serial V9 parity failed for {name}")
    if not common.exit_reason_fixed.fillna("").astype(str).eq(common.exit_reason_serial.fillna("").astype(str)).all():
        raise AssertionError("independent/serial V9 parity failed for exit_reason")


def _controls(prepared: engine.PreparedArm, outcomes: pd.DataFrame) -> pd.DataFrame:
    """Persist the source selector's one deterministic draw for every valid event."""
    valid = outcomes.loc[outcomes.valid_entry.astype(bool)].copy()
    if valid.empty:
        return pd.DataFrame(columns=["event_key"])
    targets = valid.assign(arm="v9")
    controls = source_replay.random_controls(prepared, targets)
    controls = controls.merge(valid[["event_key", "signal_i"]], on="signal_i", how="left", validate="one_to_one")
    if controls.event_key.isna().any() or len(controls) != len(valid):
        raise AssertionError("control output does not cover each valid candidate exactly once")
    frame = prepared.frame
    fraction = prepared.atr / prepared.close
    bins = np.searchsorted(source_replay.VOL_BINS, fraction, side="left")
    selected_month, selected_bin, checked = [], [], []
    for item in controls.itertuples(index=False):
        stamp = getattr(item, "control_signal_time")
        if pd.isna(stamp):
            selected_month.append(pd.NA); selected_bin.append(pd.NA); checked.append(True)
            continue
        i = int(frame.index.get_loc(pd.Timestamp(stamp)))
        selected_month.append(frame.index[i].strftime("%Y-%m")); selected_bin.append(int(bins[i]))
        checked.append(str(selected_month[-1]) == str(item.month) and int(selected_bin[-1]) == int(item.vol_bin))
    controls["control_month"] = selected_month
    controls["control_vol_bin"] = selected_bin
    controls["selection_verified"] = checked
    if not controls.selection_verified.astype(bool).all():
        raise AssertionError("persisted control no longer matches source month/volatility bin")
    return controls


def _completed(folder: Path, identity: str) -> dict[str, Any] | None:
    receipt_path = folder / "completion.json"
    if not receipt_path.exists():
        return None
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != "complete" or receipt.get("run_identity") != identity:
        raise ValueError(f"completion identity mismatch: {folder}")
    for name, expected in receipt.get("files", {}).items():
        if digest(folder / name) != expected:
            raise ValueError(f"completed stream output drift: {folder / name}")
    return receipt


def run_stream(args: tuple[str, str, str]) -> dict[str, Any]:
    """Build one receipt-authenticated stream; stale staging is preserved."""
    key, output_text, identity = args
    output = Path(output_text); final = output / "streams" / key
    existing = _completed(final, identity)
    if existing is not None:
        source = _validated_source(key, _source_receipt_pins()[key])
        if (digest(RAW / key / "control_cache.pkl.gz") != source["cache_sha256"] or
                digest(RAW / key / "completion.json") != source["raw_completion_sha256"]):
            raise ValueError("resumed raw source drift")
        return existing
    staging = output / "streams" / f".{key}.staging"
    if staging.exists():
        raise ValueError(f"incomplete stream staging preserved: {staging}")
    staging.mkdir()
    started = time.perf_counter()
    try:
        source_receipt = _validated_source(key, _source_receipt_pins()[key])
        cache_path = RAW / key / "control_cache.pkl.gz"
        raw_cache_before = digest(cache_path)
        if (raw_cache_before != source_receipt["cache_sha256"] or
                digest(RAW / key / "completion.json") != source_receipt["raw_completion_sha256"]):
            raise ValueError("raw source chain differs before loading cache")
        context = base.load_verified_stream(RAW / key)
        if source_receipt.get("raw_completion_sha256") != digest(context.path / "completion.json"):
            raise ValueError(f"source/raw completion chain mismatch: {key}")
        if source_receipt.get("cache_sha256") != context.receipt.get("cache_sha256"):
            raise ValueError(f"source/raw cache chain mismatch: {key}")
        prepared = engine.prepare_arm(context, arm="v8")
        v9, candidates = _candidate_table(prepared)
        features = candidate_features(v9, candidates)
        outcomes = _outcomes(context, v9, candidates)
        archived = pd.read_csv(SOURCE_RESULTS / "streams" / key / "v9.trades.csv.gz")
        archived_decisions = pd.read_csv(SOURCE_RESULTS / "streams" / key / "decisions.csv.gz")
        _assert_candidate_eventset(candidates, archived_decisions)
        # Replaying serial V9 first independently proves the archived ledger is
        # still the same state machine before fixed-event comparisons are made.
        serial, _, _ = engine.replay_serial(context, arm="v8", enable_be=False, prepared=v9)
        assert_baseline_parity(serial, archived)
        _assert_common_event_parity(outcomes, archived)
        controls = _controls(v9, outcomes)
        raw_cache_after = digest(cache_path)
        if raw_cache_before != raw_cache_after or raw_cache_before != str(source_receipt["cache_sha256"]):
            raise ValueError(f"raw cache changed or differs from V9 source receipt: {key}")
        _gzip_csv(features, staging / "features.csv.gz")
        _gzip_csv(outcomes, staging / "outcomes.csv.gz")
        _gzip_csv(controls, staging / "controls.csv.gz")
        valid = outcomes.valid_entry.astype(bool)
        receipt = {
            "status": "complete", "stream_key": key, "run_identity": identity,
            "source_completion_sha256": digest(SOURCE_RESULTS / "streams" / key / "completion.json"),
            "raw_completion_sha256": digest(context.path / "completion.json"), "cache_sha256": context.receipt["cache_sha256"],
            "raw_cache_sha256_before": raw_cache_before, "raw_cache_sha256_after": raw_cache_after,
            "source_serial_parity": True, "common_fixed_event_parity": True,
            "candidates": int(len(candidates)), "valid_entries": int(valid.sum()),
            "invalid_entries": int((~valid).sum()), "censored": int(outcomes.censored.astype(bool).sum()),
            "controls": int(len(controls)), "files": {p.name: digest(p) for p in staging.glob("*.csv.gz")},
            "wall_seconds": time.perf_counter() - started,
        }
        (staging / "completion.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        staging.replace(final)
        return receipt
    except Exception as exc:
        (staging / "failure.json").write_text(json.dumps({"stream_key": key, "type": type(exc).__name__, "error": str(exc)}, indent=2) + "\n")
        (output / "failures" / f"{key}.{time.time_ns()}").parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), output / "failures" / f"{key}.{time.time_ns()}")
        raise


def _identity(config: dict[str, Any], raw_before: str) -> dict[str, str]:
    return {str(path): digest(path) for path in DEPENDENCIES} | {
        "source_manifest_sha256": digest(SOURCE_RESULTS / "manifest.json"),
        "raw_manifest_sha256": raw_before, "statistics_receipt_sha256": digest(SOURCE_STATS_RECEIPT),
        "configured_statistics_receipt_sha256": str(config["statistics_receipt_sha256"]),
    }


def _aggregate(output: Path, keys: list[str], identity_hash: str, config: dict[str, Any], raw_before: str) -> None:
    receipts = [_completed(output / "streams" / key, identity_hash) for key in keys]
    if any(item is None for item in receipts):
        raise ValueError("cannot aggregate incomplete streams")
    features = pd.concat([pd.read_csv(output / "streams" / key / "features.csv.gz") for key in keys], ignore_index=True)
    outcomes = pd.concat([pd.read_csv(output / "streams" / key / "outcomes.csv.gz") for key in keys], ignore_index=True)
    controls = pd.concat([pd.read_csv(output / "streams" / key / "controls.csv.gz") for key in keys], ignore_index=True)
    repeated = [name for name in outcomes.columns if name in features.columns and name != "event_key"]
    candidates = features.merge(outcomes.drop(columns=repeated), on="event_key", how="inner", validate="one_to_one")
    # Outcomes deliberately repeat candidate metadata; only one event may own it.
    if len(candidates) != len(features) or len(candidates) != len(outcomes):
        raise AssertionError("feature/outcome aggregation lost a candidate")
    if len(candidates) != int(config["expected_long_candidates"]):
        raise AssertionError(f"candidate count {len(candidates)} != expected {config['expected_long_candidates']}")
    if candidates.event_key.duplicated().any() or candidates[["stream_key", "signal_i"]].duplicated().any():
        raise AssertionError("aggregate candidate key collision")
    if len(receipts) != int(config["expected_streams"]) or {str(item["stream_key"]) for item in receipts if item} != set(keys):
        raise AssertionError("aggregate completion coverage mismatch")
    valid = candidates.valid_entry.astype(bool)
    if len(controls) != int(valid.sum()) or controls.event_key.duplicated().any():
        raise AssertionError("aggregate controls do not cover valid events once")
    raw_after = digest(RAW.parent / "manifest.json")
    if raw_after != raw_before:
        raise ValueError("raw source manifest changed during build")
    _gzip_csv(candidates, output / "candidates.csv.gz")
    _gzip_csv(controls, output / "controls.csv.gz")
    manifest = {
        "complete": True, "experiment_id": config["experiment_id"], "run_identity": identity_hash,
        "streams": len(keys), "expected_streams": int(config["expected_streams"]), "candidates": len(candidates),
        "expected_long_candidates": int(config["expected_long_candidates"]), "valid_entries": int(valid.sum()),
        "invalid_entries": int((~valid).sum()), "censored": int(candidates.censored.astype(bool).sum()),
        "strict_label": "valid and uncensored realized net_r > 10", "feature_columns": list(FEATURE_COLUMNS),
        "statistics_receipt_sha256": str(config["statistics_receipt_sha256"]),
        "raw_manifest_sha256_before": raw_before, "raw_manifest_sha256_after": raw_after,
        "stream_completion_sha256": {key: digest(output / "streams" / key / "completion.json") for key in keys},
        "candidate_sha256": digest(output / "candidates.csv.gz"), "controls_sha256": digest(output / "controls.csv.gz"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    receipt = {"status": "complete", "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "manifest_sha256": digest(output / "manifest.json"), "files": {
        "candidates.csv.gz": manifest["candidate_sha256"], "controls.csv.gz": manifest["controls_sha256"],
        "manifest.json": digest(output / "manifest.json"), "identity.json": digest(output / "identity.json"),
    }}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def run(output: Path, *, workers: int = 3) -> None:
    """Build all 3,531 source streams or fail without an aggregate receipt."""
    config = _read_config()
    if not _committed(DEPENDENCIES):
        raise ValueError("commit unchanged 10R builder/tests/config/plan/dependencies before market replay")
    if digest(SOURCE_STATS_RECEIPT) != str(config["statistics_receipt_sha256"]):
        raise ValueError("configured V9 statistics receipt does not authenticate source")
    raw_before = digest(RAW.parent / "manifest.json")
    source_pins = _source_receipt_pins()
    source_keys = sorted(source_pins)
    raw_keys = sorted(p.name for p in RAW.iterdir() if (p / "completion.json").is_file())
    if source_keys != raw_keys or len(source_keys) != int(config["expected_streams"]):
        raise ValueError("V9 source/raw stream coverage changed")
    identity = _identity(config, raw_before)
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("streams", "failures"):
        (output / name).mkdir(exist_ok=True)
    identity_path = output / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("output identity drift; choose a new output directory")
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=max(1, min(int(workers), 4))) as pool:
        futures = {pool.submit(run_stream, (key, str(output), identity_hash)): key for key in source_keys}
        for number, future in enumerate(as_completed(futures), 1):
            receipt = future.result()
            if number == 1 or number % 250 == 0 or number == len(futures):
                print(json.dumps({"completed": number, "target": len(futures), "stream_key": receipt["stream_key"],
                                  "elapsed_seconds": round(time.perf_counter() - started, 2)}), flush=True)
    _aggregate(output, source_keys, identity_hash, config, raw_before)


def load_verified_aggregate(output: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load only a complete aggregate whose receipt and content hashes agree."""
    receipt_path = output / "receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"missing aggregate receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("status") != "complete":
        raise ValueError("aggregate is not complete")
    for name, expected in receipt.get("files", {}).items():
        if digest(output / name) != expected:
            raise ValueError(f"aggregate output drift: {output / name}")
    manifest = json.loads((output / "manifest.json").read_text())
    if not manifest.get("complete") or digest(output / "manifest.json") != receipt.get("manifest_sha256"):
        raise ValueError("aggregate manifest mismatch")
    return pd.read_csv(output / "candidates.csv.gz"), pd.read_csv(output / "controls.csv.gz")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parsed = parser.parse_args()
    run(parsed.output, workers=parsed.workers)

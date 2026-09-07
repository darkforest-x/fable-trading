"""Source-first V25 saved-hourly event SUPPORT audit, never an outcome reader.

Only SHA-frozen V24 identities/allocations, V4 mother identities, V20 saved
complete-hour features and provenance metadata are read. UTC-hour timestamps
are preflighted before materializing saved OHLC. No raw archive, labels,
statistics, future markouts, new random allocation or economic evaluation.
The original frozen reference replays only past/current OHLC per trace row.

Official pandas 2.3.3 usecols contract:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_structure import HOURLY_STRUCTURE_COLUMNS
from yoyo.evaluation.hourly_impulse_structure_event_support import (
    DEFAULT_FOLDS, _time, build_structure_event_support,
)

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-structure-event-support-preholdout-20260907-v25"
EXPERIMENT = Path("experiments/active") / EXPERIMENT_ID
V20 = Path("experiments/active/exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20/results")
V24 = Path("experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results")
MOTHERS = Path("experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz")
INPUTS = {
    str(V20 / "started.json"): "143ccd389bad92fa3c77cf61488d26e963965094593eba34d44aa7703dbeee5f",
    str(V20 / "context_frozen.json"): "6fc551bd388c0ea44bbe675227e1f3bd105177e577027d245fd4f45594b42504",
    str(V20 / "hourly_trace.csv.gz"): "be9b9e73108047ada00ffac0ed0c4d5b2a3c137000435872c2d33c4ec1cbf7dd",
    str(V20 / "failure.json"): "0170fb54b1d9e08ff49f9d2728d18ee43437c86a875ba5eb5a8efd33d6898cb5",
    str(V20 / "outcomes_started.json"): "fbfebd064cea51c872b3b6cbdc81173e51b2d9b41c5f7aa6b136866ff1fb2dfc",
    str(V20 / "outcomes_resumed_1.json"): "a275e4cb883c33ed0e8a90133c4a3ffa56143eaed4932bbeb3132a5126d8302a",
    str(V24 / "started.json"): "bc501918b0c87647b5bdb53d9e5a3eb2096a3b1c4ea2fd4504a18a807a9293ab",
    str(V24 / "sampling_frozen.json"): "22d5a3c91dbaed277a68a5fab6cf54df17b04663b80244f5e5da4d90f591dc64",
    str(V24 / "case_requests.csv.gz"): "0444142d5d99f6013bcbf9ff160d307818316130177e3e2d48fad7abf38cbe27",
    str(V24 / "control_requests.csv.gz"): "327c655fb1f989467d4ccc5ea4c9f6293a7a976e594ad853e7ebb2cbc8ef2a71",
    str(V24 / "random_assignments.csv.gz"): "2d308c1bfc59b1c4364269acfd3fee9a86b6b6609c69bc803fa0a495b7aebdc7",
    str(V24 / "random_allocation.csv.gz"): "0d7a6dc560a9193b792796025a69405af055a1b1d1aa51aa59223fc16e491629",
    str(MOTHERS): "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
}
FEATURES = {
    "yoyo/data/hourly_impulse.py": "f1128f514456f0e1a7bf89b719b2333ebe3b12f66402d58c82764d355be97a3b",
    "yoyo/data/hourly_impulse_structure.py": "84124cbecc1d31c00e22aa4c80b41741461f2f1fc2f17ccab5b0b838e9743128",
}
SOURCES = ["yoyo/evaluation/hourly_impulse_structure_event_research.py",
           "yoyo/evaluation/hourly_impulse_structure_event_support.py",
           "tests/test_hourly_impulse_structure_event_research.py",
           "tests/test_hourly_impulse_structure_event_support.py",
           *FEATURES, str(EXPERIMENT / "PROJECT_PLAN.md"), str(EXPERIMENT / "config.json")]
IDENTITY = ["event_id", "signal_time", "decision_time", "direction", "fold"]
TRACE_COLUMNS = ["open_time", "open", "high", "low", "close", *HOURLY_STRUCTURE_COLUMNS]


def frozen_config():
    return {"experiment_id": EXPERIMENT_ID, "inputs": INPUTS, "feature_sources": FEATURES,
            "gate": "known_and_current_first_establishing_or_reversing_directional_break",
            "folds": [list(v) for v in DEFAULT_FOLDS], "embargo_hours": 72,
            "population": {"cases": 251, "controls": 744, "matched": 248, "unmatched": 3},
            "support": {"minimum_events": 80, "minimum_per_fold": 12,
                        "minimum_active_months": 12, "minimum_months_per_fold": 3},
            "trace_rows": 18222, "trace_start": "2022-11-30T16:00:00Z",
            "trace_end": "2024-12-28T22:00:00Z", "phase_end_exclusive": "2025-01-01T00:00:00Z",
            "outcomes_read_or_computed": False, "economic_acceptance": False,
            "holdout_consumed": False, "training_eligible": False, "production_eligible": False}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    def default(item):
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, (Path, pd.Timestamp)):
            return str(item)
        raise TypeError(type(item).__name__)
    Path(path).write_text(json.dumps(value, default=default, ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n")


def _git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def committed_sources(root):
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    receipts = []
    for path in SOURCES:
        sha = hashlib.sha256(_git(root, "show", commit + ":" + path)).hexdigest()
        if digest(root / path) != sha:
            raise ValueError("Uncommitted source: " + path)
        receipts.append({"path": path, "sha256": sha})
    return commit, receipts


def _stamp(value):
    time = pd.Timestamp(value)
    if not isinstance(value, str) or pd.isna(time) or time.tzinfo is None or time.utcoffset().total_seconds() != 0:
        raise ValueError("Metadata requires explicit UTC")
    return time


def _source_receipt(root, started):
    commit = started["builder_commit"]
    commit_time = pd.Timestamp(_git(root, "show", "-s", "--format=%cI", commit).decode().strip())
    if commit_time > _stamp(started["at"]):
        raise ValueError("Parent builder followed materialization")
    for source in started["sources"]:
        actual = hashlib.sha256(_git(root, "show", commit + ":" + source["path"])).hexdigest()
        if actual != source["sha256"]:
            raise ValueError("Parent source commit mismatch: " + source["path"])
    return len(started["sources"])


def verify_inputs(root):
    """Hash locked inputs; inspect checkpoints only, not their economic outputs."""
    for path, sha in {**INPUTS, **FEATURES}.items():
        if digest(root / path) != sha:
            raise ValueError("Input/source SHA mismatch: " + path)
    def read(parent, name):
        return json.loads((root / parent / name).read_text())
    old, freeze = read(V20, "started.json"), read(V20, "context_frozen.json")
    first, failure, resumed = [read(V20, n) for n in
                               ("outcomes_started.json", "failure.json", "outcomes_resumed_1.json")]
    times = [_stamp(m["at"]) for m in (old, freeze, first, failure, resumed)]
    if times != sorted(times) or freeze["outcomes_read"] is not False:
        raise ValueError("V20 feature checkpoint chronology failed")
    if failure["status"] != "failed_not_evidence" or failure["error_type"] != "AssertionError":
        raise ValueError("V20 historical failure identity changed")
    for marker in (first, resumed):
        if marker["context_frozen_sha256"] != INPUTS[str(V20 / "context_frozen.json")]:
            raise ValueError("V20 recovery does not reference original frozen features")
    if freeze["output_hashes"]["hourly_trace.csv.gz"] != INPUTS[str(V20 / "hourly_trace.csv.gz")]:
        raise ValueError("V20 trace not frozen")
    receipt = freeze["source_receipt"]
    if receipt["holdout_price_rows"] != 0 or _stamp(receipt["phase_price_last_open"]) >= _time(frozen_config()["phase_end_exclusive"]):
        raise ValueError("V20 feature phase exceeds pre2025")
    new, sampling = read(V24, "started.json"), read(V24, "sampling_frozen.json")
    if _stamp(new["at"]) > _stamp(sampling["at"]) or new["sources"] != sampling["sources"]:
        raise ValueError("V24 sampling chronology/source mismatch")
    if not sampling["before_any_label"] or not sampling["before_any_raw_read"]:
        raise ValueError("V24 sampling was not frozen first")
    expected = {"mothers": 251, "matched_mothers": 248, "controls": 744, "unmatched_mothers": 3,
                "seed": 20260907, "streams": 1, "bit_generator": "PCG64", "no_reuse": True,
                "fallback_used": False, "outcomes_used": False, "selection_frozen_before_labels": True}
    if any(sampling["sampling"].get(key) != value for key, value in expected.items()):
        raise ValueError("V24 frozen allocation contract mismatch")
    for name in ("case_requests.csv.gz", "control_requests.csv.gz", "random_assignments.csv.gz", "random_allocation.csv.gz"):
        if sampling["output_hashes"].get(name) != INPUTS[str(V24 / name)]:
            raise ValueError("V24 membership file not frozen: " + name)
    return {"inputs": INPUTS, "parent_commits": [old["builder_commit"], new["builder_commit"]],
            "parent_sources_verified": [_source_receipt(root, old), _source_receipt(root, new)],
            "v20_failure_after_feature_freeze": True, "v20_recovery_same_feature_sha": True,
            "raw5_read": False, "outcomes_read": False}


def preflight_trace(frame, config):
    times = pd.DatetimeIndex([_time(v) for v in frame.open_time])
    if not times.is_unique or not times.is_monotonic_increasing or len(times) != config["trace_rows"]:
        raise ValueError("Saved trace clock count/order/uniqueness failed")
    if len(times) == 0 or times[0] != _time(config["trace_start"]) or times[-1] != _time(config["trace_end"]) or times[-1] >= _time(config["phase_end_exclusive"]):
        raise ValueError("Saved trace date phase failed")
    return times


def validate_requests(cases, controls, original):
    if len(cases) != 251 or len(controls) != 744 or len(original) != 251:
        raise ValueError("Original population changed")
    a, b = cases[IDENTITY].sort_values("event_id").reset_index(drop=True), original[IDENTITY].sort_values("event_id").reset_index(drop=True)
    for frame in (a, b):
        for column in ("signal_time", "decision_time"):
            frame[column] = frame[column].map(_time)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    if cases.groupby("fold").size().to_dict() != {"2023H1": 55, "2023H2": 66, "2024H1": 55, "2024H2": 75}:
        raise ValueError("Original fold population changed")
    bounds = {f: (_time(s), _time(e) - pd.Timedelta(hours=72)) for f, s, e in DEFAULT_FOLDS}
    for frame in (cases, controls):
        for row in frame.itertuples():
            time, decision = _time(row.signal_time), _time(row.decision_time)
            if row.fold not in bounds or not bounds[row.fold][0] <= decision < bounds[row.fold][1] or decision != time + pd.Timedelta(hours=1):
                raise ValueError("Request violates own clock or 72h fold embargo")


def run(root=ROOT):
    root = Path(root)
    config = json.loads((root / EXPERIMENT / "config.json").read_text())
    if config != frozen_config():
        raise ValueError("Frozen V25 configuration changed")
    commit, sources = committed_sources(root)
    directory = root / EXPERIMENT / "results"
    directory.mkdir(exist_ok=False)
    write_json(directory / "started.json", {"at": pd.Timestamp.now(tz="UTC"), "builder_commit": commit,
               "sources": sources, "inputs": INPUTS, "config_sha256": digest(root / EXPERIMENT / "config.json")})
    try:
        receipt = verify_inputs(root)
        trace_path = root / V20 / "hourly_trace.csv.gz"
        times = preflight_trace(pd.read_csv(trace_path, usecols=["open_time"]), config)
        trace = pd.read_csv(trace_path, usecols=TRACE_COLUMNS)
        if not pd.DatetimeIndex([_time(v) for v in trace.open_time]).equals(times):
            raise ValueError("Trace clocks changed after timestamp preflight")
        cases, controls, assignments, allocation = [pd.read_csv(root / V24 / name) for name in
              ("case_requests.csv.gz", "control_requests.csv.gz", "random_assignments.csv.gz", "random_allocation.csv.gz")]
        original = pd.read_csv(root / MOTHERS, usecols=IDENTITY)
        validate_requests(cases, controls, original)
        tables, summary = build_structure_event_support(cases, controls, trace, assignments, allocation)
        # A saved-file/source mutation during replay must not inherit old pins.
        if verify_inputs(root) != receipt or committed_sources(root)[1] != sources:
            raise ValueError("Inputs or source receipts changed during support computation")
        hashes = {}
        for name, frame in tables.items():
            path = directory / (name + ".csv.gz")
            frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
            hashes[path.name] = digest(path)
        frozen = {"at": pd.Timestamp.now(tz="UTC"), "builder_commit": commit, "sources": sources,
                  "input_receipt": receipt, "output_hashes": hashes, "timestamp_preflight_before_prices": True,
                  "outcomes_read_or_computed": False, "raw5_read": False, "holdout_consumed": False}
        write_json(directory / "support_frozen.json", frozen)
        summary.update(generated_at=str(pd.Timestamp.now(tz="UTC")), experiment_id=EXPERIMENT_ID,
                       builder_commit=commit, output_hashes=hashes,
                       support_frozen_sha256=digest(directory / "support_frozen.json"))
        write_json(directory / "summary.json", summary)
        return summary
    except Exception as error:
        write_json(directory / "failure.json", {"at": pd.Timestamp.now(tz="UTC"),
                   "status": "failed_not_evidence", "error_type": type(error).__name__, "message": str(error)})
        raise


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

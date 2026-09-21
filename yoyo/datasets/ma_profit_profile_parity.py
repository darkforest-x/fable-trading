"""Compare the frozen profile extractor and its local-window adapter exactly.

Read only explicitly selected, hash-pinned existing scan evidence and source
OHLCV. Every profile receives the same already-enriched causal prefix through
its confirmation bar. This is an implementation parity audit, not a change to
the event population, morphology thresholds, profit labels, or training data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_miner as miner
from yoyo.datasets.ma_profit_profile_window import extract_profile_window


ROOT = miner.ROOT
ADAPTER = ROOT / "yoyo/datasets/ma_profit_profile_window.py"


def audit(selection_path: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite parity audit: {output}")
    selection = json.loads(selection_path.read_text())
    frozen = [Path(__file__), ADAPTER, Path(miner.__file__), *miner.RULE_DEPENDENCY_PATHS, selection_path,
              *(miner._repo_path(row[key]) for row in selection["sources"] for key in ("manifest_path", "master_path"))]
    names = [miner._relative(path) for path in frozen]
    subprocess.check_output(["git", "ls-files", "--error-unmatch", "--", *names], cwd=ROOT, text=True)
    commit = miner._assert_committed(frozen)
    results, exact, mismatches = [], 0, 0
    for spec in selection["sources"]:
        for prefix in ("manifest", "master"):
            if miner.sha256_file(miner._repo_path(spec[prefix + "_path"])) != spec[prefix + "_sha256"]:
                raise ValueError("parity frozen manifest/master SHA drift")
        source, candidates = miner._repo_path(spec["source_path"]), miner._repo_path(spec["candidates_path"])
        if miner.sha256_file(source) != spec["source_sha256"] or miner.sha256_file(candidates) != spec["candidates_sha256"]:
            raise ValueError("parity source/candidate SHA drift")
        rows = miner._read_jsonl(candidates)
        chosen = [rows[index] for index in spec["candidate_indices"]]
        cutoff = max(pd.Timestamp(row["confirmation_close_utc"]) for row in chosen) + pd.Timedelta(nanoseconds=1)
        raw, _ = miner.read_preholdout_prefix(source, end_exclusive=cutoff, bar_minutes=int(spec["bar_minutes"]))
        frame = miner.add_candidate_features(raw)
        original_seconds, adapted_seconds = 0.0, 0.0
        source_results = []
        for row in chosen:
            visible = frame.iloc[:int(row["confirm_i"]) + 1]
            options = {"bar_minutes": int(spec["bar_minutes"]),
                       "visibility_end_exclusive": pd.Timestamp(row["confirmation_close_utc"]) + pd.Timedelta(nanoseconds=1)}
            tick = time.perf_counter()
            try:
                old = miner.extract_profile(visible, row, **options)
                old_error = None
            except Exception as exc:
                old, old_error = None, (type(exc).__name__, str(exc))
            original_seconds += time.perf_counter() - tick
            tick = time.perf_counter()
            try:
                new = extract_profile_window(visible, row, **options)
                new_error = None
            except Exception as exc:
                new, new_error = None, (type(exc).__name__, str(exc))
            adapted_seconds += time.perf_counter() - tick
            equal = old_error == new_error
            if old_error is None and new_error is None:
                equal = (old.metrics == new.metrics and np.array_equal(old.sequence, new.sequence)
                         and old.core_start_i == new.core_start_i and old.core_end_i == new.core_end_i)
                exact += int(equal)
            mismatches += int(not equal)
            source_results.append({"profile_key": miner._profile_key(row), "equal": bool(equal),
                                   "original_error": old_error, "adapter_error": new_error,
                                   "core_bars": int(row["source_core_end_i"]) - int(row["source_core_start_i"]) + 1,
                                   "direction": row["direction"], "confirmation_index": int(row["confirm_i"])})
        results.append({**spec, "tested_profiles": len(chosen), "loaded_rows": len(frame),
                        "original_profile_seconds": original_seconds, "adapter_profile_seconds": adapted_seconds,
                        "results": source_results})
        print(f"profile parity {spec['symbol']} {spec['bar_minutes']}m: {len(chosen)} checked", flush=True)
    receipt = {"status": "passed" if not mismatches and exact >= 20 else "failed", "builder_commit": commit,
               "builder_sha256": miner.sha256_file(Path(__file__)),
               "selection_sha256": miner.sha256_file(selection_path), "adapter_sha256": miner.sha256_file(ADAPTER),
               "miner_sha256": miner.sha256_file(Path(miner.__file__)),
               "rule_dependency_sha256": miner._rule_dependency_hashes(),
               "exact_profiles": exact, "mismatches": mismatches, "sources": results,
               "timing_scope": "profile function calls only; excludes reading, feature calculation, weak discovery and scoring",
               "training_eligible": False, "production_eligible": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    miner._write_json(output, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.selection.resolve(), args.out.resolve())
    print(json.dumps({key: result[key] for key in ("status", "exact_profiles", "mismatches")}))


if __name__ == "__main__":
    main()

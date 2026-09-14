"""Compare timestamped indicator events with frozen effective human rectangles.

No outcomes/returns are read. Source access is delegated to the bounded bridge;
the development/later phase filter runs before any OHLC materialization.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.gold_ma_candidate import replay
from yoyo.evaluation.owner_gold_indicator_bridge import DEFAULT_ANSWERS, DEFAULT_MANIFEST, load_cases, load_source

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CUT = pd.Timestamp("2025-07-01T00:00:00Z")
SOURCES = ["yoyo/evaluation/gold_ma_candidate.py", "yoyo/evaluation/ma_drift_v1_reference.py",
           "yoyo/evaluation/owner_gold_indicator_bridge.py", str(Path(__file__).relative_to(ROOT))]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_committed():
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise RuntimeError("Shared main is required")
    for name in SOURCES:
        if subprocess.check_output(["git", "show", "HEAD:"+name], cwd=ROOT) != (ROOT/name).read_bytes():
            raise RuntimeError("Commit replay source before producing evidence: "+name)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def measure(case, events, columns):
    left, right = case["main_start_i"], case["main_end_i"]
    any_events = sorted(set(int(i) for name in columns for i in np.flatnonzero(events[name].to_numpy(bool)) if left <= i <= right))
    result = {"displayed_events_in_window": any_events, "window_event_count": len(any_events)}
    start, end = case.get("core_start_i"), case.get("core_end_i")
    if start is None:
        result["false_positive_on_owner_no_target"] = bool(any_events)
        return result
    inside = [i for i in any_events if start <= i <= end]
    later = [i for i in any_events if end < i <= min(right, end+5)]
    result.update(hit_inside=bool(inside), first_inside=inside[0] if inside else None,
                  first_late_delay=later[0]-end if later else None,
                  late_bars_observable=min(5, right-end))
    # Same-window boundary randomization, NOT newly labeled negative samples.
    width = end-start+1
    other_starts = [s for s in range(left, right-width+2) if s+width-1 < start or s > end]
    null_hits = [any(s <= i < s+width for i in any_events) for s in other_starts]
    result["disjoint_boundary_null_hit_rate"] = float(np.mean(null_hits)) if null_hits else None
    result["disjoint_boundary_null_positions"] = len(other_starts)
    return result


def run(phase, signals="marker", output_root=None, verify_against=None):
    commit = check_committed()
    input_sha = {str(p.relative_to(ROOT)): digest(p) for p in (DEFAULT_ANSWERS, DEFAULT_MANIFEST)}
    cases, excluded = load_cases()
    selected, reserved = [], []
    for case in cases:
        start = pd.Timestamp(case["main_start_time"])
        end = pd.Timestamp(case["main_end_time"]) + pd.Timedelta(minutes=15)
        part = "dev" if end <= CUT else "later" if start >= CUT else "cross_cut"
        (selected if part == phase else reserved).append(case)
    groups = defaultdict(list)
    for case in selected:
        groups[case["source_path"]].append(case)
    results, audits, failures = [], [], []
    expected_audits = None
    if verify_against is not None:
        expected_audits = {r["source_path"]: r for r in json.loads(Path(verify_against).read_text())}
    for source_number, (source, group) in enumerate(sorted(groups.items()), 1):
        try:
            frame, audit = load_source(group)
            if expected_audits is not None:
                expected = expected_audits.get(audit["source_path"])
                if expected is None or any(audit[k] != expected[k] for k in ("bounded_prefix_sha256", "end_exclusive", "rows_materialized")):
                    raise RuntimeError("Frozen source prefix identity changed before replay: "+source)
            events = replay(frame, bar_minutes=15)
            audits.append(audit)
            for case in group:
                item = dict(case)
                if case.get("core_start_i") is None:
                    old_cols = ["warning" if signals == "marker" else "confirmation"]
                    new_cols = [f"a_long_{signals}", f"a_short_{signals}"]
                else:
                    # V1 has no long branch; empty list is a structural miss.
                    old_cols = ["warning" if signals == "marker" else "confirmation"] if case["side"].lower() == "short" else []
                    new_cols = ["a_"+case["side"].lower()+"_"+signals]
                item["v1"] = measure(case, events, old_cols)
                item["candidate_a"] = measure(case, events, new_cols)
                if case.get("core_start_i") is not None:
                    core = events.iloc[case["core_start_i"]:case["core_end_i"]+1]
                    names = ["ready", "a_compact", "a_contact", "a_topology", "a_near", "a_range"]
                    names += [f"a_{case['side'].lower()}_{gate}" for gate in ("slope", "progress", "outside")]
                    item["candidate_a_predicate_support_in_core"] = {name: int(core[name].sum()) for name in names}
                    item["candidate_a_setup_bars_in_core"] = int(core[f"a_{case['side'].lower()}_setup"].sum())
                results.append(item)
            if source_number % 20 == 0:
                print(json.dumps({"completed_sources": source_number, "total_sources": len(groups)}, ensure_ascii=False), flush=True)
        except Exception as error:
            failures.extend({"task_id": c["task_id"], "review_id": c["review_id"], "source_path": source,
                             "reason": type(error).__name__+": "+str(error)} for c in group)
    positive = [r for r in results if r.get("core_start_i") is not None]
    negative = [r for r in results if r.get("core_start_i") is None]
    metrics = {}
    for model in ("v1", "candidate_a"):
        inside = sum(r[model]["hit_inside"] for r in positive)
        one3 = sum(not r[model]["hit_inside"] and r[model]["first_late_delay"] in (1, 2, 3) for r in positive)
        four5 = sum(not r[model]["hit_inside"] and r[model]["first_late_delay"] in (4, 5) for r in positive)
        null = [r[model]["disjoint_boundary_null_hit_rate"] for r in positive if r[model]["disjoint_boundary_null_hit_rate"] is not None]
        metrics[model] = {"positive_n": len(positive), "inside": inside, "late_1_3_only": one3,
            "late_4_5_only": four5, "no_inside_or_late_through_5": len(positive)-inside-one3-four5,
            "inside_recall": inside/len(positive) if positive else None,
            "negative_n": len(negative), "negative_false_positive": sum(r[model]["false_positive_on_owner_no_target"] for r in negative),
            "null_mean_hit_rate": float(np.mean(null)) if null else None,
            "side_inside": {side: {"n": sum(r["side"].lower() == side for r in positive),
                "hit": sum(r["side"].lower() == side and r[model]["hit_inside"] for r in positive)} for side in ("long", "short")}}
    output = (Path(output_root) if output_root else HERE/"results")/(phase if signals == "marker" else phase+"_confirmation")
    output.mkdir(parents=True, exist_ok=True)
    if (output/"summary.json").exists():
        raise RuntimeError("Frozen result exists; use a new version rather than overwrite")
    if any(digest(ROOT/name) != value for name, value in input_sha.items()):
        raise RuntimeError("Annotation metadata changed during replay")
    summary = {"phase": phase, "created_at": datetime.now(timezone.utc).isoformat(), "source_commit": commit,
        "signal_kind": signals,
        "input_sha256": input_sha,
        "source_sha256": {name: digest(ROOT/name) for name in SOURCES},
        "metadata_accepted": len(cases), "selected": len(selected), "reserved": len(reserved),
        "source_success_cases": len(results), "source_failures": failures, "metadata_exclusions": excluded,
        "positive_n": len(positive), "negative_n": len(negative), "metrics": metrics,
        "new_training": False, "holdout_read": False, "production_eligible": False,
        "time_cut": CUT.isoformat(), "note": "Human review references, not an admission as new Gold; no outcomes evaluated."}
    for name, value in (("summary.json", summary), ("source_audits.json", audits)):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+"\n")
    (output/"cases.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, default=str)+"\n" for r in results))
    print(json.dumps({"output": str(output), "metrics": metrics, "failures": len(failures)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("dev", "later"), required=True)
    parser.add_argument("--signals", choices=("marker", "confirmation"), default="marker")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--verify-against", type=Path)
    args = parser.parse_args()
    run(args.phase, args.signals, args.output_root, args.verify_against)

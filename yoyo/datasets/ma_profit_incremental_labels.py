"""Incrementally label frozen MA-profit events without changing label semantics.

This is deliberately a narrow wrapper around :mod:`ma_profit_pipeline`: it
rehashes every current source, reuses only an outcome whose event semantics,
frozen plan, source bytes, and the three implementation files agree exactly,
and otherwise invokes the original reader, resolver, and split function.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

import numpy as np
import pandas as pd

from yoyo.contracts.ma_profit_filter import resolve_ma_profit_event
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix
from yoyo.datasets.ma_profit_pipeline import ROOT, committed, digest, dump, read_rows, split_for_event, write_rows


PIPELINE = ROOT / "yoyo/datasets/ma_profit_pipeline.py"
RESOLVER = ROOT / "yoyo/contracts/ma_profit_filter.py"
READER = ROOT / "yoyo/datasets/fifteen_minute_launch_candidates.py"
IMPLEMENTATION_PATHS = {"pipeline": PIPELINE, "resolver": RESOLVER, "reader": READER}
COMPUTED_FIELDS = (
    "bar_minutes", "source_sha256", "source_core_start_i", "source_core_end_i", "profit", "split",
    "purge_reason", "sample_owner_confirmed", "production_eligible",
)


class ReuseRejected(ValueError):
    """A prior label directory is not auditable enough to serve as a cache."""


def _sha(path: Path) -> str:
    return digest(path)


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _implementation_hashes() -> dict[str, str]:
    return {name: _sha(path) for name, path in IMPLEMENTATION_PATHS.items()}


def _git_file_sha(commit: str, path: Path) -> str:
    relative = str(path.relative_to(ROOT))
    try:
        content = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=ROOT)
    except subprocess.CalledProcessError as error:
        raise ReuseRejected(f"builder commit cannot prove {relative}: {commit}") from error
    return hashlib.sha256(content).hexdigest()


def _read_source(path: Path, bar_minutes: int, cutoff: str) -> pd.DataFrame:
    frame, _ = read_preholdout_prefix(path, end_exclusive=pd.Timestamp(cutoff), bar_minutes=int(bar_minutes))
    closes = pd.to_datetime(frame.open_time, utc=True) + pd.Timedelta(minutes=int(bar_minutes))
    return frame.loc[closes <= pd.Timestamp(cutoff)].reset_index(drop=True)


def _specs(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text())
    rows = value["sources"] if isinstance(value, dict) else value
    return {str(row.get("source_path", row.get("path"))): str(row.get("sha256", row.get("prefix_sha256"))) for row in rows}


def _semantic_key(event: dict[str, Any], source_sha256: str, plan_sha256: str, implementation: dict[str, str]) -> str:
    """Key only source-derived label semantics; discovery/NMS metadata is intentionally absent."""

    value = {
        "source_path": str(event["source_path"]), "source_sha256": source_sha256,
        "bar_minutes": int(event.get("bar_minutes", 15)), "direction": event["direction"],
        "core_start_time": pd.Timestamp(event["core_start_time"]).isoformat(),
        "core_end_time": pd.Timestamp(event["core_end_time"]).isoformat(), "core_bars": int(event["core_bars"]),
        "plan_sha256": plan_sha256, "pipeline_sha256": implementation["pipeline"],
        "resolver_sha256": implementation["resolver"], "reader_sha256": implementation["reader"],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_cached_profit(row: dict[str, Any], target_r: float) -> None:
    if any(name not in row for name in COMPUTED_FIELDS):
        raise ReuseRejected("cached outcome lacks original computed fields")
    profit = row["profit"]
    if not isinstance(profit, dict) or not isinstance(profit.get("retained"), bool):
        raise ReuseRejected("cached outcome has malformed profit")
    try:
        expected = profit["outcome"] == "TP" and float(profit["gross_r"]) >= target_r - 1e-10 and float(profit["net_r"]) > 0
    except (KeyError, TypeError, ValueError):
        raise ReuseRejected("cached outcome lacks auditable retention values") from None
    if profit["retained"] != expected:
        raise ReuseRejected("cached retained flag conflicts with original resolver contract")


def _bound_file(path: Path, expected_sha: str, name: str) -> Path:
    if not path.exists() or _sha(path) != expected_sha:
        raise ReuseRejected(f"cache frozen {name} file drifted: {path}")
    return path


def _frozen_paths(
    summary: dict[str, Any], *, events_path: Path, sources_path: Path,
    reuse_events_path: Path | None, reuse_sources_path: Path | None,
) -> tuple[dict[str, Path], bool]:
    bindings = summary.get("frozen_inputs")
    if not isinstance(bindings, dict):
        # A direct output from the original labeler predates this wrapper's
        # explicit paths.  It is usable only for its *identical* event file;
        # an expanded input has no auditable way to identify the prior file.
        old_events = (reuse_events_path or events_path).resolve()
        old_sources = (reuse_sources_path or sources_path).resolve()
        if _sha(old_events) != summary.get("input_events_sha256"):
            raise ReuseRejected("legacy cache old events do not match summary")
        if _sha(old_sources) != summary.get("source_manifest_sha256"):
            raise ReuseRejected("legacy cache old sources do not match summary")
        return {"events": old_events, "sources": old_sources}, True
    required = {"events": "input_events_sha256", "sources": "source_manifest_sha256", "plan": "plan_sha256"}
    result: dict[str, Path] = {}
    for name, digest_name in required.items():
        binding = bindings.get(name)
        if not isinstance(binding, dict) or not binding.get("path") or binding.get("sha256") != summary.get(digest_name):
            raise ReuseRejected(f"cache frozen {name} binding is missing or inconsistent")
        path = Path(binding["path"])
        if not path.is_absolute():
            path = ROOT / path
        result[name] = _bound_file(path, binding["sha256"], name)
    return result, False


def _load_cache(
    cache_dir: Path, *, plan_path: Path, events_path: Path, sources_path: Path,
    current_implementation: dict[str, str], reuse_events_path: Path | None = None,
    reuse_sources_path: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return validated source-semantic cached fields, or explain why none are usable."""

    summary_path, outcomes_path, errors_path = cache_dir / "summary.json", cache_dir / "outcomes.jsonl", cache_dir / "lineage_errors.jsonl"
    if not summary_path.exists() or not outcomes_path.exists() or not errors_path.exists():
        raise ReuseRejected("cache requires summary.json, outcomes.jsonl, and lineage_errors.jsonl")
    summary = json.loads(summary_path.read_text())
    if _sha(outcomes_path) != summary.get("outcomes_sha256"):
        raise ReuseRejected("cache outcomes SHA does not match summary")
    if summary.get("lineage_errors") != 0 or read_rows(errors_path):
        raise ReuseRejected("cache contains old lineage errors")
    frozen, legacy_input_binding = _frozen_paths(
        summary, events_path=events_path, sources_path=sources_path,
        reuse_events_path=reuse_events_path, reuse_sources_path=reuse_sources_path,
    )
    if _sha(plan_path) != summary.get("plan_sha256"):
        raise ReuseRejected("cache plan differs from current frozen plan")
    builder = summary.get("builder_commit")
    if not isinstance(builder, str):
        raise ReuseRejected("cache lacks builder commit")
    implementation = summary.get("original_implementation")
    if not isinstance(implementation, dict) or set(implementation) != set(IMPLEMENTATION_PATHS):
        # Pipeline outputs before this module do have a builder commit.  Pin
        # each dependency from that exact tree rather than assuming its
        # current contents; this is the only safe compatibility path.
        implementation = {name: _git_file_sha(builder, path) for name, path in IMPLEMENTATION_PATHS.items()}
        implementation_evidence = "builder_commit_derived"
    else:
        implementation_evidence = "explicit"
    for name, path in IMPLEMENTATION_PATHS.items():
        if implementation[name] != _git_file_sha(builder, path):
            raise ReuseRejected(f"cache {name} SHA disagrees with builder commit")
        if implementation[name] != current_implementation[name]:
            raise ReuseRejected(f"cache {name} implementation differs from current semantics")
    plan = json.loads(plan_path.read_text())
    old_events = read_rows(frozen["events"])
    old_pins = _specs(frozen["sources"])
    old_plan_sha = _sha(plan_path)
    expected: set[str] = set()
    for event in old_events:
        source = str(event.get("source_path", ""))
        if source not in old_pins:
            raise ReuseRejected("cache old event source is absent from frozen manifest")
        key = _semantic_key(event, old_pins[source], old_plan_sha, current_implementation)
        if key in expected:
            raise ReuseRejected("cache frozen event semantics are not unique")
        expected.add(key)
    cached: dict[str, dict[str, Any]] = {}
    for row in read_rows(outcomes_path):
        _validate_cached_profit(row, float(plan["label_contract"]["target_r"]))
        source_sha = str(row.get("source_sha256", ""))
        if not source_sha:
            raise ReuseRejected("cached outcome lacks source SHA")
        key = _semantic_key(row, source_sha, _sha(plan_path), current_implementation)
        if key not in expected or source_sha != old_pins.get(str(row.get("source_path", ""))):
            raise ReuseRejected("cached outcome has invalid old event/source lineage")
        fields = {name: row[name] for name in COMPUTED_FIELDS}
        if key in cached:
            raise ReuseRejected("cache outcomes are not unique by event semantics")
        cached[key] = fields
    if len(old_events) != summary.get("events") or set(cached) != expected:
        raise ReuseRejected("cache outcomes do not completely cover frozen old events")
    return cached, {"status": "accepted", "cache_dir": _rel(cache_dir), "cached_events": len(cached),
                    "frozen_events": _rel(frozen["events"]), "legacy_input_binding": legacy_input_binding,
                    "implementation_evidence": implementation_evidence}


def formal_guard(paths: list[Path]) -> str:
    """Require this wrapper, original semantics, current inputs, and cache artifacts on main."""

    return committed([Path(__file__), *IMPLEMENTATION_PATHS.values(), *paths])


def _cache_formal_paths(
    cache_dir: Path, *, events_path: Path, sources_path: Path,
    reuse_events_path: Path | None, reuse_sources_path: Path | None,
) -> list[Path]:
    """List cache evidence that must itself be immutable before a formal run."""

    paths = [path for path in (cache_dir / "summary.json", cache_dir / "outcomes.jsonl", cache_dir / "lineage_errors.jsonl") if path.exists()]
    summary_path = cache_dir / "summary.json"
    if not summary_path.exists():
        return paths
    try:
        bindings = json.loads(summary_path.read_text()).get("frozen_inputs")
    except (json.JSONDecodeError, OSError):
        return paths
    if isinstance(bindings, dict):
        for binding in bindings.values():
            if isinstance(binding, dict) and binding.get("path"):
                path = Path(binding["path"])
                paths.append((ROOT / path if not path.is_absolute() else path).resolve())
    else:
        paths.extend((reuse_events_path or events_path, reuse_sources_path or sources_path))
    return paths


def label_incremental(
    plan_path: Path, events_path: Path, sources_path: Path, output: Path, *, reuse_label_dir: Path | None = None,
    reuse_events_path: Path | None = None, reuse_sources_path: Path | None = None,
) -> dict[str, Any]:
    plan_path, events_path, sources_path, output = (Path(value).resolve() for value in (plan_path, events_path, sources_path, output))
    if output.exists():
        raise FileExistsError(output)
    if (reuse_events_path is None) != (reuse_sources_path is None):
        raise ValueError("--reuse-events and --reuse-sources must be supplied together")
    if (reuse_events_path is not None or reuse_sources_path is not None) and reuse_label_dir is None:
        raise ValueError("--reuse-events/--reuse-sources require --reuse-label-dir")
    implementation = _implementation_hashes()
    guard_paths = [plan_path, events_path, sources_path]
    if reuse_label_dir is not None:
        cache_dir = Path(reuse_label_dir).resolve()
        guard_paths.extend(_cache_formal_paths(cache_dir, events_path=events_path, sources_path=sources_path,
                                               reuse_events_path=reuse_events_path, reuse_sources_path=reuse_sources_path))
    commit = formal_guard(guard_paths)
    plan = json.loads(plan_path.read_text())
    args = plan["label_contract"]
    pins = _specs(sources_path)
    rows = read_rows(events_path)
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["source_path"]), int(row.get("bar_minutes", 15)))].append(row)
    actual_sources: dict[str, str] = {}
    # A source can appear under more than one interval group.  Its full-byte
    # digest remains mandatory, but exactly once per invocation.
    for source in sorted({source for source, _ in groups}):
        actual = _sha(ROOT / source)
        if actual != pins.get(source):
            raise ValueError("source SHA drift or source unpinned: " + source)
        actual_sources[source] = actual
    cache: dict[str, dict[str, Any]] = {}
    receipt: dict[str, Any] = {"status": "not_requested"}
    if reuse_label_dir is not None:
        try:
            cache, receipt = _load_cache(Path(reuse_label_dir).resolve(), plan_path=plan_path, events_path=events_path, sources_path=sources_path,
                                         current_implementation=implementation, reuse_events_path=reuse_events_path,
                                         reuse_sources_path=reuse_sources_path)
        except ReuseRejected as error:
            receipt = {"status": "rejected", "cache_dir": _rel(Path(reuse_label_dir)), "reason": str(error)}
    output.mkdir(parents=True)
    dump(output / "reuse_receipt.json", receipt)
    result: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    hits = misses = 0
    plan_sha = _sha(plan_path)
    for order, ((source, minutes), events) in enumerate(sorted(groups.items()), 1):
        source_sha = actual_sources[source]
        pending: list[tuple[int, dict[str, Any]]] = []
        resolved: dict[int, dict[str, Any]] = {}
        for index, event in enumerate(events):
            key = _semantic_key(event, source_sha, plan_sha, implementation)
            fields = cache.get(key)
            if fields is not None:
                resolved[index] = {**event, **fields}
                hits += 1
            else:
                pending.append((index, event))
                misses += 1
        if pending:
            frame = _read_source(ROOT / source, minutes, plan["discovery"]["data_end_exclusive"])
            times = pd.DatetimeIndex(pd.to_datetime(frame.open_time, utc=True))
            for index, event in pending:
                start, end = times.get_indexer(pd.to_datetime([event["core_start_time"], event["core_end_time"]], utc=True))
                if start < 0 or end < start or end - start + 1 != int(event["core_bars"]):
                    errors.append({"event_id": event["event_id"], "error": "core_timestamp_lineage"})
                    continue
                profit = resolve_ma_profit_event(frame, int(start), int(end), event["direction"], bar_minutes=minutes,
                    confirmation_bars=args["confirmation_bars"], horizon_hours=args["horizon_hours"],
                    target_r=args["target_r"], round_trip_cost=args["round_trip_cost"])
                split, reason = split_for_event(frame, int(start), int(end), minutes, plan)
                resolved[index] = {**event, "bar_minutes": minutes, "source_sha256": source_sha,
                    "source_core_start_i": int(start), "source_core_end_i": int(end), "profit": profit,
                    "split": split, "purge_reason": reason, "sample_owner_confirmed": False,
                    "production_eligible": False}
        # The original labeler writes each group's input order.  Cache hits and
        # misses must not reorder the output simply because misses need a frame.
        result.extend(resolved[index] for index in sorted(resolved))
        if order % 25 == 0 or order == len(groups):
            print(f"incremental label sources {order}/{len(groups)} events={len(result)} hits={hits} misses={misses}", flush=True)
    write_rows(output / "outcomes.jsonl", result)
    write_rows(output / "lineage_errors.jsonl", errors)
    counts = Counter(row["profit"]["outcome"] for row in result)
    retained = [row for row in result if row["profit"]["retained"]]
    frozen_inputs = {name: {"path": _rel(path), "sha256": _sha(path)} for name, path in
                     {"events": events_path, "sources": sources_path, "plan": plan_path}.items()}
    summary = {"builder_commit": commit, "input_events_sha256": frozen_inputs["events"]["sha256"],
        "source_manifest_sha256": frozen_inputs["sources"]["sha256"], "plan_sha256": frozen_inputs["plan"]["sha256"],
        "frozen_inputs": frozen_inputs, "original_implementation": implementation,
        "runtime": {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__, "platform": platform.platform()},
        "events": len(result), "lineage_errors": len(errors), "outcomes": dict(counts),
        "retained_before_purge": len(retained), "retained_by_split": dict(Counter(row["split"] for row in retained)),
        "all_by_split": dict(Counter(row["split"] for row in result)), "gross_r_target": args["target_r"],
        "cost": args["round_trip_cost"], "outcomes_sha256": _sha(output / "outcomes.jsonl"),
        "reuse": {**receipt, "hits": hits, "misses": misses}, "production_eligible": False}
    dump(output / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    if errors:
        raise ValueError("lineage errors: inspect output")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--reuse-label-dir", type=Path)
    parser.add_argument("--reuse-events", type=Path, help="explicit frozen event file for a legacy original-labeler cache")
    parser.add_argument("--reuse-sources", type=Path, help="explicit frozen source manifest for a legacy original-labeler cache")
    args = parser.parse_args()
    label_incremental(args.plan, args.events, args.sources, args.out, reuse_label_dir=args.reuse_label_dir,
                      reuse_events_path=args.reuse_events, reuse_sources_path=args.reuse_sources)


if __name__ == "__main__":
    main()

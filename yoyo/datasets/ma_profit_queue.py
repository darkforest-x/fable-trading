"""Fail-closed, resumable controller for the frozen MA-profit mining queue.

It observes the externally-owned first 1m batch, then runs 3m and numbered 1m
batches one at a time.  A batch cannot enter collection until its committed
manifest, every source receipt, and master receipt agree on the frozen plan and
rule hashes.  Collection, labelling, and selection are separately committed so
a restart can audit each irreversible frozen artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-ma-profit3r-20260922-v1"
PLAN = EXP / "plan.json"
SCAN = EXP / "source_scans"
PROFILE_DRIVER = ROOT / "yoyo/datasets/ma_profit_window_miner.py"
PROFILE_ADAPTER = ROOT / "yoyo/datasets/ma_profit_profile_window.py"
MAX_COMMIT_BYTES = 95 * 1024 * 1024
WORKERS = 2
RULES = [ROOT / part for part in (
    "yoyo/datasets/ma_profit_miner.py",
    "yoyo/datasets/ma_launch_snapshot_scan.py",
    "yoyo/datasets/ma_launch_owner_perfect_filter.py",
    "yoyo/datasets/ma_launch_owner_autofill10000.py",
    "yoyo/datasets/ma_launch_owner_autofill_review.py",
    "yoyo/datasets/fifteen_minute_launch_candidates.py",
)]
BASE_MANIFESTS = tuple(EXP / name for name in (
    "sources_local_15m.json", "sources_local_5m.json", "sources_high_30m.json",
    "sources_high_60m.json", "sources_high_240m.json",
))


class QueueError(RuntimeError):
    """A queue artifact is absent, stale, uncommitted, or inconsistent."""


class CapacityReached(QueueError):
    """The frozen selection satisfies the independent training-winner gate."""


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dump_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def branch_main(output: Callable[[Sequence[str]], str] | None = None) -> None:
    branch = (output or command_output)(["git", "branch", "--show-current"]).strip()
    if branch != "main":
        raise QueueError("main branch required for frozen queue artifacts")


def command_output(args: Sequence[str]) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True)


def ensure_committed(path: Path, *, output: Callable[[Sequence[str]], str] = command_output) -> str:
    """Require a tracked, clean path and return the commit that froze it."""
    path = Path(path)
    if not path.exists():
        raise QueueError(f"missing frozen input: {path}")
    relative = str(path.relative_to(ROOT))
    if output(["git", "status", "--porcelain", "--", relative]).strip():
        raise QueueError(f"input is not frozen in main: {relative}")
    try:
        output(["git", "ls-files", "--error-unmatch", "--", relative])
    except subprocess.CalledProcessError as exc:
        raise QueueError(f"input is untracked: {relative}") from exc
    commit = output(["git", "log", "-1", "--format=%H", "--", relative]).strip()
    if not commit:
        raise QueueError(f"input has no freezing commit: {relative}")
    return commit


def source_specs(manifest: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(manifest).read_text())
    rows = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise QueueError(f"manifest has no sources: {manifest}")
    required = {"source_path", "sha256"}
    if any(not required <= set(row) for row in rows):
        raise QueueError(f"manifest source lacks source_path/sha256: {manifest}")
    paths = [str(row["source_path"]) for row in rows]
    if len(paths) != len(set(paths)):
        raise QueueError(f"manifest has duplicate source_path: {manifest}")
    return rows


def rule_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): sha(path) for path in RULES[1:]}


def _window_miner():
    """Import the isolated driver only when profile lineage is needed."""
    from yoyo.datasets import ma_profit_window_miner
    return ma_profit_window_miner


def profile_paths() -> tuple[Path, Path, Path, Path]:
    """Resolve the driver-selected contract name; never pin a stale v1 filename."""
    driver = _window_miner()
    contract = EXP / driver.CONTRACT_NAME
    try:
        parity = ROOT / str(json.loads(contract.read_text())["parity_receipt_path"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError("invalid profile-window contract") from exc
    return PROFILE_DRIVER, PROFILE_ADAPTER, contract, parity


def profile_implementation() -> dict[str, Any]:
    """Load the frozen profile proof only when a new master needs it."""
    return _window_miner().implementation(PLAN)


def _profile_code_sha(implementation: Mapping[str, Any]) -> str:
    original = json_sha({"miner": sha(RULES[0]), "rules": rule_hashes()})
    return json_sha({"original_code_sha256": original, "profile_implementation": implementation})


def _matching_source_receipts(manifest: Path, *, expected_implementation: Mapping[str, Any] | None) -> dict[str, Path]:
    """Find one receipt/source with the master implementation and immutable rules."""
    expected = {str(row["source_path"]): str(row["sha256"]) for row in source_specs(manifest)}
    found: dict[str, list[Path]] = {key: [] for key in expected}
    wanted_rules = rule_hashes()
    expected_code = None if expected_implementation is None else _profile_code_sha(expected_implementation)
    for receipt_path in SCAN.rglob("receipt.json"):
        try:
            receipt = json.loads(receipt_path.read_text())
            binding = receipt["binding"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        source = str(binding.get("source_path", ""))
        if source not in expected:
            continue
        if not (receipt.get("status") == "completed" and binding.get("source_sha256") == expected[source]
                and binding.get("plan_sha256") == sha(PLAN)
                and binding.get("miner_sha256") == sha(RULES[0])
                and binding.get("rule_dependency_sha256") == wanted_rules):
            continue
        implementation = binding.get("profile_implementation")
        if expected_implementation is None:
            if implementation is not None:
                continue
        elif implementation != expected_implementation or binding.get("code_sha256") != expected_code:
            continue
        found[source].append(receipt_path)
    missing = sorted(source for source, paths in found.items() if len(paths) != 1)
    if missing:
        raise QueueError("source receipt coverage/drift: " + ", ".join(missing[:5]))
    return {source: paths[0] for source, paths in found.items()}


def validate_master(master: Path, manifest: Path, *, check_receipts: bool = True,
                    expected_implementation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Verify exact source coverage and the selected legacy/profile implementation."""
    data = json.loads(Path(master).read_text())
    expected_rows = source_specs(manifest)
    expected_paths = {str(row["source_path"]) for row in expected_rows}
    if data.get("plan_sha256") != sha(PLAN) or data.get("sources_manifest_sha256") != sha(manifest):
        raise QueueError("plan or manifest SHA drift")
    if data.get("miner_sha256") != sha(RULES[0]) or data.get("rule_dependency_sha256") != rule_hashes():
        raise QueueError("miner or rule dependency SHA drift")
    implementation = data.get("profile_implementation")
    if implementation is not None:
        expected_implementation = expected_implementation or profile_implementation()
        if implementation != expected_implementation:
            raise QueueError("profile implementation drift or unknown master")
    summaries = data.get("source_summaries")
    summary_paths = {str(row.get("source_path", "")) for row in summaries} if isinstance(summaries, list) else set()
    if (not data.get("source_coverage_complete") or data.get("failed_sources") or data.get("errors")
            or data.get("completed_sources") != data.get("attempted_sources")
            or data.get("attempted_sources") != len(expected_rows)
            or summary_paths != expected_paths or len(summaries) != len(expected_rows)):
        raise QueueError("incomplete, failed, or source-set drift")
    if check_receipts:
        _matching_source_receipts(manifest, expected_implementation=implementation if implementation is not None else None)
    return data


def archive_3m_receipt_valid(manifest: Path, receipt: Path) -> None:
    """Require the 3m importer receipt to be complete and bound to its manifest."""
    data = json.loads(receipt.read_text())
    sources = source_specs(manifest)
    if data.get("sources_manifest_sha256", data.get("manifest_sha256")) != sha(manifest):
        raise QueueError("3m archive receipt manifest SHA drift")
    # The archive fetcher records symbols_complete rather than a generic status
    # field.  Accept that schema only when its count exactly equals the frozen
    # manifest; generic receipts must state full coverage explicitly.
    if "symbols_complete" in data:
        complete = data.get("symbols_complete") == len(sources) and int(data.get("rows", 0)) > 0
    else:
        complete = (bool(data.get("source_coverage_complete")) and not data.get("failed_sources")
                    and not data.get("errors") and data.get("completed_sources") == data.get("attempted_sources"))
    if not complete:
        raise QueueError("3m archive receipt reports incomplete coverage")


def queue_items() -> list[tuple[str, Path, bool]]:
    return [("3m", EXP / "sources_archive_3m.json", True)] + [
        (f"1m_batch{number:02d}", EXP / f"sources_archive_1m_batch{number:02d}.json", False)
        for number in range(2, 28)
    ]


def queue_config() -> dict[str, Any]:
    return {"workers": WORKERS, "base_manifests": [str(p.relative_to(ROOT)) for p in BASE_MANIFESTS],
            "items": [(name, str(path.relative_to(ROOT)), needs_receipt) for name, path, needs_receipt in queue_items()]}


def binding() -> dict[str, str]:
    files = {"controller_sha256": sha(Path(__file__)), "plan_sha256": sha(PLAN),
             "training_contract_sha256": sha(EXP / "training_contract.json"),
             "pipeline_sha256": sha(ROOT / "yoyo/datasets/ma_profit_pipeline.py"),
             "cohort_sha256": sha(ROOT / "yoyo/datasets/ma_profit_cohort.py"),
             "incremental_labeler_sha256": sha(ROOT / "yoyo/datasets/ma_profit_incremental_labels.py"),
             "resolver_sha256": sha(ROOT / "yoyo/contracts/ma_profit_filter.py"),
             "reader_sha256": sha(ROOT / "yoyo/datasets/fifteen_minute_launch_candidates.py")}
    driver, adapter, contract, parity = profile_paths()
    files.update({"profile_window_driver_sha256": sha(driver),
                  "profile_window_adapter_sha256": sha(adapter),
                  "profile_window_contract_sha256": sha(contract),
                  "profile_window_parity_receipt_sha256": sha(parity)})
    files["config_sha256"] = json_sha(queue_config())
    return files


def state_path() -> Path:
    return EXP / "queue_state.json"


def load_state(path: Path | None = None) -> dict[str, Any]:
    path = path or state_path()
    current = binding()
    state = (json.loads(path.read_text()) if path.exists() else
             {"schema_version": 2, "binding": current, "batches": {}, "rounds": [], "status": "idle"})
    if state.get("binding") != current:
        raise QueueError("queue binding drift; start a new immutable queue state")
    if not isinstance(state.get("batches"), dict) or not isinstance(state.get("rounds"), list):
        raise QueueError("invalid queue state schema")
    return state


def save_state(state: Mapping[str, Any], path: Path | None = None) -> None:
    dump_atomic(path or state_path(), dict(state))


def wait_until(predicate: Callable[[], bool], description: str, *, once: bool = False,
               sleep: Callable[[float], None] = time.sleep) -> None:
    while not predicate():
        if once:
            raise QueueError(f"not ready: {description}")
        print(f"queue waiting: {description}", flush=True)
        sleep(60)


def no_active_miner(output: Callable[[Sequence[str]], str] = command_output) -> None:
    try:
        pids = output(["pgrep", "-f", "yoyo.datasets.ma_profit_(window_)?miner"]).strip()
    except subprocess.CalledProcessError:
        return
    if pids:
        raise QueueError(f"another miner is active (PIDs {pids}); refusing parallel run")


def run_miner(manifest: Path, *, runner: Callable[..., Any] = subprocess.run,
              output: Callable[[Sequence[str]], str] = command_output) -> None:
    branch_main(output)
    no_active_miner(output)
    for path in profile_paths():
        ensure_committed(path, output=output)
    runner([sys.executable, "-m", "yoyo.datasets.ma_profit_window_miner", "--plan", str(PLAN),
            "--sources", str(manifest), "--workers", str(WORKERS)], cwd=ROOT, check=True)


def _compact(master: Path, data: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("attempted_sources", "completed_sources", "failed_sources", "source_coverage_complete",
            "strict_grade_a_before_cross_timeframe_dedup", "plan_sha256", "miner_sha256",
            "rule_dependency_sha256", "sources_manifest_sha256", "training_gate_pass", "profile_implementation")
    return {**{key: data.get(key) for key in keys}, "master_path": str(master.relative_to(ROOT)),
            "master_sha256": sha(master)}


def commit_exact(paths: Iterable[Path], *, runner: Callable[..., Any] = subprocess.run,
                 output: Callable[[Sequence[str]], str] = command_output) -> str:
    """Commit only listed new artifacts, rejecting staging contamination or large blobs."""
    paths = [Path(path) for path in paths]
    if not paths or len(set(paths)) != len(paths):
        raise QueueError("commit paths must be non-empty and unique")
    branch_main(output)
    for path in paths:
        if not path.exists():
            raise QueueError(f"missing artifact for commit: {path}")
        if path.stat().st_size > MAX_COMMIT_BYTES:
            raise QueueError(f"artifact exceeds 95MiB auto-commit limit: {path}")
    relative = [str(path.relative_to(ROOT)) for path in paths]
    runner(["git", "add", "--", *relative], cwd=ROOT, check=True)
    staged = set(output(["git", "diff", "--cached", "--name-only", "--", *relative]).splitlines())
    if not staged:
        commits = {ensure_committed(path, output=output) for path in paths}
        if len(commits) != 1:
            raise QueueError("unchanged artifacts do not share one freezing commit")
        return commits.pop()
    if staged != set(relative):
        raise QueueError("partially changed stage requires manual recovery")
    branch_main(output)
    # Explicit pathspec protects concurrent agents' staged changes even if they
    # stage between our index inspection and this commit.
    runner(["git", "commit", "-m", "Freeze MA-profit queue artifact", "--", *relative], cwd=ROOT, check=True)
    return output(["git", "rev-parse", "HEAD"]).strip()


def acquire_lock(path: Path | None = None) -> Path:
    path = path or (EXP / "queue.lock")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise QueueError(f"queue lock exists: {path}; another controller may be active") from exc
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"pid": os.getpid(), "binding": binding(), "created_utc": time.time()}, handle, sort_keys=True)
        handle.write("\n")
    return path


def release_lock(path: Path) -> None:
    path.unlink(missing_ok=True)


def validate_saved_batch(name: str, record: Mapping[str, Any], manifest: Path) -> None:
    master = ROOT / str(record.get("master_path", ""))
    summary = EXP / str(record.get("summary_path", ""))
    if not master.exists() or record.get("master_sha256") != sha(master):
        raise QueueError(f"saved batch master drift: {name}")
    if not summary.exists() or record.get("summary_sha256") != sha(summary):
        raise QueueError(f"saved batch summary drift: {name}")
    if record.get("manifest_sha256") != sha(manifest):
        raise QueueError(f"saved batch manifest drift: {name}")
    validate_master(master, manifest)


def _manifest_frozen(path: Path, output: Callable[[Sequence[str]], str]) -> bool:
    """A producer writes a manifest before freezing it; wait through that gap."""
    if not path.exists():
        return False
    try:
        ensure_committed(path, output=output)
    except QueueError:
        return False
    return True


def observe_or_run(name: str, manifest: Path, *, existing: bool, once: bool,
                   runner: Callable[..., Any] = subprocess.run,
                   output: Callable[[Sequence[str]], str] = command_output,
                   sleeper: Callable[[float], None] = time.sleep,
                   waiter: Callable[..., None] = wait_until) -> tuple[dict[str, Any], Path]:
    master = SCAN / f"master_{manifest.stem}.json"
    waiter(manifest.exists, f"{manifest.name} frozen manifest", once=once, sleep=sleeper)
    if existing:
        waiter(master.exists, f"external {name} master", once=once, sleep=sleeper)
    elif not master.exists():
        run_miner(manifest, runner=runner, output=output)
    data = validate_master(master, manifest)
    summary = EXP / f"queue_summary_{name}.json"
    dump_atomic(summary, _compact(master, data))
    return json.loads(summary.read_text()), summary


def frozen_manifests(scanned: Mapping[str, Mapping[str, Any]]) -> list[Path]:
    result = list(BASE_MANIFESTS)
    for name in ("3m", *[f"1m_batch{i:02d}" for i in range(1, 28)]):
        if name in scanned:
            result.append(EXP / str(scanned[name]["manifest_path"]))
    return result


def validate_collection_inputs(manifests: Iterable[Path], *, output: Callable[[Sequence[str]], str] = command_output) -> None:
    """Recheck prior masters; load the profile proof at most once per collection round."""
    expected_implementation: dict[str, Any] | None = None
    for manifest in manifests:
        ensure_committed(manifest, output=output)
        master = SCAN / f"master_{Path(manifest).stem}.json"
        if not master.exists():
            raise QueueError(f"collection input has no master: {manifest}")
        try:
            uses_profile = json.loads(master.read_text()).get("profile_implementation") is not None
        except (OSError, ValueError) as exc:
            raise QueueError(f"invalid collection master: {master}") from exc
        if uses_profile and expected_implementation is None:
            expected_implementation = profile_implementation()
        validate_master(master, manifest, expected_implementation=expected_implementation)


def _relative(path: Path) -> str:
    return str(Path(path).resolve().relative_to(ROOT.resolve()))


def _input_has_sha(inputs: object, path: Path) -> bool:
    if not isinstance(inputs, list):
        return False
    target = _relative(path)
    return any(isinstance(item, Mapping) and item.get("path") == target and item.get("sha256") == sha(path)
               for item in inputs)


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise QueueError(f"invalid JSONL artifact: {path}") from exc


def validate_collection_stage(cohort: Path, manifests: Iterable[Path]) -> None:
    """Bind collected event files to this exact plan and manifest set."""
    events, sources, exclusions, receipt_path = (cohort / "frozen_events.jsonl", cohort / "frozen_sources.json",
                                                   cohort / "collection_exclusions.jsonl", cohort / "collection_receipt.json")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as exc:
        raise QueueError("invalid collection receipt") from exc
    fields = (("frozen_events_path", events, "frozen_events_sha256"),
              ("frozen_sources_path", sources, "frozen_sources_sha256"),
              ("collection_exclusions_path", exclusions, "collection_exclusions_sha256"))
    if receipt.get("plan_sha256") != sha(PLAN):
        raise QueueError("collection receipt plan SHA drift")
    for path_key, path, sha_key in fields:
        if receipt.get(path_key) != _relative(path) or receipt.get(sha_key) != sha(path):
            raise QueueError(f"collection receipt artifact SHA/path drift: {path.name}")
    if not _input_has_sha(receipt.get("inputs"), PLAN) or any(not _input_has_sha(receipt.get("inputs"), path) for path in manifests):
        raise QueueError("collection receipt manifest input drift")


def validate_label_stage(cohort: Path, outcomes: Path, *, require_reuse_receipt: bool = False) -> None:
    """Require the label summary to attest to the exact frozen cohort files."""
    events, sources = cohort / "frozen_events.jsonl", cohort / "frozen_sources.json"
    output, errors, summary_path = outcomes / "outcomes.jsonl", outcomes / "lineage_errors.jsonl", outcomes / "summary.json"
    try:
        summary = json.loads(summary_path.read_text())
    except (OSError, ValueError) as exc:
        raise QueueError("invalid label summary") from exc
    if (summary.get("plan_sha256") != sha(PLAN) or summary.get("input_events_sha256") != sha(events)
            or summary.get("source_manifest_sha256") != sha(sources) or summary.get("outcomes_sha256") != sha(output)
            or summary.get("lineage_errors") != 0 or errors.read_text().strip()):
        raise QueueError("label summary lineage/SHA drift")
    _bind_event_rows(_jsonl_rows(events), _jsonl_rows(output), upstream_label="frozen events",
                     downstream_label="outcomes", exact=True, compare_profit=False)
    if require_reuse_receipt:
        receipt_path = outcomes / "reuse_receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text())
        except (OSError, ValueError) as exc:
            raise QueueError("invalid incremental reuse receipt") from exc
        reuse = summary.get("reuse")
        if (not isinstance(reuse, Mapping) or not isinstance(receipt, Mapping)
                or receipt.get("status") != "accepted"
                or any(receipt.get(key) != reuse.get(key) for key in ("status", "cache_dir"))):
            raise QueueError("incremental reuse receipt/summary drift")


def _round_number(tag: str) -> int:
    prefix = "queue_round_"
    if not tag.startswith(prefix) or not tag[len(prefix):].isdigit() or int(tag[len(prefix):]) < 1:
        raise QueueError(f"invalid downstream round tag: {tag}")
    return int(tag[len(prefix):])


IMMUTABLE_EVENT_FIELDS = (
    "event_id", "cluster_id", "symbol", "canonical_asset", "direction", "bar_minutes",
    "source_path", "source_sha256", "core_start_time", "core_end_time", "core_bars",
)


def _rows_by_event_id(rows: Iterable[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("event_id", ""))
        if not event_id or event_id in indexed:
            raise QueueError(f"{label} event identity is absent or duplicated")
        indexed[event_id] = row
    return indexed


def _bind_event_rows(upstream: Iterable[Mapping[str, Any]], downstream: Iterable[Mapping[str, Any]], *,
                     upstream_label: str, downstream_label: str, exact: bool, compare_profit: bool) -> dict[str, Mapping[str, Any]]:
    """Require downstream rows to preserve frozen identities while allowing added fields."""
    before, after = _rows_by_event_id(upstream, upstream_label), _rows_by_event_id(downstream, downstream_label)
    identities_valid = set(before) == set(after) if exact else set(after).issubset(before)
    if not identities_valid:
        raise QueueError(f"{downstream_label} event identities do not bind to {upstream_label}")
    fields = (*IMMUTABLE_EVENT_FIELDS, *(("profit", "split", "purge_reason", "source_core_start_i", "source_core_end_i") if compare_profit else ()))
    for event_id, row in after.items():
        source = before[event_id]
        # Collection rows from legacy caches can legitimately omit fields that
        # the labeler reconstructs (for example bar_minutes/source_sha256).
        # Every identity field present upstream remains immutable.
        if any(field in source and source[field] != row.get(field) for field in fields):
            raise QueueError(f"{downstream_label} immutable event field drift: {event_id}")
    return after


def _retained_winner(row: Mapping[str, Any]) -> bool:
    profit = row.get("profit")
    if not isinstance(profit, Mapping) or profit.get("retained") is not True or profit.get("outcome") != "TP":
        return False
    try:
        gross, net = float(profit["gross_r"]), float(profit["net_r"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(gross) and math.isfinite(net) and gross >= 3.0 - 1e-10 and net > 0.0


def validate_selection_stage(outcomes: Path, selection: Path) -> dict[str, Any]:
    """Verify selection hashes, source lineage, and the actual capacity decision."""
    selection_path, dataset_path, receipt_path = (selection / "selection_ledger.jsonl", selection / "dataset_ledger.jsonl",
                                                   selection / "selection_receipt.json")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as exc:
        raise QueueError("invalid selection receipt") from exc
    if (receipt.get("selection_ledger_sha256") != sha(selection_path)
            or receipt.get("dataset_ledger_sha256") != sha(dataset_path)
            or receipt.get("selected_events_sha256") != sha(dataset_path)
            or receipt.get("training_contract_sha256") != sha(EXP / "training_contract.json")
            or not _input_has_sha(receipt.get("inputs"), outcomes / "outcomes.jsonl")):
        raise QueueError("selection ledger/input SHA drift")
    outcome_rows, all_rows, kept_rows = _jsonl_rows(outcomes / "outcomes.jsonl"), _jsonl_rows(selection_path), _jsonl_rows(dataset_path)
    selected_by_id = _bind_event_rows(outcome_rows, all_rows, upstream_label="outcomes",
                                      downstream_label="selection ledger", exact=True, compare_profit=True)
    clusters = [row.get("cluster_id") for row in all_rows]
    if any(not value for value in clusters) or len(set(clusters)) != len(clusters):
        raise QueueError("selection ledger requires independent unique cluster representatives")
    kept_by_id = _bind_event_rows(all_rows, kept_rows, upstream_label="selection ledger",
                                  downstream_label="dataset ledger", exact=False, compare_profit=True)
    expected_kept = {event_id for event_id, row in selected_by_id.items() if row.get("dataset_kept") is True}
    if set(kept_by_id) != expected_kept:
        raise QueueError("dataset ledger does not exactly match selected kept events")
    actual = sum(row.get("split") == "train" and _retained_winner(row) for row in all_rows)
    minimum, maximum = int(receipt.get("minimum_train_winners", -1)), int(receipt.get("maximum_train_winners", -1))
    expected_gate = actual >= minimum
    selected = sum(row.get("split") == "train" and _retained_winner(row) for row in kept_rows)
    if (minimum, maximum) != (3000, 5000) or receipt.get("train_retained_winners_actual") != actual or bool(receipt.get("capacity_gate")) != expected_gate:
        raise QueueError("selection capacity calculation drift")
    expected_selected = min(actual, maximum) if expected_gate else 0
    if receipt.get("train_retained_winners_selected") != selected or selected != expected_selected or (not expected_gate and kept_rows):
        raise QueueError("selection selected-winner/capacity drift")
    return receipt


def frozen_stage(paths: Iterable[Path], *, output: Callable[[Sequence[str]], str] = command_output) -> str:
    """Return the one commit for a complete prior stage; reject partial output."""
    paths = [Path(path) for path in paths]
    exists = [path.exists() for path in paths]
    if any(exists) and not all(exists):
        raise QueueError("partial downstream stage exists; refusing to overwrite it")
    if not all(exists):
        return ""
    commits = {ensure_committed(path, output=output) for path in paths}
    if len(commits) != 1:
        raise QueueError("downstream stage files were not frozen together")
    return commits.pop()


def prior_incremental_inputs(tag: str, *, output: Callable[[Sequence[str]], str]) -> tuple[Path, Path, Path]:
    """Return committed prior cohort/outcomes or fail before a later label run."""

    number = _round_number(tag)
    if number < 2:
        raise QueueError("round001 has no prior incremental label input")
    prior_tag = f"queue_round_{number - 1:03d}"
    cohort, outcomes = EXP / f"cohort_{prior_tag}", EXP / f"outcomes_{prior_tag}"
    cohort_paths = [cohort / name for name in ("frozen_events.jsonl", "frozen_sources.json",
                                                "collection_exclusions.jsonl", "collection_receipt.json")]
    outcome_names = ["outcomes.jsonl", "lineage_errors.jsonl", "summary.json"]
    if number > 2:
        outcome_names.append("reuse_receipt.json")
    outcome_paths = [outcomes / name for name in outcome_names]
    if not frozen_stage(cohort_paths, output=output) or not frozen_stage(outcome_paths, output=output):
        raise QueueError("incremental label requires a complete frozen prior cohort and outcomes")
    validate_label_stage(cohort, outcomes, require_reuse_receipt=number > 2)
    return cohort / "frozen_events.jsonl", cohort / "frozen_sources.json", outcomes


def run_downstream(manifests: list[Path], tag: str, *, runner: Callable[..., Any] = subprocess.run,
                   output: Callable[[Sequence[str]], str] = command_output,
                   commit_fn: Callable[[Iterable[Path]], str] | None = None,
                   input_validator: Callable[[Iterable[Path]], None] | None = None) -> dict[str, Any]:
    """Run and freeze collect → label → select; returns a capacity decision."""
    commit_fn = commit_fn or (lambda paths: commit_exact(paths, runner=runner, output=output))
    (input_validator or (lambda paths: validate_collection_inputs(paths, output=output)))(manifests)
    cohort, outcomes, selection = (EXP / f"cohort_{tag}", EXP / f"outcomes_{tag}", EXP / f"selection_{tag}")
    incremental = _round_number(tag) >= 2
    prior_events = prior_sources = prior_outcomes = None
    if incremental:
        prior_events, prior_sources, prior_outcomes = prior_incremental_inputs(tag, output=output)
    collection_paths = [cohort / name for name in ("frozen_events.jsonl", "frozen_sources.json", "collection_exclusions.jsonl", "collection_receipt.json")]
    collection_commit = frozen_stage(collection_paths, output=output)
    if not collection_commit:
        collect = [sys.executable, "-m", "yoyo.datasets.ma_profit_cohort", "collect", "--plan", str(PLAN)]
        for manifest in manifests:
            collect.extend(["--sources", str(manifest)])
        collect.extend(["--compact-events", "--out", str(cohort)])
        runner(collect, cwd=ROOT, check=True)
        collection_commit = commit_fn(collection_paths)
    validate_collection_stage(cohort, manifests)
    outcome_names = ["outcomes.jsonl", "lineage_errors.jsonl", "summary.json"]
    if incremental:
        outcome_names.append("reuse_receipt.json")
    outcome_paths = [outcomes / name for name in outcome_names]
    outcome_commit = frozen_stage(outcome_paths, output=output)
    if not outcome_commit:
        if incremental:
            runner([sys.executable, "-m", "yoyo.datasets.ma_profit_incremental_labels", "--plan", str(PLAN),
                    "--events", str(cohort / "frozen_events.jsonl"), "--sources", str(cohort / "frozen_sources.json"),
                    "--out", str(outcomes), "--reuse-label-dir", str(prior_outcomes),
                    "--reuse-events", str(prior_events), "--reuse-sources", str(prior_sources)], cwd=ROOT, check=True)
        else:
            runner([sys.executable, "-m", "yoyo.datasets.ma_profit_pipeline", "label", "--plan", str(PLAN),
                    "--events", str(cohort / "frozen_events.jsonl"), "--sources", str(cohort / "frozen_sources.json"),
                    "--out", str(outcomes)], cwd=ROOT, check=True)
        outcome_commit = commit_fn(outcome_paths)
    validate_label_stage(cohort, outcomes, require_reuse_receipt=incremental)
    selection_paths = [selection / name for name in ("selection_ledger.jsonl", "dataset_ledger.jsonl", "selection_receipt.json")]
    selection_commit = frozen_stage(selection_paths, output=output)
    if not selection_commit:
        runner([sys.executable, "-m", "yoyo.datasets.ma_profit_cohort", "select", "--plan", str(PLAN),
                "--events", str(outcomes / "outcomes.jsonl"), "--reference-exclusion", str(EXP / "reference_exclusion.json"),
                "--out", str(selection)], cwd=ROOT, check=True)
        selection_commit = commit_fn(selection_paths)
    receipt_path = selection / "selection_receipt.json"
    receipt = validate_selection_stage(outcomes, selection)
    result = {"tag": tag, "collection_commit": collection_commit, "outcome_commit": outcome_commit,
              "selection_commit": selection_commit, "selection_receipt_path": str(receipt_path.relative_to(ROOT)),
              "selection_receipt_sha256": sha(receipt_path), "capacity_gate": bool(receipt.get("capacity_gate"))}
    if result["capacity_gate"]:
        ready = EXP / "queue_ready.json"
        result["ready_commit"] = frozen_stage([ready], output=output)
        if result["ready_commit"]:
            try:
                ready_payload = json.loads(ready.read_text())
            except (OSError, ValueError) as exc:
                raise QueueError("invalid prior queue-ready receipt") from exc
            if (ready_payload.get("binding") != binding()
                    or ready_payload.get("selection_receipt_sha256") != result["selection_receipt_sha256"]
                    or ready_payload.get("capacity_gate") is not True):
                raise QueueError("prior queue-ready receipt drift")
        else:
            dump_atomic(ready, {"binding": binding(), **result})
            result["ready_commit"] = commit_fn([ready])
    return result


def _record_batch(state: dict[str, Any], name: str, manifest: Path, summary: Mapping[str, Any], summary_path: Path,
                  summary_commit: str) -> None:
    state["batches"][name] = {"manifest_path": str(manifest.relative_to(EXP)), "manifest_sha256": sha(manifest),
                                "master_path": summary["master_path"], "master_sha256": summary["master_sha256"],
                                "summary_path": str(summary_path.relative_to(EXP)), "summary_sha256": sha(summary_path),
                                "summary_commit": summary_commit}


def _round_tag(state: Mapping[str, Any]) -> str:
    return f"queue_round_{len(state['rounds']) + 1:03d}"


def run_queue(*, once: bool = False, runner: Callable[..., Any] = subprocess.run,
              output: Callable[[Sequence[str]], str] = command_output,
              sleeper: Callable[[float], None] = time.sleep,
              commit_fn: Callable[[Iterable[Path]], str] | None = None,
              waiter: Callable[..., None] = wait_until) -> dict[str, Any]:
    """Run the ordered queue, or fail on a missing/unfrozen next input in --once mode."""
    lock = acquire_lock()
    try:
        branch_main(output)
        state = load_state()
        commit_fn = commit_fn or (lambda paths: commit_exact(paths, runner=runner, output=output))
        first = EXP / "sources_archive_1m_batch01.json"
        # batch01 and the currently parent-owned 3m run are external.  The
        # controller only waits for their masters and never launches a second
        # miner.  Later numbered batches are its serialized responsibility.
        schedule = [("1m_batch01", first, False, True)]
        for name, path, needs_receipt in queue_items():
            schedule.append((name, path, needs_receipt, name == "3m"))
        for name, manifest, needs_archive_receipt, external in schedule:
            if state["binding"] != binding():
                raise QueueError("queue code or frozen configuration changed during execution")
            existing = state["batches"].get(name)
            if existing:
                ensure_committed(manifest, output=output)
                ensure_committed(EXP / existing["summary_path"], output=output)
                validate_saved_batch(name, existing, manifest)
            else:
                waiter(lambda: _manifest_frozen(manifest, output), f"{name} committed manifest", once=once, sleep=sleeper)
                if needs_archive_receipt:
                    receipt = EXP / "archive3m_receipt.json"
                    waiter(lambda: _manifest_frozen(receipt, output), "3m committed archive receipt", once=once, sleep=sleeper)
                    archive_3m_receipt_valid(manifest, receipt)
                summary, summary_path = observe_or_run(name, manifest, existing=external, once=once, runner=runner, output=output, sleeper=sleeper, waiter=waiter)
                summary_commit = commit_fn([summary_path])
                _record_batch(state, name, manifest, summary, summary_path, summary_commit)
                save_state(state)
            if name != "1m_batch01":
                completed_rounds = [row for row in state["rounds"] if row.get("after_batch") == name]
                if completed_rounds:
                    if len(completed_rounds) != 1:
                        raise QueueError("multiple downstream rounds for one batch")
                    prior = completed_rounds[0]
                    receipt_path = ROOT / prior["selection_receipt_path"]
                    ensure_committed(receipt_path, output=output)
                    if sha(receipt_path) != prior["selection_receipt_sha256"]:
                        raise QueueError("saved downstream selection receipt drift")
                    if prior["capacity_gate"]:
                        raise CapacityReached(f"capacity gate already reached in {prior['tag']}")
                    continue
                tag = _round_tag(state)
                decision = run_downstream(frozen_manifests(state["batches"]), tag, runner=runner, output=output, commit_fn=commit_fn)
                decision["after_batch"] = name
                state["rounds"].append(decision)
                save_state(state)
                if decision["capacity_gate"]:
                    state["status"] = "capacity_reached"
                    save_state(state)
                    raise CapacityReached(f"capacity gate reached in {tag}")
            if once:
                state["status"] = "paused_after_one_batch"
                save_state(state)
                return state
        state["status"] = "complete"
        save_state(state)
        return state
    except CapacityReached:
        raise
    except Exception as exc:
        error = {"binding": binding(), "error": f"{type(exc).__name__}: {exc}", "state_path": str(state_path().relative_to(ROOT))}
        dump_atomic(EXP / "queue_error.json", error)
        raise
    finally:
        release_lock(lock)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="fail if the next frozen input is not ready")
    args = parser.parse_args()
    try:
        run_queue(once=args.once)
    except CapacityReached as exc:
        print(json.dumps({"status": "capacity_reached", "detail": str(exc)}), flush=True)


if __name__ == "__main__":
    main()

"""Freeze outcome-blind Profit3R events and enforce the train-only capacity gate.

Collection uses only Grade-A morphology rows.  It applies quality-sorted 4-hour
NMS across venues and timeframes before labels exist.  Selection keeps the
labeler's already-frozen train/val/test split: 3,000--5,000 applies only to
independent retained training winners, never to validation or test outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-ma-profit3r-20260922-v1"
GAP = pd.Timedelta(hours=4)
DEFAULT_BINANCE_SCORED = ROOT / "experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/binance_scored.jsonl"
DEFAULT_RANKED_MANIFEST = ROOT / "experiments/active/exp-15m-ma-launch-owner-perfect-filter10000-v1/results/ranked_manifest.jsonl"
RULE_DEPENDENCY_PATHS = (
    ROOT / "yoyo/datasets/ma_launch_snapshot_scan.py",
    ROOT / "yoyo/datasets/ma_launch_owner_perfect_filter.py",
    ROOT / "yoyo/datasets/ma_launch_owner_autofill10000.py",
    ROOT / "yoyo/datasets/ma_launch_owner_autofill_review.py",
    ROOT / "yoyo/datasets/fifteen_minute_launch_candidates.py",
)


class ProfitCohortError(RuntimeError):
    """Raised when frozen-event provenance or a cohort gate is invalid."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    return _sha(path)


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _sha(path)


def _path(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ProfitCohortError(f"timestamp requires timezone: {value!r}")
    return stamp.tz_convert("UTC")


def canonical_asset(value: object) -> str:
    """Normalize USDT-swap spelling and documented 1000/1000000 aliases."""

    symbol = str(value).upper().strip().replace("/", "_")
    for suffix in ("_USDT_SWAP", "-USDT-SWAP", "_USDT", "-USDT", "USDT"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
            break
    symbol = symbol.replace("_", "").replace("-", "")
    for multiplier in ("1000000", "1000"):
        if symbol.startswith(multiplier) and len(symbol) > len(multiplier):
            symbol = symbol[len(multiplier) :]
            break
    if not symbol:
        raise ProfitCohortError(f"cannot canonicalize asset: {value!r}")
    return symbol


def _event_time(row: Mapping[str, Any]) -> pd.Timestamp:
    return _utc(row.get("core_end_time") or row.get("confirmation_close_utc"))


def _event_key(row: Mapping[str, Any]) -> str:
    return str(row.get("event_id") or row.get("sample_id") or row.get("profile_id") or "")


def _is_grade_a(row: Mapping[str, Any]) -> bool:
    return str(row.get("quality_tier")) == "PERFECT_CANDIDATE" and row.get("reference_gate_pass") is not False


def _training_contract(plan_path: Path, *, required: bool) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    path = plan_path.parent / "training_contract.json"
    if not path.exists():
        if required:
            raise ProfitCohortError(f"missing training contract: {path}")
        return None, None
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("experiment_id") != EXPERIMENT_ID or contract.get("quota_scope") != "train_independent_retained_events":
        raise ProfitCohortError(f"invalid training contract: {path}")
    if contract.get("original_plan_sha256") != _sha(plan_path):
        raise ProfitCohortError(f"training contract plan SHA drift: {path}")
    return contract, {"path": _relative(path), "sha256": _sha(path)}


def _source_rows_from_receipt(receipt_path: Path, spec: Mapping[str, Any], plan_sha: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "completed":
        raise ProfitCohortError(f"source receipt is not completed: {receipt_path}")
    binding = receipt.get("binding", {})
    if binding.get("source_path") != str(spec["source_path"]) or binding.get("source_sha256") != str(spec["sha256"]) or binding.get("plan_sha256") != plan_sha:
        raise ProfitCohortError(f"source receipt SHA binding drift: {receipt_path}")
    if binding.get("rule_dependency_sha256") != {_relative(path): _sha(path) for path in RULE_DEPENDENCY_PATHS}:
        raise ProfitCohortError(f"source receipt rule dependency drift: {receipt_path}")
    if binding.get("miner_sha256") != _sha(ROOT / "yoyo/datasets/ma_profit_miner.py"):
        raise ProfitCohortError(f"source receipt miner implementation drift: {receipt_path}")
    artifact = receipt.get("artifacts", {}).get("strictGradeA.jsonl")
    strict_path = receipt_path.parent / "strictGradeA.jsonl"
    if not artifact or not strict_path.exists() or _sha(strict_path) != artifact:
        raise ProfitCohortError(f"strict Grade-A artifact drift: {receipt_path}")
    return _read_jsonl(strict_path), {"receipt_path": _relative(receipt_path), "receipt_sha256": _sha(receipt_path), "strict_grade_a_path": _relative(strict_path), "strict_grade_a_sha256": artifact}


def _receipt_index(plan_path: Path) -> dict[tuple[str, str], list[Path]]:
    """Index once per collection instead of rereading every receipt per source."""

    index: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for receipt in (plan_path.parent / "source_scans").glob("*/receipt.json"):
        try:
            binding = json.loads(receipt.read_text(encoding="utf-8")).get("binding", {})
            index[(str(binding.get("source_path", "")), str(binding.get("source_sha256", "")))].append(receipt)
        except json.JSONDecodeError:
            continue
    return index


def _find_receipt(plan_path: Path, spec: Mapping[str, Any], index: Mapping[tuple[str, str], list[Path]]) -> Path:
    if spec.get("receipt_path"):
        return _path(str(spec["receipt_path"]))
    candidates = index.get((str(spec["source_path"]), str(spec["sha256"])), [])
    if len(candidates) != 1:
        raise ProfitCohortError(f"expected one receipt for source {spec['source_path']}, found {len(candidates)}")
    return candidates[0]


def _manifest_sources(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise ProfitCohortError(f"sources manifest lacks sources list: {path}")
    for row in rows:
        if not isinstance(row, Mapping) or not {"source_path", "sha256"}.issubset(row):
            raise ProfitCohortError(f"un-pinned source manifest entry: {path}")
    return [dict(row) for row in rows]


def _origin_row(row: Mapping[str, Any], origin: str) -> dict[str, Any]:
    event_id = _event_key(row)
    if not event_id:
        raise ProfitCohortError(f"event lacks a stable id from {origin}")
    direction = str(row.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ProfitCohortError(f"invalid direction for {event_id}")
    clone = dict(row)
    clone.update({"origin": origin, "origin_event_id": event_id, "canonical_asset": canonical_asset(row.get("symbol") or row.get("exchange_symbol")), "direction": direction, "core_end_time": _event_time(row).isoformat(), "quality_score": float(row.get("quality_score", 0.0))})
    return clone


def _freeze(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply pre-registered quality-sorted 4-hour NMS without profit fields.

    A 0h/4h/8h chain can retain 0h and 8h: representatives must be strictly
    more than four hours apart. Rejected rows use nearest chosen representative;
    equal distance uses representative quality rank, preventing double members.
    """

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["canonical_asset"]), str(row["direction"]))].append(dict(row))
    frozen: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for (asset, direction), group in sorted(grouped.items()):
        ranked = sorted(group, key=lambda row: (-float(row["quality_score"]), row["origin_event_id"], row["origin"]))
        representatives: list[dict[str, Any]] = []
        members: dict[int, list[dict[str, Any]]] = {}
        for row in ranked:
            close = [rep for rep in representatives if abs(_event_time(row) - _event_time(rep)) <= GAP]
            if not close:
                representatives.append(row)
                members[id(row)] = [row]
                continue
            rep = min(close, key=lambda item: (abs(_event_time(row) - _event_time(item)), representatives.index(item)))
            members[id(rep)].append(row)
            exclusions.append({"reason": "quality_sorted_4h_nms", "representative_origin_event_id": rep["origin_event_id"], **row})
        for rep in representatives:
            assigned = sorted(members[id(rep)], key=lambda row: (_event_time(row), row["origin"], row["origin_event_id"]))
            cluster_id = "cluster_" + _json_sha({"asset": asset, "direction": direction, "representative": (rep["origin"], rep["origin_event_id"]), "members": [(row["origin"], row["origin_event_id"]) for row in assigned]})[:24]
            frozen.append({**rep, "event_id": cluster_id, "cluster_id": cluster_id, "frozen_event_id": cluster_id, "cluster_member_count": len(assigned), "cluster_first_core_end_utc": _event_time(assigned[0]).isoformat(), "cluster_last_core_end_utc": _event_time(assigned[-1]).isoformat(), "cluster_members": [{"origin": row["origin"], "origin_event_id": row["origin_event_id"], "core_end_time": row["core_end_time"], "quality_score": row["quality_score"]} for row in assigned]})
    frozen.sort(key=lambda row: (_event_time(row), row["cluster_id"]))
    return frozen, exclusions


def _pin_source(row: Mapping[str, Any], known_sha: str | None = None) -> dict[str, Any]:
    source = str(row.get("source_path", ""))
    if not source:
        raise ProfitCohortError(f"event lacks source path: {_event_key(row)}")
    actual = known_sha or str(row.get("source_sha256") or "")
    if not actual:
        path = _path(source)
        if not path.exists():
            raise ProfitCohortError(f"cannot pin missing source: {source}")
        actual = _sha(path)
    return {"source_path": source, "sha256": actual, **{key: row[key] for key in ("bar_minutes", "venue", "symbol") if key in row}}


def collect(plan: Path, source_manifest_paths: Sequence[str | Path], output_dir: Path) -> dict[str, Any]:
    """Freeze SHA-pinned Grade-A candidates and emit label-compatible sources."""

    if Path(output_dir).exists():
        raise ProfitCohortError(f"refusing to overwrite frozen collection output: {output_dir}")
    plan_path = _path(plan)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise ProfitCohortError("wrong experiment plan")
    _, contract_input = _training_contract(plan_path, required=False)
    inputs: list[dict[str, str]] = [{"path": _relative(plan_path), "sha256": _sha(plan_path)}]
    if contract_input:
        inputs.append(contract_input)
    candidates: list[dict[str, Any]] = []
    pinned_sources: dict[str, dict[str, Any]] = {}
    receipt_index = _receipt_index(plan_path)
    for raw_path in source_manifest_paths:
        manifest_path = _path(raw_path)
        manifest_input = {"path": _relative(manifest_path), "sha256": _sha(manifest_path)}
        inputs.append(manifest_input)
        for spec in _manifest_sources(manifest_path):
            receipt_path = _find_receipt(plan_path, spec, receipt_index)
            rows, receipt_input = _source_rows_from_receipt(receipt_path, spec, _sha(plan_path))
            inputs.append(receipt_input)
            for row in rows:
                sourced = {**row, "source_path": spec["source_path"], "source_sha256": spec["sha256"]}
                pinned_sources[str(spec["source_path"])] = _pin_source(sourced, str(spec["sha256"]))
                candidates.append(_origin_row(sourced, "mined_strict_grade_a"))
    for origin, path in (("legacy_binance_scored", DEFAULT_BINANCE_SCORED), ("legacy_ranked_manifest", DEFAULT_RANKED_MANIFEST)):
        if not path.exists():
            raise ProfitCohortError(f"missing required legacy cache: {path}")
        inputs.append({"path": _relative(path), "sha256": _sha(path)})
        for row in _read_jsonl(path):
            if _is_grade_a(row):
                pinned_sources[str(row["source_path"])] = _pin_source(row)
                candidates.append(_origin_row(row, origin))
    frozen, exclusions = _freeze(candidates)
    output_dir = Path(output_dir)
    events_path, exclusions_path, sources_path = output_dir / "frozen_events.jsonl", output_dir / "collection_exclusions.jsonl", output_dir / "frozen_sources.json"
    events_sha = _write_jsonl(events_path, frozen)
    exclusions_sha = _write_jsonl(exclusions_path, exclusions)
    sources_sha = _write_json(sources_path, {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "sources": [pinned_sources[key] for key in sorted(pinned_sources)], "inputs": inputs, "candidate_rows_before_clustering": len(candidates), "frozen_clusters": len(frozen)})
    receipt = {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "plan_sha256": _sha(plan_path), "training_contract_sha256": contract_input["sha256"] if contract_input else None, "inputs": inputs, "cluster_rule": "same canonical asset and direction; quality-sorted 4h NMS; retained representatives are strictly more than 4h apart; each rejected row is assigned once to its nearest selected representative", "profit_used_for_collection": False, "frozen_events_path": _relative(events_path), "frozen_events_sha256": events_sha, "collection_exclusions_path": _relative(exclusions_path), "collection_exclusions_sha256": exclusions_sha, "frozen_sources_path": _relative(sources_path), "frozen_sources_sha256": sources_sha, "frozen_clusters": len(frozen)}
    _write_json(output_dir / "collection_receipt.json", receipt)
    return receipt


def _load_rows(value: Sequence[Mapping[str, Any]] | Path | str) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    if isinstance(value, (str, Path)):
        path = _path(value)
        return _read_jsonl(path), {"path": _relative(path), "sha256": _sha(path)}
    return [dict(row) for row in value], None


def _is_retained_winner(row: Mapping[str, Any]) -> bool:
    profit = row.get("profit")
    if not isinstance(profit, Mapping) or profit.get("retained") is not True or profit.get("outcome") != "TP":
        return False
    try:
        gross, net = float(profit["gross_r"]), float(profit["net_r"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(gross) and math.isfinite(net) and gross >= 3.0 - 1e-10 and net > 0.0


def _is_resolved(row: Mapping[str, Any]) -> bool:
    profit = row.get("profit")
    return bool(isinstance(profit, Mapping) and str(profit.get("outcome", "")).upper() in {"TP", "SL", "TIMEOUT"})


def _reference_neighborhoods(value: Sequence[Mapping[str, Any]] | Path | str) -> tuple[list[tuple[str, pd.Timestamp]], dict[str, str] | None]:
    if isinstance(value, (str, Path)):
        path = _path(value)
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("events", payload) if isinstance(payload, Mapping) else payload
        source = {"path": _relative(path), "sha256": _sha(path)}
    else:
        rows, source = value, None
    return [(canonical_asset(row["symbol"]), _utc(row.get("anchor_time") or row.get("core_end_time"))) for row in rows], source


def _selected_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("event_id", "cluster_id", "split", "canonical_asset", "direction", "core_end_time")}


def select_training_cohort(plan: Path | Mapping[str, Any], labelled_events: Sequence[Mapping[str, Any]] | Path | str, reference_exclusion: Sequence[Mapping[str, Any]] | Path | str, out: Path) -> dict[str, Any]:
    """Gate actual label output; never recompute or overwrite pipeline splits."""

    if Path(out).exists():
        raise ProfitCohortError(f"refusing to overwrite frozen selection output: {out}")
    contract: dict[str, Any] | None = None
    if isinstance(plan, Mapping):
        plan_payload, plan_input, contract_input = dict(plan), None, None
    else:
        plan_path = _path(plan)
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_input = {"path": _relative(plan_path), "sha256": _sha(plan_path)}
        contract, contract_input = _training_contract(plan_path, required=True)
    if plan_payload.get("experiment_id") != EXPERIMENT_ID:
        raise ProfitCohortError("wrong experiment plan")
    plan_min, plan_max = int(plan_payload["discovery"]["minimum_independent_winners"]), int(plan_payload["discovery"]["maximum_dataset_winners"])
    minimum, maximum = (int(contract["minimum_train_winners"]), int(contract["maximum_train_winners"])) if contract else (plan_min, plan_max)
    if (minimum, maximum) != (plan_min, plan_max) or (minimum, maximum) != (3000, 5000):
        raise ProfitCohortError("training quota must remain the pinned 3000/5000 contract")
    labels, labels_input = _load_rows(labelled_events)
    references, references_input = _reference_neighborhoods(reference_exclusion)
    seen_clusters: set[str] = set()
    selection_ledger: list[dict[str, Any]] = []
    for row in labels:
        cluster_id, split = str(row.get("cluster_id", "")), str(row.get("split", ""))
        if not cluster_id or cluster_id in seen_clusters:
            raise ProfitCohortError("labelled events must contain one unique frozen cluster representative")
        if split not in {"train", "val", "test", "purged"}:
            raise ProfitCohortError(f"missing or invalid pipeline split: {split!r}")
        seen_clusters.add(cluster_id)
        event = dict(row)
        stamp = _event_time(event)
        asset = canonical_asset(event.get("canonical_asset") or event.get("symbol"))
        ref_excluded = split in {"val", "test"} and any(ref_asset == asset and abs(stamp - ref_time) <= GAP for ref_asset, ref_time in references)
        event.update({"canonical_asset": asset, "reference69_excluded": ref_excluded})
        selection_ledger.append(event)
    train_winners = [row for row in selection_ledger if row["split"] == "train" and _is_retained_winner(row)]
    capacity_gate = len(train_winners) >= minimum
    chosen_clusters: set[str] = set()
    if capacity_gate:
        chosen_clusters = {str(row["cluster_id"]) for row in sorted(train_winners, key=lambda row: hashlib.sha256(str(row["event_id"]).encode()).hexdigest())[:maximum]}
    dataset_ledger: list[dict[str, Any]] = []
    for row in selection_ledger:
        selected_train = row["split"] == "train" and str(row["cluster_id"]) in chosen_clusters
        evaluation_kept = capacity_gate and row["split"] in {"val", "test"} and _is_resolved(row) and not row["reference69_excluded"]
        reason = "selected_train_winner" if selected_train else "evaluation_resolved" if evaluation_kept else "capacity_gate_closed" if not capacity_gate else "reference69_neighborhood" if row["reference69_excluded"] else "purged_by_label_pipeline" if row["split"] == "purged" else "unresolved_evaluation" if row["split"] in {"val", "test"} else "train_nonwinner_or_capacity_gate"
        row["dataset_kept"], row["dataset_reason"] = selected_train or evaluation_kept, reason
        if row["dataset_kept"]:
            dataset_ledger.append(row)
    out = Path(out)
    selection_path, dataset_path = out / "selection_ledger.jsonl", out / "dataset_ledger.jsonl"
    selection_sha, dataset_sha = _write_jsonl(selection_path, selection_ledger), _write_jsonl(dataset_path, dataset_ledger)
    events = [_selected_projection(row) for row in dataset_ledger]
    receipt = {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "quota_scope": "train_independent_retained_events", "capacity_gate": capacity_gate, "train_retained_winners_actual": len(train_winners), "train_retained_winners_selected": len(chosen_clusters), "minimum_train_winners": minimum, "maximum_train_winners": maximum, "capacity_semantics": "3000-5000 applies only to retained independent train winners; resolved val/test events are never removed by the train cap", "production_dataset_allowed": capacity_gate, "training_contract_sha256": contract_input["sha256"] if contract_input else None, "selection_ledger_path": _relative(selection_path), "selection_ledger_sha256": selection_sha, "dataset_ledger_path": _relative(dataset_path), "dataset_ledger_sha256": dataset_sha, "selected_events_sha256": dataset_sha, "events": events, "inputs": [item for item in (plan_input, contract_input, labels_input, references_input) if item]}
    _write_json(out / "selection_receipt.json", receipt)
    return receipt


def verify_selected_cohort(ledger_path: Path | str, receipt_path: Path | str, training_contract_path: Path | str) -> dict[str, Any]:
    """Fail closed for renderer/trainer use of a selected cohort."""

    ledger_file, receipt_file, contract_file = _path(ledger_path), _path(receipt_path), _path(training_contract_path)
    ledger, receipt, contract = _read_jsonl(ledger_file), json.loads(receipt_file.read_text(encoding="utf-8")), json.loads(contract_file.read_text(encoding="utf-8"))
    if receipt.get("experiment_id") != EXPERIMENT_ID or contract.get("experiment_id") != EXPERIMENT_ID:
        raise ProfitCohortError("wrong selection experiment")
    if receipt.get("dataset_ledger_sha256") != _sha(ledger_file) or receipt.get("selected_events_sha256") != _sha(ledger_file):
        raise ProfitCohortError("selection receipt ledger SHA drift")
    if receipt.get("training_contract_sha256") != _sha(contract_file):
        raise ProfitCohortError("selection receipt training contract SHA drift")
    if not receipt.get("capacity_gate"):
        raise ProfitCohortError("selection capacity gate is closed")
    if (receipt.get("minimum_train_winners"), receipt.get("maximum_train_winners"), contract.get("minimum_train_winners"), contract.get("maximum_train_winners")) != (3000, 5000, 3000, 5000):
        raise ProfitCohortError("selection quota drift")
    if receipt.get("quota_scope") != "train_independent_retained_events" or contract.get("quota_scope") != "train_independent_retained_events":
        raise ProfitCohortError("selection quota scope drift")
    projection = [_selected_projection(row) for row in ledger]
    if receipt.get("events") != projection:
        raise ProfitCohortError("selection receipt events do not exactly match dataset ledger")
    clusters = [str(row.get("cluster_id", "")) for row in ledger]
    if not all(clusters) or len(clusters) != len(set(clusters)):
        raise ProfitCohortError("dataset ledger does not contain one representative per cluster")
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ledger:
        if row.get("split") not in {"train", "val", "test"}:
            raise ProfitCohortError("invalid selected split")
        if canonical_asset(row.get("canonical_asset") or row.get("symbol")) != row.get("canonical_asset"):
            raise ProfitCohortError("canonical asset drift")
        by_key[(str(row["canonical_asset"]), str(row["direction"]))].append(row)
    for group in by_key.values():
        ordered = sorted(group, key=_event_time)
        if any(_event_time(right) - _event_time(left) <= GAP for left, right in zip(ordered, ordered[1:])):
            raise ProfitCohortError("representatives are not strictly more than 4h apart")
    train = [row for row in ledger if row["split"] == "train"]
    if not (3000 <= len(train) <= 5000) or any(not _is_retained_winner(row) for row in train):
        raise ProfitCohortError("selected train winners violate capacity contract")
    if len(train) != receipt.get("train_retained_winners_selected"):
        raise ProfitCohortError("selection receipt train count drift")
    return {"verified": True, "selected_train_winners": len(train), "ledger_sha256": _sha(ledger_file)}


def _formal_guard(paths: Sequence[Path]) -> None:
    """Require committed builders and immutable formal inputs at the CLI boundary."""

    from yoyo.datasets.ma_profit_pipeline import committed

    committed([Path(__file__), *paths])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--plan", required=True, type=Path)
    collect_parser.add_argument("--sources", required=True, action="append", type=Path)
    collect_parser.add_argument("--out", required=True, type=Path)
    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--plan", required=True, type=Path)
    select_parser.add_argument("--events", required=True, type=Path)
    select_parser.add_argument("--reference-exclusion", required=True, type=Path)
    select_parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = _path(args.plan)
    contract = plan.parent / "training_contract.json"
    if not contract.exists():
        raise ProfitCohortError(f"missing committed training contract: {contract}")
    if args.command == "collect":
        _formal_guard([plan, contract, *(_path(path) for path in args.sources)])
        result = collect(plan, args.sources, args.out)
    else:
        _formal_guard([plan, contract, _path(args.events), _path(args.reference_exclusion)])
        result = select_training_cohort(plan, args.events, args.reference_exclusion, args.out)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

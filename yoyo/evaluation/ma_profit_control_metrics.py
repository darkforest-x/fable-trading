"""Pair frozen Profit3R model events with preselected random-entry controls.

Controls are averaged within each target before averaging across targets.  This
preserves the evaluation event as the unit of analysis when some events have
fewer matches or a raw control is reused.  No matching, resampling or threshold
selection occurs here; only frozen labels and actual model scores are consumed.
The resulting differences are descriptive, not independent-trade significance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
RESOLVED = {"TP", "SL", "TIMEOUT"}


class ControlMetricError(ValueError):
    """A frozen control, target or prediction lineage failed validation."""


def _indexed(rows: Sequence[Mapping[str, Any]], name: str) -> dict[str, Mapping[str, Any]]:
    indexed = {str(row.get("event_id", "")): row for row in rows}
    if "" in indexed or len(indexed) != len(rows):
        raise ControlMetricError(f"blank or duplicate {name} event ID")
    return indexed


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ControlMetricError("missing or invalid economic value") from exc
    if not math.isfinite(result):
        raise ControlMetricError("nonfinite economic value")
    return result


def _economics(row: Mapping[str, Any]) -> dict[str, float]:
    profit = row["profit"]
    risk, entry = _number(profit["risk_price"]), _number(profit["entry_price"])
    if risk <= 0 or entry <= 0 or profit["outcome"] not in RESOLVED:
        raise ControlMetricError("economic comparison requires resolved positive risk and entry")
    gross_r, net_r = _number(profit["gross_r"]), _number(profit["net_r"])
    if not math.isclose((gross_r-net_r)*risk/entry, .002, abs_tol=1e-9):
        raise ControlMetricError("economic outcome differs from frozen round-trip cost")
    return {"gross_bp": gross_r*risk/entry*1e4, "net_bp": net_r*risk/entry*1e4,
            "gross_r": gross_r, "net_r": net_r, "risk_bp": risk/entry*1e4,
            "tp_rate": float(profit["outcome"] == "TP"), "net_profitable_rate": float(net_r > 0)}


def _mean(rows: Sequence[Mapping[str, float]]) -> dict[str, float | None]:
    keys = ("gross_bp", "net_bp", "gross_r", "net_r", "risk_bp", "tp_rate", "net_profitable_rate")
    return {key: sum(row[key] for row in rows)/len(rows) if rows else None for key in keys}


def compare_controls(
    model_events: Sequence[Mapping[str, Any]], target_ledger: Sequence[Mapping[str, Any]],
    frozen_controls: Sequence[Mapping[str, Any]], labelled_controls: Sequence[Mapping[str, Any]],
    selection_receipt: Mapping[str, Any], *, split: str,
) -> dict[str, Any]:
    """Report same-population candidate/control differences without reweighting."""
    if split not in {"val", "test"}:
        raise ControlMetricError("val and test must be compared separately")
    all_targets = _indexed([row for row in target_ledger if row.get("split") in {"val", "test"}], "target")
    targets = {key: row for key, row in all_targets.items() if row["split"] == split}
    events = _indexed(model_events, "model")
    if not targets or set(events) != set(targets) or any(row.get("split") != split for row in model_events):
        raise ControlMetricError("model events must cover the exact target split")
    selected, labelled = _indexed(frozen_controls, "selected control"), _indexed(labelled_controls, "labelled control")
    if set(selected) != set(labelled):
        raise ControlMetricError("selected and labelled control sets differ; no outcome resampling allowed")
    desired = int(selection_receipt.get("desired_per_event", 0))
    shortages = selection_receipt.get("shortages", [])
    by_target = {str(row["matched_event_id"]): row for row in shortages}
    if desired != 5 or len(by_target) != len(shortages) or set(by_target) != set(all_targets):
        raise ControlMetricError("selection receipt does not cover the frozen full target pool")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    reused_outcomes: dict[str, Mapping[str, Any]] = {}
    for event_id, original in selected.items():
        row = labelled[event_id]
        # The label resolver must preserve all frozen selection fields.
        if any(key not in row or row[key] != value for key, value in original.items()):
            raise ControlMetricError(f"control lineage changed during labelling: {event_id}")
        target_id = str(original.get("matched_event_id", ""))
        if target_id not in all_targets:
            raise ControlMetricError("control references a target outside the full pool")
        target = all_targets[target_id]
        for key in ("source_path", "canonical_asset", "direction", "bar_minutes", "core_bars", "split"):
            if original[key] != target[key]:
                raise ControlMetricError(f"control/target matching drift: {key}")
        identity = "|".join(str(original[key]) for key in ("source_path", "direction", "source_core_start_i", "source_core_end_i"))
        if original.get("control_identity") != identity:
            raise ControlMetricError("control identity differs from source coordinates")
        if row.get("profit", {}).get("outcome") not in RESOLVED | {"UNKNOWN", "INVALID"}:
            raise ControlMetricError("control missing resolver outcome")
        if identity in reused_outcomes and reused_outcomes[identity] != row["profit"]:
            raise ControlMetricError("reused raw control has inconsistent resolver outcomes")
        reused_outcomes[identity] = row["profit"]
        grouped[target_id].append(row)
    for event_id, shortage in by_target.items():
        count = len(grouped[event_id])
        orders = [int(row["match_order"]) for row in grouped[event_id]]
        if int(shortage["requested"]) != desired or int(shortage["selected"]) != count or int(shortage["shortage"]) != desired-count or sorted(orders) != list(range(1, count+1)) or count > desired:
            raise ControlMetricError("control count/order differs from frozen receipt")
    paired: dict[str, dict[str, Any]] = {}
    for event_id, target in targets.items():
        event, economics = events[event_id], _economics(target)
        for key in ("direction", "bar_minutes", "split"):
            if event[key] != target[key]:
                raise ControlMetricError(f"model/target identity drift: {key}")
        for key in ("gross_bp", "net_bp", "gross_r", "net_r"):
            if not math.isclose(_number(event[key]), economics[key], abs_tol=1e-8):
                raise ControlMetricError(f"model/target economic drift: {key}")
        if event["outcome"] != target["profit"]["outcome"] or bool(event["retained"]) != bool(target["profit"]["retained"]):
            raise ControlMetricError("model/target label drift")
        controls = grouped[event_id]
        resolved = [row for row in controls if row["profit"]["outcome"] in RESOLVED]
        paired[event_id] = {"event_id": event_id, "candidate": economics,
                            "control_mean": _mean([_economics(row) for row in resolved]),
                            "selected_controls": len(controls), "resolved_controls": len(resolved),
                            "unresolved_controls": dict(Counter(row["profit"]["outcome"] for row in controls if row["profit"]["outcome"] not in RESOLVED)),
                            "shortage_reason": by_target[event_id]["reason"]}

    def summarize(ids: Sequence[str]) -> dict[str, Any]:
        available = [paired[event_id] for event_id in ids if paired[event_id]["resolved_controls"]]
        control_rows = [row for event_id in ids for row in grouped[event_id]]
        reuse = Counter(str(row["control_identity"]) for row in control_rows)
        candidate = _mean([row["candidate"] for row in available])
        controls = _mean([row["control_mean"] for row in available])
        return {"requested_events": len(ids), "paired_events": len(available),
                "unmatched_events": len(ids)-len(available),
                "fewer_than_five_resolved": sum(paired[event_id]["resolved_controls"] < desired for event_id in ids),
                "candidate_on_all_requested": _mean([paired[event_id]["candidate"] for event_id in ids]),
                "candidate_on_paired_events": candidate, "per_event_mean_control": controls,
                "candidate_minus_control": {key: candidate[key]-controls[key] if available else None for key in candidate},
                "selected_controls": len(control_rows), "control_outcomes": dict(Counter(row["profit"]["outcome"] for row in control_rows)),
                "unique_raw_controls": len(reuse), "reused_raw_controls": sum(count > 1 for count in reuse.values()), "max_raw_control_reuse": max(reuse.values(), default=0)}

    def groups(ids: Sequence[str]) -> dict[str, Any]:
        model = sorted(ids, key=lambda key: (-_number(events[key]["score"]), key))
        count = math.ceil(.1*len(ids))
        result = {"all": summarize(ids), "model_top10": summarize(model[:count]),
                  "model_top10_selection": "constant_score_arbitrary_id_tiebreak" if len({events[key]["score"] for key in ids}) <= 1 else "score_ranked_with_id_tiebreak",
                  "triggered_candidate_direction": summarize([key for key in ids if events[key]["same_direction_deployed"]])}
        if any(events[key].get("quality_score") is None for key in ids):
            result["quality_top10"] = {"status": "not_computable_missing_score"}
        else:
            quality = sorted(ids, key=lambda key: (-_number(events[key]["quality_score"]), key))
            result["quality_top10"] = summarize(quality[:count])
            result["quality_top10_selection"] = "constant_score_arbitrary_id_tiebreak" if len({events[key]["quality_score"] for key in ids}) <= 1 else "score_ranked_with_id_tiebreak"
        return result

    strata: dict[str, list[str]] = defaultdict(list)
    for event_id, row in targets.items():
        strata[f"direction={row['direction']}"].append(event_id)
        strata[f"timeframe={row['bar_minutes']}m"].append(event_id)
    return {"status": "completed", "split": split, "overall": groups(sorted(targets)),
            "strata": {key: groups(sorted(ids)) for key, ids in sorted(strata.items())},
            "pairs": [paired[key] for key in sorted(paired)],
            "estimand": "Candidate return minus per-event average resolved matched controls, averaged equally across paired target events. UNKNOWN/INVALID controls remain counted and are never replaced. Reuse and overlapping horizons prevent an independent-trade claim.",
            "ranking_note": "Top-decile membership is selected from the full score pool before matching availability; unmatched top events are not replaced.",
            "production_eligible": False}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True, help="one arm's completed evaluation directory")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True, help="frozen random control directory")
    parser.add_argument("--outcomes", type=Path, required=True, help="labelled control directory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ControlMetricError("refusing to overwrite existing comparison")
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ControlMetricError("main branch required")
    builder = str(Path(__file__).relative_to(ROOT))
    if subprocess.check_output(["git", "status", "--porcelain", "--", builder], cwd=ROOT, text=True).strip():
        raise ControlMetricError("commit comparison builder before formal use")
    evaluation = json.loads((args.evaluation/"receipt.json").read_text())
    selected = json.loads((args.controls/"receipt.json").read_text())
    labelled = json.loads((args.outcomes/"summary.json").read_text())
    ledger_sha = _sha(args.ledger)
    if evaluation.get("status") != "completed" or evaluation.get("ledger_sha256") != ledger_sha:
        raise ControlMetricError("evaluation is incomplete or uses another ledger")
    if not any(row.get("sha256") == ledger_sha for row in selected.get("inputs", [])):
        raise ControlMetricError("control selection was not frozen for this target ledger")
    control_sha = _sha(args.controls/"frozen_events.jsonl")
    source_sha = _sha(args.controls/"frozen_sources.json")
    if control_sha != selected["frozen_events_sha256"] or source_sha != selected["frozen_sources_sha256"] or control_sha != labelled["input_events_sha256"] or source_sha != labelled["source_manifest_sha256"] or labelled["lineage_errors"] != 0 or _sha(args.outcomes/"outcomes.jsonl") != labelled["outcomes_sha256"]:
        raise ControlMetricError("control selection/label integrity failure")
    reports = {}
    paths = [Path(__file__), args.ledger, args.evaluation/"receipt.json", args.controls/"receipt.json", args.controls/"frozen_events.jsonl", args.controls/"frozen_sources.json", args.outcomes/"summary.json", args.outcomes/"outcomes.jsonl"]
    for split in evaluation["splits"]:
        path = args.evaluation/f"events_{split}.jsonl"
        if _sha(path) != evaluation["artifacts"][path.name]:
            raise ControlMetricError("evaluation event score integrity failure")
        paths.append(path)
        reports[split] = compare_controls(_jsonl(path), _jsonl(args.ledger), _jsonl(args.controls/"frozen_events.jsonl"), _jsonl(args.outcomes/"outcomes.jsonl"), selected, split=split)
    args.out.mkdir(parents=True)
    for split, report in reports.items():
        (args.out/f"matched_metrics_{split}.json").write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    receipt = {"status": "completed", "arm": evaluation["arm"], "inputs": [{"path": str(path.resolve()), "sha256": _sha(path)} for path in paths], "artifacts": {f"matched_metrics_{split}.json": _sha(args.out/f"matched_metrics_{split}.json") for split in reports}, "production_eligible": False}
    (args.out/"receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True)+"\n")
    print(json.dumps({"status": "completed", "arm": evaluation["arm"], "splits": list(reports)}))


if __name__ == "__main__":
    main()

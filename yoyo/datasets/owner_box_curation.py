"""Publish frozen Owner-review audit findings without mutating source labels.

The 2026-09-07 Owner authorized installed dataset tools and review prioritization.
Only the primary pre-holdout review images are cataloged. Their rectangles are
unconfirmed geometry proposals, not ground truth or learned-model predictions.
FiftyOne 1.21 public SDK (Dataset, Sample, Detections, saved views) is used in its
already installed isolated environment and the configured MCP database directory.
No OHLCV, reference/future pixels, trained models or random splits are used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from yoyo.contracts.holdout import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
EXPERIMENT = ROOT / "experiments/active/exp-owner-box-curation-20260907-v1"
RESULTS = EXPERIMENT / "results"
PREREG = EXPERIMENT / "preregistration.json"
PROTOCOL = "owner_box_curation_v1"
NAME = "fable_owner_box_curation_20260907_v1"
MANIFEST_SHA = "7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641"
OLD_RESULTS = ROOT / "experiments/active/exp-owner-box-refinement-20260907-v1/results"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def frozen_json(path: Path, value: object) -> None:
    """Permit identical re-entry; never overwrite prior evidence with drift."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text() != text:
            raise ValueError(f"Frozen output differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(text)


def committed_source() -> dict:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ValueError("This repository requires main")
    paths = [Path(__file__), PREREG, ROOT / "tests/test_owner_box_curation.py", ROOT / "yoyo/contracts/holdout.py"]
    hashes = {}
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        content = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT)
        if hashlib.sha256(content).hexdigest() != sha(path):
            raise ValueError(f"Commit source before execution: {rel}")
        hashes[rel] = sha(path)
    commit = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", *hashes], cwd=ROOT, text=True).strip()
    return {"source_commit": commit, "code_sha256": hashes}


def load_rows() -> tuple[list[dict], dict]:
    config = json.loads(PREREG.read_text())
    for path, expected in config["inputs"].items():
        if sha(ROOT / path) != expected:
            raise ValueError(f"Preregistered input changed: {path}")
    rows = [json.loads(line) for line in (PACK / "manifest.jsonl").read_text().splitlines()]
    if len(rows) != 2513 or len({r["review_id"] for r in rows}) != 2513:
        raise ValueError("Frozen population or unique review identity differs")
    for row in rows:
        validate_row(row)
    return rows, config


def validate_row(row: dict) -> Path:
    relative = Path(row["asset_roles"]["image"])
    if relative.parts != ("annotation_images", row["review_id"] + ".png"):
        raise ValueError("Only primary annotation-image roles are permitted")
    start, end = [datetime.fromisoformat(row[key].replace("Z", "+00:00")) for key in ("main_start_time", "main_end_time")]
    if (start.tzinfo is None or end.tzinfo is None or start > end or end + timedelta(minutes=15) > HOLDOUT_START):
        raise ValueError("Unproven or post-boundary primary window")
    if (row["owner_side"] not in {"long", "short"} or row["main_canvas_role"] != "review_only_not_a_training_input"
            or any(row[k] is not False for k in ("new_gold", "sample_owner_geometry_confirmed", "training_eligible", "production_eligible"))):
        raise ValueError("Proposal confirmation or eligibility changed")
    width, height = row["chart_transform"]["width"], row["chart_transform"]["height"]
    if (width, height) != (1280, 742):
        raise ValueError("Primary canvas dimensions changed")
    p = row["proposal"]
    coordinates = [p[k] for k in ("x0", "y0", "x1", "y1")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in coordinates):
        raise ValueError("Invalid proposal coordinate")
    if not (0 <= p["x0"] < p["x1"] <= width and 0 <= p["y0"] < p["y1"] <= height):
        raise ValueError("Proposal escapes image")
    path = PACK / relative
    if path.resolve() != PACK.resolve() / relative:
        raise ValueError("Primary image path redirects outside its declared role")
    return path


def make_queue(rows: list[dict], quality: dict, overlap: dict, task_ids: dict) -> dict:
    """Rank review effort, never claim a quality probability or population error rate."""
    by_id = {r["review_id"]: r for r in rows}
    if len(by_id) != len(rows) or set(task_ids) != set(by_id) or len(set(task_ids.values())) != len(by_id):
        raise ValueError("Review/task identity join is not one-to-one")
    tool_rows = quality["rows"]
    if len(tool_rows) != len(rows) or {r["review_id"] for r in tool_rows} != set(by_id):
        raise ValueError("Quality results must cover every source identity")
    if any(r.get("retained") is not True for r in tool_rows):
        raise ValueError("An audit must not remove samples")
    if any(r.get("box_id") != by_id[r["review_id"]]["box_id"] for r in tool_rows):
        raise ValueError("Quality row belongs to a different Owner box")
    issues = {r["review_id"]: r["issues"] for r in tool_rows}
    duplicate_ids = set()
    for kind in ("exact_duplicates", "near_duplicates"):
        for group in quality["groups"][kind]:
            if len(group) < 2 or len(set(group)) != len(group) or not set(group) <= set(by_id):
                raise ValueError("Invalid duplicate review group")
            duplicate_ids.update(group)
    conflicts = defaultdict(set)
    for pair in overlap["possible_visible_negative_conflict"]["pairs"]:
        conflicts[pair["owner_box_id"]].add(pair["current_event_key"])
    records = []
    for row in rows:
        rid = row["review_id"]
        w, p = row["wick_reference_box"], row["proposal"]
        ref_height = w["y1"] - w["y0"]
        if ref_height <= 0:
            raise ValueError("Wick reference cannot have zero height")
        ratio = (p["y1"] - p["y0"]) / ref_height
        flags = list(issues[rid])
        if rid in duplicate_ids:
            flags.append("duplicate_review_candidate")
        current_conflicts = sorted(conflicts[row["box_id"]])
        if current_conflicts:
            flags.append("existing_negative_window_conflict")
        if row["exact_star"]:
            flags.append("original_exact_star")
        records.append({"review_id": rid, "owner_box_id": row["box_id"], "task_id": task_ids[rid],
            "symbol": row["symbol"], "owner_side": row["owner_side"], "source_split": row["original_split"],
            "image_relative": row["asset_roles"]["image"], "image_sha256": row["assets"][row["asset_roles"]["image"]],
            "primary_start_time": row["main_start_time"], "primary_end_time": row["main_end_time"],
            "original_window_dependency_id": row["original_window_dependency_id"],
            "alias_candidate_group": row["alias_candidate_group"],
            "proposal_bbox": [p["x0"] / 1280, p["y0"] / 742, (p["x1"] - p["x0"]) / 1280, (p["y1"] - p["y0"]) / 742],
            "core_bars": p["core_bars"], "exact_star": row["exact_star"], "audit_flags": sorted(set(flags)),
            "negative_event_conflicts": current_conflicts, "envelope_expansion_ratio": ratio,
            "sample_owner_geometry_confirmed": False, "training_eligible": False, "production_eligible": False})
    records.sort(key=lambda r: (not bool(r["negative_event_conflicts"]),
        "duplicate_review_candidate" not in r["audit_flags"], -r["envelope_expansion_ratio"], r["review_id"]))
    for rank, record in enumerate(records, 1):
        record["review_priority_rank"] = rank
    priority = records[:50]
    view_specs = [
        ("priority", "先审：重点50张", priority),
        ("duplicates", "复核：重复候选", [r for r in records if "duplicate_review_candidate" in r["audit_flags"]]),
        ("negative_conflicts", "复核：正负冲突", [r for r in records if r["negative_event_conflicts"]]),
        ("star_references", "参考：原有星标", [r for r in records if r["exact_star"]]),
    ]
    groups = defaultdict(list)
    for r in rows:
        groups[r["assets"][r["asset_roles"]["image"]]].append(r)
    exact = [g for g in groups.values() if len(g) > 1]
    return {"schema_version": 1, "protocol_id": PROTOCOL, "source_manifest_sha256": MANIFEST_SHA,
        "population": len(records), "ranking_is_label_quality_probability": False,
        "all_records_retained": True, "rows": records,
        "views": [{"key": key, "title": title, "review_ids": [r["review_id"] for r in selected]}
                  for key, title, selected in view_specs if selected],
        "summary": {"owner_side_counts": dict(Counter(r["owner_side"] for r in records)),
            "exact_image_groups": len(exact), "exact_image_members": sum(len(g) for g in exact),
            "exact_image_cross_direction_groups": sum(len({r["owner_side"] for r in g}) > 1 for g in exact),
            "exact_image_cross_source_split_groups": sum(len({r["original_split"] for r in g}) > 1 for g in exact),
            "duplicate_review_candidates": len(duplicate_ids), "negative_conflict_owner_boxes": sum(bool(r["negative_event_conflicts"]) for r in records),
            "priority50_flags": dict(Counter(flag for r in priority for flag in r["audit_flags"]))}}


def prepare() -> dict:
    source = committed_source()
    rows, config = load_rows()
    for row in rows:
        path = validate_row(row)
        if sha(path) != row["assets"][row["asset_roles"]["image"]]:
            raise ValueError("Primary image changed before curation")
    quality_path = RESULTS / "quality_tools.json"
    quality = json.loads(quality_path.read_text())
    if (quality.get("protocol_id") != "owner_box_quality_tools_v1" or quality.get("manifest_sha256") != MANIFEST_SHA
            or quality.get("input_count") != len(rows) or quality.get("selected_role") != "image"
            or quality.get("deleted_count") != 0 or quality.get("labels_changed_count") != 0):
        raise ValueError("Quality report does not belong to this non-destructive primary-image audit")
    for key in ("future_images_read", "original_images_read", "comparison_images_read", "new_training", "new_model_inference", "new_gold"):
        if quality.get(key) is not False:
            raise ValueError("Quality report scope changed")
    for relative, expected in quality["details_sha256"].items():
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or sha(RESULTS / "quality_tools_details" / rel) != expected:
            raise ValueError("Quality detail drifted")
    overlap = json.loads((OLD_RESULTS / "current_event_coverage.json").read_text())
    task_ids = json.loads((OLD_RESULTS / "import_receipt.json").read_text())["task_ids"]
    queue = make_queue(rows, quality, overlap, task_ids)
    queue.update(source)
    queue["quality_tools_sha256"] = sha(quality_path)
    queue["preregistration_sha256"] = sha(PREREG)
    frozen_json(RESULTS / "review_queue.json", queue)
    return queue


def configure_fiftyone() -> None:
    """Use the pre-existing MCP database; never fall back to an older FO environment."""
    base = Path.home() / ".local/share/fable-trading/fiftyone"
    for suffix, directory in (("DATABASE_DIR", "database"), ("DATASET_ZOO_DIR", "dataset-zoo"),
                              ("DEFAULT_DATASET_DIR", "datasets"), ("MODEL_ZOO_DIR", "model-zoo"), ("PLUGINS_DIR", "plugins")):
        expected = str(base / directory)
        if os.environ.get("FIFTYONE_" + suffix, expected) != expected:
            raise ValueError("FiftyOne storage environment differs from configured MCP")
        os.environ["FIFTYONE_" + suffix] = expected
    if os.environ.get("FIFTYONE_DATABASE_URI"):
        raise ValueError("This audit does not connect to an alternate database URI")
    os.environ["FIFTYONE_DO_NOT_TRACK"] = "true"


def verify_sample(sample, record: dict) -> None:
    for key, value in record.items():
        if key != "proposal_bbox" and sample[key] != value:
            raise ValueError(f"Existing catalog field differs: {record['review_id']} {key}")
    if sample.filepath != str(PACK / record["image_relative"]):
        raise ValueError("Existing catalog image path changed")
    labels = sample.proposal_boxes.detections
    if (len(labels) != 1 or labels[0].label != record["owner_side"].upper()
            or labels[0].bounding_box != record["proposal_bbox"] or labels[0].confidence is not None):
        raise ValueError("Existing catalog proposal changed")
    if sorted(sample.tags) != sorted(set(record["audit_flags"] + ["review_only", "unconfirmed_proposal"])):
        raise ValueError("Existing catalog audit tags changed")


def publish(receipt_path: Path) -> dict:
    committed_source()
    queue = prepare()
    configure_fiftyone()
    import fiftyone as fo
    if fo.__version__ != "1.21.0":
        raise ValueError("Use the isolated FiftyOne 1.21.0 environment")
    fingerprint = digest(queue)
    created = NAME not in fo.list_datasets()
    dataset = fo.Dataset(NAME, persistent=True) if created else fo.load_dataset(NAME)
    info = {"protocol_id": PROTOCOL, "source_manifest_sha256": MANIFEST_SHA,
        "queue_fingerprint": fingerprint, "proposal_is_ground_truth": False,
        "proposal_is_model_prediction": False, "training_eligible": False,
        "production_eligible": False, "label_studio_project_id": 77}
    if created:
        dataset.info = info
        dataset.save()
    elif dataset.info != info:
        raise ValueError("Existing backend dataset provenance changed; never overwrite")
    expected = {r["review_id"]: r for r in queue["rows"]}
    seen = set()
    for sample in dataset:
        rid = sample["review_id"]
        if rid not in expected or rid in seen:
            raise ValueError("Existing catalog contains foreign or duplicate identities")
        verify_sample(sample, expected[rid])
        seen.add(rid)
    samples = []
    for record in queue["rows"]:
        if record["review_id"] in seen:
            continue
        fields = {k: v for k, v in record.items() if k != "proposal_bbox"}
        sample = fo.Sample(filepath=str(PACK / record["image_relative"]),
            tags=sorted(set(record["audit_flags"] + ["review_only", "unconfirmed_proposal"])),
            proposal_boxes=fo.Detections(detections=[fo.Detection(label=record["owner_side"].upper(), bounding_box=record["proposal_bbox"])]),
            metadata=fo.ImageMetadata(width=1280, height=742, num_channels=3, mime_type="image/png",
                                      size_bytes=(PACK / record["image_relative"]).stat().st_size), **fields)
        samples.append(sample)
    if samples:
        dataset.add_samples(samples)
    dataset.create_index("review_id", unique=True)
    if len(dataset) != len(expected):
        raise ValueError("Catalog population differs after insertion")
    for sample in dataset:
        verify_sample(sample, expected[sample["review_id"]])
    schema = dataset.get_field_schema()
    required = set(next(iter(expected.values()))) - {"proposal_bbox"}
    if not required <= set(schema) or "proposal_boxes" not in schema or "ground_truth" in schema or "predictions" in schema:
        raise ValueError("Catalog schema does not preserve unconfirmed-proposal semantics")
    views = []
    for spec in queue["views"]:
        view = dataset.match(fo.ViewField("review_id").is_in(spec["review_ids"])).sort_by("review_priority_rank")
        if spec["key"] not in dataset.list_saved_views():
            dataset.save_view(spec["key"], view, description=spec["title"] + "; review candidates only")
        actual = dataset.load_saved_view(spec["key"]).values("review_id")
        if actual != spec["review_ids"]:
            raise ValueError("Saved view membership or order changed")
        views.append({"key": spec["key"], "title": spec["title"], "count": len(actual)})
    receipt = {"generated_at": datetime.now(timezone.utc).isoformat(), "dataset_name": NAME,
        "created": created, "inserted": len(samples), "reused": len(seen), "samples": len(dataset),
        "views": views, "fiftyone_version": fo.__version__, "database_dir": fo.config.database_dir,
        "queue_sha256": sha(RESULTS / "review_queue.json"), "image_role": "annotation_images_only",
        "proposal_is_ground_truth": False, "source_labels_modified": False, "samples_deleted": 0}
    if receipt_path.exists():
        prior = json.loads(receipt_path.read_text())
        stable = ("dataset_name", "samples", "views", "fiftyone_version", "database_dir", "queue_sha256",
                  "image_role", "proposal_is_ground_truth", "source_labels_modified", "samples_deleted")
        if any(prior.get(key) != receipt[key] for key in stable):
            raise ValueError("Existing receipt describes another catalog; preserve it")
        receipt["existing_receipt_preserved"] = True
    else:
        frozen_json(receipt_path, receipt)
    return receipt


def serve() -> None:
    """Keep the optional backend App alive; Label Studio remains the user workbench."""
    configure_fiftyone()
    import fiftyone as fo
    dataset = fo.load_dataset(NAME)
    session = fo.launch_app(view=dataset.load_saved_view("priority"), port=5151, address="127.0.0.1", auto=False)
    print(json.dumps({"url": session.url, "dataset": NAME}), flush=True)
    session.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish", "serve"))
    parser.add_argument("--receipt", type=Path, default=RESULTS / "fiftyone_receipt.json")
    args = parser.parse_args()
    if args.command == "serve":
        serve()
    elif args.command == "publish":
        print(json.dumps(publish(args.receipt), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(prepare()["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

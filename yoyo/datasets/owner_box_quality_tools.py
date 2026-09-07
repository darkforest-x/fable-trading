"""Audit the frozen Owner review canvases with Datumaro and CleanVision.

Only manifest-selected main images are read. Historical direction and derived
geometry remain proposals, never ground truth. No OHLCV, future/reference image,
model, or training input is opened. Flags are review hints and never deletions.
SDK calls follow the locally installed Datumaro 1.12 / CleanVision 0.3.7 sources.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

from yoyo.contracts.holdout import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
EXPERIMENT = ROOT / "experiments/active/exp-owner-box-curation-20260907-v1"
MANIFEST_SHA256 = "7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641"
PROTOCOL = "owner_box_quality_tools_v1"
SIDES = ("long", "short")
ROLES = {"image": "annotation_images", "original_image": "original_reference_only",
         "comparison_image": "comparison_images", "future_image": "future_only/images"}
ISSUES = {"exact_duplicates": {"hash_type": "md5"},
          "near_duplicates": {"hash_type": "phash", "hash_size": 8},
          "odd_size": {"iqr_factor": 3.0}, "odd_aspect_ratio": {"threshold": 0.35}}
VALIDATOR_CONFIG = {"few_samples_thr": 1, "imbalance_ratio_thr": 50,
                    "far_from_mean_thr": 5, "dominance_ratio_thr": 0.8, "topk_bins": 0.1}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("naive main-window timestamp")
    return result.astimezone(timezone.utc)


def validate_metadata(rows: list[dict], pack: Path, *, expected_count: int = 2513) -> list[dict]:
    """Validate every row before any image is opened; returns explicit main-only items."""
    if len(rows) != expected_count or not rows:
        raise ValueError("unexpected manifest count")
    records, identities, boxes, paths = [], set(), set(), set()
    for row in rows:
        rid, box_id = row["review_id"], row["box_id"]
        if not re.fullmatch(r"[a-f0-9]{24}", rid) or rid in identities or box_id in boxes:
            raise ValueError("invalid or duplicate identity")
        identities.add(rid)
        boxes.add(box_id)
        if row["owner_side"] not in SIDES:
            raise ValueError("unsupported Owner direction")
        for flag in ("training_eligible", "production_eligible", "new_gold",
                     "sample_owner_geometry_confirmed", "future_values_used_for_proposal"):
            if row[flag] is not False:
                raise ValueError(f"unexpected confirmation/eligibility: {flag}")
        if row["main_canvas_role"] != "review_only_not_a_training_input":
            raise ValueError("unexpected main canvas role")
        roles = row["asset_roles"]
        if set(roles) != set(ROLES) or set(row["assets"]) != set(roles.values()):
            raise ValueError("expected exactly four separately identified asset roles")
        for role, directory in ROLES.items():
            relative = Path(roles[role])
            suffixes = {".png", ".jpg"} if role == "original_image" else {".png"}
            if relative.parent.as_posix() != directory or relative.stem != rid or relative.suffix not in suffixes:
                raise ValueError(f"asset role/path mismatch: {role}")
            if not re.fullmatch(r"[a-f0-9]{64}", row["assets"][roles[role]]):
                raise ValueError("invalid asset SHA256")
        start, end = instant(row["main_start_time"]), instant(row["main_end_time"])
        bars = row["main_end_i"] - row["main_start_i"] + 1
        if start > end or end + timedelta(minutes=15) > HOLDOUT_START:
            raise ValueError("main image crosses holdout close boundary")
        if end - start != timedelta(minutes=15 * (bars - 1)) or bars != row["chart_transform"]["n_bars"]:
            raise ValueError("main image clock/index mismatch")
        tf = row["chart_transform"]
        if (tf["width"], tf["height"]) != (1280, 742):
            raise ValueError("unexpected main canvas dimensions")
        proposal = row["proposal"]
        xyxy = [float(proposal[k]) for k in ("x0", "y0", "x1", "y1")]
        x0, y0, x1, y1 = xyxy
        if not all(math.isfinite(x) for x in xyxy) or not (0 <= x0 < x1 <= tf["width"] and 0 <= y0 < y1 <= tf["height"]):
            raise ValueError("invalid proposal geometry")
        main = pack / roles["image"]
        # Resolve only the selected main path. A symlink cannot redirect it into
        # a future/original directory (or outside this immutable pack).
        if main.resolve() != pack.resolve() / roles["image"] or str(main.resolve()) in paths:
            raise ValueError("main image symlink or duplicate path")
        paths.add(str(main.resolve()))
        records.append({"review_id": rid, "box_id": box_id, "owner_side": row["owner_side"],
                        "image": str(main.resolve()), "image_relative": roles["image"],
                        "image_sha256": row["assets"][roles["image"]], "xyxy": xyxy,
                        "width": tf["width"], "height": tf["height"],
                        "main_start_time": start.isoformat(), "main_end_time": end.isoformat(),
                        "alias_candidate_group": row["alias_candidate_group"],
                        "original_window_dependency_id": row["original_window_dependency_id"],
                        "exact_star": row["exact_star"], "annotation_role": "unconfirmed_geometry_proposal"})
    return records


def verify_images(records: list[dict]) -> None:
    """Read main assets only, after whole-manifest metadata validation."""
    from PIL import Image
    for record in records:
        path = Path(record["image"])
        if sha256(path) != record["image_sha256"]:
            raise ValueError(f"main image SHA mismatch: {record['review_id']}")
        with Image.open(path) as image:
            if image.size != (record["width"], record["height"]) or image.format != "PNG":
                raise ValueError(f"main image format/dimensions mismatch: {record['review_id']}")
            image.verify()


def jsonable(value):
    """Keep SDK reports, including tuple item keys and NumPy scalar statistics."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return jsonable(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def sdk_audit(records: list[dict], output: Path) -> dict:
    """Run actual installed SDKs on an already validated, explicit main-image list."""
    import datumaro as dm
    from datumaro.plugins.validators import DetectionValidator
    from cleanvision import Imagelab

    versions = {name: importlib.metadata.version(name)
                for name in ("datumaro", "cleanvision", "numpy", "pillow", "pandas", "ImageHash")}
    if versions["datumaro"] != "1.12.0" or versions["cleanvision"] != "0.3.7":
        raise ValueError("unexpected audit SDK versions")
    output.mkdir(parents=True, exist_ok=False)
    began = time.monotonic()
    items = []
    for r in records:
        x0, y0, x1, y1 = r["xyxy"]
        ann = dm.Bbox(x0, y0, x1-x0, y1-y0, label=SIDES.index(r["owner_side"]), id=1,
                      attributes={"role": "unconfirmed_geometry_proposal"})
        items.append(dm.DatasetItem(id=r["review_id"], subset="review",
                                    media=dm.Image.from_file(r["image"], size=(r["height"], r["width"])),
                                    annotations=[ann], attributes={"box_id": r["box_id"]}))
    labels = dm.LabelCategories.from_iterable([(side, "", {"role"}) for side in SIDES])
    dataset = dm.Dataset.from_iterable(items, categories={dm.AnnotationType.label: labels})
    dataset.export(str(output / "datumaro_proposals"), format="datumaro", save_media=False)
    imported = dm.Dataset.import_from(str(output / "datumaro_proposals"), format="datumaro")
    if len(imported) != len(records):
        raise ValueError("Datumaro round-trip lost identities")
    by_id = {r["review_id"]: r for r in records}
    for item in imported:
        r = by_id[item.id]
        if len(item.annotations) != 1 or item.subset != "review":
            raise ValueError("Datumaro round-trip changed annotations")
        ann = item.annotations[0]
        x0, y0, x1, y1 = r["xyxy"]
        if (ann.label != SIDES.index(r["owner_side"]) or item.attributes["box_id"] != r["box_id"]
                or ann.attributes["role"] != "unconfirmed_geometry_proposal"
                or not all(abs(a-b) < 1e-5 for a, b in zip(ann.get_bbox(), [x0, y0, x1-x0, y1-y0]))):
            raise ValueError("Datumaro round-trip changed proposal")
    validation = DetectionValidator(**VALIDATOR_CONFIG).validate(dataset)
    write_json(output / "datumaro_validation.json", validation)
    datumaro_seconds = time.monotonic() - began
    began = time.monotonic()
    paths = [r["image"] for r in records]
    lab = Imagelab(filepaths=paths, verbose=False)
    lab.find_issues(issue_types=ISSUES, n_jobs=1, verbose=False)
    if len(lab.issues) != len(records) or set(lab.issues.index) != set(paths):
        raise ValueError("CleanVision lost or added image identities")
    expected_columns = {f"{issue}_{suffix}" for issue in ISSUES for suffix in ("score",)}
    expected_columns |= {f"is_{issue}_issue" for issue in ISSUES}
    if set(lab.issues.columns) != expected_columns:
        raise ValueError("CleanVision ran an unexpected issue type")
    detail = []
    for r in records:
        flags = lab.issues.loc[r["image"]].to_dict()
        detail.append({**r, "cleanvision": jsonable(flags), "retained": True,
                       "review_requested": any(bool(flags[f"is_{key}_issue"]) for key in ISSUES)})
    (output / "per_image.jsonl").write_text("".join(json.dumps(jsonable(r), ensure_ascii=False,
                         sort_keys=True, allow_nan=False) + "\n" for r in detail), encoding="utf-8")
    by_path = {r["image"]: r["review_id"] for r in records}
    groups = {issue: [[by_path[p] for p in group] for group in lab.info[issue]["sets"]]
              for issue in ("exact_duplicates", "near_duplicates")}
    write_json(output / "duplicate_groups.json", groups)
    summary = {"versions": versions, "python": platform.python_version(),
               "sdk_source_sha256": {inspect.getfile(cls): sha256(Path(inspect.getfile(cls)))
                                     for cls in (DetectionValidator, Imagelab)},
               "datumaro": {"validation_summary": validation["summary"], "format_roundtrip_count": len(imported),
                            "categories": list(SIDES), "config": VALIDATOR_CONFIG, "wall_seconds": datumaro_seconds},
               "cleanvision": {"config": ISSUES, "n_jobs": 1, "wall_seconds": time.monotonic()-began,
                               "issue_summary": lab.issue_summary.to_dict(orient="records"),
                               "duplicate_group_counts": {k: len(v) for k, v in groups.items()},
                               "near_duplicate_semantics": "equal 64-bit pHash; exact-only groups omitted; no distance search"},
               "rows": [{"review_id": r["review_id"], "box_id": r["box_id"], "retained": True,
                         "issues": [key for key in ISSUES if r["cleanvision"][f"is_{key}_issue"]]} for r in detail],
               "groups": groups,
               "preserved_count": len(detail), "review_requested_count": sum(r["review_requested"] for r in detail),
               "deleted_count": 0, "labels_changed_count": 0, "annotation_role": "unconfirmed_geometry_proposal"}
    # No SDK is permitted to mutate the audited source images.
    verify_images(records)
    return summary


def source_identity() -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    if git("branch", "--show-current").decode().strip() != "main":
        raise ValueError("formal audit requires main")
    dependencies = [Path(__file__), ROOT / "tests/test_owner_box_quality_tools.py",
                    ROOT / "yoyo/contracts/holdout.py", EXPERIMENT / "preregistration.json"]
    hashes = {}
    for path in dependencies:
        relative = path.relative_to(ROOT).as_posix()
        if path.read_bytes() != git("show", f"HEAD:{relative}"):
            raise ValueError(f"source must be committed before formal audit: {relative}")
        hashes[relative] = sha256(path)
    return {"source_commit": git("rev-parse", "HEAD").decode().strip(), "code_sha256": hashes}


def run() -> dict:
    """Formal frozen-pack entry point; source-first gate precedes all image reads."""
    identity = source_identity()
    manifest = PACK / "manifest.jsonl"
    if sha256(manifest) != MANIFEST_SHA256:
        raise ValueError("frozen manifest SHA mismatch")
    receipt_path = PACK / "admin/build_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    prereg = json.loads((EXPERIMENT / "preregistration.json").read_text())
    for path in (manifest, receipt_path):
        if prereg["inputs"][path.relative_to(ROOT).as_posix()] != sha256(path):
            raise ValueError("preregistered input SHA mismatch")
    if prereg["population"] != 2513 or instant(prereg["holdout_start"]) != HOLDOUT_START:
        raise ValueError("preregistered population/boundary mismatch")
    if receipt["manifest_sha256"] != MANIFEST_SHA256 or receipt["tasks"] != 2513:
        raise ValueError("pack receipt/manifest mismatch")
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    records = validate_metadata(rows, PACK)
    results = EXPERIMENT / "results"
    results.mkdir(parents=True, exist_ok=True)
    final, details = results / "quality_tools.json", results / "quality_tools_details"
    if final.exists() or details.exists():
        raise FileExistsError("audit results already exist; never overwrite a prior run")
    verify_images(records)
    with tempfile.TemporaryDirectory(prefix="quality-tools-", dir=results) as temp:
        stage = Path(temp) / "details"
        tools = sdk_audit(records, stage)
        files = {p.relative_to(stage).as_posix(): sha256(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        report = {"protocol_id": PROTOCOL, "created_at": datetime.now(timezone.utc).isoformat(), **identity,
                  "manifest": str(manifest.relative_to(ROOT)), "manifest_sha256": MANIFEST_SHA256,
                  "build_receipt_sha256": sha256(receipt_path), "input_count": len(records),
                  "selected_role": "image", "excluded_roles": sorted(set(ROLES)-{"image"}),
                  "main_start_min": min(r["main_start_time"] for r in records),
                  "main_end_max": max(r["main_end_time"] for r in records),
                  "holdout_close_boundary": HOLDOUT_START.isoformat(), "holdout_read": False,
                  "ohlcv_read": False, "future_images_read": False, "original_images_read": False,
                  "comparison_images_read": False, "new_training": False, "new_model_inference": False,
                  "new_gold": False, "training_eligible": False, "production_eligible": False,
                  "flag_policy": "review_hint_only_all_identities_retained_no_automatic_removal_or_relabel",
                  "details_directory": details.name, "details_sha256": files, **tools}
        if sha256(manifest) != MANIFEST_SHA256 or source_identity()["code_sha256"] != identity["code_sha256"]:
            raise ValueError("source changed during audit")
        stage.rename(details)
        write_json(final, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

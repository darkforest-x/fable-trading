"""Add resolved outcome-target negatives without changing any parent v4 asset.

Owner requested a redo after discovering that train contained no negatives.
The target remains profitable_dense_{long,short}, not morphology alone. Labels
may use the frozen 12-hour outcome; rendered OHLC/HL2 SMA/EMA inputs stop at
core_end+5 close, including price bounds and the 1200-bar MA-only warmup.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as parent
from yoyo.datasets.ma_profit_cohort import canonical_asset

ROOT = Path(__file__).resolve().parents[2]
KINDS = {"SL", "TIMEOUT"}


class NegativeRedoError(RuntimeError):
    """A frozen input, label, temporal or population contract failed."""


def sha256(path: Path) -> str:
    return parent.sha256_file(path)


def resolve(raw: str | Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_rows(path: Path, value: list[dict]) -> None:
    path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in value), encoding="utf-8")


def utc(value: Any) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        raise NegativeRedoError("timestamps must be timezone-aware")
    return t.tz_convert("UTC")


def checked_plan(path: Path) -> tuple[dict, dict[str, Path]]:
    p = read_json(path)
    if p.get("schema_version") != 1 or p.get("experiment_id") != "exp-ma-profit3r-negatives-20260922-v2":
        raise NegativeRedoError("unknown redo contract")
    if not p.get("owner_authorization", {}).get("training_authorized"):
        raise NegativeRedoError("missing redo authorization")
    if p.get("training_eligible") is not False or p.get("production_eligible") is not False or p["owner_authorization"].get("promote") is not False or p["owner_authorization"].get("live_money") is not False:
        raise NegativeRedoError("offline-only safety contract drift")
    inputs = {k: resolve(v["path"]) for k, v in p["inputs"].items()}
    for k, v in inputs.items():
        if sha256(v) != p["inputs"][k]["sha256"]:
            raise NegativeRedoError("input SHA drift: " + k)
    if p["negative_policy"] != {"population": "all_resolved_parent_train_nonwinners", "protection_hours": 4, "ratio_cap": None}:
        raise NegativeRedoError("negative policy drift")
    return p, inputs


def committed(plan: Path) -> str:
    return parent._committed([Path(__file__), plan.resolve(), ROOT / "yoyo/datasets/ma_profit_dataset.py",
                              ROOT / "yoyo/layers/l1_detection/render.py"])


def protections(old_events: list[dict], refs: dict, manual: list[dict]) -> dict[str, list[tuple]]:
    result = defaultdict(list)
    margin = pd.Timedelta(hours=4)
    for r in old_events:
        if r["profit"]["retained"]:
            result[canonical_asset(r["canonical_asset"])].append((utc(r["core_start_time"])-margin, utc(r["core_end_time"])+pd.Timedelta(minutes=r["bar_minutes"])+margin))
    for r in refs["events"]:
        t = utc(r.get("anchor_time") or r["core_end_time"])
        result[canonical_asset(r["symbol"])].append((t-margin, t+margin))
    for r in manual:
        result[canonical_asset(r["symbol"])].append((utc(r["window_start_time"])-margin, utc(r["decision_time"])+pd.Timedelta(minutes=15)+margin))
    return dict(result)


def eligible(row: Mapping[str, Any]) -> bool:
    p = row.get("profit", {})
    return row.get("split") == "train" and p.get("retained") is False and p.get("outcome") in KINDS


def select_rows(pool: list[dict], protected: dict, train_end: str) -> tuple[list[dict], list[dict]]:
    """Take the complete eligible parent population; do not sample by future magnitude."""
    selected, excluded, seen = [], [], set()
    for r in sorted(pool, key=lambda x: str(x["event_id"])):
        eid = r["event_id"]
        if not eid or eid in seen:
            raise NegativeRedoError("duplicate/blank parent event id")
        seen.add(eid)
        if not eligible(r):
            continue
        mins = int(r["bar_minutes"])
        step = pd.Timedelta(minutes=mins)
        start, end = utc(r["core_start_time"]), utc(r["core_end_time"])
        visible_start = start-11*step
        decision = utc(r["profit"]["decision_close_time_utc"])
        label_end = utc(r["profit"]["label_window_end_utc"])
        reason = None
        if mins <= 0 or int((end-start)/step)+1 not in {4, 5} or end-start not in {3*step, 4*step} or decision != end+6*step:
            raise NegativeRedoError("candidate time geometry drift: " + eid)
        if r.get("purge_reason") or decision >= utc(train_end) or label_end >= utc(train_end) or label_end != decision+pd.Timedelta(hours=12):
            reason = "time_boundary_or_purged"
        asset = canonical_asset(r["canonical_asset"])
        if reason is None and any(visible_start <= hi and decision >= lo for lo, hi in protected.get(asset, [])):
            reason = "positive_or_owner_protection"
        if reason:
            excluded.append({"event_id": eid, "reason": reason})
        else:
            selected.append({**r, "negative_kind": "outcome_target_negative", "morphology_label": "unadjudicated_rule_candidate",
                             "parent_candidate_pool": True, "dataset_reason": "redo_resolved_outcome_negative", "dataset_kept": True})
    return selected, excluded


def select(plan_path: Path, out: Path) -> dict:
    p, inputs = checked_plan(plan_path)
    commit = committed(plan_path)
    if out.exists():
        raise FileExistsError(out)
    old, pool = rows(inputs["old_ledger"]), rows(inputs["candidate_ledger"])
    selected, excluded = select_rows(pool, protections(pool, read_json(inputs["reference_exclusion"]), rows(inputs["manual_rows"])), p["splits"]["train_end_exclusive"])
    if not selected:
        raise NegativeRedoError("zero negative training events")
    if set(r["event_id"] for r in selected) & set(r["event_id"] for r in old):
        raise NegativeRedoError("negative and original events overlap")
    out.mkdir(parents=True)
    write_rows(out / "negatives.jsonl", selected)
    write_rows(out / "exclusions.jsonl", excluded)
    receipt = {"status": "completed", "builder_commit": commit, "plan_sha256": sha256(plan_path),
               "negative_events": len(selected), "eligible_before_protection": sum(eligible(r) for r in pool),
               "negative_outcomes": dict(Counter(r["profit"]["outcome"] for r in selected)),
               "excluded": dict(Counter(r["reason"] for r in excluded)),
               "artifacts": {n: sha256(out/n) for n in ("negatives.jsonl", "exclusions.jsonl")}}
    write_json(out / "receipt.json", receipt)
    return receipt


def negative_assets(frame: pd.DataFrame, row: Mapping[str, Any]) -> list[dict]:
    """Render only the known prefix; profit label never changes pixel geometry."""
    if not eligible(row):
        raise NegativeRedoError("only resolved train outcome negatives may be rendered")
    times = pd.to_datetime(frame["open_time"], utc=True)
    found = [np.flatnonzero(times == utc(row[k])) for k in ("core_start_time", "core_end_time")]
    if any(len(x) != 1 for x in found):
        raise NegativeRedoError("negative core time missing/duplicated")
    start, end = (int(x[0]) for x in found)
    support, decision = start-11-parent.SUPPORT_BARS, end+5
    mins = int(row["bar_minutes"])
    if end-start+1 not in {4, 5} or support < 0 or decision >= len(frame):
        raise NegativeRedoError("negative support/confirmation missing")
    decision_close = times.iloc[decision]+pd.Timedelta(minutes=mins)
    if decision_close != utc(row["profit"]["decision_close_time_utc"]):
        raise NegativeRedoError("negative decision time drift")
    if not parent._known_input_continuous(times, support, decision, mins):
        raise NegativeRedoError("known input gap")
    prefix = frame.iloc[:decision+1]
    result = []
    for variant, pre in (("A", 9), ("B1", 7), ("B2", 11)):
        png, box, visible = parent._window_asset(prefix, core_start_i=start, core_end_i=end, pre_bars=pre,
            post_bars=5, support_start_i=support, price_scale=parent.VISIBLE_RANGE_PRICE_SCALE)
        visible.update({"visible_end_close_time_utc": decision_close.isoformat(), "decision_at_utc": decision_close.isoformat()})
        result.append({"variant": variant, "png": png, "candidate_box": box, "visible": visible})
    return result


def population_counts(manifest: list[dict]) -> dict:
    c = Counter()
    events = defaultdict(set)
    for r in manifest:
        kind = "positive" if r["class_id"] is not None else "negative"
        for a in r["arms"]:
            key = f"{a}/{r['split']}/{kind}"
            c[key] += 1
            events[key].add(r["event_id"])
    return {k: {"images": n, "events": len(events[k])} for k, n in sorted(c.items())}


def build(plan_path: Path, selection: Path, out: Path) -> dict:
    p, inputs = checked_plan(plan_path)
    commit = committed(plan_path)
    sr = read_json(selection/"receipt.json")
    if sr["status"] != "completed" or sr["plan_sha256"] != sha256(plan_path):
        raise NegativeRedoError("selection binding mismatch")
    for name, digest in sr["artifacts"].items():
        if Path(name).name != name or sha256(selection/name) != digest:
            raise NegativeRedoError("selection artifact mismatch")
    if out.exists():
        raise FileExistsError(out)
    old_root = resolve(p["parent_dataset_root"])
    manifest = rows(inputs["old_manifest"])
    old_events = rows(inputs["old_ledger"])
    negative_rows = rows(selection/"negatives.jsonl")
    pool = rows(inputs["candidate_ledger"])
    expected, excluded = select_rows(pool, protections(pool, read_json(inputs["reference_exclusion"]), rows(inputs["manual_rows"])), p["splits"]["train_end_exclusive"])
    if expected != negative_rows or excluded != rows(selection/"exclusions.jsonl"):
        raise NegativeRedoError("selection is not the complete frozen population")
    out.mkdir(parents=True)
    for split in ("train", "val", "test"):
        for kind in ("images", "labels"):
            (out/kind/split).mkdir(parents=True)
    for r in manifest:
        for field, hashfield in (("image_path", "image_sha256"), ("label_path", "label_sha256")):
            rel = Path(r[field])
            if rel.is_absolute() or ".." in rel.parts or sha256(old_root/rel) != r[hashfield]:
                raise NegativeRedoError("parent asset mismatch")
            shutil.copyfile(old_root/rel, out/rel)
    grouped = defaultdict(list)
    for r in negative_rows:
        grouped[r["source_path"]].append(r)
    skipped, accepted = [], []
    used_hashes = {r["image_sha256"] for r in manifest}
    for gi, (source, group) in enumerate(sorted(grouped.items()), 1):
        path = resolve(source)
        source_sha = sha256(path)
        if any(r["source_sha256"] != source_sha for r in group):
            raise NegativeRedoError("negative OHLC source SHA drift: " + source)
        boundary = max(utc(r["profit"]["decision_close_time_utc"]) for r in group)
        frame, _ = parent.read_preholdout_prefix(path, end_exclusive=boundary, bar_minutes=int(group[0]["bar_minutes"]))
        for r in group:
            try:
                assets = negative_assets(frame, r)
            except NegativeRedoError as exc:
                # Invalid known support is not a financial outcome or a label.
                skipped.append({"event_id": r["event_id"], "reason": str(exc)})
                continue
            digests = {hashlib.sha256(a["png"]).hexdigest() for a in assets}
            if digests & used_hashes or len(digests) != 3:
                skipped.append({"event_id": r["event_id"], "reason": "duplicate_rendered_image"})
                continue
            used_hashes.update(digests)
            accepted.append(r)
            for asset in assets:
                variant = asset["variant"]
                stem = parent.asset_stem(r["event_id"], variant)
                img, lab = f"images/train/{stem}.png", f"labels/train/{stem}.txt"
                if (out/img).exists() or (out/lab).exists():
                    raise NegativeRedoError("new asset collides with original")
                (out/img).write_bytes(asset["png"])
                (out/lab).write_text("", encoding="utf-8")
                v = asset["visible"]
                manifest.append({"arms": list(parent.arms_for_asset("train", variant)), "event_id": r["event_id"],
                    "cluster_id": r["cluster_id"], "canonical_asset": r["canonical_asset"], "core_end_time": r["core_end_time"],
                    "bar_minutes": int(r["bar_minutes"]), "split": "train", "variant": variant, "direction": r["direction"],
                    "source_path": source, "source_sha256": source_sha, "image_path": img, "image_sha256": hashlib.sha256(asset["png"]).hexdigest(),
                    "label_path": lab, "label_sha256": sha256(out/lab), "class_id": None, "box": None,
                    "candidate_box": asset["candidate_box"], "negative_kind": "outcome_target_negative",
                    "morphology_label": "unadjudicated_rule_candidate", "parent_candidate_pool": True,
                    "profit": r["profit"], "label_horizon_end_utc": r["profit"]["label_window_end_utc"],
                    "training_eligible": False, "production_eligible": False, **v})
        print(json.dumps({"source_group": gi, "source_groups": len(grouped), "accepted_negative_events": len(accepted), "skipped": len(skipped)}), flush=True)
    if not accepted:
        raise NegativeRedoError("zero renderable negative events")
    for a in ("A", "B"):
        (out/f"train_{a}.txt").write_text("".join("./"+r["image_path"]+"\n" for r in manifest if r["split"] == "train" and a in r["arms"]), encoding="utf-8")
        (out/f"data_{a}.yaml").write_text(f"path: {out.resolve()}\ntrain: train_{a}.txt\nval: val.txt\ntest: test.txt\nnc: 2\nnames: [profitable_dense_long, profitable_dense_short]\n", encoding="utf-8")
    for name in ("val.txt", "test.txt"):
        shutil.copyfile(old_root/name, out/name)
    write_rows(out/"manifest.jsonl", manifest)
    write_rows(out/"dataset_ledger.jsonl", old_events+accepted)
    write_rows(out/"build_exclusions.jsonl", skipped)
    receipt = {"status": "completed", "builder_commit": commit, "plan_sha256": sha256(plan_path),
               "selection_receipt_sha256": sha256(selection/"receipt.json"), "parent_manifest_sha256": sha256(inputs["old_manifest"]),
               "manifest_sha256": sha256(out/"manifest.jsonl"), "ledger_sha256": sha256(out/"dataset_ledger.jsonl"),
               "preserved_parent_views": len(manifest)-len(accepted)*3, "negative_events": len(accepted), "build_excluded": skipped,
               "counts": population_counts(manifest), "production_eligible": False, "training_eligible": False}
    write_json(out/"summary.json", receipt)
    return receipt


def audit(plan_path: Path, dataset: Path, selection: Path) -> dict:
    """Audit actual label files, parent preservation, event sets and portable lists."""
    p, inputs = checked_plan(plan_path)
    summary, sr = read_json(dataset/"summary.json"), read_json(selection/"receipt.json")
    if sr.get("status") != "completed" or sr.get("plan_sha256") != sha256(plan_path):
        raise NegativeRedoError("selection receipt binding mismatch")
    for name, digest in sr["artifacts"].items():
        if Path(name).name != name or sha256(selection/name) != digest:
            raise NegativeRedoError("selection artifact mismatch")
    if summary["status"] != "completed" or summary["plan_sha256"] != sha256(plan_path) or summary["selection_receipt_sha256"] != sha256(selection/"receipt.json"):
        raise NegativeRedoError("dataset summary binding mismatch")
    if summary["manifest_sha256"] != sha256(dataset/"manifest.jsonl") or summary["ledger_sha256"] != sha256(dataset/"dataset_ledger.jsonl"):
        raise NegativeRedoError("dataset manifest/ledger hash mismatch")
    original = rows(inputs["old_manifest"])
    manifest = rows(dataset/"manifest.jsonl")
    if manifest[:len(original)] != original:
        raise NegativeRedoError("parent manifest entries changed")
    ledger = rows(dataset/"dataset_ledger.jsonl")
    mapping = {r["event_id"]: r for r in ledger}
    if len(mapping) != len(ledger):
        raise NegativeRedoError("duplicate ledger event")
    original_ledger = rows(inputs["old_ledger"])
    if ledger[:len(original_ledger)] != original_ledger:
        raise NegativeRedoError("parent ledger changed")
    candidate_map = {r["event_id"]: r for r in rows(selection/"negatives.jsonl")}
    pool = rows(inputs["candidate_ledger"])
    expected_negatives, expected_excluded = select_rows(pool, protections(pool, read_json(inputs["reference_exclusion"]), rows(inputs["manual_rows"])), p["splits"]["train_end_exclusive"])
    if rows(selection/"negatives.jsonl") != expected_negatives or rows(selection/"exclusions.jsonl") != expected_excluded:
        raise NegativeRedoError("selected negative population drift")
    omitted = rows(dataset/"build_exclusions.jsonl")
    if omitted:
        raise NegativeRedoError("build exclusions require investigation before training; do not silently shrink the selected population")
    omitted_ids = {r["event_id"] for r in omitted}
    new_ids = {r["event_id"] for r in ledger[len(original_ledger):]}
    if len(omitted_ids) != len(omitted) or omitted_ids & new_ids or omitted_ids | new_ids != set(candidate_map) or omitted != summary["build_excluded"]:
        raise NegativeRedoError("build omissions are unaccounted for")
    for r in ledger[len(original_ledger):]:
        if r != candidate_map.get(r["event_id"]) or not eligible(r):
            raise NegativeRedoError("negative lineage/eligibility drift")
    image_paths, label_paths, hashes, seen = set(), set(), {}, defaultdict(list)
    for r in manifest:
        eid, split = r["event_id"], r["split"]
        if eid not in mapping or split != mapping[eid]["split"]:
            raise NegativeRedoError("manifest/ledger membership drift")
        for key in ("cluster_id", "canonical_asset", "direction", "bar_minutes", "core_end_time", "source_path", "source_sha256"):
            if r[key] != mapping[eid][key]:
                raise NegativeRedoError("manifest/ledger identity drift: " + key)
        if r["visible_end_close_time_utc"] != r["decision_at_utc"] or utc(r["decision_at_utc"]) != utc(mapping[eid]["profit"]["decision_close_time_utc"]):
            raise NegativeRedoError("future-visible input")
        for key, digest, collection, prefix in (("image_path", "image_sha256", image_paths, "images"), ("label_path", "label_sha256", label_paths, "labels")):
            rel = Path(r[key])
            if rel.is_absolute() or ".." in rel.parts or not r[key].startswith(f"{prefix}/{split}/") or r[key] in collection:
                raise NegativeRedoError("duplicate/escaping asset path")
            collection.add(r[key])
            if sha256(dataset/rel) != r[digest]:
                raise NegativeRedoError("asset SHA mismatch")
        signature = (split, r["label_sha256"])
        if r["image_sha256"] in hashes and hashes[r["image_sha256"]] != signature:
            raise NegativeRedoError("identical image crosses split or conflicts in label")
        hashes[r["image_sha256"]] = signature
        pixels = cv2.imread(str(dataset/r["image_path"]))
        if pixels is None or pixels.shape != (742, 1280, 3):
            raise NegativeRedoError("image shape/decoding failed")
        label = (dataset/r["label_path"]).read_text(encoding="utf-8").strip()
        positive = bool(mapping[eid]["profit"]["retained"])
        if positive:
            from scripts.windows.train_ma_profit3r import _validate_pixels_and_label
            _validate_pixels_and_label(dataset, r)
            if not label or r["class_id"] != (0 if r["direction"] == "LONG" else 1):
                raise NegativeRedoError("positive missing/wrong class")
        elif label or r["class_id"] is not None or r["box"] is not None:
            raise NegativeRedoError("outcome negative must have actual empty label")
        if not positive and split == "train" and (r.get("negative_kind") != "outcome_target_negative" or r.get("profit") != mapping[eid]["profit"]):
            raise NegativeRedoError("negative semantic lineage missing")
        seen[eid].append(r)
    if set(seen) != set(mapping):
        raise NegativeRedoError("rendered population differs from final ledger")
    for eid, views in seen.items():
        split = mapping[eid]["split"]
        variants = sorted(r["variant"] for r in views)
        if variants != (["A", "B1", "B2"] if split == "train" else ["A"]):
            raise NegativeRedoError("missing/extra event views")
        if any(tuple(r["arms"]) != parent.arms_for_asset(split, r["variant"]) for r in views):
            raise NegativeRedoError("wrong view routing")
    for split in ("train", "val", "test"):
        for a in ("A", "B"):
            name = f"train_{a}.txt" if split == "train" else f"{split}.txt"
            actual = [x.strip() for x in (dataset/name).read_text(encoding="utf-8").splitlines() if x.strip()]
            expected = ["./"+r["image_path"] for r in manifest if r["split"] == split and a in r["arms"]]
            if actual != expected or len(actual) != len(set(actual)):
                raise NegativeRedoError("loader lists do not match audited labels")
    for kind, expected in (("images", image_paths), ("labels", label_paths)):
        physical = {x.relative_to(dataset).as_posix() for x in (dataset/kind).rglob("*") if x.is_file()}
        if physical != expected:
            raise NegativeRedoError("orphan/missing dataset files")
    counts = population_counts(manifest)
    for a, views in (("A", 1), ("B", 2)):
        pos, neg = counts.get(f"{a}/train/positive", {}), counts.get(f"{a}/train/negative", {})
        if pos.get("events") != p["expected_positive_train_events"] or neg.get("events", 0) <= 0:
            raise NegativeRedoError("training positive count changed or negative count is ZERO")
        if neg["images"] != neg["events"]*views or pos["images"] != pos["events"]*views:
            raise NegativeRedoError("image/event multiplicity drift")
    if counts != summary["counts"] or summary["negative_events"] != counts["A/train/negative"]["events"]:
        raise NegativeRedoError("summary sample counts drift")
    return {"status": "passed", "manifest_sha256": summary["manifest_sha256"], "ledger_sha256": summary["ledger_sha256"],
            "plan_sha256": sha256(plan_path), "selection_receipt_sha256": sha256(selection/"receipt.json"),
            "actual_label_counts": counts, "verified_files": len(image_paths)+len(label_paths), "preserved_parent_views": len(original)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("select", "build", "audit"))
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--selection", type=Path)
    args = parser.parse_args()
    if args.mode == "select":
        result = select(args.plan, args.out)
    elif args.mode == "build":
        result = build(args.plan, args.selection, args.out)
    else:
        result = audit(args.plan, args.out, args.selection)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

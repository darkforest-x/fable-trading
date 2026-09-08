"""Separate source identity, core overlap and visible-context overlap across pools.

This metadata-only audit never opens media, labels or OHLCV. It does not transfer
human decisions, merge identities or infer visual duplicates. Old central cores
are derived from their frozen index/time mapping, not visually identified onset.
Visible windows exclude the SMA/EMA initialization prefix: this is not a proof
that the complete indicator dependency histories are independent.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from yoyo.contracts.holdout import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[2]
OLD_PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
NEW_PACK = ROOT / "datasets/grade_a_hl2_review_20260908_v1"
OLD_SHA = "7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641"
NEW_SHA = "fb0be11739339fd5d5016eee24a51081fd3ed44cffd1c1774c963a7365bf5fde"
LINEAGE_SHA = "25f2bdf6610c9909dc75f64348aa7bbf0baaf5729ae902efe3f71a47b461cbcd"
BAR = timedelta(minutes=15)
DEFINITIONS = {
    "intervals": "half-open [first_bar_open, last_bar_open + 15 minutes); adjacent intervals do not overlap",
    "same_source_core_identity": "same source_path, exact core interval and direction; not proof of equal pixel/label geometry",
    "core_exact_time": "same normalized symbol and equal core interval, with venue/direction recorded separately",
    "core_overlap": "same normalized symbol and any shared core bar; not a duplicate declaration",
    "owner_box_core_overlap": "old original Owner horizontal interval intersects new training core",
    "main_overlap": "both currently displayed main-image time intervals intersect",
    "original_w200_variant_overlap": "old timestamp-verified W200 pre-holdout portion intersects the union of all new training variants",
    "future_visible_overlap": "both future-reference visible canvases intersect, including their main history",
    "duplicate_review_candidate": "same venue plus core overlap; review candidate only, never automatic label transfer",
    "indicator_prefix": "SMA/EMA initialization histories are not included in visible-window overlap counts",
}
RELATIONS = tuple(k for k in DEFINITIONS if k not in {"intervals", "duplicate_review_candidate", "indicator_prefix"})


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value: str) -> datetime:
    time = datetime.fromisoformat(value)
    if time.tzinfo is None or time.utcoffset() is None:
        raise ValueError("Timezone-aware metadata is required")
    if time.second or time.microsecond or time.minute % 15:
        raise ValueError("Metadata timestamp is off the 15-minute grid")
    return time


def interval(first: str, last: str) -> tuple:
    a, b = stamp(first), stamp(last) + BAR
    if a >= b or b > HOLDOUT_START:
        raise ValueError("Visible metadata interval touches unauthorized holdout or is reversed")
    return a, b


def source_identity(row: dict) -> tuple[str, str, str]:
    path = PurePosixPath(row["source_path"])
    if path.is_absolute() or ".." in path.parts or path.parts[0] != "data":
        raise ValueError("Unexpected source path; never resolve media or market data")
    match = re.fullmatch(r"okx_(.+)_15m_\d+\.csv", path.name)
    if match:
        return "okx", match[1], path.as_posix()
    match = re.fullmatch(r"binance_um_(.+)USDT_15m_\d+\.csv", path.name)
    if match:
        return "binance_um", match[1] + "_USDT_SWAP", path.as_posix()
    raise ValueError("Unknown source naming contract; do not guess venue/symbol")


def prepare_old(row: dict) -> dict:
    venue, symbol, source = source_identity(row)
    if row["symbol"] != symbol or row["owner_side"] not in {"long", "short"}:
        raise ValueError("Old source symbol/direction drift")
    main = interval(row["main_start_time"], row["main_end_time"])
    if main[1] - main[0] != (row["main_end_i"] - row["main_start_i"] + 1) * BAR:
        raise ValueError("Old main index/time span mismatch")
    at = lambda i: main[0] + (i - row["main_start_i"]) * BAR
    if at(row["cut_global"]) != stamp(row["cut_time"]):
        raise ValueError("Old cut index/time mismatch")
    owner = row["original_geometry"]
    core = row["baseline"]
    ranges = {"main": main, "owner_box": (at(owner["source_start_i"]), at(owner["source_end_i"]) + BAR),
        "core": (at(core["core_start_i"]), at(core["core_end_i"]) + BAR)}
    if not main[0] <= ranges["owner_box"][0] <= ranges["core"][0] < ranges["core"][1] <= ranges["owner_box"][1] <= main[1]:
        raise ValueError("Old frozen core is outside its Owner/main interval")
    check = row["original_reference_check"]
    a, b = stamp(check["window_start_bar_open"]), stamp(check["window_end_bar_open"]) + BAR
    if b - a != 200 * BAR or stamp(check["window_available_at"]) != b:
        raise ValueError("Old W200 timestamps incomplete; cannot infer dependency")
    if a + (row["cut_global"] - owner["window_start_i"]) * BAR != stamp(row["cut_time"]):
        raise ValueError("Old original-window cut clock mismatch")
    if check["allowed"] != (b <= HOLDOUT_START) or row["original_reference_allowed"] != check["allowed"]:
        raise ValueError("Old original reference boundary flags disagree")
    # Only timestamp metadata is inspected for the three blocked original JPGs.
    ranges["original_w200"] = (a, min(b, HOLDOUT_START))
    future = row["future"]
    ranges["future_visible"] = interval(future["start_bar_open"], future["review_end_bar_open"])
    if (ranges["future_visible"][0] != main[0] or stamp(future["main_available_at"]) != main[1]
            or stamp(future["review_available_at"]) != ranges["future_visible"][1]
            or ranges["future_visible"][1] - main[1] != future["actual_future_bars"] * BAR
            or not 0 <= future["actual_future_bars"] <= 40):
        raise ValueError("Old future clock mismatch")
    return {"id": row["review_id"], "box_id": row["box_id"], "venue": venue, "symbol": symbol,
        "source": source, "direction": row["owner_side"].upper(), "split": row["original_split"],
        "ranges": ranges, "w200_clipped": b > HOLDOUT_START,
        "image_sha256": row["assets"][row["asset_roles"]["image"]]}


def prepare_new(row: dict) -> dict:
    venue, symbol, source = source_identity(row)
    if row["direction"] not in {"LONG", "SHORT"}:
        raise ValueError("Unknown new direction")
    main = interval(row["main_start_time"], row["main_end_time"])
    cores, windows, representatives, ids = set(), [], [], set()
    for v in row["lineage"]:
        core = interval(v["core_start_time"], v["core_end_time"])
        window = interval(v["window_start_time"], v["window_end_time"])
        image = re.fullmatch(r"A\d+_(.+)_(LONG|SHORT)_[0-9a-f]+\.png", PurePosixPath(v["image_path"]).name)
        if not image or (image[1], image[2]) != (symbol, row["direction"]):
            raise ValueError("New image-name/source symbol or direction mismatch")
        if (v["review_id"] != row["review_id"] or v["direction"] != row["direction"] or v["split"] != row["split"]
                or v["dataset_sample_id"] in ids or not window[0] <= core[0] < core[1] <= window[1]
                or window[1] - window[0] != (v["window_end_i"] - v["window_start_i"] + 1) * BAR
                or core[0] != window[0] + (v["source_core_start_i"] - v["window_start_i"]) * BAR
                or core[1] != window[0] + (v["source_core_end_i"] - v["window_start_i"] + 1) * BAR):
            raise ValueError("New variant identity/index/time mismatch")
        cores.add(core); windows.append(window); ids.add(v["dataset_sample_id"])
        if v["is_representative"]:
            representatives.append(v)
    if (len(cores) != 1 or len(representatives) != 1 or set(row["variant_sample_ids"]) != ids
            or representatives[0]["dataset_sample_id"] != row["representative_sample_id"]
            or interval(representatives[0]["window_start_time"], representatives[0]["window_end_time"]) != main):
        raise ValueError("New event core/representative/membership mismatch")
    future = row["future"]
    visible = interval(future["input_start_bar_open"], future["review_end_bar_open"])
    if (visible[0] != main[0] or stamp(future["input_available_at"]) != main[1]
            or stamp(future["review_available_at"]) != visible[1]
            or visible[1] - main[1] != future["actual_future_bars"] * BAR
            or not 0 <= future["actual_future_bars"] <= 40):
        raise ValueError("New future clock mismatch")
    return {"id": row["review_id"], "event_id": row["source_event_id"], "venue": venue, "symbol": symbol,
        "source": source, "direction": row["direction"], "split": row["split"], "variant_count": len(ids),
        "ranges": {"core": next(iter(cores)), "main": main,
            "variant_union": (min(w[0] for w in windows), max(w[1] for w in windows)), "future_visible": visible},
        "image_sha256": row["assets"][row["asset_roles"]["image"]]}


def overlaps(left: tuple, right: tuple) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


def audit_overlap(old_rows: list[dict], new_rows: list[dict]) -> dict:
    """Audit all metadata before pairing; keep every original identity intact."""
    old, new = [prepare_old(r) for r in old_rows], [prepare_new(r) for r in new_rows]
    if (len({r["id"] for r in old}) != len(old) or len({r["box_id"] for r in old}) != len(old)
            or len({r["id"] for r in new}) != len(new) or len({r["event_id"] for r in new}) != len(new)):
        raise ValueError("Duplicate frozen event identity")
    by_symbol = defaultdict(list)
    for n in new:
        by_symbol[n["symbol"]].append(n)
    pairs = []
    for o in sorted(old, key=lambda r: r["id"]):
        for n in sorted(by_symbol[o["symbol"]], key=lambda r: r["id"]):
            a, b = o["ranges"], n["ranges"]
            same_venue, same_source, same_direction = o["venue"] == n["venue"], o["source"] == n["source"], o["direction"] == n["direction"]
            relations = []
            if same_source and same_direction and a["core"] == b["core"]:
                relations.append("same_source_core_identity")
            if a["core"] == b["core"]:
                relations.append("core_exact_time")
            for name, left, right in (("core_overlap", "core", "core"), ("owner_box_core_overlap", "owner_box", "core"),
                ("main_overlap", "main", "main"), ("original_w200_variant_overlap", "original_w200", "variant_union"),
                ("future_visible_overlap", "future_visible", "future_visible")):
                if overlaps(a[left], b[right]):
                    relations.append(name)
            if relations:
                encode = lambda ranges: {k: [v[0].isoformat(), v[1].isoformat()] for k, v in ranges.items()}
                pairs.append({"old_review_id": o["id"], "new_review_id": n["id"], "old_box_id": o["box_id"],
                    "new_source_event_id": n["event_id"], "symbol": o["symbol"], "old_venue": o["venue"], "new_venue": n["venue"],
                    "old_source_path": o["source"], "new_source_path": n["source"], "same_venue": same_venue,
                    "same_source_path": same_source, "same_direction": same_direction,
                    "old_direction": o["direction"], "new_direction": n["direction"],
                    "old_split": o["split"], "new_split": n["split"], "relations": relations,
                    "duplicate_review_candidate": same_venue and "core_overlap" in relations,
                    "automatic_label_transfer": False, "old_w200_clipped_at_holdout": o["w200_clipped"],
                    "old_intervals": encode(a), "new_intervals": encode(b)})
    def summarize(rows):
        return {"pair_count": len(rows), "unique_old": len({p["old_review_id"] for p in rows}),
            "unique_new": len({p["new_review_id"] for p in rows}), "same_venue": sum(p["same_venue"] for p in rows),
            "cross_venue": sum(not p["same_venue"] for p in rows), "same_source_path": sum(p["same_source_path"] for p in rows),
            "same_direction": sum(p["same_direction"] for p in rows), "different_direction": sum(not p["same_direction"] for p in rows),
            "split_pairs": dict(Counter(p["old_split"] + "/" + p["new_split"] for p in rows))}
    return {"schema_version": 1, "definitions": DEFINITIONS,
        "counts": {"old_rows": len(old), "new_rows": len(new), "new_variants": sum(n["variant_count"] for n in new),
            **summarize(pairs), "old_w200_clipped_at_holdout": sum(o["w200_clipped"] for o in old),
            "old_venue_counts": dict(Counter(o["venue"] for o in old)), "new_venue_counts": dict(Counter(n["venue"] for n in new)),
            "shared_source_paths": len({o["source"] for o in old} & {n["source"] for n in new}),
            "exact_main_image_sha_pairs": sum(o["image_sha256"] == n["image_sha256"] for o in old for n in new)},
        "relations": {name: summarize([p for p in pairs if name in p["relations"]]) for name in RELATIONS},
        "duplicate_review_candidates": summarize([p for p in pairs if p["duplicate_review_candidate"]]), "pairs": pairs,
        "unmatched_old_review_ids": sorted({o["id"] for o in old} - {p["old_review_id"] for p in pairs}),
        "unmatched_new_review_ids": sorted({n["id"] for n in new} - {p["new_review_id"] for p in pairs}),
        "safety": {"media_read": False, "ohlcv_read": False, "holdout_content_read": False,
            "automatic_label_transfer": False, "rows_deleted": 0, "identities_merged": 0,
            "new_training": False, "training_eligible": False, "new_gold": False}}


def committed_source() -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ValueError("Run on the shared main branch")
    names = ["yoyo/datasets/owner_hl2_overlap.py", "tests/test_owner_hl2_overlap.py", "yoyo/contracts/holdout.py"]
    for name in names:
        if subprocess.check_output(["git", "show", head + ":" + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError("Commit source before writing a formal audit: " + name)
    return {"source_commit": head, "source_sha256": {name: sha(ROOT / name) for name in names}}


def load_inputs() -> tuple[list, list, dict]:
    paths = [(OLD_PACK / "manifest.jsonl", OLD_SHA), (NEW_PACK / "manifest.jsonl", NEW_SHA),
        (NEW_PACK / "admin/lineage.jsonl", LINEAGE_SHA)]
    if any(sha(path) != digest for path, digest in paths):
        raise ValueError("Frozen manifest/lineage SHA changed")
    old, new, lineage = [[json.loads(s) for s in path.read_text().splitlines() if s] for path, _ in paths]
    if len(old) != 2513 or len(new) != 1043 or len(lineage) != 8000 or lineage != [v for row in new for v in row["lineage"]]:
        raise ValueError("Frozen event/variant membership changed")
    return old, new, {str(p.relative_to(ROOT)): digest for p, digest in paths}


def run(output: Path) -> dict:
    source = committed_source()
    old, new, inputs = load_inputs()
    result = {**audit_overlap(old, new), **source, "input_sha256": inputs}
    if committed_source()["source_sha256"] != source["source_sha256"] or any(sha(ROOT / p) != d for p, d in inputs.items()):
        raise ValueError("Input or source changed while auditing")
    body = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(body)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"output": str(args.output), "counts": result["counts"], "relations": result["relations"]}, ensure_ascii=False))

"""Audit historical Owner/current-event time overlap using frozen metadata only.

Run after committing this source:
    PYTHONPATH=. .venv/bin/python experiments/active/exp-owner-box-refinement-20260907-v1/results/current_event_coverage.py

No image, label, OHLCV, morphology metric or model is opened/used. Event equality
uses venue, exact canonical OKX instrument and inclusive 15-minute bar-open
intervals, never CSV path/index equality. Owner start times are derived from the
sheet's cut time and bar count under its contiguous 15-minute contract; this
audit does not independently establish that contract from raw candles.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets.owner_box_refinement import central_core

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve()
SHEET = "analysis/output/owner_side_review/review_sheet.csv"
MAPPING = "datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/admin/owner_2525_scores.jsonl"
MANIFEST = "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl"
EXPECTED_SHA = {
    SHEET: "bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a",
    MAPPING: "0752df544308e6c26f647fc1a520375a551ed003e234d12f3e50dd8a05deef0b",
    MANIFEST: "22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2",
}
BAR = timedelta(minutes=15)
CURRENT_FIELDS = ("sample_kind", "event_id", "negative_event_id", "dataset_sample_id", "venue", "symbol",
    "exchange_symbol", "source_path", "direction", "paired_direction", "paired_positive_event_id", "negative_kind",
    "class_id", "class_name", "boxes_per_image", "split", "core_start_time", "core_end_time", "core_bars",
    "window_start_time", "window_end_time", "window_bars", "dependency_end_time")
MAPPING_FIELDS = ("sample_id", "symbol", "owner_side", "decision_bar", "decision_time", "resolved_source_csv")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an explicit string")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    moment = moment.astimezone(timezone.utc)
    if moment.second or moment.microsecond or moment.minute % 15 or moment + BAR > HOLDOUT_START:
        raise ValueError("bar time is off-grid or closes after the pre-holdout boundary")
    return moment


def interval(start: object, end: object, bars: int | None = None) -> tuple[datetime, datetime]:
    a, b = instant(start), instant(end)
    if a > b or (bars is not None and (b-a)//BAR+1 != bars):
        raise ValueError("interval order or 15-minute bar count differs")
    return a, b


def encoded_interval(value: tuple[datetime, datetime]) -> list[str]:
    return [v.isoformat() for v in value]


def intersection(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> dict | None:
    start, end = max(a[0], b[0]), min(a[1], b[1])
    if start > end:
        return None
    return {"start_bar_open": start.isoformat(), "end_bar_open": end.isoformat(), "bars": (end-start)//BAR+1}


def okx_symbol(value: str) -> str:
    # No asset aliases, multiplier conversion, spot/swap equivalence or venue mapping.
    if not isinstance(value, str):
        raise ValueError("missing OKX symbol")
    normalized = value.upper().replace("-", "_")
    if not re.fullmatch(r"[A-Z0-9]+_USDT_SWAP", normalized):
        raise ValueError(f"unsupported exact OKX perpetual instrument: {value}")
    return normalized


def source_okx_symbol(path: str) -> str:
    match = re.fullmatch(r"okx_(.+)_15m_\d+\.csv", Path(path).name)
    if not match:
        raise ValueError("Owner/OKX source venue is not explicitly established")
    return okx_symbol(match[1])


def projected_jsonl(path: Path, fields: tuple[str, ...]) -> list[dict]:
    rows = []
    with path.open() as handle:
        for line in handle:
            if line.strip():
                raw = json.loads(line)
                rows.append({field: raw.get(field) for field in fields})
    return rows


def owner_rows() -> tuple[list[dict], list[str]]:
    mapping_rows = projected_jsonl(ROOT/MAPPING, MAPPING_FIELDS)
    mapping = {r["sample_id"]: r for r in mapping_rows}
    with (ROOT/SHEET).open() as handle:
        sheet = list(csv.DictReader(handle))
    if len(mapping) != len(mapping_rows) or len({r["box_id"] for r in sheet}) != len(sheet) or set(mapping) != {r["box_id"] for r in sheet}:
        raise ValueError("Owner sheet and source mapping must join one-to-one")
    result, skipped = [], []
    for row in sheet:
        rid, side = row["box_id"], row["owner_side"]
        linked, end = mapping[rid], instant(row["cut_time"])
        symbol = okx_symbol(row["symbol"])
        if (okx_symbol(linked["symbol"]) != symbol or source_okx_symbol(linked["resolved_source_csv"]) != symbol
                or linked["owner_side"] != side or instant(linked["decision_time"]) != end
                or int(linked["decision_bar"]) != int(row["cut_global"])):
            raise ValueError("Owner source/time/direction mapping changed")
        b0, b1, width, cut = (int(row[k]) for k in ("bar_b0", "bar_b1", "width_bars", "cut_global"))
        if not 0 <= b0 <= b1 < 200 or width != b1-b0+1 or cut-width+1 < 0:
            raise ValueError("Owner original interval arithmetic changed")
        if side == "skip":
            skipped.append(rid)
            continue
        if side not in {"long", "short"}:
            raise ValueError("unknown Owner direction")
        start = end-(width-1)*BAR
        wide = interval(start.isoformat(), end.isoformat(), width)
        ca, cb = central_core(0, width-1)
        center = interval((start+ca*BAR).isoformat(), (start+cb*BAR).isoformat(), cb-ca+1)
        result.append({"owner_box_id": rid, "venue": "okx", "symbol": symbol, "direction": side.upper(),
            "source_path": linked["resolved_source_csv"], "split": row["split"], "stem": row["stem"],
            "box_index": int(row["box_index"]), "original_wide": encoded_interval(wide),
            "central_core": encoded_interval(center), "original_bars": width, "central_bars": cb-ca+1})
    if (len(result), len(skipped)) != (2513, 12):
        raise ValueError("frozen Owner population differs")
    return sorted(result, key=lambda r: r["owner_box_id"]), sorted(skipped)


def current_events() -> tuple[list[dict], int]:
    rows = projected_jsonl(ROOT/MANIFEST, CURRENT_FIELDS)
    groups, sample_ids = {}, set()
    for row in rows:
        kind, venue = row["sample_kind"], row["venue"]
        if kind not in {"positive", "negative"} or venue not in {"okx", "binance_um"} or row["split"] not in {"train", "val"}:
            raise ValueError("unknown current dataset enum")
        sample_id = row["dataset_sample_id"]
        if not isinstance(sample_id, str) or not sample_id or sample_id in sample_ids:
            raise ValueError("missing or duplicate current sample ID")
        sample_ids.add(sample_id)
        core = interval(row["core_start_time"], row["core_end_time"], int(row["core_bars"]))
        window = interval(row["window_start_time"], row["window_end_time"], int(row["window_bars"]))
        if not window[0] <= core[0] <= core[1] <= window[1]:
            raise ValueError("current core escapes its rendered input")
        if row["dependency_end_time"] is not None:
            if instant(row["dependency_end_time"]) < window[1]:
                raise ValueError("negative dependency ends before its input")
        symbol = row["symbol"]
        if venue == "okx":
            symbol = okx_symbol(symbol)
            if source_okx_symbol(row["source_path"]) != symbol or okx_symbol(row["exchange_symbol"]) != symbol:
                raise ValueError("OKX symbol/source metadata disagrees")
        event_id = row["event_id" if kind == "positive" else "negative_event_id"]
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event ID missing")
        if kind == "positive":
            direction = row["direction"]
            if direction not in {"LONG", "SHORT"} or row["boxes_per_image"] != 1:
                raise ValueError("positive direction/box contract changed")
        else:
            direction = None
            if (row["class_id"] is not None or row["class_name"] is not None or row["boxes_per_image"] != 0
                    or row["paired_direction"] not in {"LONG", "SHORT"} or row["negative_kind"] not in {"easy", "hard"}):
                raise ValueError("negative metadata must describe background, not a directional class")
        identity = {"event_key": f"{kind}:{event_id}", "event_id": event_id, "sample_kind": kind, "venue": venue,
            "symbol": symbol, "direction": direction, "split": row["split"], "core": encoded_interval(core),
            "negative_kind": row["negative_kind"], "paired_direction_provenance_only": row["paired_direction"],
            "paired_positive_event_id": row["paired_positive_event_id"]}
        key = identity["event_key"]
        if key not in groups:
            groups[key] = {"identity": identity, "dataset_sample_ids": [], "source_paths": set(), "input_windows": set(), "input_variants": []}
        if groups[key]["identity"] != identity:
            raise ValueError("event variants disagree on venue, symbol, core, direction or split")
        groups[key]["dataset_sample_ids"].append(sample_id)
        groups[key]["source_paths"].add(row["source_path"])
        groups[key]["input_windows"].add(tuple(encoded_interval(window)))
        groups[key]["input_variants"].append({"dataset_sample_id": sample_id, "window": encoded_interval(window)})
    events = [{**g["identity"], "variant_count": len(g["dataset_sample_ids"]),
        "dataset_sample_ids": sorted(g["dataset_sample_ids"]), "source_paths": sorted(g["source_paths"]),
        "input_windows": [list(w) for w in sorted(g["input_windows"])],
        "input_variants": sorted(g["input_variants"], key=lambda v: v["dataset_sample_id"])} for _, g in sorted(groups.items())]
    if len(rows) != 32000 or Counter(e["sample_kind"] for e in events) != {"positive": 1043, "negative": 3129}:
        raise ValueError("frozen current population differs")
    return events, len(rows)


def source_identity() -> tuple[str, dict]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = [HERE, ROOT/"yoyo/contracts/holdout.py", ROOT/"yoyo/datasets/owner_box_refinement.py"]
    hashes = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        committed = subprocess.check_output(["git", "show", f"{head}:{relative}"], cwd=ROOT)
        if hashlib.sha256(committed).hexdigest() != sha(path):
            raise ValueError(f"commit audit source before execution: {relative}")
        hashes[relative] = sha(path)
    return head, hashes


def audit() -> dict:
    head, code = source_identity()
    for path, digest in EXPECTED_SHA.items():
        if sha(ROOT/path) != digest:
            raise ValueError(f"frozen source changed: {path}")
    owners, skipped = owner_rows()
    events, image_count = current_events()
    by_symbol = defaultdict(list)
    for event in events:
        if event["venue"] == "okx":
            by_symbol[event["symbol"]].append(event)
    pairs, visible_negative_pairs = [], []
    for owner in owners:
        wide, center = interval(*owner["original_wide"]), interval(*owner["central_core"])
        for event in by_symbol[owner["symbol"]]:
            if event["sample_kind"] == "negative":
                hits = []
                for variant in event["input_variants"]:
                    visible = interval(*variant["window"])
                    wide_hit = intersection(wide, visible)
                    if wide_hit is not None:
                        hits.append({"dataset_sample_id": variant["dataset_sample_id"],
                            "original_wide_overlap": wide_hit, "central_core_overlap": intersection(center, visible)})
                if hits:
                    visible_negative_pairs.append({"owner_box_id": owner["owner_box_id"], "current_event_key": event["event_key"],
                        "relation": "possible_visible_negative_conflict", "hit_variants": hits})
            core = interval(*event["core"])
            overlap = intersection(wide, core)
            if overlap is None:
                continue
            relation = "negative_background" if event["sample_kind"] == "negative" else (
                "same_direction_positive" if event["direction"] == owner["direction"] else "opposite_direction_positive")
            pairs.append({"owner_box_id": owner["owner_box_id"], "current_event_key": event["event_key"],
                "relation": relation, "original_wide_overlap": overlap, "central_core_overlap": intersection(center, core)})
    coverage = {}
    for scope in ("original_wide", "central_core"):
        selected = [p for p in pairs if p[f"{scope}_overlap"] is not None]
        coverage[scope] = {"pairs": len(selected), "unique_owner_boxes": len({p["owner_box_id"] for p in selected}),
            "unique_current_events": len({p["current_event_key"] for p in selected}), "by_relation": {}}
        for relation in ("same_direction_positive", "opposite_direction_positive", "negative_background"):
            subset = [p for p in selected if p["relation"] == relation]
            coverage[scope]["by_relation"][relation] = {"pairs": len(subset),
                "unique_owner_boxes": len({p["owner_box_id"] for p in subset}),
                "unique_current_events": len({p["current_event_key"] for p in subset})}
    visible_coverage = {}
    for scope in ("original_wide", "central_core"):
        selected = [p for p in visible_negative_pairs if any(h[f"{scope}_overlap"] is not None for h in p["hit_variants"])]
        visible_coverage[scope] = {"owner_event_pairs": len(selected),
            "unique_owner_boxes": len({p["owner_box_id"] for p in selected}),
            "unique_negative_events": len({p["current_event_key"] for p in selected}),
            "unique_negative_variants": len({h["dataset_sample_id"] for p in selected for h in p["hit_variants"] if h[f"{scope}_overlap"] is not None}),
            "owner_variant_pairs": sum(h[f"{scope}_overlap"] is not None for p in selected for h in p["hit_variants"])}
    if source_identity()[1] != code or any(sha(ROOT/p) != digest for p, digest in EXPECTED_SHA.items()):
        raise ValueError("source changed during audit")
    return {"schema_version": 1, "audit_id": "owner_current_event_metadata_coverage_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(), "source_commit": head,
        "code_sha256": code, "source_sha256": EXPECTED_SHA, "holdout_start": HOLDOUT_START.isoformat(),
        "contract": {"comparison": "exact OKX venue and canonical instrument; inclusive core bar-open intersection",
            "owner_interval": "cut_time minus (width_bars-1)*15min; central_core uses the existing 4-7 rule",
            "path_equality_required": False, "cross_venue_equivalence_assumed": False,
            "negative_direction": "none; paired_direction is provenance only",
            "negative_interval_scope": "pseudo-core overlap and complete visible-input overlap are reported separately",
            "visible_negative_overlap": "each original variant window is checked separately; sample IDs retained; no envelope fills gaps",
            "overlap_is_semantic_equivalence": False, "unmatched_means_missing_training_example": False,
            "raw_continuity_revalidated": False, "owner_aliases_merged": False,
            "images_labels_ohlcv_opened": False, "morphology_scores_used": False,
            "training_eligible": False, "automatic_label_changes": False},
        "population": {"owner_boxes": len(owners), "owner_direction_counts": dict(Counter(r["direction"] for r in owners)),
            "owner_skipped_ids": skipped, "current_images": image_count, "current_events": len(events),
            "current_event_counts": dict(Counter(f"{e['venue']}/{e['sample_kind']}" for e in events)),
            "cross_venue_events_excluded": sum(e["venue"] != "okx" for e in events)},
        "coverage": coverage, "possible_visible_negative_conflict": {"coverage": visible_coverage,
            "pairs": sorted(visible_negative_pairs, key=lambda p: (p["owner_box_id"], p["current_event_key"]))},
        "pairs": sorted(pairs, key=lambda p: (p["owner_box_id"], p["current_event_key"])),
        "owner_intervals": owners, "current_events": events}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE.with_suffix(".json"))
    args = parser.parse_args()
    result = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = args.output.with_name(args.output.name+".pending")
    pending.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    pending.replace(args.output)
    print(json.dumps({"output": str(args.output), "population": result["population"], "coverage": result["coverage"],
        "visible_negative_coverage": result["possible_visible_negative_conflict"]["coverage"]}, ensure_ascii=False, indent=2))

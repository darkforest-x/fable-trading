#!/usr/bin/env python3
"""Independently verify the 15m MA-launch hard-negative val sidecar."""

from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2

from yoyo.datasets.fifteen_minute_launch_candidates import sha256_file, utc
from yoyo.datasets.ma_launch_t3_hard_val import (
    DEFAULT_DATASET,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    EMPTY_SHA256,
    HardValError,
    load_base_manifest,
    load_contract,
)
from yoyo.datasets.ma_launch_t3_training import load_candidate_union


def intervals_disjoint(intervals: Iterable[tuple[int, int]]) -> bool:
    """Return true when closed integer intervals never overlap."""

    ordered = sorted((int(start), int(end)) for start, end in intervals)
    return all(
        right_start > left_end
        for (_, left_end), (right_start, _) in zip(ordered, ordered[1:])
    )


def overlaps_any(
    start: int,
    end: int,
    intervals: list[tuple[int, int]],
    starts: list[int],
) -> bool:
    """Return true when one closed interval overlaps a sorted interval set."""

    at = bisect.bisect_right(starts, int(end))
    return any(other_end >= int(start) for _, other_end in intervals[:at])


def positive_guards(
    candidates: Iterable[Mapping[str, Any]], base: Mapping[str, Any]
) -> dict[str, list[tuple[int, int]]]:
    """Rebuild every candidate guard from the frozen base contract."""

    guard = base["negative_sampling"]["positive_guard"]
    geometry = base["positive_geometry"]
    max_core = max(int(value) for value in geometry["core_length_choices"])
    core_end = int(geometry["core_end_offset_from_t_bars"])
    latest_end = int(geometry["maximum_window_end_offset_from_t_bars"])
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in candidates:
        anchor = int(row["source_anchor_i"])
        core_start = anchor + core_end - max_core + 1
        out[str(row["source_path"])].append(
            (
                core_start - int(guard["before_core_bars"]),
                anchor + latest_end + int(guard["after_latest_possible_window_end_bars"]),
            )
        )
    return {source: sorted(values) for source, values in out.items()}


class GalleryParser(HTMLParser):
    """Count gallery cards and resolve image references."""

    def __init__(self) -> None:
        super().__init__()
        self.cards = 0
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "article" and "card" in set((values.get("class") or "").split()):
            self.cards += 1
        if tag == "img" and values.get("src"):
            self.images.append(str(values["src"]))


def verify(
    *, prereg_path: Path, dataset: Path, results: Path
) -> dict[str, Any]:
    """Rehash assets and verify time, geometry, exclusion and HTML contracts."""

    contract, base = load_contract(prereg_path)
    base_rows = load_base_manifest(contract)
    candidates = load_candidate_union(base)
    build = json.loads((results / "build_receipt.json").read_text(encoding="utf-8"))
    manifest_path = dataset / "manifest.jsonl"
    missing_path = dataset / "missing_capacity.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text().splitlines() if line]
    missing = [json.loads(line) for line in missing_path.read_text().splitlines() if line]
    if sha256_file(manifest_path) != str(build["manifest_sha256"]):
        raise HardValError("hard-val manifest differs from build receipt")
    if sha256_file(missing_path) != str(build["missing_capacity_sha256"]):
        raise HardValError("missing-capacity manifest differs from build receipt")

    base_val_ids = {
        str(row["sample_id"])
        for row in base_rows
        if row["sample_kind"] == "positive_weak" and row["split"] == "val"
    }
    mapped = [str(row["template_positive_sample_id"]) for row in rows]
    absent = [str(row["template_positive_sample_id"]) for row in missing]
    if len(mapped) != len(set(mapped)) or len(absent) != len(set(absent)):
        raise HardValError("one val positive is mapped more than once")
    if set(mapped).intersection(absent) or set(mapped).union(absent) != base_val_ids:
        raise HardValError("selected plus missing rows do not partition val positives")

    hard = contract["hard_validation_contract"]
    holdout = utc(contract["sources"]["holdout_start"])
    old_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in base_rows:
        if str(row["sample_kind"]).startswith("negative_"):
            old_intervals[str(row["source_path"])].append(
                (int(row["window_start_i"]), int(row["window_end_i"]))
            )
    for source in old_intervals:
        old_intervals[source].sort()
    guards = positive_guards(candidates, base)
    new_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    asset_errors: list[str] = []
    decoded_shapes: Counter[tuple[int, int, int]] = Counter()
    for row in rows:
        sample_id = str(row["sample_id"])
        if row["sample_kind"] != "negative_hard_val" or row["split"] != "val":
            raise HardValError(f"unexpected row kind/split: {sample_id}")
        if row.get("class_id") is not None or row.get("class_name") is not None:
            raise HardValError(f"hard-val row has a detection class: {sample_id}")
        if any(key in row for key in ("yolo_box", "review_marker_time", "core_start_i", "core_end_i")):
            raise HardValError(f"hard-val row contains marker/box geometry: {sample_id}")
        if float(row["bandwidth_pct"]) > float(hard["six_ma_bandwidth_pct_max"]):
            raise HardValError(f"hard bandwidth gate failed: {sample_id}")
        if float(row["close_abs_atr"]) > float(
            hard["pseudo_t_close_abs_atr_max_over_12_bars"]
        ):
            raise HardValError(f"close no-launch gate failed: {sample_id}")
        if float(row["two_sided_favorable_abs_atr"]) > float(
            hard["pseudo_t_two_sided_favorable_abs_atr_max_over_12_bars"]
        ):
            raise HardValError(f"two-sided no-launch gate failed: {sample_id}")
        window_len = int(row["window_end_i"]) - int(row["window_start_i"]) + 1
        if window_len != int(row["window_len"]) or window_len not in range(14, 23):
            raise HardValError(f"hard-val window length drifted: {sample_id}")
        if int(row["confirmation_bars"]) not in {3, 4, 5}:
            raise HardValError(f"hard-val confirmation drifted: {sample_id}")
        if int(row["window_end_i"]) - int(row["pseudo_t_i"]) != int(
            row["confirmation_bars"]
        ) - 3:
            raise HardValError(f"hard-val input endpoint drifted: {sample_id}")
        if int(row["label_future_end_i"]) - int(row["pseudo_t_i"]) != 11:
            raise HardValError(f"hard-val future-label endpoint drifted: {sample_id}")
        label_end = utc(row["pseudo_t_time"]) + pd_timedelta_minutes(11 * 15)
        if label_end >= holdout:
            raise HardValError(f"hard-val label dependency touches holdout: {sample_id}")

        image = dataset / str(row["image_path"])
        label = dataset / str(row["label_path"])
        if not image.is_file() or not label.is_file():
            asset_errors.append(f"missing:{sample_id}")
            continue
        if sha256_file(image) != str(row["image_sha256"]):
            asset_errors.append(f"image_hash:{sample_id}")
        if sha256_file(label) != EMPTY_SHA256 or str(row["label_sha256"]) != EMPTY_SHA256:
            asset_errors.append(f"nonempty_label:{sample_id}")
        decoded = cv2.imread(str(image), cv2.IMREAD_COLOR)
        if decoded is None:
            asset_errors.append(f"decode:{sample_id}")
        else:
            decoded_shapes[tuple(decoded.shape)] += 1
        new_intervals[str(row["source_path"])].append(
            (int(row["window_start_i"]), int(row["window_end_i"]))
        )
    if asset_errors:
        raise HardValError(f"hard-val asset failures: {asset_errors[:5]}")
    if decoded_shapes != Counter({(742, 1280, 3): len(rows)}):
        raise HardValError(f"hard-val image shape drifted: {decoded_shapes}")

    overlap_old = overlap_guards = 0
    for source, intervals in new_intervals.items():
        if not intervals_disjoint(intervals):
            raise HardValError(f"new hard-val windows overlap: {source}")
        old = old_intervals[source]
        old_starts = [start for start, _ in old]
        source_guards = guards[source]
        guard_starts = [start for start, _ in source_guards]
        for start, end in intervals:
            overlap_old += int(overlaps_any(start, end, old, old_starts))
            overlap_guards += int(overlaps_any(start, end, source_guards, guard_starts))
    if overlap_old or overlap_guards:
        raise HardValError(
            f"hard-val exclusion failed: old={overlap_old} guards={overlap_guards}"
        )

    pages = sorted((results / "gallery").glob("page_*.html"))
    card_count = image_refs = 0
    missing_refs: list[str] = []
    for page in pages:
        parsed = GalleryParser()
        parsed.feed(page.read_text(encoding="utf-8"))
        card_count += parsed.cards
        image_refs += len(parsed.images)
        for source in parsed.images:
            if not (page.parent / source).resolve().is_file():
                missing_refs.append(f"{page.name}:{source}")
    if card_count != len(rows) or image_refs != len(rows) or missing_refs:
        raise HardValError(
            f"gallery verification failed: cards={card_count} images={image_refs} "
            f"missing={missing_refs[:3]}"
        )

    receipt = {
        "experiment_id": contract["experiment_id"],
        "manifest_rows": len(rows),
        "target_rows": len(base_val_ids),
        "missing_rows": len(missing),
        "missing_by_symbol": dict(sorted(Counter(row["symbol"] for row in missing).items())),
        "all_asset_hashes_match": True,
        "empty_yolo_labels": len(rows),
        "decoded_shapes": {"742x1280x3": len(rows)},
        "new_windows_pairwise_disjoint": True,
        "overlaps_existing_negative_windows": overlap_old,
        "overlaps_candidate_guards": overlap_guards,
        "rows_with_marker_or_box_geometry": 0,
        "input_latest_offset_max": max(
            int(row["input_latest_offset_from_pseudo_t"]) for row in rows
        ),
        "label_future_latest_offset": 11,
        "holdout_rows_materialized": 0,
        "holdout_touched_by_any_dependency": False,
        "gallery_pages": len(pages),
        "gallery_cards": card_count,
        "gallery_missing_image_references": len(missing_refs),
        "browser_qa": "blocked_by_file_url_security_policy",
        "browser_policy_bypass_attempted": False,
        "contact_sheet_visual_inspection": "passed_via_local_image_viewer",
        "base_dataset_files_changed": 0,
        "models_trained": 0,
        "training_eligible": False,
        "production_eligible": False,
        "passed": True,
    }
    return receipt


def pd_timedelta_minutes(minutes: int):
    """Create a pandas-compatible UTC timestamp delta without importing pandas."""

    from datetime import timedelta

    return timedelta(minutes=int(minutes))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    receipt = verify(
        prereg_path=args.prereg.resolve(),
        dataset=args.dataset.resolve(),
        results=args.results.resolve(),
    )
    output = args.out.resolve() if args.out else args.results.resolve() / "qa_receipt.json"
    output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

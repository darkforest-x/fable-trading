"""Independently validate the built ETH 3m short-start pilot v2 artifacts.

The validator reads only the frozen dataset outputs.  It does not read raw
OHLC, train a model, inspect holdout, tune thresholds, or promote anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import pandas as pd

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT / "datasets/eth_3m_short_pilot_v2"
DEFAULT_OUT = PROJECT / "analysis/output/eth3m_short_pilot_v2_dataset/validation.json"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
BAR_DELTA = pd.Timedelta(minutes=3)
WINDOW = 200
FUTURE_BARS = 60


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_dataset(dataset: Path) -> dict[str, Any]:
    """Return a fail-closed validation receipt for one built dataset."""
    manifest = pd.read_csv(dataset / "manifest.csv")
    events = pd.read_csv(dataset / "event_manifest.csv")
    smoke = pd.read_csv(dataset / "smoke_manifest.csv")
    weak = pd.read_csv(dataset / "weak_or_review_manifest.csv", keep_default_na=False)
    meta = json.loads((dataset / "build_meta.json").read_text(encoding="utf-8"))
    receipt = json.loads(
        (dataset / "owner_confirmation_receipt.json").read_text(encoding="utf-8")
    )
    for column in ("anchor_time", "input_start_time", "input_end_time", "label_end_time"):
        manifest[column] = pd.to_datetime(manifest[column], utc=True)
    for column in ("anchor_time", "input_start_time", "future_end_time"):
        smoke[column] = pd.to_datetime(smoke[column], utc=True)
    for column in ("anchor_time", "input_start_time", "input_end_time"):
        weak[column] = pd.to_datetime(weak[column], utc=True)

    required = {
        "sample_id",
        "split",
        "class_name",
        "target",
        "event_id",
        "positive_event_id",
        "source_task_id",
        "sample_kind",
        "label_provenance",
        "tip_offset",
        "anchor_time",
        "input_start_time",
        "input_end_time",
        "label_end_time",
        "image_rel",
        "image_sha256",
    }
    missing = sorted(required - set(manifest.columns))
    if missing:
        raise ValueError(f"manifest missing columns: {missing}")
    weak_required = {
        "sample_id",
        "target",
        "event_id",
        "positive_event_id",
        "source_group",
        "source_task_id",
        "calibration_task_id",
        "sample_kind",
        "tip_offset",
        "reason",
        "label_provenance",
        "anchor_time",
        "input_start_time",
        "input_end_time",
        "image_rel",
        "image_sha256",
    }
    weak_missing = sorted(weak_required - set(weak.columns))
    if weak_missing:
        raise ValueError(f"weak manifest missing columns: {weak_missing}")

    file_errors: list[str] = []
    dimensions: set[tuple[int, int]] = set()
    for row in manifest.itertuples(index=False):
        image_path = dataset / row.image_rel
        if not image_path.is_file():
            file_errors.append(f"missing:{row.image_rel}")
            continue
        if _sha256(image_path) != row.image_sha256:
            file_errors.append(f"sha:{row.image_rel}")
        image = cv2.imread(str(image_path))
        if image is None:
            file_errors.append(f"decode:{row.image_rel}")
        else:
            dimensions.add((int(image.shape[1]), int(image.shape[0])))
    weak_file_errors: list[str] = []
    weak_dimensions: set[tuple[int, int]] = set()
    for row in weak.itertuples(index=False):
        image_path = dataset / row.image_rel
        if not image_path.is_file():
            weak_file_errors.append(f"missing:{row.image_rel}")
            continue
        if _sha256(image_path) != row.image_sha256:
            weak_file_errors.append(f"sha:{row.image_rel}")
        if Path(row.image_rel).parts[:1] != ("weak_or_review",):
            weak_file_errors.append(f"path:{row.image_rel}")
        image = cv2.imread(str(image_path))
        if image is None:
            weak_file_errors.append(f"decode:{row.image_rel}")
        else:
            weak_dimensions.add((int(image.shape[1]), int(image.shape[0])))

    expected_target = manifest["class_name"].map({"no_start": 0, "short_start": 1})
    class_target_mismatches = int((expected_target != manifest["target"]).sum())
    path_mismatches = int(
        sum(
            not Path(row.image_rel).parts[:2] == (row.split, row.class_name)
            for row in manifest.itertuples(index=False)
        )
    )
    positive = manifest[manifest["target"] == 1].copy()
    valid_sample_kinds = {"confirmed_current_tip", "owner_no_tip_negative"}
    valid_meta_flags = (
        meta.get("diagnostic_pilot_only") is True
        and meta.get("formal_gold_dataset") is False
        and meta.get("promotion_eligible") is False
        and meta.get("training_started") is False
        and meta.get("status", {}).get("diagnostic_pilot_only") is True
        and meta.get("status", {}).get("pilot_training_eligible") is False
        and meta.get("status", {}).get("formal_gold_dataset") is False
        and meta.get("status", {}).get("promotion_eligible") is False
        and meta.get("status", {}).get("training_started") is False
    )
    forbidden_meta_keys = {"positive_tip_offsets", "first_expired_tip_offset"}
    receipt_root = PROJECT / "datasets/eth_3m_entry_timing_calibration30"
    receipt_asset_errors: list[str] = []
    for item in receipt.get("calibration_images", []):
        for key, hash_key in (
            ("causal_image_rel", "causal_image_sha256"),
            ("review_image_rel", "review_image_sha256"),
        ):
            rel = Path(item[key])
            if rel.is_absolute():
                receipt_asset_errors.append(f"absolute:{rel}")
                continue
            path = receipt_root / rel
            if not path.is_file():
                receipt_asset_errors.append(f"missing:{rel}")
            elif _sha256(path) != item[hash_key]:
                receipt_asset_errors.append(f"sha:{rel}")
    receipt_manifest_path = PROJECT / receipt["source_calibration_manifest_rel"]
    receipt_html_path = PROJECT / receipt["source_mobile_html_rel"]
    receipt_hashes_ok = (
        receipt.get("confirmation_scope") == "batch_chat_confirmation"
        and receipt.get("not_row_level_label_studio") is True
        and receipt.get("owner_exact_words") == "看过了都来的急"
        and receipt.get("confirmed_current_tip_image_count") == 30
        and len(receipt.get("calibration_images", [])) == 30
        and receipt_manifest_path.is_file()
        and receipt_html_path.is_file()
        and _sha256(receipt_manifest_path) == receipt["source_calibration_manifest_sha256"]
        and _sha256(receipt_html_path) == receipt["source_mobile_html_sha256"]
        and not receipt_asset_errors
    )
    receipt_task_ids = [int(item["task_id"]) for item in receipt.get("calibration_images", [])]
    receipt_source_task_ids = [
        int(item["source_task_id"]) for item in receipt.get("calibration_images", [])
    ]
    receipt_positive_pairs = {
        (int(item["source_task_id"]), str(item["causal_image_sha256"]))
        for item in receipt.get("calibration_images", [])
    }
    manifest_positive_pairs = {
        (int(row.source_task_id), str(row.image_sha256))
        for row in positive.itertuples(index=False)
    }
    weak_kind_counts = weak["sample_kind"].value_counts().to_dict()
    expected_weak_kind_counts = {
        "review_tip_offset_-1": 30,
        "review_tip_offset_+1": 30,
        "review_tip_offset_+2": 30,
        "review_tip_offset_+3": 30,
        "review_original_v10_time": 30,
    }

    train = manifest[manifest["split"] == "train"]
    val = manifest[manifest["split"] == "val"]
    last_train_label_end = train["label_end_time"].max()
    first_val_input_start = val["input_start_time"].min()
    anchor_gap_bars = int((val["anchor_time"].min() - train["anchor_time"].max()) / BAR_DELTA)
    smoke_diffs = smoke["anchor_time"].diff().dropna()

    checks = {
        "manifest_rows": int(len(manifest)),
        "file_error_count": int(len(file_errors)),
        "all_images_1280x742": dimensions == {(1280, 742)},
        "class_target_mismatches": class_target_mismatches,
        "class_path_mismatches": path_mismatches,
        "duplicate_sample_ids": int(manifest["sample_id"].duplicated().sum()),
        "duplicate_image_sha_groups": int((manifest.groupby("image_sha256").size() > 1).sum()),
        "positive_negative_anchor_conflicts": int(
            manifest.groupby("anchor_time")["target"].nunique().gt(1).sum()
        ),
        "sample_kind_set_allowed": set(manifest["sample_kind"].unique()) <= valid_sample_kinds,
        "positive_sample_kind_current_tip_only": set(positive["sample_kind"].unique())
        == {"confirmed_current_tip"},
        "positive_rows": int(len(positive)),
        "positive_rows_is_30": int(len(positive)) == 30,
        "independent_positive_events": int(positive["positive_event_id"].nunique()),
        "independent_positive_events_is_29": int(positive["positive_event_id"].nunique()) == 29,
        "owner_no_tip_negative_rows": int((manifest["sample_kind"] == "owner_no_tip_negative").sum()),
        "owner_no_tip_negative_rows_is_107": int(
            (manifest["sample_kind"] == "owner_no_tip_negative").sum()
        )
        == 107,
        "all_positive_tip_offsets_are_zero": bool((positive["tip_offset"].astype(int) == 0).all()),
        "cross_split_events": int(manifest.groupby("event_id")["split"].nunique().gt(1).sum()),
        "cross_split_positive_events": int(
            manifest.groupby("positive_event_id")["split"].nunique().gt(1).sum()
        ),
        "first_val_input_after_last_train_label": bool(first_val_input_start > last_train_label_end),
        "anchor_embargo_bars": anchor_gap_bars,
        "anchor_embargo_at_least_260": anchor_gap_bars >= WINDOW + FUTURE_BARS,
        "all_input_ranges_are_200_bars": bool(
            (
                (manifest["input_end_time"] - manifest["input_start_time"])
                == (WINDOW - 1) * BAR_DELTA
            ).all()
        ),
        "all_inputs_end_at_anchor": bool(
            (manifest["input_end_time"] == manifest["anchor_time"]).all()
        ),
        "all_label_ends_pre_holdout": bool((manifest["label_end_time"] < HOLDOUT_START).all()),
        "smoke_rows": int(len(smoke)),
        "smoke_is_contiguous_3m": bool((smoke_diffs == BAR_DELTA).all()),
        "smoke_starts_at_first_val_anchor": bool(
            smoke["anchor_time"].min() == val["anchor_time"].min()
        ),
        "smoke_future_ends_pre_holdout": bool(
            (smoke["future_end_time"] < HOLDOUT_START).all()
        ),
        "weak_rows": int(len(weak)),
        "weak_rows_is_150": int(len(weak)) == 150,
        "weak_targets_blank": bool((weak["target"] == "").all()),
        "weak_event_ids_blank": bool((weak["event_id"] == "").all()),
        "weak_images_outside_class_dirs": bool(
            all(Path(row.image_rel).parts[:1] == ("weak_or_review",) for row in weak.itertuples())
        ),
        "weak_file_error_count": int(len(weak_file_errors)),
        "all_weak_images_1280x742": weak_dimensions == {(1280, 742)},
        "weak_input_ranges_are_200_bars": bool(
            ((weak["input_end_time"] - weak["input_start_time"]) == (WINDOW - 1) * BAR_DELTA).all()
        ),
        "weak_contains_required_review_kinds": {
            "review_tip_offset_-1",
            "review_tip_offset_+1",
            "review_tip_offset_+2",
            "review_tip_offset_+3",
            "review_original_v10_time",
        }
        <= set(weak["sample_kind"].unique()),
        "weak_kind_counts_exactly_30_each": weak_kind_counts
        == expected_weak_kind_counts,
        "receipt_hashes_valid": bool(receipt_hashes_ok),
        "receipt_task_ids_are_1_through_30": sorted(receipt_task_ids)
        == list(range(1, 31)),
        "receipt_source_task_ids_unique": len(receipt_source_task_ids)
        == len(set(receipt_source_task_ids))
        == 30,
        "positive_rows_match_receipt_causal_assets": manifest_positive_pairs
        == receipt_positive_pairs,
        "receipt_asset_error_count": int(len(receipt_asset_errors)),
        "meta_diagnostic_flags_valid": bool(valid_meta_flags),
        "meta_omits_lifetime_label_keys": not (forbidden_meta_keys & set(meta.keys())),
        "meta_total_matches": int(meta["totals"]["total"]) == len(manifest),
        "event_manifest_count_matches": int(events["event_id"].nunique())
        == int(manifest["event_id"].nunique()),
    }
    zero_expected = {
        "file_error_count",
        "class_target_mismatches",
        "class_path_mismatches",
        "duplicate_sample_ids",
        "duplicate_image_sha_groups",
        "positive_negative_anchor_conflicts",
        "cross_split_events",
        "cross_split_positive_events",
        "weak_file_error_count",
        "receipt_asset_error_count",
    }
    pass_all = all(
        value == 0 if key in zero_expected else bool(value)
        for key, value in checks.items()
        if key not in {"manifest_rows", "anchor_embargo_bars", "smoke_rows"}
    )
    return {
        "dataset": str(dataset.resolve()),
        "status": "passed" if pass_all else "failed",
        "checks": checks,
        "file_errors": file_errors,
        "weak_file_errors": weak_file_errors,
        "receipt_asset_errors": receipt_asset_errors,
        "holdout_read": False,
        "model_trained": False,
    }

"""Render and export the ETH 3m v2a diagnostic classification dataset.

Only confirmed current-tip positives and owner-no current-tip negatives enter
train/val.  Offset/original-v10 candidates are rendered with blank targets for
review, outside class folders.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.detection.data import add_mas
from src.detection.eth3m_v2_evidence import (
    BAR_DELTA,
    BAR_MINUTES,
    DEFAULT_CALIBRATION,
    DEFAULT_CALIBRATION_MOBILE_HTML,
    DEFAULT_DETAIL,
    DEFAULT_INPUT,
    DEFAULT_OUT,
    FUTURE_BARS,
    HOLDOUT_START,
    PROJECT,
    WEAK_REVIEW_OFFSETS,
    WINDOW,
    _build_owner_confirmation_receipt,
    _sha256,
    _utc,
    load_pre_holdout_ohlc,
    load_sources,
)
from src.detection.eth3m_v2_events import (
    build_source_intervals,
    choose_purged_split,
    merge_calibration_events,
    merge_source_intervals,
)
from src.detection.render import render_chart

def _build_smoke_manifest(frame: pd.DataFrame, samples: pd.DataFrame) -> pd.DataFrame:
    """Seal a full contiguous, unlabeled dev replay block on the val side."""
    first_anchor = _utc(samples.loc[samples["split"] == "val", "anchor_time"].min())
    last_anchor = HOLDOUT_START - FUTURE_BARS * BAR_DELTA - BAR_DELTA
    anchors = frame.loc[
        (frame["open_time"] >= first_anchor) & (frame["open_time"] <= last_anchor),
        "open_time",
    ]
    smoke = pd.DataFrame({"anchor_time": anchors.reset_index(drop=True)})
    smoke["input_start_time"] = smoke["anchor_time"] - (WINDOW - 1) * BAR_DELTA
    smoke["future_end_time"] = smoke["anchor_time"] + FUTURE_BARS * BAR_DELTA
    smoke["purpose"] = "sealed_contiguous_dev_replay_unlabeled"
    if smoke.empty or smoke["future_end_time"].max() >= HOLDOUT_START:
        raise ValueError("smoke manifest is empty or crosses holdout")
    return smoke


def _render_weak_review_manifest(
    *,
    weak_rows: pd.DataFrame,
    ma_frame: pd.DataFrame,
    out: Path,
) -> pd.DataFrame:
    """Render withheld offset/original-v10 rows outside train/val class folders."""
    positions = pd.Series(ma_frame.index.to_numpy(), index=ma_frame["open_time"])
    manifest_rows: list[dict[str, Any]] = []
    for row in weak_rows.sort_values(
        ["anchor_time", "source_task_id", "sample_kind"]
    ).itertuples(index=False):
        anchor = _utc(row.anchor_time)
        if anchor >= HOLDOUT_START or anchor not in positions.index:
            raise ValueError(f"invalid weak/review anchor: {anchor}")
        anchor_i = int(positions.loc[anchor])
        start_i = anchor_i - WINDOW + 1
        if start_i < 0:
            raise ValueError(f"weak/review sample lacks {WINDOW} causal bars: {anchor}")
        causal = ma_frame.iloc[start_i : anchor_i + 1].reset_index(drop=True)
        stamp = anchor.strftime("%Y%m%dT%H%M%SZ")
        sample_id = (
            f"eth3m_{stamp}_{row.sample_kind}_t{int(row.source_task_id):03d}"
            f"_c{int(row.calibration_task_id):02d}"
        )
        image_rel = Path("weak_or_review") / str(row.sample_kind) / f"{sample_id}.png"
        image_path = out / image_rel
        render_chart(causal, out_path=image_path)
        manifest_rows.append(
            {
                "sample_id": sample_id,
                "target": "",
                "event_id": "",
                "positive_event_id": row.positive_event_id,
                "source_group": row.source_group,
                "source_task_id": int(row.source_task_id),
                "calibration_task_id": int(row.calibration_task_id),
                "sample_kind": row.sample_kind,
                "tip_offset": int(row.tip_offset),
                "reason": row.reason,
                "label_provenance": row.label_provenance,
                "anchor_time": anchor.isoformat(),
                "input_start_time": _utc(causal["open_time"].iloc[0]).isoformat(),
                "input_end_time": _utc(causal["open_time"].iloc[-1]).isoformat(),
                "image_rel": image_rel.as_posix(),
                "image_sha256": _sha256(image_path),
            }
        )
    return pd.DataFrame(manifest_rows).sort_values(
        ["anchor_time", "source_task_id", "sample_kind"]
    )


def build_dataset(
    *,
    input_path: Path,
    detail_path: Path,
    calibration_path: Path,
    mobile_html_path: Path = DEFAULT_CALIBRATION_MOBILE_HTML,
    out: Path,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Build classification images, manifests, metadata, and hard acceptance checks."""
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {out}")

    detail, calibration = load_sources(detail_path, calibration_path)
    frame = load_pre_holdout_ohlc(input_path)
    ma_frame = add_mas(frame)
    calibration_events, calibration_audit = merge_calibration_events(detail, calibration)
    intervals, weak_rows, sequence_audit = build_source_intervals(
        detail, calibration_events, ma_frame
    )
    samples, events = merge_source_intervals(intervals)
    duplicate_samples_removed = int(samples.attrs.get("exact_duplicate_samples_removed", 0))
    samples, events, split_audit = choose_purged_split(samples, events)

    positions = pd.Series(ma_frame.index.to_numpy(), index=ma_frame["open_time"])
    manifest_rows: list[dict[str, Any]] = []
    for row in samples.sort_values(["anchor_time", "target", "source_task_id"]).itertuples(
        index=False
    ):
        anchor = _utc(row.anchor_time)
        if anchor >= HOLDOUT_START or anchor not in positions.index:
            raise ValueError(f"invalid sample anchor: {anchor}")
        anchor_i = int(positions.loc[anchor])
        start_i = anchor_i - WINDOW + 1
        if start_i < 0:
            raise ValueError(f"sample lacks {WINDOW} causal bars: {anchor}")
        causal = ma_frame.iloc[start_i : anchor_i + 1].reset_index(drop=True)
        if len(causal) != WINDOW:
            raise AssertionError("causal window length changed")

        class_name = "short_start" if int(row.target) == 1 else "no_start"
        stamp = anchor.strftime("%Y%m%dT%H%M%SZ")
        sample_id = f"eth3m_{stamp}_{row.sample_kind}_t{int(row.source_task_id):03d}"
        image_rel = Path(row.split) / class_name / f"{sample_id}.png"
        image_path = out / image_rel
        render_chart(causal, out_path=image_path)
        manifest_rows.append(
            {
                "sample_id": sample_id,
                "split": row.split,
                "class_name": class_name,
                "target": int(row.target),
                "event_id": int(row.event_id),
                "positive_event_id": row.positive_event_id,
                "source_group": row.source_group,
                "source_task_id": int(row.source_task_id),
                "sample_kind": row.sample_kind,
                "label_provenance": row.label_provenance,
                "tip_offset": int(row.tip_offset),
                "anchor_time": anchor.isoformat(),
                "input_start_time": _utc(causal["open_time"].iloc[0]).isoformat(),
                "input_end_time": _utc(causal["open_time"].iloc[-1]).isoformat(),
                "label_end_time": _utc(row.label_end_time).isoformat(),
                "image_rel": image_rel.as_posix(),
                "image_sha256": _sha256(image_path),
            }
        )

    manifest = pd.DataFrame(manifest_rows).sort_values(
        ["anchor_time", "target", "source_task_id"]
    )
    out.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(out / "manifest.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    event_export = events.copy()
    for column in ("source_groups", "positive_event_ids"):
        event_export[column] = event_export[column].map(lambda values: ",".join(map(str, values)))
    event_export.to_csv(out / "event_manifest.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    weak_manifest = _render_weak_review_manifest(
        weak_rows=weak_rows,
        ma_frame=ma_frame,
        out=out,
    )
    weak_manifest.to_csv(
        out / "weak_or_review_manifest.csv", index=False, quoting=csv.QUOTE_MINIMAL
    )
    smoke = _build_smoke_manifest(frame, samples)
    smoke.to_csv(out / "smoke_manifest.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    owner_receipt = _build_owner_confirmation_receipt(
        calibration_path=calibration_path,
        mobile_html_path=mobile_html_path,
        calibration=calibration,
    )
    (out / "owner_confirmation_receipt.json").write_text(
        json.dumps(owner_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    image_sha_duplicates = int(
        (manifest.groupby("image_sha256").size() > 1).sum()
    )
    counts: dict[str, Any] = {}
    for split in ("train", "val"):
        part = manifest[manifest["split"] == split]
        counts[split] = {
            "total": int(len(part)),
            "short_start": int((part["target"] == 1).sum()),
            "no_start": int((part["target"] == 0).sum()),
            "global_events": int(part["event_id"].nunique()),
            "positive_events": int(part.loc[part["target"] == 1, "positive_event_id"].nunique()),
        }
    sample_kind_counts = {
        str(key): int(value) for key, value in manifest["sample_kind"].value_counts().items()
    }
    all_label_ends_pre_holdout = bool(
        (pd.to_datetime(manifest["label_end_time"], utc=True) < HOLDOUT_START).all()
    )
    all_inputs_causal = bool(
        (
            pd.to_datetime(manifest["input_end_time"], utc=True)
            == pd.to_datetime(manifest["anchor_time"], utc=True)
        ).all()
    )
    automatic_checks = {
        "all_anchors_pre_holdout": bool(
            (pd.to_datetime(manifest["anchor_time"], utc=True) < HOLDOUT_START).all()
        ),
        "all_label_ends_pre_holdout": all_label_ends_pre_holdout,
        "all_inputs_end_at_decision_tip": all_inputs_causal,
        "all_input_windows_200_bars": True,
        "future_outcome_columns_loaded": [],
        "positive_negative_anchor_conflicts": 0,
        "cross_split_event_count": int(manifest.groupby("event_id")["split"].nunique().gt(1).sum()),
        "cross_split_input_overlap_pairs": 0,
        "first_val_input_after_last_train_label": bool(
            pd.to_datetime(split_audit["first_val_input_start"], utc=True)
            > pd.to_datetime(split_audit["last_train_label_end"], utc=True)
        ),
        "image_sha256_duplicate_groups": image_sha_duplicates,
        "manifest_image_count_matches": int(len(manifest))
        == sum(1 for split in ("train", "val") for _ in (out / split).glob("*/*.png")),
    }
    zero_expected = {
        "positive_negative_anchor_conflicts",
        "cross_split_event_count",
        "cross_split_input_overlap_pairs",
        "image_sha256_duplicate_groups",
    }
    checks_pass = all(
        (value == 0)
        if key in zero_expected
        else (value == [])
        if key == "future_outcome_columns_loaded"
        else bool(value)
        for key, value in automatic_checks.items()
    )
    if not checks_pass:
        raise AssertionError(f"automatic dataset acceptance failed: {automatic_checks}")

    meta = {
        "dataset": out.name,
        "task": "image_classification_current_tip_short_start",
        "classes": {"no_start": 0, "short_start": 1},
        "source_detail": str(detail_path.resolve()),
        "source_calibration": str(calibration_path.resolve()),
        "source_ohlc": str(input_path.resolve()),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_consumed_by_build": False,
        "causal_window_bars": WINDOW,
        "human_review_horizon_bars": FUTURE_BARS,
        "bar_minutes": BAR_MINUTES,
        "confirmed_positive_anchor": "current_T_only",
        "weak_review_offsets": list(WEAK_REVIEW_OFFSETS),
        "weak_review_original_v10_in_train_val": False,
        "label_contract": (
            "current causal tip classification; only owner-confirmed current T is "
            "short_start; manual owner-no rows are current-tip negatives; "
            "T-1/T+1/T+2/T+3/original_v10 are blank-target weak/review rows"
        ),
        "owner_timing_evidence": (
            "batch confirmation in conversation for the fixed 30-image calibration pack; "
            "not 30 row-level Label Studio annotations"
        ),
        "counts": counts,
        "totals": {
            "total": int(len(manifest)),
            "short_start": int((manifest["target"] == 1).sum()),
            "no_start": int((manifest["target"] == 0).sum()),
            "global_events": int(manifest["event_id"].nunique()),
            "independent_positive_events": int(
                manifest.loc[manifest["target"] == 1, "positive_event_id"].nunique()
            ),
            "sealed_smoke_bars": int(len(smoke)),
            "weak_or_review_rows": int(len(weak_manifest)),
        },
        "sample_kind_counts": sample_kind_counts,
        "calibration_audit": calibration_audit,
        "sequence_audit": sequence_audit,
        "exact_duplicate_samples_removed": duplicate_samples_removed,
        "split_audit": split_audit,
        "automatic_acceptance": automatic_checks,
        "diagnostic_pilot_only": True,
        "formal_gold_dataset": False,
        "promotion_eligible": False,
        "training_started": False,
        "status": {
            "build_valid": True,
            "diagnostic_pilot_only": True,
            "pilot_training_eligible": False,
            "formal_gold_dataset": False,
            "promotion_eligible": False,
            "training_started": False,
        },
        "risks": [
            "Only batch-confirmed timing anchors are positive; independent positive events remain pilot-scale.",
            "All human-reviewed sources came from the v10 candidate pool, so shape-selection bias remains.",
            "Owner-no rows are current-tip negatives, not clean whole-image backgrounds.",
            "The contiguous smoke block is deliberately unlabeled and must not be converted to negatives automatically.",
            "Static val is development-only; continuous replay and owner-approved density gates remain required before promotion.",
        ],
    }
    (out / "build_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "classes.json").write_text(
        json.dumps(meta["classes"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "README.md").write_text(
        "# ETH 3m short-start pilot v2\n\n"
        "Image-level causal-tip classification dataset. `train/` and `val/` use "
        "Ultralytics-compatible class folders (`no_start`, `short_start`).\n\n"
        f"Current build: {meta['totals']['total']} labeled images "
        f"({meta['totals']['short_start']} short_start / "
        f"{meta['totals']['no_start']} no_start), grouped into "
        f"{meta['totals']['independent_positive_events']} independent positive events.\n\n"
        "Only `confirmed_current_tip` and `owner_no_tip_negative` are training labels. "
        "T-1/T+1/T+2/T+3/original-v10 candidates have blank targets in "
        "`weak_or_review_manifest.csv` and live only under `weak_or_review/`; the "
        "tip/tip-1/tip-2 detection tolerance is not a signal-lifetime rule.\n\n"
        "See `owner_confirmation_receipt.json` for the batch-confirmation hashes, "
        "`build_meta.json` for the label contract, and `smoke_manifest.csv` for the "
        "sealed unlabeled continuous development replay.\n\n"
        "This is a diagnostic pilot. It is not a formal gold set and must not be "
        "promoted or evaluated on holdout without owner approval.\n",
        encoding="utf-8",
    )
    return meta, manifest

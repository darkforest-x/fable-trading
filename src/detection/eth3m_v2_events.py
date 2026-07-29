"""Event grouping and purged chronological split logic for ETH 3m v2a.

The functions here operate on causal owner-evidence rows and MA-augmented OHLC
frames.  They never load holdout data, train, or promote model artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from src.detection.data import ALL_MA_COLS
from src.detection.eth3m_v2_evidence import (
    BAR_DELTA,
    BAR_MINUTES,
    FUTURE_BARS,
    HOLDOUT_START,
    MIN_LEAD_BARS,
    TARGET_TRAIN_FRACTION,
    WEAK_REVIEW_OFFSETS,
    WINDOW,
    _utc,
)

@dataclass
class SourceInterval:
    """One human-reviewed source interval and the samples derived from it."""

    source_group: str
    start: pd.Timestamp
    label_end: pd.Timestamp
    positive_event_id: str | None
    samples: list[dict[str, Any]] = field(default_factory=list)

def merge_calibration_events(
    detail: pd.DataFrame, calibration: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Merge overlapping 3h review intervals while keeping all 30 current-T labels."""
    joined = calibration.merge(
        detail,
        left_on="source_task_id",
        right_on="task_id",
        how="left",
        validate="one_to_one",
        suffixes=("_calibration", "_detail"),
    )
    if joined["owner_is_target"].isna().any():
        raise ValueError("calibration source task missing from project-53 detail")
    if not (joined["owner_is_target"] == 1).all():
        raise ValueError("calibration contains a task not marked owner yes")
    expected_entry = joined["candidate_time"] - pd.to_timedelta(
        pd.to_numeric(joined["first_below_all_mas_lag_bars"], errors="raise")
        * BAR_MINUTES,
        unit="m",
    )
    if not expected_entry.equals(joined["entry_candidate_time"]):
        raise ValueError("calibration entry anchor no longer matches causal reconstruction")
    if (joined["first_below_all_mas_lag_bars"] < MIN_LEAD_BARS).any():
        raise ValueError("calibration entry does not meet minimum lead")

    ordered = joined.sort_values(["box_start_time", "entry_candidate_time", "source_task_id"])
    groups: list[list[int]] = []
    group_end: pd.Timestamp | None = None
    for index, row in ordered.iterrows():
        start = _utc(row["box_start_time"])
        end = _utc(row["entry_candidate_time"]) + FUTURE_BARS * BAR_DELTA
        if group_end is None or start > group_end:
            groups.append([index])
            group_end = end
        else:
            groups[-1].append(index)
            group_end = max(group_end, end)

    rows: list[dict[str, Any]] = []
    for event_number, indices in enumerate(groups, start=1):
        part = ordered.loc[indices].copy()
        event_id = f"p{event_number:03d}"
        event_start = part["box_start_time"].min()
        event_label_end = (part["entry_candidate_time"] + FUTURE_BARS * BAR_DELTA).max()
        for _, item in part.sort_values(["entry_candidate_time", "source_task_id"]).iterrows():
            rows.append(
                {
                    **item.to_dict(),
                    "positive_event_id": event_id,
                    "event_start": event_start,
                    "event_label_end": event_label_end,
                    "calibration_rows_in_event": int(len(part)),
                }
            )
    events = pd.DataFrame(rows).sort_values("entry_candidate_time").reset_index(drop=True)
    return events, {
        "batch_confirmed_calibration_rows": int(len(joined)),
        "independent_positive_events": int(events["positive_event_id"].nunique()),
        "overlapping_calibration_rows_grouped": int(
            len(joined) - events["positive_event_id"].nunique()
        ),
    }


def _below_all_mas(row: pd.Series) -> bool:
    values = row[list(ALL_MA_COLS)]
    return bool(values.notna().all() and float(row["close"]) < float(values.min()))


def build_source_intervals(
    detail: pd.DataFrame,
    calibration_events: pd.DataFrame,
    ma_frame: pd.DataFrame,
) -> tuple[list[SourceInterval], pd.DataFrame, dict[str, int]]:
    """Build confirmed train/val intervals plus blank-target weak review rows."""
    positions = pd.Series(ma_frame.index.to_numpy(), index=ma_frame["open_time"])
    intervals: list[SourceInterval] = []
    weak_rows: list[dict[str, Any]] = []

    for row in calibration_events.itertuples(index=False):
        anchor = _utc(row.entry_candidate_time)
        if anchor not in positions.index:
            raise ValueError(f"calibration anchor missing from OHLC: {anchor}")
        anchor_i = int(positions.loc[anchor])
        if anchor_i < WINDOW:
            raise ValueError("calibration anchor lacks causal history")
        if not _below_all_mas(ma_frame.iloc[anchor_i]):
            raise ValueError(f"calibration anchor is not below all six MAs: {anchor}")

        source_task = int(row.source_task_id)
        source_group = f"calibration_{row.positive_event_id}_t{source_task:03d}"
        interval = SourceInterval(
            source_group=source_group,
            start=_utc(row.event_start),
            label_end=_utc(row.event_label_end),
            positive_event_id=str(row.positive_event_id),
        )
        interval.samples.append(
            {
                "anchor_time": anchor,
                "target": 1,
                "sample_kind": "confirmed_current_tip",
                "tip_offset": 0,
                "source_task_id": source_task,
                "calibration_task_id": int(row.task_id_calibration),
                "label_provenance": "owner_batch_chat_confirmed_current_T",
            }
        )
        intervals.append(interval)

        for offset in WEAK_REVIEW_OFFSETS:
            weak_rows.append(
                {
                    "anchor_time": anchor + offset * BAR_DELTA,
                    "target": "",
                    "event_id": "",
                    "positive_event_id": str(row.positive_event_id),
                    "source_group": source_group,
                    "source_task_id": source_task,
                    "calibration_task_id": int(row.task_id_calibration),
                    "sample_kind": f"review_tip_offset_{offset:+d}",
                    "tip_offset": offset,
                    "reason": "tip_geometry_offset_is_not_owner_confirmed_current_tip_label",
                    "label_provenance": "withheld_from_train_val_geometry_offset_not_lifetime",
                }
            )

        original_time = _utc(row.original_v10_time)
        original_offset = int((original_time - anchor) / BAR_DELTA)
        weak_rows.append(
            {
                "anchor_time": original_time,
                "target": "",
                "event_id": "",
                "positive_event_id": str(row.positive_event_id),
                "source_group": source_group,
                "source_task_id": source_task,
                "calibration_task_id": int(row.task_id_calibration),
                "sample_kind": "review_original_v10_time",
                "tip_offset": original_offset,
                "reason": "original_v10_time_is_not_owner_confirmed_current_tip_label",
                "label_provenance": "withheld_from_train_val_original_v10_not_current_T_confirmation",
            }
        )

    owner_no = detail[detail["owner_is_target"] == 0].sort_values(
        ["candidate_time", "task_id"]
    )
    for row in owner_no.itertuples(index=False):
        anchor = _utc(row.candidate_time)
        intervals.append(
            SourceInterval(
                source_group=f"owner_no_{int(row.task_id):03d}",
                start=_utc(row.box_start_time),
                label_end=anchor + FUTURE_BARS * BAR_DELTA,
                positive_event_id=None,
                samples=[
                    {
                        "anchor_time": anchor,
                        "target": 0,
                        "sample_kind": "owner_no_tip_negative",
                        "tip_offset": 0,
                        "source_task_id": int(row.task_id),
                        "calibration_task_id": "",
                        "label_provenance": "label_studio_project_53_owner_no",
                    }
                ],
            )
        )

    audit = {
        "weak_review_rows_withheld_from_train_val": int(len(weak_rows)),
        "weak_review_offsets_per_calibration": list(WEAK_REVIEW_OFFSETS),
        "weak_review_original_v10_rows": int(len(calibration_events)),
        "manual_owner_no_rows": int(len(owner_no)),
    }
    return intervals, pd.DataFrame(weak_rows), audit


def merge_source_intervals(intervals: Iterable[SourceInterval]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Merge overlapping review horizons, map samples to global event groups."""
    ordered = sorted(intervals, key=lambda item: (item.start, item.label_end, item.source_group))
    global_events: list[dict[str, Any]] = []
    source_to_event: dict[str, int] = {}
    for interval in ordered:
        if not global_events or interval.start > global_events[-1]["label_end"]:
            global_events.append(
                {
                    "event_id": len(global_events) + 1,
                    "start": interval.start,
                    "label_end": interval.label_end,
                    "source_groups": [interval.source_group],
                    "positive_event_ids": [interval.positive_event_id]
                    if interval.positive_event_id
                    else [],
                }
            )
        else:
            global_events[-1]["label_end"] = max(
                global_events[-1]["label_end"], interval.label_end
            )
            global_events[-1]["source_groups"].append(interval.source_group)
            if interval.positive_event_id:
                global_events[-1]["positive_event_ids"].append(interval.positive_event_id)
        source_to_event[interval.source_group] = int(global_events[-1]["event_id"])

    sample_rows: list[dict[str, Any]] = []
    interval_by_group = {item.source_group: item for item in ordered}
    for source_group, event_id in source_to_event.items():
        interval = interval_by_group[source_group]
        for sample in interval.samples:
            anchor = _utc(sample["anchor_time"])
            sample_rows.append(
                {
                    **sample,
                    "anchor_time": anchor,
                    "input_start_time": anchor - (WINDOW - 1) * BAR_DELTA,
                    "label_end_time": interval.label_end,
                    "source_group": source_group,
                    "positive_event_id": interval.positive_event_id or "",
                    "event_id": event_id,
                }
            )
    samples = pd.DataFrame(sample_rows)

    conflicts = samples.groupby("anchor_time")["target"].nunique()
    conflict_times = conflicts[conflicts > 1].index.tolist()
    if conflict_times:
        raise ValueError(f"positive/negative anchor conflicts: {conflict_times[:5]}")

    priority = {
        "owner_no_tip_negative": 0,
        "confirmed_current_tip": 1,
    }
    samples["_priority"] = samples["sample_kind"].map(priority).fillna(99)
    before = len(samples)
    samples = (
        samples.sort_values(["anchor_time", "_priority", "source_task_id"])
        .drop_duplicates(["anchor_time", "target"], keep="first")
        .drop(columns="_priority")
        .reset_index(drop=True)
    )
    samples.attrs["exact_duplicate_samples_removed"] = int(before - len(samples))

    events = pd.DataFrame(global_events)
    events["positive_event_count"] = events["positive_event_ids"].map(
        lambda values: len(set(values))
    )
    events["sample_anchor_min"] = events["event_id"].map(
        samples.groupby("event_id")["anchor_time"].min()
    )
    events["sample_anchor_max"] = events["event_id"].map(
        samples.groupby("event_id")["anchor_time"].max()
    )
    return samples, events


def choose_purged_split(
    samples: pd.DataFrame,
    events: pd.DataFrame,
    *,
    target_train_fraction: float = TARGET_TRAIN_FRACTION,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Choose the closest chronological event split with full label/input purge."""
    if not 0 < target_train_fraction < 1:
        raise ValueError("target_train_fraction must be between zero and one")
    ordered = events.sort_values("event_id").reset_index(drop=True).copy()
    total_positive_events = int(ordered["positive_event_count"].sum())
    if total_positive_events < 2:
        raise ValueError("need at least two positive events for chronological split")
    ordered["cumulative_positive_events"] = ordered["positive_event_count"].cumsum()

    candidates: list[dict[str, Any]] = []
    for index in range(len(ordered) - 1):
        train = ordered.iloc[: index + 1]
        val = ordered.iloc[index + 1 :]
        train_positive = int(train["positive_event_count"].sum())
        val_positive = int(val["positive_event_count"].sum())
        if train_positive == 0 or val_positive == 0:
            continue
        last_train_label_end = _utc(train["label_end"].max())
        first_val_event_ids = set(val["event_id"].astype(int))
        first_val_input_start = _utc(
            samples[samples["event_id"].isin(first_val_event_ids)]["input_start_time"].min()
        )
        if first_val_input_start <= last_train_label_end:
            continue
        fraction = train_positive / total_positive_events
        candidates.append(
            {
                "index": index,
                "distance": abs(fraction - target_train_fraction),
                "fraction": fraction,
                "train_positive": train_positive,
                "val_positive": val_positive,
                "last_train_label_end": last_train_label_end,
                "first_val_input_start": first_val_input_start,
            }
        )
    if not candidates:
        raise ValueError("no event boundary satisfies the 200+60 bar purge")
    selected = sorted(
        candidates,
        key=lambda item: (item["distance"], abs(item["train_positive"] - item["val_positive"])),
    )[0]
    split_index = int(selected["index"])
    train_ids = set(ordered.iloc[: split_index + 1]["event_id"].astype(int))
    out = samples.copy()
    out["split"] = out["event_id"].map(lambda value: "train" if int(value) in train_ids else "val")
    event_out = ordered.copy()
    event_out["split"] = event_out["event_id"].map(
        lambda value: "train" if int(value) in train_ids else "val"
    )

    last_train_anchor = _utc(out.loc[out["split"] == "train", "anchor_time"].max())
    first_val_anchor = _utc(out.loc[out["split"] == "val", "anchor_time"].min())
    anchor_gap_bars = int((first_val_anchor - last_train_anchor) / BAR_DELTA)
    if anchor_gap_bars < WINDOW + FUTURE_BARS:
        raise AssertionError("selected split does not preserve the 200+60 anchor embargo")
    if out.groupby("event_id")["split"].nunique().max() != 1:
        raise AssertionError("global event crossed train/val")
    audit = {
        "target_train_positive_event_fraction": target_train_fraction,
        "actual_train_positive_event_fraction": selected["fraction"],
        "train_positive_events": selected["train_positive"],
        "val_positive_events": selected["val_positive"],
        "last_train_anchor": last_train_anchor.isoformat(),
        "last_train_label_end": selected["last_train_label_end"].isoformat(),
        "first_val_input_start": selected["first_val_input_start"].isoformat(),
        "first_val_anchor": first_val_anchor.isoformat(),
        "anchor_embargo_bars": anchor_gap_bars,
        "required_anchor_embargo_bars": WINDOW + FUTURE_BARS,
    }
    return out, event_out, audit

"""Validate Pattern Event records (v3).

Two classes of finding, kept apart on purpose:

  errors    the record is unusable -- missing field, wrong type, anchor outside
            its own source window
  warnings  the record is usable but unusual -- launch_i before confirm, for
            instance. §7.1 of the spec says such exceptions must reach the audit
            rather than be reordered into compliance, because an anchor order
            that is always satisfied tells you nothing about whether the
            labelling was any good.

Null anchors are never an error. Until a human has stepped through the bars with
the future hidden, null is the correct value.
"""
from __future__ import annotations

from typing import Any

from .schema import ANCHOR_FIELDS, ORDERED_ANCHORS, SCHEMA_VERSION, SIDE_SOURCES

REQUIRED_TOP = (
    "schema_version", "event_id", "source_pattern_id", "source",
    "symbol", "timeframe", "source_window", "original_box", "anchors",
)


def validate_event(d: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for k in REQUIRED_TOP:
        if k not in d:
            errors.append(f"missing field: {k}")
    if errors:
        return errors, warnings

    if d["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version {d['schema_version']} != {SCHEMA_VERSION}")

    sw = d["source_window"]
    for k in ("start_i", "end_i", "bars"):
        if not isinstance(sw.get(k), int):
            errors.append(f"source_window.{k} must be int")
    if not errors and sw["start_i"] > sw["end_i"]:
        errors.append("source_window.start_i > end_i")
    if not errors and sw["end_i"] - sw["start_i"] + 1 != sw["bars"]:
        errors.append("source_window.bars inconsistent with start_i/end_i")

    box = d["original_box"]
    bs, be = box.get("box_start_i"), box.get("box_end_i")
    if bs is not None and be is not None and bs > be:
        errors.append("box_start_i > box_end_i")

    anchors = d["anchors"]
    for k in ANCHOR_FIELDS:
        v = anchors.get(k)
        if v is not None and not isinstance(v, int):
            errors.append(f"anchors.{k} must be int or null")

    # ordering over whichever ordered anchors are present
    present = [(k, (box.get(k) if k.startswith("box_") else anchors.get(k)))
               for k in ORDERED_ANCHORS]
    present = [(k, v) for k, v in present if v is not None]
    for (k1, v1), (k2, v2) in zip(present, present[1:]):
        if v1 > v2:
            errors.append(f"anchor order violated: {k1}({v1}) > {k2}({v2})")

    side = d.get("side")
    if side is not None:
        if side not in ("short", "long"):
            errors.append(f"side must be 'short', 'long' or null, got {side!r}")
        # A side with no stated origin cannot be told apart from one a rule wrote,
        # and a geometry-written side would be circular (AUC 0.988 by construction).
        src = d.get("side_source")
        if src is None:
            errors.append("side is set but side_source is null")
        elif src not in SIDE_SOURCES:
            errors.append(f"side_source {src!r} not in {SIDE_SOURCES}")
    elif d.get("side_source") is not None:
        warnings.append(f"side_source set ({d['side_source']}) while side is null")

    confirm = anchors.get("formation_confirm_i")
    launch = anchors.get("launch_i")
    if confirm is not None and launch is not None and launch < confirm:
        warnings.append(f"launch_i({launch}) < formation_confirm_i({confirm})")

    # anchors must fall inside the window they were judged in
    if not errors:
        lo, hi = sw["start_i"], sw["end_i"]
        for k in ANCHOR_FIELDS:
            v = anchors.get(k)
            if v is not None and not (lo <= v <= hi):
                errors.append(f"anchors.{k}={v} outside source_window [{lo},{hi}]")

    return errors, warnings


def validate_many(records: list[dict[str, Any]]) -> dict[str, Any]:
    n_err = n_warn = 0
    per_record = []
    seen_ids: set[str] = set()
    dup_ids: list[str] = []
    for i, d in enumerate(records):
        e, w = validate_event(d)
        eid = d.get("event_id")
        if eid in seen_ids:
            e = e + [f"duplicate event_id: {eid}"]
        seen_ids.add(eid)
        if e or w:
            per_record.append({"index": i, "event_id": eid, "errors": e, "warnings": w})
        n_err += len(e)
        n_warn += len(w)
    return {
        "n_records": len(records),
        "n_errors": n_err,
        "n_warnings": n_warn,
        "schema_valid_rate": round(
            sum(1 for d in records if not validate_event(d)[0]) / len(records), 6
        ) if records else None,
        "duplicate_event_ids": dup_ids,
        "findings": per_record,
    }

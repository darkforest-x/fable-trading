"""Immutable YOLO context for an owner-initiated manual order draft.

The owner requested a manual-confirmation entry point on 2026-10-17. A saved
box supplies context only: it is not promoted into the automatic signal feed,
and its core/first-seen clocks never replace the complete input endpoint.
Sizing, stops, exit policy and execution authorization are separate inputs.
"""
from __future__ import annotations

import re

from yoyo.yolo_workbench.models import TIMEFRAMES


def detection_context(book, job_id, symbol, detection_id, *, now_ms):
    """Resolve identity and time lineage from saved results, never browser prices.

    Reads only the selected job's frozen spec and raw detection. The returned
    values contain no entry instruction or implied trading permission.
    """
    if (not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id)
            or not isinstance(detection_id, str) or not re.fullmatch(r"[a-f0-9]{24}", detection_id)
            or not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{1,24}-USDT-SWAP", symbol)):
        raise ValueError("invalid detection reference")
    job = book.get(job_id)
    result = book.result(job_id, symbol) if job else None
    if not job or not result or result.get("error"):
        raise ValueError("saved detection is unavailable")
    spec = job["spec"]
    timeframe = spec.get("timeframe")
    if (timeframe not in TIMEFRAMES or result.get("symbol") != symbol
            or any(result.get(k) != spec.get(k) for k in ("timeframe", "model_id", "model_sha256", "mode"))
            or result.get("cutoff_ms") != spec.get("end_ms")):
        raise ValueError("detection result differs from its frozen job")
    matches = [box for box in result.get("raw_detections", []) if box.get("id") == detection_id]
    if len(matches) != 1:
        raise ValueError("detection identity is missing or ambiguous")
    box = matches[0]
    step = TIMEFRAMES[timeframe]
    fields = ("window_start_ms", "window_end_ms", "core_start_ms", "core_end_ms", "detected_at_ms")
    if (any(type(box.get(k)) is not int or box[k] < 0 for k in fields)
            or any(box[k] % step for k in fields)
            or not box["window_start_ms"] <= box["core_start_ms"] <= box["core_end_ms"] <= box["window_end_ms"]
            or box["detected_at_ms"] != box["window_end_ms"] + step
            or not box["detected_at_ms"] <= result["cutoff_ms"] <= now_ms
            or box.get("side") not in {"long", "short"}):
        raise ValueError("invalid detection time lineage or direction")
    return dict(job_id=job_id, detection_id=detection_id, symbol=symbol, timeframe=timeframe,
                proposal_side=box["side"], model_id=result["model_id"], model_sha256=result["model_sha256"],
                source_mode=result["mode"], input_close_ms=box["detected_at_ms"],
                core_start_ms=box["core_start_ms"], core_end_ms=box["core_end_ms"],
                structural_pass=box.get("structural_pass") is True,
                detection_age_ms=now_ms-box["detected_at_ms"], automatic_entry_eligible=False)

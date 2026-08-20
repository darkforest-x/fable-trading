"""Migrate Pattern Library v1 into Pattern Event v2 drafts.

What carries over is only what v1 actually knows: the box, the window it was
drawn in, the quality grade owner gave it, and provenance. box_start_i and
box_end_i come from the bbox geometry, which is mechanical and reproducible.

What does NOT carry over is every causal anchor. formation_start_i,
causal_onset_i, formation_confirm_i and launch_i are all null after migration,
without exception.

It is tempting to seed causal_onset_i from box_end_i -- they are both "where the
pattern is" and it would fill 2366 rows instantly. That is exactly the failure
this whole phase exists to undo: the right edge marks where owner drew, having
seen what came after, and Formation v1 already showed that predicting "K bars
before the box edge" is a different question from predicting the earliest
causally identifiable bar. Seeding would bake the old semantics into the new
schema and make the pilot unable to detect it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..common.hashing import file_sha256
from .schema import (
    Anchors,
    OriginalBox,
    PatternEvent,
    Provenance,
    Review,
    SourceWindow,
)


def box_bar_span(bbox_xywhn: list[float] | None, window_start_i: int,
                 window_bars: int) -> tuple[int | None, int | None]:
    """Left and right edge of the box as bar indices.

    Pure geometry over the window the box was drawn in; no lookahead, no model.
    """
    if not bbox_xywhn:
        return None, None
    cx, _cy, w, _h = bbox_xywhn
    lo = window_start_i + int(round((cx - w / 2) * window_bars))
    hi = window_start_i + int(round((cx + w / 2) * window_bars))
    return lo, hi


def migrate_library(library_path: str | Path, code_commit: str | None = None,
                    render_version: str | None = None) -> list[dict[str, Any]]:
    library_path = Path(library_path)
    lib = json.loads(library_path.read_text())
    lib_sha = file_sha256(library_path)
    out: list[dict[str, Any]] = []

    for n, p in enumerate(lib.get("patterns", []), 1):
        win = p.get("window") or {}
        start_i = int(win.get("start_i", 0))
        bars = int(win.get("bars", 200))
        end_i = int(win.get("end_i", start_i + bars - 1))
        bs, be = box_bar_span(p.get("bbox_xywhn"), start_i, bars)

        ev = PatternEvent(
            event_id=f"evt_{n:06d}",
            source_pattern_id=p["pattern_id"],
            source=p.get("source", "unknown"),
            symbol=p["symbol"],
            timeframe=p.get("timeframe", "15m"),
            source_window=SourceWindow(start_i=start_i, end_i=end_i, bars=bars,
                                       available_at=None),
            original_box=OriginalBox(xywhn=p.get("bbox_xywhn"),
                                     box_start_i=bs, box_end_i=be),
            # every causal anchor stays null -- see module docstring
            anchors=Anchors(),
            anchors_time={},
            quality_label=p.get("human_label"),
            event_validity="unreviewed",
            review=Review(reviewer=("owner" if p.get("human_label") else None),
                          reviewed_at=p.get("human_reviewed_at"),
                          protocol_version=("quality_grading_v1"
                                            if p.get("human_label") else None)),
            provenance=Provenance(pattern_library_sha256=lib_sha,
                                  render_version=render_version,
                                  code_commit=code_commit),
        )
        out.append(ev.to_dict())
    return out

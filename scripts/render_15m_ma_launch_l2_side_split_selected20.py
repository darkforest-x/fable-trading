#!/usr/bin/env python3
"""Render the 20 frozen q90 decisions from the side-split L2 experiment.

The source ledger is the immutable final-validation score file produced by
``retrain_15m_ma_launch_l2_by_side.py``.  A selected event must be the earliest
representative of its complete exposure dependency block and pass its side's
tune-only q90 threshold.  Rendering reuses the exact frozen pre-holdout OHLCV
snapshot and the causal 168-bar global chart renderer from the parent L2
experiment.  Each PNG ends at ``feature_bar_i``; no label/outcome bar is drawn.

This is a review export only.  It does not train, tune, read holdout, promote,
deploy, mutate ACTIVE/frozen/forward state, send Telegram, or place orders.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    DEFAULT_OUT as GLOBAL_OUT,
    DEFAULT_PREREG as GLOBAL_PREREG,
    DEFAULT_RESULTS as GLOBAL_RESULTS,
    load_preregistration,
    load_snapshot,
    pixel_sha256,
    render_global_chart,
    sha256_file,
    utc,
)


ROOT = Path(__file__).resolve().parents[1]
SCORED = ROOT / "analysis/output/ma_launch_l2_side_split_v1/final_validation_side_split_scored.csv"
EXPECTED_SCORED_SHA256 = "5b63941e21fe56930c2aec78c54fe93430cdab6576bfff5dbb50d743e4134c25"
OUTPUT = ROOT / "analysis/output/ma_launch_l2_side_split_v1/selected20_charts"
EXPECTED_COUNTS = {"long": 13, "short": 7}
CONTACT_COLUMNS = 2
CONTACT_TILE_WIDTH = 960
CONTACT_TILE_HEIGHT = 625
CONTACT_HEADER_HEIGHT = 92


class Selected20RenderError(RuntimeError):
    """Raised when source identity, selection, or rendered pixels drift."""


def parse_bool(series: pd.Series, *, label: str) -> pd.Series:
    """Parse a persisted boolean column without treating ``"False"`` as true."""

    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin(("true", "false")).all():
        raise Selected20RenderError(f"{label} contains non-boolean values")
    return normalized.map({"true": True, "false": False}).astype(bool)


def select_frozen_q90_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only q90-kept dependency representatives, ordered by side rank."""

    required = {
        "episode_id",
        "symbol",
        "side",
        "split",
        "dependency_representative",
        "l2_keep",
        "side_percentile_score",
        "available_at",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise Selected20RenderError(f"scored ledger missing columns: {missing}")
    work = frame.copy()
    work["dependency_representative"] = parse_bool(
        work["dependency_representative"], label="dependency_representative"
    )
    work["l2_keep"] = parse_bool(work["l2_keep"], label="l2_keep")
    selected = work.loc[
        (work["split"] == "final_validation")
        & work["dependency_representative"]
        & work["l2_keep"]
    ].copy()
    counts = selected["side"].value_counts().to_dict()
    if counts != EXPECTED_COUNTS:
        raise Selected20RenderError(f"selected side counts drifted: {counts} != {EXPECTED_COUNTS}")
    if selected["episode_id"].duplicated().any():
        raise Selected20RenderError("selected episode_id is not unique")
    selected["_side_order"] = selected["side"].map({"long": 0, "short": 1})
    if selected["_side_order"].isna().any():
        raise Selected20RenderError("selected ledger contains an unknown side")
    return selected.sort_values(
        ["_side_order", "side_percentile_score", "available_at", "episode_id"],
        ascending=[True, False, True, True],
        kind="stable",
    ).drop(columns="_side_order").reset_index(drop=True)


def contact_sheet(images: Sequence[np.ndarray], *, side: str) -> np.ndarray:
    """Build a readable two-column high-resolution sheet for one side."""

    if not images:
        raise Selected20RenderError(f"cannot build empty {side} contact sheet")
    rows = math.ceil(len(images) / CONTACT_COLUMNS)
    canvas = np.full(
        (
            CONTACT_HEADER_HEIGHT + rows * CONTACT_TILE_HEIGHT,
            CONTACT_COLUMNS * CONTACT_TILE_WIDTH,
            3,
        ),
        255,
        dtype=np.uint8,
    )
    title = f"L2 SIDE-SPLIT Q90 SELECTED | {side.upper()} | {len(images)} EVENTS | DECISION-ONLY 168 BARS"
    cv2.putText(
        canvas,
        title,
        (24, 58),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (20, 20, 20),
        2,
        cv2.LINE_AA,
    )
    for index, image in enumerate(images):
        thumb = cv2.resize(
            image,
            (CONTACT_TILE_WIDTH, CONTACT_TILE_HEIGHT),
            interpolation=cv2.INTER_AREA,
        )
        row, column = divmod(index, CONTACT_COLUMNS)
        y0 = CONTACT_HEADER_HEIGHT + row * CONTACT_TILE_HEIGHT
        x0 = column * CONTACT_TILE_WIDTH
        canvas[y0 : y0 + CONTACT_TILE_HEIGHT, x0 : x0 + CONTACT_TILE_WIDTH] = thumb
    return canvas


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def load_sources() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    if sha256_file(SCORED) != EXPECTED_SCORED_SHA256:
        raise Selected20RenderError("side-split scored ledger SHA drifted")
    selected = select_frozen_q90_events(pd.read_csv(SCORED))
    prereg = load_preregistration(GLOBAL_PREREG)
    frames = load_snapshot(prereg, out=GLOBAL_OUT, results=GLOBAL_RESULTS)
    return selected, frames


def render_selected(output: Path = OUTPUT) -> dict[str, Any]:
    """Render all selected events and deterministic per-side contact sheets."""

    if output.exists():
        raise FileExistsError(f"refusing to replace existing selected20 gallery: {output}")
    building = output.with_name(output.name + ".building")
    if building.exists():
        raise FileExistsError(f"stale building directory requires inspection: {building}")
    building.mkdir(parents=True)
    selected, frames = load_sources()
    manifest_rows: list[dict[str, Any]] = []
    side_images: dict[str, list[np.ndarray]] = {"long": [], "short": []}
    side_orders = Counter()
    try:
        for overall_order, row in enumerate(selected.to_dict("records"), 1):
            side = str(row["side"])
            side_orders[side] += 1
            image = render_global_chart(row, frames[str(row["symbol"])])
            side_images[side].append(image)
            filename = (
                f"{side_orders[side]:02d}_{side.upper()}_{row['symbol']}_"
                f"{utc(row['available_at']):%Y%m%dT%H%M}.png"
            )
            relative_inside = Path(side) / filename
            building_path = building / relative_inside
            building_path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(building_path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise Selected20RenderError(f"could not write {building_path}")
            final_path = output / relative_inside
            manifest_rows.append(
                {
                    "display_order": overall_order,
                    "side_order": side_orders[side],
                    "episode_id": str(row["episode_id"]),
                    "symbol": str(row["symbol"]),
                    "side": side,
                    "available_at": str(row["available_at"]),
                    "l1_confidence": float(row["l1_confidence"]),
                    "l2_score": float(row["l2_score"]),
                    "l2_threshold": float(row["l2_threshold"]),
                    "side_percentile_score": float(row["side_percentile_score"]),
                    "outcome_metadata_not_rendered": str(row["outcome"]),
                    "net_ret_metadata_not_rendered": float(row["net_ret"]),
                    "chart_path": repo_relative(final_path),
                    "chart_png_sha256": sha256_file(building_path),
                    "chart_pixel_sha256": pixel_sha256(image),
                }
            )

        contacts: dict[str, dict[str, Any]] = {}
        for side in ("long", "short"):
            sheet = contact_sheet(side_images[side], side=side)
            filename = f"contact_{side}_{len(side_images[side]):02d}.png"
            building_path = building / filename
            if not cv2.imwrite(str(building_path), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise Selected20RenderError(f"could not write {building_path}")
            contacts[side] = {
                "path": repo_relative(output / filename),
                "png_sha256": sha256_file(building_path),
                "pixel_sha256": pixel_sha256(sheet),
                "width": int(sheet.shape[1]),
                "height": int(sheet.shape[0]),
            }

        manifest_path = building / "manifest.csv"
        pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
        receipt = {
            "protocol": "15m_grade_a_l2_side_split_selected20_decision_gallery_v1",
            "source_scored_path": repo_relative(SCORED),
            "source_scored_sha256": EXPECTED_SCORED_SHA256,
            "selection": "split=final_validation AND dependency_representative=true AND l2_keep=true",
            "events": len(manifest_rows),
            "side_counts": dict(sorted(Counter(row["side"] for row in manifest_rows).items())),
            "context_bars": 168,
            "future_outcome_pixels": 0,
            "manifest_path": repo_relative(output / "manifest.csv"),
            "manifest_sha256": sha256_file(manifest_path),
            "contacts": contacts,
            "holdout_rows_read": 0,
            "training_or_tuning": False,
            "promoted_or_deployed": False,
            "production_eligible": False,
        }
        write_json(building / "receipt.json", receipt)
        building.replace(output)
        return receipt
    except Exception:
        # Leave the building directory intact as evidence; never hide a partial render.
        raise


def verify_selected(output: Path = OUTPUT) -> dict[str, Any]:
    """Re-render all individual and contact-sheet pixels and verify lineage."""

    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("source_scored_sha256") != EXPECTED_SCORED_SHA256:
        raise Selected20RenderError("receipt source SHA drifted")
    manifest_path = output / "manifest.csv"
    if sha256_file(manifest_path) != receipt.get("manifest_sha256"):
        raise Selected20RenderError("manifest SHA drifted")
    manifest = pd.read_csv(manifest_path)
    selected, frames = load_sources()
    source_by_id = {str(row["episode_id"]): row for row in selected.to_dict("records")}
    failures: list[str] = []
    side_images: dict[str, list[np.ndarray]] = {"long": [], "short": []}
    for record in manifest.to_dict("records"):
        episode_id = str(record["episode_id"])
        source = source_by_id.get(episode_id)
        if source is None:
            failures.append(f"missing-source:{episode_id}")
            continue
        image = render_global_chart(source, frames[str(source["symbol"])])
        side_images[str(source["side"])].append(image)
        path = ROOT / str(record["chart_path"])
        if not path.is_file() or sha256_file(path) != str(record["chart_png_sha256"]):
            failures.append(f"png:{episode_id}")
        if pixel_sha256(image) != str(record["chart_pixel_sha256"]):
            failures.append(f"pixels:{episode_id}")
    for side in ("long", "short"):
        sheet = contact_sheet(side_images[side], side=side)
        contact = receipt["contacts"][side]
        path = ROOT / str(contact["path"])
        if not path.is_file() or sha256_file(path) != str(contact["png_sha256"]):
            failures.append(f"contact-png:{side}")
        if pixel_sha256(sheet) != str(contact["pixel_sha256"]):
            failures.append(f"contact-pixels:{side}")
    result = {
        "passed": not failures,
        "events_checked": len(manifest),
        "side_counts": dict(sorted(Counter(manifest["side"]).items())),
        "failures": failures,
        "future_outcome_pixels": 0,
        "holdout_rows_read": 0,
    }
    if failures:
        raise Selected20RenderError(f"selected20 verification failed: {failures[:10]}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--render", action="store_true")
    actions.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = render_selected(args.output) if args.render else verify_selected(args.output)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Render versioned research references from frozen, per-event candle bounds.

The owner requested pre-launch MA cores only. Geometry uses high/low and close
SMA/EMA 20/60/120 within the selected core; the first launch candle is outside
the box. Full source prefixes preserve EMA seeding. Later candles remain visible
as retrospective context, so these are neither causal training inputs nor gold
labels. Original images and annotations are never modified.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd

from yoyo.datasets.ma_launch_owner_recrop_review import core_box, draw_box, encode_png
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l1_detection.render import render_chart

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-spike-gemini-vision-20260923-v1"
SPEC = EXPERIMENT / "references_v3.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bounds(item: dict) -> tuple[int, int, int]:
    """Reject a core that includes its individually reviewed launch candle."""
    anchor = item["source_anchor_i"]
    first = anchor + item["core_start_offset"]
    last = anchor + item["core_end_offset"]
    launch = anchor + item["first_launch_offset"]
    if not 4 <= last - first + 1 <= 7:
        raise ValueError("reference core must contain 4-7 candles")
    if not item["window_start_i"] <= first <= last < launch <= item["window_end_i"]:
        raise ValueError("core must exclude launch and stay inside the chart")
    return first, last, launch


def render_item(item: dict) -> tuple[bytes, dict]:
    first, last, launch = validate_bounds(item)
    source = ROOT / item["source_ohlc_path"]
    original = ROOT / item["original_image_path"]
    if sha256(original) != item["original_image_sha256"]:
        raise ValueError("original reference image changed")
    prefix = pd.read_csv(source, nrows=item["window_end_i"] + 1,
                         float_precision="round_trip")
    if len(prefix) != item["window_end_i"] + 1:
        raise ValueError("source prefix is incomplete")
    prefix["open_time"] = pd.to_datetime(prefix["open_time"], utc=True)
    if prefix.iloc[item["source_anchor_i"]]["open_time"] != pd.Timestamp(item["anchor_time"]):
        raise ValueError("source anchor does not match the frozen event")
    prefix_hash = hashlib.sha256(prefix.to_csv(index=False).encode()).hexdigest()
    if prefix_hash != item["expected_source_prefix_sha256"]:
        raise ValueError("frozen OHLC prefix changed")
    frame = add_six_mas(prefix)
    start = item["window_start_i"]
    window = frame.iloc[start:item["window_end_i"] + 1].reset_index(drop=True)
    if not window["open_time"].diff().iloc[1:].eq(pd.Timedelta(minutes=15)).all():
        raise ValueError("reference chart has non-contiguous 15-minute candles")
    canvas, transform = render_chart(window)
    clean_sha = hashlib.sha256(encode_png(canvas)).hexdigest()
    if item.get("expected_clean_sha256") and clean_sha != item["expected_clean_sha256"]:
        raise ValueError("regenerated OHLC/MA pixels differ from the original chart")
    box = core_box(transform, window, start_local=first - start, end_local=last - start)
    if box["x1"] + 3 >= transform.x_at(launch - start) - transform.candle_half_w:
        raise ValueError("box stroke overlaps the launch candle")
    draw_box(canvas, box)
    data = encode_png(canvas)
    metadata = {
        **item, "source_sha256": hashlib.sha256(data).hexdigest(),
        "clean_sha256": clean_sha, "source_prefix_sha256": prefix_hash,
        "source_prefix_hash_format": "pandas round-trip CSV through window_end_i",
        "source_box": box, "core_bars": last - first + 1,
        "core_start_time": frame.iloc[first]["open_time"].isoformat(),
        "core_end_time": frame.iloc[last]["open_time"].isoformat(),
        "first_launch_time": frame.iloc[launch]["open_time"].isoformat(),
        "box_semantics": "core_wicks_and_six_moving_averages",
        "sample_owner_geometry_confirmed": False,
        "selection_status": "assistant_visual_review_pending_owner",
        "time_boundary": "retrospective_reference_only",
        "training_eligible": False, "production_eligible": False,
    }
    return data, metadata


def build() -> dict:
    """Require the builder and frozen decisions to be committed before rendering."""
    dependencies = [Path(__file__), SPEC,
                    ROOT / "yoyo/datasets/ma_launch_owner_recrop_review.py",
                    ROOT / "yoyo/datasets/ma_rope_filter.py",
                    ROOT / "yoyo/layers/l1_detection/render.py"]
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    if git("branch", "--show-current") != "main":
        raise RuntimeError("reference packs must be built on main")
    paths = [str(path.relative_to(ROOT)) for path in dependencies]
    if git("status", "--short", "--", *paths):
        raise RuntimeError("commit the reference builder and decisions before building")
    for path in paths:
        git("ls-files", "--error-unmatch", path)
    spec = json.loads(SPEC.read_text())
    output = EXPERIMENT / spec["output_directory"]
    if output.exists():
        raise FileExistsError("preserve existing packs; use a new version")
    temporary = output.with_name(output.name + ".building")
    temporary.mkdir()
    try:
        items = []
        for item in spec["items"]:
            data, metadata = render_item(item)
            (temporary / item["file"]).write_bytes(data)
            items.append(metadata)
        manifest = {
            "version": spec["version"], "builder_commit": git("rev-parse", "HEAD"),
            "spec_sha256": sha256(SPEC),
            "purpose": "Pre-launch core references with retrospective context, pending Owner review.",
            "items": items,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        temporary.rename(output)
    except Exception:
        shutil.rmtree(temporary)
        raise
    return {"output": str(output), "images": len(items), "builder_commit": manifest["builder_commit"]}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))

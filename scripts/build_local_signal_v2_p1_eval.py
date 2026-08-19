#!/usr/bin/env python3
"""Build one common pre-holdout event-evaluation ruler for P1 arms.

The source endpoints are the strict-negative V2 validation rows.  Every arm is
re-rendered from the same raw market series and ends on the same decision bar;
only the visible window length changes.  This keeps A/B1/B2/C3 event metrics
comparable without reading the project holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from scripts.build_local_signal_v2_stageb import HOLDOUT_START  # noqa: E402
from scripts.build_w20_midbox_dataset import resolve_series  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

DEFAULT_DATASET = PROJECT / "datasets" / "local_signal_v2_stageb_strictneg_v2"
DEFAULT_OUT = PROJECT / "analysis" / "output" / "local_signal_v2_p1_eval"
ARM_WINDOWS: dict[str, int | None] = {"A": 200, "B1": 24, "B2": 30, "C3": None}
MAX_EVAL_WINDOW = 200


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_source_rows(dataset: Path) -> list[dict]:
    pos = json.loads((dataset / "w20_manifest.json").read_text())
    neg = json.loads((dataset / "w20_neg_manifest.json").read_text())
    rows: list[dict] = []
    for row in pos:
        if row.get("split") != "val":
            continue
        rows.append({**row, "sample_type": "positive"})
    for row in neg:
        if row.get("split") != "val":
            continue
        rows.append({**row, "sample_type": "easy_negative"})
    # The frozen 200-bar baseline cannot score endpoints from newly listed
    # symbols with fewer than 200 preceding bars.  Remove those endpoints from
    # every arm rather than silently giving A a different denominator.
    common = []
    for row in rows:
        end_bar = int(
            row.get("decision_bar", int(row["win_start"]) + int(row["win_len"]) - 1)
        )
        if end_bar >= MAX_EVAL_WINDOW - 1:
            common.append(row)
    return sorted(common, key=lambda row: (row["sample_type"], row["stem"]))


def render_arm(rows: list[dict], arm: str, fixed_window: int | None, out: Path) -> list[dict]:
    image_dir = out / arm / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for stale in image_dir.glob("*.png"):
        stale.unlink()
    enriched_cache: dict[str, pd.DataFrame] = {}
    result: list[dict] = []
    for index, row in enumerate(rows, start=1):
        symbol = str(row["symbol"])
        if symbol not in enriched_cache:
            series = resolve_series(symbol)
            if series is None:
                raise RuntimeError(f"no market series for {symbol}")
            enriched_cache[symbol] = add_mas(series)
        series = enriched_cache[symbol]
        source_len = int(row["win_len"])
        source_start = int(row["win_start"])
        end_bar = int(row.get("decision_bar", source_start + source_len - 1))
        window_len = source_len if fixed_window is None else fixed_window
        start_bar = end_bar - window_len + 1
        if start_bar < 0 or end_bar >= len(series):
            raise RuntimeError(
                f"window out of bounds arm={arm} symbol={symbol} "
                f"start={start_bar} end={end_bar} n={len(series)}"
            )
        end_time = pd.to_datetime(
            series.iloc[end_bar].get("open_time", series.index[end_bar]),
            utc=True,
            errors="coerce",
        )
        if pd.isna(end_time) or end_time >= HOLDOUT_START:
            raise RuntimeError(f"holdout row refused: {symbol} {end_time}")
        frame = series.iloc[start_bar : end_bar + 1].reset_index(drop=True)
        image_path = image_dir / f"{row['stem']}.png"
        image, _ = render_chart(frame, out_path=None)
        if not cv2.imwrite(str(image_path), image):
            raise RuntimeError(f"failed to write {image_path}")
        anchor_global = row.get("mid_global")
        anchor_local = (
            None if anchor_global is None else int(anchor_global) - start_bar
        )
        result.append(
            {
                "eval_id": row["stem"],
                "arm": arm,
                "sample_type": row["sample_type"],
                "event_id": row.get("event_id"),
                "symbol": symbol,
                "window_len": window_len,
                "window_start_bar": start_bar,
                "window_end_bar": end_bar,
                "window_end_timestamp": str(end_time),
                "anchor_bar_index": anchor_global,
                "anchor_local_bar": anchor_local,
                "confirm_delay": row.get("confirm_delay"),
                "image_path": str(image_path.relative_to(PROJECT)),
                "image_sha256": sha256_file(image_path),
            }
        )
        if index % 100 == 0:
            print(f"{arm}: {index}/{len(rows)}", flush=True)
    manifest = out / arm / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rows = load_source_rows(args.dataset)
    if not rows:
        parser.error("no validation rows")
    counts = {
        kind: sum(row["sample_type"] == kind for row in rows)
        for kind in ("positive", "easy_negative")
    }
    summary = {
        "source_dataset": str(args.dataset),
        "holdout_start_exclusive": str(HOLDOUT_START),
        "source_counts": counts,
        "arms": {},
    }
    for arm, window in ARM_WINDOWS.items():
        built = render_arm(rows, arm, window, args.out)
        summary["arms"][arm] = {
            "n": len(built),
            "window": "source_20_30" if window is None else window,
            "max_end": max(row["window_end_timestamp"] for row in built),
        }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

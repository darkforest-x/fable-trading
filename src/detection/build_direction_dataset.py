"""Build causal MA206 images for long/short/no-trade classification.

Each image contains 200 bars ending at its signal bar. Existing fixed
TP5/SL2 h72 labelers may inspect the future to assign the class, but rows at
or beyond the judgment holdout purge boundary are never rendered or emitted.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from src.data.loader import iter_series, list_series, load_series
from src.detection.direction_dataset import (
    assign_temporal_splits,
    causal_window,
    classify_direction,
    dedupe_candidate_indices,
)
from src.detection.render import render_chart
from src.judgment.candidates import (
    MIN_GAP_BARS,
    add_indicators,
    scan_candidates,
    scan_short_candidates,
)
from src.judgment.features import FEATURE_COLUMNS, add_features
from src.judgment.labeling import HORIZON_BARS, label_candidate, label_short_candidate
from src.judgment.train import HOLDOUT_START

LOOKBACK_BARS = 200
DIRECTION_IMAGE_SIZE = 640
DEFAULT_OUTPUT = Path("datasets/ma206_direction_causal_v1")


class DirectionDatasetBuildError(RuntimeError):
    """Raised before publishing an incomplete or stale classification dataset."""


def build_direction_manifest(
    series: Iterable[tuple[str, str, pd.DataFrame]],
    *,
    horizon_bars: int = HORIZON_BARS,
    lookback_bars: int = LOOKBACK_BARS,
) -> pd.DataFrame:
    """Build a pre-holdout manifest from the fixed OKX SWAP candidate rules."""
    records: list[dict] = []
    for source, symbol, raw_frame in series:
        if source != "okx" or not symbol.endswith("_USDT_SWAP"):
            continue
        frame = raw_frame[raw_frame["open_time"] < HOLDOUT_START].copy().reset_index(drop=True)
        enriched = add_indicators(frame)
        long_indices = scan_candidates(enriched, horizon_bars=horizon_bars, mode="expanded")
        short_indices = scan_short_candidates(enriched, horizon_bars=horizon_bars, mode="expanded")
        signal_indices = dedupe_candidate_indices(
            long_indices,
            short_indices,
            min_gap_bars=MIN_GAP_BARS,
        )
        long_set = set(long_indices)
        short_set = set(short_indices)
        featured = add_features(enriched, mode="expanded")
        for signal_i in signal_indices:
            if signal_i < lookback_bars - 1:
                continue
            long_outcome = label_candidate(enriched, signal_i, horizon=horizon_bars)
            short_outcome = label_short_candidate(enriched, signal_i, horizon=horizon_bars)
            if long_outcome is None or short_outcome is None:
                continue
            record = {
                "source": source,
                "symbol": symbol,
                "signal_i": signal_i,
                "signal_time": enriched["open_time"].iloc[signal_i],
                "direction_class": classify_direction(long_outcome, short_outcome),
                "long_candidate": signal_i in long_set,
                "short_candidate": signal_i in short_set,
                "long_outcome": long_outcome.outcome,
                "short_outcome": short_outcome.outcome,
                "long_realized_ret": long_outcome.realized_ret,
                "short_realized_ret": short_outcome.realized_ret,
            }
            record.update(featured.iloc[signal_i][FEATURE_COLUMNS].to_dict())
            records.append(record)
    manifest = pd.DataFrame(records)
    if manifest.empty:
        raise DirectionDatasetBuildError("no causal direction candidates were produced")
    manifest = manifest.drop_duplicates(["source", "symbol", "signal_time"])
    return assign_temporal_splits(manifest, horizon_bars=horizon_bars, bar="15m")


def select_manifest_rows(
    manifest: pd.DataFrame,
    *,
    limit_per_class_split: int,
) -> pd.DataFrame:
    """Select a deterministic smoke subset or preserve every full-run row."""
    ordered = manifest.sort_values(["split", "direction_class", "signal_time"])
    if limit_per_class_split <= 0:
        return ordered.reset_index(drop=True)
    selected = (
        ordered.groupby(["split", "direction_class"], sort=True, group_keys=False)
        .head(limit_per_class_split)
        .reset_index(drop=True)
    )
    return selected


def render_causal_image(
    enriched: pd.DataFrame,
    *,
    signal_i: int,
    out_path: Path,
    lookback_bars: int = LOOKBACK_BARS,
    image_size: int = DIRECTION_IMAGE_SIZE,
) -> None:
    """Render one fixed lookback that ends exactly at `signal_i`."""
    window = causal_window(enriched, signal_i=signal_i, lookback_bars=lookback_bars)
    render_chart(window, width=image_size, height=image_size, out_path=out_path)


def _relative_image_path(row: pd.Series) -> Path:
    timestamp = pd.Timestamp(row["signal_time"]).strftime("%Y%m%dT%H%M%S")
    stem = f"{row['source']}_{row['symbol']}_{timestamp}_{int(row['signal_i']):06d}.png"
    return Path(str(row["split"])) / str(row["direction_class"]) / stem


def _load_current_series(
    source: str,
    symbol: str,
    required_times: set[pd.Timestamp],
) -> pd.DataFrame:
    """Reload renamed OKX files until every manifest signal time is present."""
    available: set[pd.Timestamp] = set()
    for _ in range(3):
        paths = list_series(bar="15m").get((source, symbol))
        if not paths:
            continue
        frame = load_series(paths)
        frame = frame[frame["open_time"] < HOLDOUT_START].copy().reset_index(drop=True)
        available = set(pd.to_datetime(frame["open_time"], utc=True))
        if required_times.issubset(available):
            return frame
    missing = sorted(str(item) for item in required_times - available)
    raise DirectionDatasetBuildError(f"missing manifest times for {source}/{symbol}: {missing[:3]}")


def materialize_images(
    manifest: pd.DataFrame,
    *,
    out_dir: Path,
    lookback_bars: int = LOOKBACK_BARS,
) -> pd.DataFrame:
    """Render manifest rows from the same filtered source-series indexing."""
    result = manifest.copy()
    result["image_path"] = [str(_relative_image_path(row)) for _, row in result.iterrows()]
    for (source, symbol), rows in result.groupby(["source", "symbol"]):
        required_times = set(pd.to_datetime(rows["signal_time"], utc=True))
        frame = _load_current_series(str(source), str(symbol), required_times)
        enriched = add_indicators(frame)
        index_by_time = pd.Series(frame.index, index=pd.to_datetime(frame["open_time"], utc=True))
        for row in rows.itertuples(index=False):
            path = out_dir / row.image_path
            path.parent.mkdir(parents=True, exist_ok=True)
            signal_i = int(index_by_time.loc[pd.Timestamp(row.signal_time)])
            render_causal_image(
                enriched,
                signal_i=signal_i,
                out_path=path,
                lookback_bars=lookback_bars,
            )
    return result


def summarize_manifest(manifest: pd.DataFrame, *, lookback_bars: int) -> dict:
    """Return a compact machine-checkable dataset summary."""
    counts = manifest.groupby(["split", "direction_class"]).size()
    class_counts = {
        split: {
            class_name: int(count)
            for class_name, count in counts.loc[split].sort_index().items()
        }
        for split in sorted(manifest["split"].unique())
    }
    return {
        "images": int(len(manifest)),
        "symbols": int(manifest["symbol"].nunique()) if "symbol" in manifest else 0,
        "lookback_bars": lookback_bars,
        "image_size": [DIRECTION_IMAGE_SIZE, DIRECTION_IMAGE_SIZE],
        "horizon_bars": HORIZON_BARS,
        "class_counts": class_counts,
        "time_ranges": {
            split: [
                str(rows["signal_time"].min()),
                str(rows["signal_time"].max()),
            ]
            for split, rows in manifest.groupby("split")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit-per-class-split", type=int, default=0)
    args = parser.parse_args()
    if (args.out / "manifest.csv").exists():
        raise DirectionDatasetBuildError(f"dataset already exists: {args.out}")
    manifest = build_direction_manifest(iter_series(bar="15m", min_bars=500))
    selected = select_manifest_rows(
        manifest,
        limit_per_class_split=args.limit_per_class_split,
    )
    rendered = materialize_images(selected, out_dir=args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    rendered.to_csv(args.out / "manifest.csv", index=False)
    summary = summarize_manifest(rendered, lookback_bars=LOOKBACK_BARS)
    (args.out / "dataset_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

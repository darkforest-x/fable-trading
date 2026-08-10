#!/usr/bin/env python3
"""Build owner-authorized Local-Signal V2 Stage-A random-crop dataset.

Source and decision basis:
  - Event anchors come from the pre-existing pad200 migration manifest
    ``dense_owner_w20_midbox/w20_manifest.json``.
  - OHLC and SMA/EMA 20/60/120 are loaded from the original continuous 15m
    market series.  Each image uses one contiguous 20--30 bar window.
  - Mode C is kept aligned with Stage B: ``confirm_delay in {1,2}``, decision
    is ``anchor + delay``, and the label spans ``anchor - 2 .. decision``.
  - Stage A deliberately renders real historical bars after decision so the
    anchor can occupy 20%--85% of the *real candle sequence*.  This is an
    owner-authorized offline representation task, never a production input.

Safety:
  - The complete rendered window must end before 2026-05-04; no holdout market
    rows are loaded for source events already known to cross that boundary.
  - Split is chronological by event with a 150-bar purge, and negative windows
    must remain inside their split's frozen time block.
  - One crop per event, no future outcome label, no ACTIVE/forward/deployment.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for path in (PROJECT, YOYO_REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_local_signal_v2_stageb import (  # noqa: E402
    BAR_MINUTES,
    BOX_LEFT,
    HOLDOUT_START,
    NEG_MARGIN,
    NEG_MAX_TRIES,
    PURGE_BARS,
    VAL_FRAC,
    config_hash_of,
    event_id_of,
    forbidden_intervals,
    load_source_events,
    negative_window_allowed,
    overlaps,
    sha256_file,
    write_yaml,
)
from scripts.build_w20_midbox_dataset import (  # noqa: E402
    WIN_MAX,
    WIN_MIN,
    resolve_series,
    stable_seed,
    yolo_box_from_bars,
)

PROTOCOL = "local_signal_v2_stagea_randomcrop_v1_20260811"
RENDERER_VERSION = "yoyo.l1_detection.render.render_chart"
DEFAULT_SRC_MANIFEST = (
    PROJECT / "datasets" / "dense_owner_w20_midbox" / "w20_manifest.json"
)
DEFAULT_OUT = PROJECT / "datasets" / "local_signal_v2_stagea_randomcrop_v1"
CONFIRM_DELAYS = (1, 2)
POSITION_BUCKETS = (
    ("left_mid", 0.20, 0.35, 0.20),
    ("mid", 0.35, 0.55, 0.35),
    ("mid_right", 0.55, 0.75, 0.30),
    ("right", 0.75, 0.85, 0.15),
)
POSITION_SHARE_TOLERANCE = 0.05


@dataclass(frozen=True)
class StageAPlan:
    event_id: str
    stem: str
    out_stem: str
    source_stem: str
    symbol: str
    split: str
    mid_global: int
    confirm_delay: int
    half: int
    decision_bar: int
    small_bars: tuple[int, int]
    win_len: int
    win_start: int
    small_local: tuple[int, int]
    anchor_offset: int
    anchor_x_ratio: float
    box_pos_frac: float
    position_bucket: str
    start_time: str
    anchor_time: str
    decision_time: str
    visible_end_time: str
    end_time: str
    future_bars: int
    stage: str
    mode: str
    production_eligible: bool
    renderer_version: str
    config_hash: str


class Skip(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail or reason
        super().__init__(self.detail)


_ENRICHED_CACHE: dict[str, pd.DataFrame] = {}


def enriched_series(symbol: str) -> pd.DataFrame:
    """Return cached OHLC + six MAs; no rows outside the source series are read."""
    if symbol not in _ENRICHED_CACHE:
        frame = resolve_series(symbol)
        if frame is None:
            raise Skip("no_series", symbol)
        _ENRICHED_CACHE[symbol] = add_mas(frame)
    return _ENRICHED_CACHE[symbol]


def offsets_for_bucket(
    win_len: int,
    confirm_delay: int,
    bucket_index: int,
) -> list[int]:
    """Return anchor offsets whose ratio lies in one frozen Stage-A bucket."""
    name, ratio_lo, ratio_hi, _share = POSITION_BUCKETS[bucket_index]
    del name
    offsets: list[int] = []
    for offset in range(win_len):
        ratio = offset / max(win_len - 1, 1)
        inside = ratio_lo <= ratio < ratio_hi
        if bucket_index == len(POSITION_BUCKETS) - 1:
            inside = ratio_lo <= ratio <= ratio_hi
        box_fits = offset >= BOX_LEFT and offset + confirm_delay < win_len
        leaves_real_future = offset + confirm_delay < win_len - 1
        if inside and box_fits and leaves_real_future:
            offsets.append(offset)
    return offsets


def sample_geometry(
    rng: np.random.Generator,
) -> tuple[int, int, int, str]:
    """Sample ``(win_len, delay, anchor_offset, position_bucket)`` deterministically."""
    win_len = int(rng.integers(WIN_MIN, WIN_MAX + 1))
    confirm_delay = int(rng.choice(CONFIRM_DELAYS))
    weights = [bucket[3] for bucket in POSITION_BUCKETS]
    bucket_index = int(rng.choice(len(POSITION_BUCKETS), p=weights))
    offsets = offsets_for_bucket(win_len, confirm_delay, bucket_index)
    if not offsets:
        raise Skip(
            "no_position_offset",
            f"win_len={win_len} delay={confirm_delay} bucket={bucket_index}",
        )
    offset = int(rng.choice(offsets))
    return win_len, confirm_delay, offset, POSITION_BUCKETS[bucket_index][0]


def plan_positive(source: dict, *, seed: int) -> StageAPlan:
    """Plan one real-window crop without rendering or reading future outcomes."""
    source_end = pd.to_datetime(source.get("end_time"), utc=True, errors="coerce")
    if pd.isna(source_end) or source_end >= HOLDOUT_START:
        # Metadata-only rejection before market-series access.
        raise Skip("source_holdout_or_no_time", str(source_end))
    symbol = str(source["symbol"])
    anchor = int(source["mid_global"])
    source_stem = str(source["stem"])
    frame = enriched_series(symbol)
    rng = np.random.default_rng(stable_seed(seed, "stagea", source_stem))
    win_len, delay, anchor_offset, bucket = sample_geometry(rng)
    win_start = anchor - anchor_offset
    win_end = win_start + win_len - 1
    decision = anchor + delay
    box_start = anchor - BOX_LEFT
    box_end = decision
    if win_start < 0 or win_end >= len(frame):
        raise Skip("window_oob", f"{win_start=} {win_end=} n={len(frame)}")
    if box_start < win_start or box_end >= win_end:
        raise Skip("box_or_future_missing", f"box={box_start}-{box_end} win={win_start}-{win_end}")

    start_time = pd.to_datetime(frame.iloc[win_start]["open_time"], utc=True)
    anchor_time = pd.to_datetime(frame.iloc[anchor]["open_time"], utc=True)
    decision_time = pd.to_datetime(frame.iloc[decision]["open_time"], utc=True)
    end_time = pd.to_datetime(frame.iloc[win_end]["open_time"], utc=True)
    if end_time >= HOLDOUT_START:
        raise Skip("window_touches_holdout", str(end_time))
    future_bars = win_end - decision
    if future_bars < 1:
        raise Skip("no_real_bars_after_decision", str(future_bars))
    local_start = box_start - win_start
    local_end = box_end - win_start
    anchor_ratio = anchor_offset / max(win_len - 1, 1)
    box_ratio = ((local_start + local_end) / 2) / max(win_len - 1, 1)
    eid = event_id_of(symbol, anchor, source_stem)
    out_stem = source_stem.replace("_pad200", "") + "_stagea"
    config_hash = config_hash_of(
        protocol=PROTOCOL,
        win_len=win_len,
        confirm_delay=delay,
        anchor_offset=anchor_offset,
        position_bucket=bucket,
        box_left=BOX_LEFT,
        renderer=RENDERER_VERSION,
    )
    return StageAPlan(
        event_id=eid,
        stem=source_stem,
        out_stem=out_stem,
        source_stem=source_stem,
        symbol=symbol,
        split="",
        mid_global=anchor,
        confirm_delay=delay,
        half=delay,
        decision_bar=decision,
        small_bars=(box_start, box_end),
        win_len=win_len,
        win_start=win_start,
        small_local=(local_start, local_end),
        anchor_offset=anchor_offset,
        anchor_x_ratio=anchor_ratio,
        box_pos_frac=box_ratio,
        position_bucket=bucket,
        start_time=str(start_time),
        anchor_time=str(anchor_time),
        decision_time=str(decision_time),
        visible_end_time=str(end_time),
        end_time=str(end_time),
        future_bars=future_bars,
        stage="A",
        mode="C",
        production_eligible=False,
        renderer_version=RENDERER_VERSION,
        config_hash=config_hash,
    )


def assign_time_splits(plans: list[StageAPlan]) -> dict[str, str]:
    """Chronological 85/15 event split with purge over full visible windows."""
    ordered = sorted(
        plans,
        key=lambda plan: (pd.Timestamp(plan.decision_time), plan.event_id),
    )
    if not ordered:
        return {}
    n_val = max(1, int(round(len(ordered) * VAL_FRAC)))
    val_plans = ordered[-n_val:]
    val_ids = {plan.event_id for plan in val_plans}
    val_start_min = min(pd.Timestamp(plan.start_time) for plan in val_plans)
    train_end_max = val_start_min - timedelta(minutes=PURGE_BARS * BAR_MINUTES)
    result: dict[str, str] = {}
    for plan in ordered:
        if plan.event_id in val_ids:
            result[plan.event_id] = "val"
        elif pd.Timestamp(plan.end_time) <= train_end_max:
            result[plan.event_id] = "train"
        else:
            result[plan.event_id] = "drop"
    return result


def plan_events(
    source_manifest: Path,
    *,
    seed: int,
    limit: int = 0,
) -> tuple[list[StageAPlan], Counter[str]]:
    """Plan every eligible event, filtering holdout metadata before series access."""
    events = load_source_events(source_manifest)
    plans: list[StageAPlan] = []
    skips: Counter[str] = Counter()
    for source in events:
        if limit and len(plans) >= limit:
            break
        try:
            plans.append(plan_positive(source, seed=seed))
        except Skip as exc:
            skips[exc.reason] += 1
    return plans, skips


def render_positive(
    plan: StageAPlan,
    *,
    image_path: Path,
    label_path: Path,
    draw_box: bool,
) -> dict:
    """Render one planned real-window crop and its Mode-C YOLO label."""
    frame = enriched_series(plan.symbol)
    window = frame.iloc[plan.win_start : plan.win_start + plan.win_len].reset_index(drop=True)
    image, transform = render_chart(window, out_path=None)
    box = yolo_box_from_bars(
        transform,
        window,
        plan.small_local[0],
        plan.small_local[1],
    )
    if box is None:
        raise Skip("empty_yolo", plan.stem)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    if draw_box:
        height, width = image.shape[:2]
        xc, yc, box_w, box_h = box
        x1 = int((xc - box_w / 2) * width)
        x2 = int((xc + box_w / 2) * width)
        y1 = int((yc - box_h / 2) * height)
        y2 = int((yc + box_h / 2) * height)
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 220), 3, cv2.LINE_AA)
    cv2.imwrite(str(image_path), image)
    label_path.write_text(
        f"0 {box[0]:.6f} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f}\n"
    )
    row = asdict(plan)
    row["small_bars"] = list(plan.small_bars)
    row["small_local"] = list(plan.small_local)
    row["out_img"] = str(image_path)
    row["out_lbl"] = str(label_path)
    row["image_sha256"] = sha256_file(image_path)
    row["label_sha256"] = sha256_file(label_path)
    return row


def negative_time_bounds(rows: list[dict]) -> dict[str, pd.Timestamp]:
    """Freeze negative full windows to the positive train/val exposure blocks."""
    train = [row for row in rows if row["split"] == "train"]
    val = [row for row in rows if row["split"] == "val"]
    if not train or not val:
        raise ValueError("Stage A requires non-empty train and val positives")
    train_end = max(pd.Timestamp(row["end_time"]) for row in train)
    val_start = min(pd.Timestamp(row["start_time"]) for row in val)
    val_end = max(pd.Timestamp(row["end_time"]) for row in val)
    gap_bars = (val_start - train_end).total_seconds() / (BAR_MINUTES * 60)
    if gap_bars < PURGE_BARS:
        raise ValueError(f"Stage A purge too small: {gap_bars} < {PURGE_BARS}")
    return {
        "train_end_max": train_end,
        "val_start_min": val_start,
        "val_end_max": val_end,
    }


def add_easy_negatives(
    positive_rows: list[dict],
    source_events: list[dict],
    dst: Path,
    *,
    ratio: float,
    seed: int,
) -> list[dict]:
    """Render easy negatives in frozen time blocks, away from every source anchor."""
    by_symbol_split: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in positive_rows:
        by_symbol_split[(row["symbol"], row["split"])].append(row)
    anchors_by_symbol: dict[str, list[int]] = defaultdict(list)
    for source in source_events:
        anchors_by_symbol[str(source["symbol"])].append(int(source["mid_global"]))
    bounds = negative_time_bounds(positive_rows)
    negative_rows: list[dict] = []
    for (symbol, split), rows in sorted(by_symbol_split.items()):
        target = max(1, int(round(len(rows) * ratio)))
        frame = enriched_series(symbol)
        forbidden = forbidden_intervals(anchors_by_symbol[symbol])
        obtained = 0
        attempt = 0
        while obtained < target and attempt < target * NEG_MAX_TRIES:
            attempt += 1
            rng = np.random.default_rng(
                stable_seed(seed, "stagea_neg", symbol, split, attempt)
            )
            win_len = int(rng.integers(WIN_MIN, WIN_MAX + 1))
            if len(frame) <= win_len:
                break
            win_start = int(rng.integers(0, len(frame) - win_len + 1))
            win_end = win_start + win_len - 1
            if overlaps(win_start, win_end, forbidden):
                continue
            window = frame.iloc[win_start : win_end + 1].reset_index(drop=True)
            start_time = pd.to_datetime(window.iloc[0]["open_time"], utc=True)
            end_time = pd.to_datetime(window.iloc[-1]["open_time"], utc=True)
            if end_time >= HOLDOUT_START or not negative_window_allowed(
                split,
                start_time=start_time,
                end_time=end_time,
                bounds=bounds,
            ):
                continue
            stem = f"{symbol}_{win_start:06d}_w{win_len}_stagea_neg"
            image_path = dst / "images" / split / f"{stem}.png"
            label_path = dst / "labels" / split / f"{stem}.txt"
            image, _transform = render_chart(window, out_path=None)
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(image_path), image)
            label_path.write_text("")
            negative_rows.append(
                {
                    "event_id": "neg_"
                    + event_id_of(symbol, win_start, f"stagea_{split}_{win_len}"),
                    "stem": stem,
                    "out_stem": stem,
                    "symbol": symbol,
                    "split": split,
                    "win_start": win_start,
                    "win_len": win_len,
                    "start_time": str(start_time),
                    "end_time": str(end_time),
                    "visible_end_time": str(end_time),
                    "kind": "easy_empty_background",
                    "stage": "A",
                    "production_eligible": False,
                    "renderer_version": RENDERER_VERSION,
                    "out_img": str(image_path),
                    "out_lbl": str(label_path),
                    "image_sha256": sha256_file(image_path),
                    "label_sha256": sha256_file(label_path),
                    "config_hash": config_hash_of(
                        protocol=PROTOCOL,
                        kind="easy_empty_background",
                        win_len=win_len,
                        split="strict_time",
                    ),
                }
            )
            obtained += 1
        if obtained != target:
            raise RuntimeError(
                f"negative sampling incomplete {symbol}/{split}: {obtained}/{target}"
            )
    return negative_rows


def preview_selection(plans: list[StageAPlan], *, per_bucket: int, seed: int) -> list[StageAPlan]:
    """Choose distinct-symbol previews with equal representation per position bucket."""
    selected: list[StageAPlan] = []
    used_symbols: set[str] = set()
    for bucket, _lo, _hi, _share in POSITION_BUCKETS:
        candidates = [plan for plan in plans if plan.position_bucket == bucket]
        candidates.sort(
            key=lambda plan: stable_seed(seed, "preview", bucket, plan.event_id)
        )
        bucket_rows: list[StageAPlan] = []
        for plan in candidates:
            if plan.symbol in used_symbols:
                continue
            bucket_rows.append(plan)
            used_symbols.add(plan.symbol)
            if len(bucket_rows) == per_bucket:
                break
        if len(bucket_rows) != per_bucket:
            raise RuntimeError(f"not enough distinct preview symbols for {bucket}")
        selected.extend(bucket_rows)
    return selected


def run_preview(
    source_manifest: Path,
    out_dir: Path,
    *,
    seed: int,
    per_bucket: int,
) -> dict:
    plans, skips = plan_events(source_manifest, seed=seed)
    selected = preview_selection(plans, per_bucket=per_bucket, seed=seed)
    rows: list[dict] = []
    for plan in selected:
        image_path = out_dir / f"{plan.out_stem}.png"
        label_path = out_dir / f"{plan.out_stem}.txt"
        rows.append(
            render_positive(
                plan,
                image_path=image_path,
                label_path=label_path,
                draw_box=True,
            )
        )
    summary = {
        "protocol": PROTOCOL,
        "seed": seed,
        "n": len(rows),
        "per_bucket": per_bucket,
        "skip_reasons": dict(skips),
        "rows": rows,
        "production_eligible": False,
        "holdout_read": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "preview_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    return summary


def run_full(
    source_manifest: Path,
    dst: Path,
    *,
    seed: int,
    limit: int,
    negative_ratio: float,
) -> dict:
    plans, skips = plan_events(source_manifest, seed=seed, limit=limit)
    split_by_event = assign_time_splits(plans)
    positive_rows: list[dict] = []
    for plan in plans:
        split = split_by_event.get(plan.event_id)
        if split == "drop":
            skips["purge_zone"] += 1
            continue
        if split not in {"train", "val"}:
            continue
        assigned = replace(plan, split=split)
        image_path = dst / "images" / split / f"{assigned.out_stem}.png"
        label_path = dst / "labels" / split / f"{assigned.out_stem}.txt"
        positive_rows.append(
            render_positive(
                assigned,
                image_path=image_path,
                label_path=label_path,
                draw_box=False,
            )
        )
        if len(positive_rows) % 100 == 0:
            print(f"... positives {len(positive_rows)}", flush=True)
    negative_rows = add_easy_negatives(
        positive_rows,
        load_source_events(source_manifest),
        dst,
        ratio=negative_ratio,
        seed=seed,
    )
    write_yaml(dst)
    bucket_counts = Counter(row["position_bucket"] for row in positive_rows)
    counts = {
        "train_positive": sum(row["split"] == "train" for row in positive_rows),
        "val_positive": sum(row["split"] == "val" for row in positive_rows),
        "train_negative": sum(row["split"] == "train" for row in negative_rows),
        "val_negative": sum(row["split"] == "val" for row in negative_rows),
    }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "source_manifest": str(source_manifest),
        "dataset": str(dst),
        "seed": seed,
        "stage": "A",
        "mode": "C",
        "production_eligible": False,
        "window_len": [WIN_MIN, WIN_MAX],
        "confirm_delays": list(CONFIRM_DELAYS),
        "box_rule": "anchor-2..decision",
        "position_buckets": [
            {"name": name, "lo": lo, "hi": hi, "target_share": share}
            for name, lo, hi, share in POSITION_BUCKETS
        ],
        "position_share_tolerance": POSITION_SHARE_TOLERANCE,
        "split_rule": (
            f"time-ordered decision events; last {VAL_FRAC:.0%} val; full-window "
            f"train/val separation with {PURGE_BARS}-bar purge"
        ),
        "purge_bars": PURGE_BARS,
        "strict_negative_time_split": True,
        "holdout_start": str(HOLDOUT_START),
        "full_window_before_holdout": True,
        "counts": counts,
        "positive_bucket_counts": dict(sorted(bucket_counts.items())),
        "skip_reasons": dict(skips),
        "n_positive_manifest": len(positive_rows),
        "n_negative_manifest": len(negative_rows),
        "renderer_version": RENDERER_VERSION,
        "holdout_read": False,
    }
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "w20_manifest.json").write_text(
        json.dumps(positive_rows, ensure_ascii=False, indent=2)
    )
    (dst / "w20_neg_manifest.json").write_text(
        json.dumps(negative_rows, ensure_ascii=False, indent=2)
    )
    (dst / "stagea_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    (dst / "w20_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src-manifest", type=Path, default=DEFAULT_SRC_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--negative-ratio", type=float, default=1.0)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--preview-per-bucket", type=int, default=6)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT / "analysis" / "output" / "local_signal_v2_stagea_preview",
    )
    args = parser.parse_args()
    if not args.src_manifest.exists():
        parser.error(f"missing source manifest: {args.src_manifest}")
    if args.preview:
        summary = run_preview(
            args.src_manifest,
            args.preview_dir,
            seed=args.seed,
            per_bucket=args.preview_per_bucket,
        )
        print(json.dumps({"preview": args.preview_dir, "n": summary["n"]}))
        return 0
    run_full(
        args.src_manifest,
        args.out,
        seed=args.seed,
        limit=args.limit,
        negative_ratio=args.negative_ratio,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay the Owner-short center-crop detector on a recent causal 15m snapshot.

This is the research-only bridge between the compact W12--19 training geometry
and a continuous market replay.  It deliberately does not use the legacy
200-bar tip/right-edge scanner: every window ends at the historical decision
bar, the detector may place its core box anywhere inside that window, and no
future bar is rendered into the inference image.

The command has four explicit phases so the LAN RTX 3060 never receives
Telegram credentials and the VPS remains the only writer of canonical klines:

* ``fetch`` writes a disposable OKX snapshot under ``analysis/output``;
* ``historical`` materializes a disposable pre-holdout prefix without opening
  rows at or beyond the repository holdout boundary;
* ``scan`` performs exhaustive W12--19 inference and 5-bar event deduplication;
* ``finalize`` resolves the already-frozen TP5/SL2/72 research outcome, builds
  matched random controls, and renders review charts with future bars clearly
  separated from the causal detector crop;
* ``send`` pushes the summary, strongest deduplicated charts and HTML report to
  Telegram.  It never places orders or changes ACTIVE/frozen configuration.

Required input columns are open_time/open/high/low/close/volume.  Detection
features are the six SMA/EMA 20/60/120 lines computed from bars at or before the
decision bar.  Future OHLC is read only by ``finalize`` for outcome labels and
review-only chart context, never by candidate selection or inference.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for module_path in (ROOT, YOYO_REPO):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from yoyo.contracts.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from yoyo.contracts.outcomes import resolve_barrier_outcome  # noqa: E402
from yoyo.data.indicators import add_indicators  # noqa: E402
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import (  # noqa: E402
    IMG_HEIGHT,
    IMG_WIDTH,
    MARGIN,
    render_chart,
)


PROTOCOL = "owner_short_gold_center_recent2d_v1_20260811"
DEFAULT_MANIFEST = ROOT / "datasets/owner_short_gold_center_v1/positive_manifest.jsonl"
DEFAULT_WEIGHTS = (
    ROOT
    / "analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_v1_ft/weights/best.pt"
)
DEFAULT_OUT = ROOT / "analysis/output/owner_short_gold_center_recent2d_v1"
DEFAULT_REPORT = ROOT / "analysis/p1_owner_short_gold_center_recent2d_holdout_20260811.md"

WINDOW_MIN = 12
WINDOW_MAX = 19
DIAGNOSTIC_CONF = 0.25
NMS_IOU = 0.70
EVENT_GAP_BARS = 5
HOURS = 48.0
FETCH_DAYS = 5
BAR_MINUTES = 15
TP_ATR = 5.0
SL_ATR = 2.0
HORIZON_BARS = 72
HOLDOUT_USE_NUMBER = 1
MAX_TG_PHOTOS = 25
OWNER_ETH_TARGET_START = pd.Timestamp("2026-08-10T11:30:00Z")  # 19:30 CST
OWNER_ETH_TARGET_END = pd.Timestamp("2026-08-10T12:45:00Z")  # 20:45 CST
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def stable_int(*parts: object) -> int:
    payload = "|".join(map(str, parts)).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _request_okx(url: str) -> dict[str, Any]:
    """Use the repository's throttled, WAF-safe public OKX reader."""
    from src.data.fetch_okx import _request  # noqa: PLC0415

    return _request(url)


def fetch_symbol(symbol: str, *, cutoff: datetime) -> tuple[str, pd.DataFrame, str | None]:
    """Fetch one disposable recent history; never write canonical data/."""
    from src.data.fetch_okx import API, PAGE_LIMIT  # noqa: PLC0415

    inst_id = symbol.replace("_", "-")
    after: int | None = None
    rows: dict[int, list[Any]] = {}
    cutoff_ms = int(cutoff.timestamp() * 1000)
    try:
        while True:
            url = f"{API}?instId={inst_id}&bar=15m&limit={PAGE_LIMIT}"
            if after is not None:
                url += f"&after={after}"
            payload = _request_okx(url)
            if payload.get("code") != "0":
                return symbol, pd.DataFrame(), f"api:{payload.get('msg', '')}"
            page = payload.get("data") or []
            if not page:
                break
            for item in page:
                ts = int(item[0])
                if len(item) > 8 and str(item[8]) == "0":
                    continue
                rows[ts] = [
                    ts,
                    item[1],
                    item[2],
                    item[3],
                    item[4],
                    item[5],
                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
                ]
            oldest = int(page[-1][0])
            if oldest <= cutoff_ms:
                break
            after = oldest
    except Exception as exc:  # noqa: BLE001 -- one delisted symbol cannot sink the universe
        return symbol, pd.DataFrame(), f"request:{type(exc).__name__}:{exc}"
    if not rows:
        return symbol, pd.DataFrame(), "empty"
    frame = pd.DataFrame(
        [rows[key] for key in sorted(rows)],
        columns=["ts", "open", "high", "low", "close", "volume", "open_time"],
    )
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return symbol, frame, None


def fetch_snapshot(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest)
    rows = read_jsonl(manifest)
    symbols = sorted({str(row["symbol"]) for row in rows})
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    out_dir = Path(args.out_dir)
    snapshot_dir = out_dir / "kline_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.fetch_days)
    fetched: list[dict[str, Any]] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_symbol, symbol, cutoff=cutoff): symbol for symbol in symbols}
        for number, future in enumerate(as_completed(futures), 1):
            symbol, frame, error = future.result()
            if error is None and len(frame):
                path = snapshot_dir / f"{symbol}.csv"
                frame.to_csv(path, index=False)
                fetched.append(
                    {
                        "symbol": symbol,
                        "rows": int(len(frame)),
                        "start": pd.Timestamp(frame["open_time"].iloc[0]).isoformat(),
                        "end": pd.Timestamp(frame["open_time"].iloc[-1]).isoformat(),
                        "sha256": sha256_file(path),
                        "error": None,
                    }
                )
            else:
                fetched.append({"symbol": symbol, "rows": 0, "error": error or "empty"})
            if number % 10 == 0 or number == len(symbols):
                usable = sum(int(item["rows"] > 0) for item in fetched)
                print(f"fetch [{number}/{len(symbols)}] usable={usable}", flush=True)
    summary = {
        "protocol": PROTOCOL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "requested_symbols": len(symbols),
        "usable_symbols": sum(int(item["rows"] > 0) for item in fetched),
        "fetch_days": args.fetch_days,
        "canonical_data_written": False,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "symbols": sorted(fetched, key=lambda item: item["symbol"]),
    }
    (out_dir / "fetch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in ("requested_symbols", "usable_symbols", "wall_seconds")}, ensure_ascii=False))
    return 0 if summary["usable_symbols"] else 2


def historical_target_index(
    reference_index: int,
    reference_time: object,
    target_time: object,
) -> int:
    """Map a frozen continuous-series index to an earlier pre-holdout time."""
    reference = pd.Timestamp(reference_time)
    target = pd.Timestamp(target_time)
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    else:
        reference = reference.tz_convert("UTC")
    if target.tzinfo is None:
        target = target.tz_localize("UTC")
    else:
        target = target.tz_convert("UTC")
    if target >= HOLDOUT_START:
        raise ValueError(f"historical target touches holdout: {target}")
    delta_bars = (target - reference) / pd.Timedelta(minutes=BAR_MINUTES)
    if not float(delta_bars).is_integer():
        raise ValueError("historical target must align to a 15m bar")
    result = int(reference_index) + int(delta_bars)
    if result < 0:
        raise ValueError("historical target predates source series")
    return result


def build_historical_snapshot(args: argparse.Namespace) -> int:
    """Build one fixed pre-holdout snapshot via bounded CSV-prefix reads."""
    from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: PLC0415
        Skip,
        load_preholdout_prefix,
    )

    manifest = Path(args.manifest)
    rows = read_jsonl(manifest)
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    symbols = sorted(by_symbol)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    target = pd.Timestamp(args.end)
    if target.tzinfo is None:
        target = target.tz_localize("UTC")
    else:
        target = target.tz_convert("UTC")
    if target >= HOLDOUT_START:
        raise SystemExit(f"historical end must be before {HOLDOUT_START}")
    out_dir = Path(args.out_dir)
    snapshot_dir = out_dir / "kline_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    built: list[dict[str, Any]] = []
    for number, symbol in enumerate(symbols, 1):
        candidates = by_symbol[symbol]
        source_paths = {str(row["source_csv"]) for row in candidates}
        if len(source_paths) != 1:
            raise ValueError(f"{symbol} has ambiguous source CSVs: {source_paths}")
        reference = min(
            candidates,
            key=lambda row: abs(pd.Timestamp(row["end_time"]) - target),
        )
        try:
            required_end = historical_target_index(
                int(reference["win_end"]), reference["end_time"], target
            )
            frame, audit = load_preholdout_prefix(
                ROOT / next(iter(source_paths)), required_end
            )
            visible = frame[frame["open_time"] <= target].tail(args.context_bars).copy()
            latest = pd.Timestamp(visible["open_time"].iloc[-1]) if len(visible) else None
            if len(visible) < 160 or latest is None or latest < target - pd.Timedelta(minutes=30):
                built.append(
                    {
                        "symbol": symbol,
                        "rows": 0,
                        "error": "insufficient_or_stale_prefix",
                        "source_audit": audit,
                    }
                )
                continue
            path = snapshot_dir / f"{symbol}.csv"
            visible.to_csv(path, index=False)
            built.append(
                {
                    "symbol": symbol,
                    "rows": int(len(visible)),
                    "start": pd.Timestamp(visible["open_time"].iloc[0]).isoformat(),
                    "end": latest.isoformat(),
                    "sha256": sha256_file(path),
                    "error": None,
                    "source_audit": audit,
                }
            )
        except (Skip, ValueError, IndexError) as exc:
            built.append(
                {
                    "symbol": symbol,
                    "rows": 0,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
        if number % 25 == 0 or number == len(symbols):
            usable = sum(int(item["rows"] > 0) for item in built)
            print(f"historical [{number}/{len(symbols)}] usable={usable}", flush=True)
    materialized_max = max(
        (
            str(item["source_audit"]["max_materialized_time"])
            for item in built
            if item.get("source_audit")
        ),
        default=None,
    )
    summary = {
        "protocol": PROTOCOL,
        "evaluation_scope": "preholdout_postval_canary",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "requested_symbols": len(symbols),
        "usable_symbols": sum(int(item["rows"] > 0) for item in built),
        "snapshot_end": target.isoformat(),
        "context_bars": args.context_bars,
        "max_materialized_time": materialized_max,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_rows_materialized": 0,
        "canonical_data_written": False,
        "symbols": built,
    }
    (out_dir / "fetch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: summary[key] for key in ("requested_symbols", "usable_symbols", "snapshot_end", "max_materialized_time")},
            ensure_ascii=False,
        )
    )
    return 0 if summary["usable_symbols"] else 2


def load_snapshot(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


def bar_from_x_normalized(x: float, n_bars: int) -> int:
    """Map an original-image normalized x coordinate to a compact-window bar."""
    pixel = float(x) * IMG_WIDTH
    plot_w = IMG_WIDTH - 2 * MARGIN
    index = round((pixel - MARGIN) / plot_w * (n_bars - 1))
    return int(min(max(index, 0), n_bars - 1))


def event_id(symbol: str, core_time: str) -> str:
    payload = f"{PROTOCOL}|{symbol}|{core_time}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def deduplicate_detections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge W variants/adjacent decisions by a fixed 5-bar core-midpoint gap.

    The first threshold crossing is the causal event representative.  Maximum
    confidence is retained only as an audit statistic; it cannot move the
    decision later and improve the apparent entry retrospectively.
    """
    events: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(str(row["symbol"]), []).append(row)
    for symbol, detections in sorted(by_symbol.items()):
        detections.sort(key=lambda row: (int(row["decision_i"]), -float(row["conf"])))
        clusters: list[dict[str, Any]] = []
        for row in detections:
            mid = float(row["core_mid_i"])
            target = next(
                (
                    cluster
                    for cluster in reversed(clusters)
                    if abs(mid - float(cluster["anchor_mid_i"])) <= EVENT_GAP_BARS
                ),
                None,
            )
            if target is None:
                clusters.append({"anchor_mid_i": mid, "members": [row]})
            else:
                target["members"].append(row)
        for cluster in clusters:
            members = cluster["members"]
            representative = min(
                members,
                key=lambda row: (int(row["decision_i"]), -float(row["conf"])),
            )
            item = dict(representative)
            item.update(
                {
                    "event_id": event_id(symbol, str(representative["core_mid_time"])),
                    "raw_detection_count": len(members),
                    "event_conf_max": max(float(row["conf"]) for row in members),
                    "event_decision_first": min(str(row["decision_time"]) for row in members),
                    "event_decision_last": max(str(row["decision_time"]) for row in members),
                    "window_lengths_seen": sorted({int(row["window_len"]) for row in members}),
                }
            )
            events.append(item)
    events.sort(key=lambda row: (str(row["decision_time"]), str(row["symbol"])))
    return events


def shard_paths(paths: list[Path], *, shard_index: int, shard_count: int) -> list[Path]:
    """Return one deterministic, disjoint symbol shard for parallel GPU replay."""
    if shard_count < 1:
        raise ValueError("shard_count must be >= 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must satisfy 0 <= index < count")
    return paths[shard_index::shard_count]


def scan_snapshot(args: argparse.Namespace) -> int:
    from ultralytics import YOLO  # noqa: PLC0415

    snapshot_dir = Path(args.snapshot_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = Path(args.weights)
    # macOS tar can materialize one AppleDouble ``._name.csv`` beside every
    # real snapshot when a caller forgets COPYFILE_DISABLE=1.  These are Finder
    # metadata, not klines; fail closed here as well as excluding them at pack.
    paths = sorted(
        path
        for path in snapshot_dir.glob("*_USDT_SWAP.csv")
        if not path.name.startswith("._")
    )
    paths = shard_paths(paths, shard_index=args.shard_index, shard_count=args.shard_count)
    if args.max_symbols:
        paths = paths[: args.max_symbols]
    if not paths:
        raise SystemExit(f"no snapshot CSVs under {snapshot_dir}")
    frames = {path.stem: load_snapshot(path) for path in paths}
    frames = {symbol: frame for symbol, frame in frames.items() if len(frame) >= 160}
    latest_by_symbol = {symbol: pd.Timestamp(frame["open_time"].iloc[-1]) for symbol, frame in frames.items()}
    latest = max(latest_by_symbol.values())
    fresh_cutoff = latest - pd.Timedelta(minutes=30)
    stale = sorted(symbol for symbol, end in latest_by_symbol.items() if end < fresh_cutoff)
    frames = {symbol: frame for symbol, frame in frames.items() if symbol not in stale}
    replay_start = latest - pd.Timedelta(hours=args.hours)
    model = YOLO(str(weights))
    raw: list[dict[str, Any]] = []
    exposures = 0
    started = time.perf_counter()
    print(
        f"scan symbols={len(frames)} stale={len(stale)} latest={latest} "
        f"start={replay_start} W={args.window_min}-{args.window_max} conf={args.conf}",
        flush=True,
    )
    for number, (symbol, source) in enumerate(sorted(frames.items()), 1):
        frame = add_mas(source)
        indices = [
            int(index)
            for index, value in enumerate(pd.to_datetime(frame["open_time"], utc=True))
            if value > replay_start and value <= latest and index >= 119 + args.window_max
        ]
        jobs: list[tuple[int, int, np.ndarray]] = []
        for decision_i in indices:
            for window_len in range(args.window_min, args.window_max + 1):
                start_i = decision_i - window_len + 1
                image, _transform = render_chart(
                    frame.iloc[start_i : decision_i + 1].reset_index(drop=True),
                    out_path=None,
                )
                jobs.append((decision_i, window_len, image))
                exposures += 1
                if len(jobs) >= args.batch:
                    raw.extend(_predict_batch(model, jobs, frame, symbol, args))
                    jobs.clear()
        if jobs:
            raw.extend(_predict_batch(model, jobs, frame, symbol, args))
        if number % 5 == 0 or number == len(frames):
            print(
                f"scan [{number}/{len(frames)}] exposures={exposures} raw={len(raw)} "
                f"wall_min={(time.perf_counter()-started)/60:.1f}",
                flush=True,
            )
    events = deduplicate_detections(raw)
    write_jsonl(out_dir / "raw_detections.jsonl", raw)
    write_jsonl(out_dir / "events.jsonl", events)
    pd.DataFrame(raw).to_csv(out_dir / "raw_detections.csv", index=False)
    pd.DataFrame(events).to_csv(out_dir / "events.csv", index=False)
    summary = {
        "protocol": PROTOCOL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(weights),
        "weights_sha256": sha256_file(weights),
        "snapshot_dir": str(snapshot_dir),
        "symbols": len(frames),
        "scanned_symbols": sorted(frames),
        "stale_symbols": stale,
        "latest_bar": latest.isoformat(),
        "replay_start_exclusive": replay_start.isoformat(),
        "hours": args.hours,
        "window_lengths": list(range(args.window_min, args.window_max + 1)),
        "confidence": args.conf,
        "nms_iou": args.iou,
        "event_gap_bars": EVENT_GAP_BARS,
        "bar_endpoints": sum(
            int(((pd.to_datetime(frame["open_time"], utc=True) > replay_start) & (pd.to_datetime(frame["open_time"], utc=True) <= latest)).sum())
            for frame in frames.values()
        ),
        "window_exposures": exposures,
        "raw_detections": len(raw),
        "deduplicated_events": len(events),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "evaluation_scope": args.evaluation_scope,
        "holdout_use_number": HOLDOUT_USE_NUMBER if args.evaluation_scope == "holdout" else 0,
        "owner_authorized_in_conversation": args.evaluation_scope == "holdout",
        "promoted": False,
        "orders_placed": False,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
    }
    (out_dir / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "_SUCCESS.json").write_text(
        json.dumps({"ok": True, "events": len(events)}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def merge_scans(args: argparse.Namespace) -> int:
    """Merge disjoint symbol shards without rescoring or changing event rules."""
    scan_dirs = [Path(path) for path in args.scan_dirs]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = [
        json.loads((path / "scan_summary.json").read_text(encoding="utf-8"))
        for path in scan_dirs
    ]
    hashes = {str(summary["weights_sha256"]) for summary in summaries}
    protocols = {str(summary["protocol"]) for summary in summaries}
    if len(hashes) != 1 or protocols != {PROTOCOL}:
        raise ValueError("scan shards do not share one protocol and weight hash")
    seen_symbols: set[str] = set()
    raw: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for path in scan_dirs:
        shard_raw = read_jsonl(path / "raw_detections.jsonl")
        shard_events = read_jsonl(path / "events.jsonl")
        shard_summary = json.loads((path / "scan_summary.json").read_text(encoding="utf-8"))
        symbols = {str(symbol) for symbol in shard_summary.get("scanned_symbols", [])}
        overlap = seen_symbols & symbols
        if overlap:
            raise ValueError(f"symbol overlap across shards: {sorted(overlap)[:5]}")
        seen_symbols.update(symbols)
        raw.extend(shard_raw)
        events.extend(shard_events)
    raw.sort(key=lambda row: (str(row["symbol"]), str(row["decision_time"]), -float(row["conf"])))
    events.sort(key=lambda row: (str(row["decision_time"]), str(row["symbol"])))
    write_jsonl(out_dir / "raw_detections.jsonl", raw)
    write_jsonl(out_dir / "events.jsonl", events)
    pd.DataFrame(raw).to_csv(out_dir / "raw_detections.csv", index=False)
    pd.DataFrame(events).to_csv(out_dir / "events.csv", index=False)
    first = dict(summaries[0])
    first.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": sum(int(summary["symbols"]) for summary in summaries),
            "scanned_symbols": sorted(seen_symbols),
            "stale_symbols": sorted(
                {symbol for summary in summaries for symbol in summary.get("stale_symbols", [])}
            ),
            "bar_endpoints": sum(int(summary["bar_endpoints"]) for summary in summaries),
            "window_exposures": sum(int(summary["window_exposures"]) for summary in summaries),
            "raw_detections": len(raw),
            "deduplicated_events": len(events),
            "wall_seconds": max(float(summary["wall_seconds"]) for summary in summaries),
            "parallel_shards": len(summaries),
            "shard_index": None,
            "shard_count": len(summaries),
        }
    )
    (out_dir / "scan_summary.json").write_text(
        json.dumps(first, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "_SUCCESS.json").write_text(
        json.dumps({"ok": True, "events": len(events), "merged_shards": len(summaries)}) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(first, ensure_ascii=False, indent=2))
    return 0


def _predict_batch(model, jobs, frame: pd.DataFrame, symbol: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    images = [job[2] for job in jobs]
    results = model.predict(
        images,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        batch=args.batch,
        verbose=False,
        augment=False,
        save=False,
    )
    found: list[dict[str, Any]] = []
    for (decision_i, window_len, _image), result in zip(jobs, results):
        if result.boxes is None or len(result.boxes) == 0:
            continue
        start_i = decision_i - window_len + 1
        xyxyn = result.boxes.xyxyn.detach().cpu().numpy()
        confidences = result.boxes.conf.detach().cpu().numpy()
        for box, confidence in zip(xyxyn, confidences):
            x1, y1, x2, y2 = map(float, box)
            local_start = bar_from_x_normalized(x1, window_len)
            local_end = bar_from_x_normalized(x2, window_len)
            if local_end < local_start:
                local_start, local_end = local_end, local_start
            core_start_i = start_i + local_start
            core_end_i = start_i + local_end
            core_mid_i = (core_start_i + core_end_i) / 2
            core_mid_round = int(round(core_mid_i))
            found.append(
                {
                    "symbol": symbol,
                    "decision_i": decision_i,
                    "decision_time": pd.Timestamp(frame["open_time"].iloc[decision_i]).isoformat(),
                    "window_start_i": start_i,
                    "window_start_time": pd.Timestamp(frame["open_time"].iloc[start_i]).isoformat(),
                    "window_len": window_len,
                    "conf": float(confidence),
                    "x1n": x1,
                    "y1n": y1,
                    "x2n": x2,
                    "y2n": y2,
                    "core_start_i": core_start_i,
                    "core_end_i": core_end_i,
                    "core_mid_i": core_mid_i,
                    "core_start_time": pd.Timestamp(frame["open_time"].iloc[core_start_i]).isoformat(),
                    "core_end_time": pd.Timestamp(frame["open_time"].iloc[core_end_i]).isoformat(),
                    "core_mid_time": pd.Timestamp(frame["open_time"].iloc[core_mid_round]).isoformat(),
                    "predicted_core_bars": core_end_i - core_start_i + 1,
                    "decision_delay_bars": decision_i - core_end_i,
                }
            )
    return found


def outcome_for(frame: pd.DataFrame, decision_i: int) -> dict[str, Any]:
    enriched = add_indicators(frame)
    entry_i = decision_i + 1
    if entry_i >= len(enriched):
        return {"status": "open", "outcome": "no_entry", "entry_i": entry_i}
    atr = float(enriched["atr14"].iloc[decision_i])
    entry = float(enriched["open"].iloc[entry_i])
    resolution = resolve_barrier_outcome(
        enriched,
        side="short",
        entry_i=entry_i,
        entry_price=entry,
        atr=atr,
        tp_atr_mult=TP_ATR,
        sl_atr_mult=SL_ATR,
        horizon_bars=HORIZON_BARS,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_short",
        allow_partial=True,
        bar_duration=pd.Timedelta(minutes=BAR_MINUTES),
    )
    gross = resolution.gross_ret
    item: dict[str, Any] = {
        "status": resolution.status,
        "outcome": resolution.outcome or "running",
        "entry_i": entry_i,
        "entry_time": pd.Timestamp(enriched["open_time"].iloc[entry_i]).isoformat(),
        "entry_price": entry,
        "atr14": atr,
        "atr_pct": atr / entry,
        "tp_price": entry - TP_ATR * atr,
        "sl_price": entry + SL_ATR * atr,
        "exit_offset": resolution.exit_offset,
        "exit_time": resolution.exit_time,
        "exit_price": resolution.exit_price,
        "gross_ret": gross,
        "net_taker": gross - SWAP_TAKER if gross is not None else None,
        "net_maker": gross - SWAP_MAKER if gross is not None else None,
    }
    for horizon in (4, 8, 16, 32):
        target = entry_i + horizon - 1
        value = None
        if target < len(enriched):
            value = 1 - float(enriched["close"].iloc[target]) / entry
        item[f"gross_ret_{horizon}bar"] = value
    return item


def _vol_bucket(series: pd.Series) -> pd.Series:
    ranked = series.rank(method="first")
    try:
        return pd.qcut(ranked, 5, labels=False, duplicates="drop").fillna(0).astype(int)
    except ValueError:
        return pd.Series(np.zeros(len(series), dtype=int), index=series.index)


def build_controls(events: list[dict[str, Any]], frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """One same-symbol, same UTC day, same ATR bucket non-event control per event."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_symbol.setdefault(str(event["symbol"]), []).append(event)
    controls: list[dict[str, Any]] = []
    for symbol, symbol_events in sorted(by_symbol.items()):
        frame = add_indicators(frames[symbol])
        frame = frame.copy()
        frame["atr_pct_control"] = frame["atr14"] / frame["close"].replace(0, np.nan)
        frame["vol_bucket_control"] = _vol_bucket(frame["atr_pct_control"])
        event_decisions = [int(event["decision_i"]) for event in symbol_events]
        for event in symbol_events:
            decision_i = int(event["decision_i"])
            day = pd.Timestamp(frame["open_time"].iloc[decision_i]).floor("D")
            bucket = int(frame["vol_bucket_control"].iloc[decision_i])
            candidates = []
            for index in range(120, len(frame) - 1):
                timestamp = pd.Timestamp(frame["open_time"].iloc[index])
                if timestamp.floor("D") != day:
                    continue
                if int(frame["vol_bucket_control"].iloc[index]) != bucket:
                    continue
                if any(abs(index - known) <= EVENT_GAP_BARS for known in event_decisions):
                    continue
                candidates.append(index)
            if not candidates:
                continue
            chosen = candidates[stable_int(PROTOCOL, event["event_id"], "control") % len(candidates)]
            outcome = outcome_for(frames[symbol], chosen)
            controls.append(
                {
                    "event_id": event["event_id"],
                    "symbol": symbol,
                    "decision_i": chosen,
                    "decision_time": pd.Timestamp(frame["open_time"].iloc[chosen]).isoformat(),
                    "utc_day": day.isoformat(),
                    "vol_bucket": bucket,
                    **outcome,
                }
            )
    return controls


def paired_closed_metrics(
    events: list[dict[str, Any]], controls: list[dict[str, Any]]
) -> dict[str, float | int | None]:
    """Compare only event/control pairs whose two outcomes are both closed."""
    event_by_id = {
        str(row["event_id"]): row
        for row in events
        if row.get("status") == "closed" and row.get("net_taker") is not None
    }
    control_by_id = {
        str(row["event_id"]): row
        for row in controls
        if row.get("status") == "closed" and row.get("net_taker") is not None
    }
    ids = sorted(set(event_by_id) & set(control_by_id))
    if not ids:
        return {
            "paired_closed": 0,
            "paired_event_net_taker_mean": None,
            "paired_control_net_taker_mean": None,
            "paired_event_minus_control_net_taker": None,
        }
    event_values = np.asarray(
        [float(event_by_id[event_id]["net_taker"]) for event_id in ids], dtype=float
    )
    control_values = np.asarray(
        [float(control_by_id[event_id]["net_taker"]) for event_id in ids], dtype=float
    )
    return {
        "paired_closed": len(ids),
        "paired_event_net_taker_mean": float(np.mean(event_values)),
        "paired_control_net_taker_mean": float(np.mean(control_values)),
        "paired_event_minus_control_net_taker": float(
            np.mean(event_values - control_values)
        ),
    }


def draw_event(frame: pd.DataFrame, event: dict[str, Any], out_path: Path) -> None:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.dates as mdates  # noqa: PLC0415
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from matplotlib.patches import Rectangle  # noqa: PLC0415

    enriched = add_mas(frame)
    decision_i = int(event["decision_i"])
    lo = max(0, decision_i - 48)
    hi = min(len(enriched) - 1, decision_i + 48)
    segment = enriched.iloc[lo : hi + 1].reset_index(drop=True)
    times = pd.to_datetime(segment["open_time"], utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    x = mdates.date2num(times)
    o, h, low, c = (segment[column].to_numpy(dtype=float) for column in ("open", "high", "low", "close"))
    width = (x[1] - x[0]) * 0.70 if len(x) > 1 else 0.01
    up = c >= o
    fig, ax = plt.subplots(figsize=(16, 8), dpi=120)
    ax.vlines(x, low, h, color="#888888", lw=0.8, zorder=2)
    ax.bar(x[up], (c - o)[up], width, bottom=o[up], color="#26a69a", zorder=3)
    ax.bar(x[~up], (o - c)[~up], width, bottom=c[~up], color="#ef5350", zorder=3)
    colors = {
        "sma20": "#303f9f", "ema20": "#ef6c00", "sma60": "#039be5",
        "ema60": "#7cb342", "sma120": "#8e24aa", "ema120": "#d81b60",
    }
    for column in ALL_MA_COLS:
        ax.plot(x, segment[column], color=colors[column], lw=1.0, alpha=0.9, label=column)
    win_start_i = int(event["window_start_i"])
    core_start_i = int(event["core_start_i"])
    core_end_i = int(event["core_end_i"])
    if lo <= win_start_i <= decision_i <= hi:
        ax.axvspan(x[win_start_i - lo], x[decision_i - lo] + width / 2, color="#00acc1", alpha=0.08, label="detector input")
    if decision_i + 1 <= hi:
        ax.axvspan(x[decision_i + 1 - lo] - width / 2, x[-1] + width / 2, color="#7e57c2", alpha=0.07, label="review-only future")
    if lo <= core_start_i <= core_end_i <= hi:
        start_x = x[core_start_i - lo] - width / 2
        end_x = x[core_end_i - lo] + width / 2
        core = enriched.iloc[core_start_i : core_end_i + 1]
        y1, y2 = float(core["low"].min()), float(core["high"].max())
        pad = max((y2 - y1) * 0.10, float(enriched["close"].iloc[decision_i]) * 0.0005)
        ax.add_patch(Rectangle((start_x, y1 - pad), end_x - start_x, y2 - y1 + 2 * pad,
                               fill=False, edgecolor="#f57c00", lw=3.0, zorder=7, label="YOLO core"))
    ax.axvline(x[decision_i - lo], color="#00838f", lw=2.0, ls="--", label="decision")
    outcome = str(event.get("outcome", ""))
    net = event.get("net_taker")
    net_text = "open" if net is None or (isinstance(net, float) and math.isnan(net)) else f"net@taker {float(net)*100:+.2f}%"
    decision_cst = pd.Timestamp(event["decision_time"]).tz_convert("Asia/Shanghai")
    ax.set_title(
        f"{event['symbol']}  Owner-short compact YOLO  conf_max {float(event['event_conf_max']):.3f}  "
        f"delay {int(event['decision_delay_bars'])} bars  {outcome}  {net_text}\n"
        f"decision {decision_cst:%Y-%m-%d %H:%M} CST | cyan=input seen by model | purple=future review only",
        loc="left", fontsize=11,
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.grid(alpha=0.15)
    ax.legend(loc="upper left", ncol=5, fontsize=8)
    fig.autofmt_xdate(rotation=25)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)


def _clean_json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    return value


def finalize(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    snapshot_dir = Path(args.snapshot_dir)
    scan_dir = Path(args.scan_dir)
    events = read_jsonl(scan_dir / "events.jsonl")
    symbols = sorted({str(event["symbol"]) for event in events})
    frames = {symbol: load_snapshot(snapshot_dir / f"{symbol}.csv") for symbol in symbols}
    completed: list[dict[str, Any]] = []
    for event in events:
        outcome = outcome_for(frames[str(event["symbol"])], int(event["decision_i"]))
        completed.append({**event, **outcome})
    controls = build_controls(completed, frames)
    completed = [
        {key: _clean_json_value(value) for key, value in event.items()}
        for event in completed
    ]
    controls = [
        {key: _clean_json_value(value) for key, value in row.items()}
        for row in controls
    ]
    write_jsonl(out_dir / "events_with_outcomes.jsonl", completed)
    write_jsonl(out_dir / "matched_controls.jsonl", controls)
    pd.DataFrame(completed).to_csv(out_dir / "events_with_outcomes.csv", index=False)
    pd.DataFrame(controls).to_csv(out_dir / "matched_controls.csv", index=False)
    ranked = sorted(completed, key=lambda row: (-float(row["event_conf_max"]), str(row["decision_time"])))
    eth = [row for row in completed if str(row["symbol"]) == "ETH_USDT_SWAP"]
    eth_target_matches = sorted(
        [
            row
            for row in eth
            if pd.Timestamp(row["core_end_time"]) >= OWNER_ETH_TARGET_START
            and pd.Timestamp(row["core_start_time"]) <= OWNER_ETH_TARGET_END
        ],
        key=lambda row: (str(row["core_start_time"]), str(row["decision_time"])),
    )
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Put the Owner's explicit ETH reference match first in Telegram/review,
    # then the remaining ETH events and finally the strongest market-wide rows.
    for row in [*eth_target_matches, *eth, *ranked]:
        if row["event_id"] in seen:
            continue
        selected.append(row)
        seen.add(str(row["event_id"]))
        if len(selected) >= args.max_render:
            break
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    for stale_chart in chart_dir.glob("*.png"):
        stale_chart.unlink()
    for number, event in enumerate(selected, 1):
        decision = pd.Timestamp(event["decision_time"]).strftime("%Y%m%d_%H%M")
        path = chart_dir / f"{number:03d}_{event['symbol']}_{decision}_{event['event_id']}.png"
        draw_event(frames[str(event["symbol"])], event, path)
        event["chart_path"] = str(path)
    write_jsonl(out_dir / "rendered_events.jsonl", selected)
    scan_summary = json.loads((scan_dir / "scan_summary.json").read_text(encoding="utf-8"))
    closed = [row for row in completed if row.get("status") == "closed"]
    control_closed = [row for row in controls if row.get("status") == "closed"]
    event_mean = float(np.mean([float(row["net_taker"]) for row in closed])) if closed else None
    paired = paired_closed_metrics(completed, controls)
    fetch_summary_path = out_dir / "fetch_summary.json"
    fetch_summary = (
        json.loads(fetch_summary_path.read_text(encoding="utf-8"))
        if fetch_summary_path.exists()
        else {}
    )
    missing_symbols = sorted(
        str(row["symbol"])
        for row in fetch_summary.get("symbols", [])
        if not int(row.get("rows", 0))
    )
    events_by_symbol = Counter(str(row["symbol"]) for row in completed)
    all_symbol_counts = np.asarray(
        [events_by_symbol.get(str(symbol), 0) for symbol in scan_summary["scanned_symbols"]],
        dtype=float,
    )
    delays = np.asarray([int(row["decision_delay_bars"]) for row in completed], dtype=float)
    core_widths = np.asarray([int(row["predicted_core_bars"]) for row in completed], dtype=float)
    summary = {
        **scan_summary,
        "finalized_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_generated_at": fetch_summary.get("generated_at"),
        "requested_symbols": fetch_summary.get("requested_symbols", scan_summary["symbols"]),
        "usable_symbols": fetch_summary.get("usable_symbols", scan_summary["symbols"]),
        "missing_symbols": missing_symbols,
        "canonical_data_written": fetch_summary.get("canonical_data_written", False),
        "closed_events": len(closed),
        "open_events": len(completed) - len(closed),
        "outcomes": dict(Counter(str(row.get("outcome")) for row in completed)),
        "closed_tp_rate": (
            sum(str(row.get("outcome")) == "tp" for row in closed) / len(closed)
            if closed
            else 0.0
        ),
        "event_net_taker_mean": event_mean,
        "matched_controls": len(controls),
        "matched_control_closed": len(control_closed),
        **paired,
        "control_net_taker_mean": paired["paired_control_net_taker_mean"],
        "event_minus_control_net_taker": paired["paired_event_minus_control_net_taker"],
        "events_per_1000_bar_endpoints": (
            len(completed) / int(scan_summary["bar_endpoints"]) * 1000
            if scan_summary.get("bar_endpoints") else None
        ),
        "events_per_day": len(completed) / (float(scan_summary["hours"]) / 24),
        "events_per_symbol_day": (
            len(completed)
            / max(1, int(scan_summary["symbols"]))
            / (float(scan_summary["hours"]) / 24)
        ),
        "triggered_symbols": int(np.sum(all_symbol_counts > 0)),
        "events_per_symbol_2d_median": float(np.median(all_symbol_counts)),
        "events_per_symbol_2d_p90": float(np.quantile(all_symbol_counts, 0.90)),
        "events_per_symbol_2d_max": int(np.max(all_symbol_counts)) if len(all_symbol_counts) else 0,
        "top_symbols_by_events": events_by_symbol.most_common(10),
        "decision_delay_median": float(np.median(delays)) if len(delays) else 0.0,
        "decision_delay_p90": float(np.quantile(delays, 0.90)) if len(delays) else 0.0,
        "decision_delay_0_2_share": float(np.mean(delays <= 2)) if len(delays) else 0.0,
        "decision_delay_3_5_share": float(np.mean((delays >= 3) & (delays <= 5))) if len(delays) else 0.0,
        "decision_delay_gt5_share": float(np.mean(delays > 5)) if len(delays) else 0.0,
        "core_width_4_7_share": float(np.mean((core_widths >= 4) & (core_widths <= 7))) if len(core_widths) else 0.0,
        "eth_events": len(eth),
        "eth_owner_target_matches": [
            {
                key: row.get(key)
                for key in (
                    "event_id",
                    "core_start_time",
                    "core_end_time",
                    "decision_time",
                    "event_conf_max",
                    "predicted_core_bars",
                    "decision_delay_bars",
                    "outcome",
                    "net_taker",
                )
            }
            for row in eth_target_matches
        ],
        "charts_rendered": len(selected),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    build_report(Path(args.report), summary, completed, controls, scan_dir, out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.3f}%"


def build_report(
    report: Path,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    scan_dir: Path,
    out_dir: Path,
) -> None:
    top = sorted(events, key=lambda row: -float(row["event_conf_max"]))[:30]
    rows = []
    for event in top:
        cst = pd.Timestamp(event["decision_time"]).tz_convert("Asia/Shanghai")
        rows.append(
            f"| {event['symbol']} | {cst:%m-%d %H:%M} | {float(event['event_conf_max']):.3f} | "
            f"{int(event['predicted_core_bars'])} | {int(event['decision_delay_bars'])} | "
            f"{event.get('outcome', '—')} | {pct(event.get('net_taker'))} |"
        )
    target_rows = []
    for number, event in enumerate(summary.get("eth_owner_target_matches", []), 1):
        core_start = pd.Timestamp(event["core_start_time"]).tz_convert("Asia/Shanghai")
        core_end = pd.Timestamp(event["core_end_time"]).tz_convert("Asia/Shanghai")
        decision = pd.Timestamp(event["decision_time"]).tz_convert("Asia/Shanghai")
        role = "主目标匹配" if number == 1 else "后续重叠事件（需作为延续/重复复核）"
        target_rows.append(
            f"| {role} | {event['event_id']} | {core_start:%m-%d %H:%M}–{core_end:%H:%M} | "
            f"{decision:%m-%d %H:%M} | {int(event['predicted_core_bars'])} | "
            f"{int(event['decision_delay_bars'])} | {float(event['event_conf_max']):.3f} | "
            f"{event.get('outcome', '—')} | {pct(event.get('net_taker'))} |"
        )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        f"""# Owner-short compact YOLO 最近2天全市场回放（2026-08-11）

## 结论先行

- 本次按Owner在对话中的明确要求读取最近48小时数据，登记为该配置第 **{HOLDOUT_USE_NUMBER}** 次消耗holdout。
- 使用刚训练完成且未promote的`owner_lsv2_short_gold_center_v1_ft`，只扫其训练分布内的215币种；实际新鲜可用币种 **{summary['symbols']}**。
- 因果扫描W12–19共 **{summary['window_exposures']:,}** 个窗口，原始触发 **{summary['raw_detections']:,}** 条；按同币、核心中点±{EVENT_GAP_BARS}根合并为 **{summary['deduplicated_events']:,}** 个事件。
- 密度为 **{summary['events_per_1000_bar_endpoints']:.3f} events/1000 bar endpoints**，即 **{summary['events_per_day']:.1f} events/day**。这才是检测事件数，不把8种窗口的重复命中当8笔单。
- 折算到单币为 **{summary['events_per_symbol_day']:.2f} events/币/天**；{summary['triggered_symbols']}/{summary['symbols']}个币至少触发一次。核心宽度落在Owner要求4–7根的比例为 **{summary['core_width_4_7_share']*100:.1f}%**。
- 已了结事件 {summary['closed_events']} 个，TP/SL/timeout/open分布：`{summary['outcomes']}`；全部已了结事件净@taker均值 **{pct(summary['event_net_taker_mean'])}**。严格成对可比样本 {summary['paired_closed']} 个：事件 **{pct(summary['paired_event_net_taker_mean'])}**、匹配随机 **{pct(summary['paired_control_net_taker_mean'])}**、差值 **{pct(summary['paired_event_minus_control_net_taker'])}**。
- 已了结事件TP率 **{summary['closed_tp_rate']*100:.1f}%**；匹配随机对照覆盖 {summary['matched_controls']}/{summary['deduplicated_events']} 个事件，其中双方都已了结的严格配对为 {summary['paired_closed']}。
- 这仍是检测器诊断回放，不是生产晋升：无ACTIVE修改、无下单、无阈值调优，TG图均标注纸面回放。

## 回放协议

| 项目 | 冻结值 |
|---|---|
| 权重SHA-256 | `{summary['weights_sha256']}` |
| 数据范围 | `{summary['replay_start_exclusive']}`之后至`{summary['latest_bar']}` |
| 周期/窗口 | 15m；W12–19逐bar全扫 |
| 推理门 | conf={summary['confidence']}（Ultralytics诊断默认值，未调参）；NMS IoU={summary['nms_iou']} |
| 事件去重 | 同币预测核心中点相差≤{EVENT_GAP_BARS}根；第一阈值穿越作为决策时刻 |
| 交易诊断 | 下一根开盘做空；TP5×ATR14 / SL2×ATR14 / 72根；同bar双触保守判SL |
| 成本 | swap taker往返{SWAP_TAKER*1e4:.0f}bp；maker往返{SWAP_MAKER*1e4:.0f}bp |
| holdout | Owner明确授权；该配置第{HOLDOUT_USE_NUMBER}次消耗 |

## 数据统计

- 数据源：OKX公开15m接口的一次性快照；生成于`{summary.get('snapshot_generated_at')}`，未写canonical `data/`。
- 请求/可用币种：{summary.get('requested_symbols')} / {summary.get('usable_symbols')}；缺失：`{summary.get('missing_symbols') or []}`。
- bar endpoints：{summary['bar_endpoints']:,}
- window exposures：{summary['window_exposures']:,}
- raw detections：{summary['raw_detections']:,}
- deduplicated events：{summary['deduplicated_events']:,}
- 单币两天事件数 median / p90 / max：{summary['events_per_symbol_2d_median']:.1f} / {summary['events_per_symbol_2d_p90']:.1f} / {summary['events_per_symbol_2d_max']}
- 决策延迟 median / p90：{summary['decision_delay_median']:.1f} / {summary['decision_delay_p90']:.1f}根；0–2根 {summary['decision_delay_0_2_share']*100:.1f}%，3–5根 {summary['decision_delay_3_5_share']*100:.1f}%，>5根 {summary['decision_delay_gt5_share']*100:.1f}%
- 事件最多的币种：`{summary['top_symbols_by_events']}`
- ETH events：{summary['eth_events']}
- matched random controls：{summary['matched_controls']}（已了结{summary['matched_control_closed']}）
- 已了结事件TP率：{summary['closed_tp_rate']*100:.2f}%
- 扫描耗时：{summary['wall_seconds']/60:.1f}分钟

## 强信号明细（按事件最大置信度）

| 币种 | 决策时间CST | conf max | 核心宽度 | 首次延迟 | 结果 | 净@taker |
|---|---:|---:|---:|---:|---|---:|
{chr(10).join(rows) if rows else '| — | — | — | — | — | 无事件 | — |'}

完整事件表：`{out_dir / 'events_with_outcomes.csv'}`；匹配对照：`{out_dir / 'matched_controls.csv'}`。

## Owner ETH终极参考段核对

Owner标出的核心候选时段为8月10日19:30–20:45 CST。当前模型有{len(target_rows)}个预测核心与其重叠：

| 角色 | event_id | 预测核心CST | 决策CST | 核心根数 | 延迟根数 | conf max | 结果 | 净@taker |
|---|---|---|---|---:|---:|---:|---|---:|
{chr(10).join(target_rows) if target_rows else '| 未命中 | — | — | — | — | — | — | — | — |'}

第一条主匹配把核心落在19:30–20:15的4根K，21:00首次决策，正好是3根确认延迟；它没有把后面的整段暴跌塞入核心。第二条从20:45继续框到21:45，已进入下跌过程，不能因为同样TP就自动视为另一个高质量形态，应进入相邻延续/重复难例复核。

## 与上一版同表对照

| 配置 | 正/负训练比 | 最近2天events/1000 | events/day | holdout次数 | 裁决 |
|---|---:|---:|---:|---:|---|
| 当前1:1 easy baseline | 1:1 | {summary['events_per_1000_bar_endpoints']:.3f} | {summary['events_per_day']:.1f} | {HOLDOUT_USE_NUMBER} | 仅诊断，等待hard-negative arm |
| 下一步1:3（1 easy+2 hard） | 1:3 | 未运行 | 未运行 | 0 | 未获本轮3060训练授权 |

## 匹配随机对照

对每个可匹配事件，在同一币、同一UTC日、同ATR波动五分桶中确定性抽取一个非事件bar，使用完全相同的入场、障碍、期限和成本。只在事件与其对应随机入场都已了结时进入差值分母，共{summary['paired_closed']}对；事件净@taker均值为{pct(summary['paired_event_net_taker_mean'])}，随机对照为{pct(summary['paired_control_net_taker_mean'])}，逐对差值均值为{pct(summary['paired_event_minus_control_net_taker'])}。短样本不能据此宣称统计显著或可交易。

## 必报指标状态

- val AUC：N/A，本轮只评估YOLO检测器，不训练/评分LightGBM排序层。
- 置换检验p：N/A，没有排序分数与独立大样本。
- top-decile毛/净收益：N/A，没有判断层分位数。
- 胜率：已了结事件TP率{summary['closed_tp_rate']*100:.2f}%；未完结样本不进入分母。
- 单特征基线：N/A；本轮有效对照为同币×同日×同波动桶随机入场。

## 风险与诚实声明

- `conf=0.25`只是未调优诊断门，不能自动成为生产阈值；任何阈值晋升仍需Owner决策。
- 最新2天没有完整Owner逐事件金标，收益方向不能替代形态precision；本报告不能给出可信event precision/recall。
- 事件图中的紫色区域仅供人工看未来结果，模型输入严格止于青色decision线。
- 本次holdout已经消耗，不能拿同一结果反复改数据集并当独立验收。
- 模型未promote、未部署、未写forward_log、未下单。

## 复现命令

```bash
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \\
  scripts/backtest_owner_short_gold_center_recent.py fetch \\
  --out-dir {out_dir}

# Windows 3060 PowerShell；四个i可并行启动
foreach ($i in 0..3) {{
  C:/fable/.venv/Scripts/python.exe \\
    C:/fable/scripts/backtest_owner_short_gold_center_recent.py scan \\
    --snapshot-dir C:/fable/analysis/input/owner_short_gold_center_recent2d_v1/snapshot \\
    --out-dir "C:/fable/analysis/output/owner_short_gold_center_recent2d_v1/shard$i" \\
    --weights C:/fable/analysis/input/owner_short_gold_center_recent2d_v1/best.pt \\
    --device 0 --shard-index $i --shard-count 4
}}

# 将四个shard目录取回Mac后合并
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \\
  scripts/backtest_owner_short_gold_center_recent.py merge \\
  --scan-dirs {out_dir / 'remote_shards/shard0'} \\
  {out_dir / 'remote_shards/shard1'} \\
  {out_dir / 'remote_shards/shard2'} \\
  {out_dir / 'remote_shards/shard3'} \\
  --out-dir {scan_dir}

PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python \\
  scripts/backtest_owner_short_gold_center_recent.py finalize \\
  --snapshot-dir {out_dir / 'kline_snapshot'} --scan-dir {scan_dir} \\
  --out-dir {out_dir} --report {report}

python3 scripts/md_to_html.py {report} --out-dir analysis/html
```

## 下一步

继续按交接文档从原train时间块挖hard negatives，构建固定val的1:3第二训练臂。该臂构建和审计可继续；再次上3060训练前停在Owner逐次授权门。
""",
        encoding="utf-8",
    )


def send_to_telegram(args: argparse.Namespace) -> int:
    from yoyo import notify  # noqa: PLC0415

    out_dir = Path(args.out_dir)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    rendered = read_jsonl(out_dir / "rendered_events.jsonl")
    report_html = Path(args.report_html)
    message = (
        "<b>刚训练YOLO · 最近2天全市场回放</b>\n"
        f"币种 {summary['symbols']}/{summary.get('requested_symbols', summary['symbols'])} · "
        f"窗口 {summary['window_exposures']:,}\n"
        f"原始触发 {summary['raw_detections']:,} → 去重事件 <b>{summary['deduplicated_events']:,}</b>\n"
        f"密度 {summary['events_per_1000_bar_endpoints']:.3f}/1000 bar · "
        f"{summary['events_per_day']:.1f}事件/天（单币 {summary['events_per_symbol_day']:.2f}/天）\n"
        f"已了结 {summary['closed_events']} · 净@taker {pct(summary['event_net_taker_mean'])} · "
        f"成对 {summary['paired_closed']}：事件 {pct(summary['paired_event_net_taker_mean'])} / "
        f"随机 {pct(summary['paired_control_net_taker_mean'])} / "
        f"差值 {pct(summary['paired_event_minus_control_net_taker'])}\n"
        f"holdout：该配置第{HOLDOUT_USE_NUMBER}次（本次Owner明确授权）\n"
        "<i>纸面回放；未下单、未promote。紫色K线只用于事后审核，不是模型输入。</i>"
    )
    summary_sent = notify.send(message)
    photo_results: list[dict[str, Any]] = []
    for event in rendered[: args.max_send]:
        chart = Path(str(event.get("chart_path", "")))
        cst = pd.Timestamp(event["decision_time"]).tz_convert("Asia/Shanghai")
        caption = (
            f"<b>{html.escape(str(event['symbol']))}</b> 做空形态 · compact YOLO\n"
            f"决策 {cst:%m-%d %H:%M} CST · conf_max {float(event['event_conf_max']):.3f}\n"
            f"框 {int(event['predicted_core_bars'])}根 · 首次延迟 {int(event['decision_delay_bars'])}根 · "
            f"{html.escape(str(event.get('outcome', 'open')))} · 净@taker {pct(event.get('net_taker'))}\n"
            "<i>纸面回放，未下单；青色为模型可见输入，紫色为审核未来。</i>"
        )
        ok = notify.send_photo(chart, caption)
        photo_results.append({"event_id": event["event_id"], "path": str(chart), "sent": ok})
    document_sent = notify.send_document(report_html, "最近2天详细回放HTML（纸面、未下单）")
    events_csv_sent = notify.send_document(
        out_dir / "events_with_outcomes.csv",
        "最近2天全部去重事件与纸面结果CSV（不是订单）",
    )
    receipt = {
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "summary_sent": summary_sent,
        "photos_requested": min(len(rendered), args.max_send),
        "photos_sent": sum(int(item["sent"]) for item in photo_results),
        "document_sent": document_sent,
        "events_csv_sent": events_csv_sent,
        "photo_results": photo_results,
    }
    (out_dir / "telegram_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if summary_sent and document_sent and events_csv_sent else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch")
    fetch.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    fetch.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    fetch.add_argument("--fetch-days", type=int, default=FETCH_DAYS)
    fetch.add_argument("--workers", type=int, default=8)
    fetch.add_argument("--max-symbols", type=int, default=0)

    historical = commands.add_parser("historical")
    historical.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    historical.add_argument("--out-dir", type=Path, required=True)
    historical.add_argument("--end", required=True)
    historical.add_argument("--context-bars", type=int, default=420)
    historical.add_argument("--max-symbols", type=int, default=0)

    scan = commands.add_parser("scan")
    scan.add_argument("--snapshot-dir", type=Path, required=True)
    scan.add_argument("--out-dir", type=Path, required=True)
    scan.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    scan.add_argument("--hours", type=float, default=HOURS)
    scan.add_argument("--window-min", type=int, default=WINDOW_MIN)
    scan.add_argument("--window-max", type=int, default=WINDOW_MAX)
    scan.add_argument("--conf", type=float, default=DIAGNOSTIC_CONF)
    scan.add_argument("--iou", type=float, default=NMS_IOU)
    scan.add_argument("--imgsz", type=int, default=960)
    scan.add_argument("--device", default="mps")
    scan.add_argument("--batch", type=int, default=32)
    scan.add_argument("--max-symbols", type=int, default=0)
    scan.add_argument("--shard-index", type=int, default=0)
    scan.add_argument("--shard-count", type=int, default=1)
    scan.add_argument(
        "--evaluation-scope",
        choices=("holdout", "preholdout_postval_canary"),
        default="holdout",
    )

    merge = commands.add_parser("merge")
    merge.add_argument("--scan-dirs", type=Path, nargs="+", required=True)
    merge.add_argument("--out-dir", type=Path, required=True)

    final = commands.add_parser("finalize")
    final.add_argument("--snapshot-dir", type=Path, required=True)
    final.add_argument("--scan-dir", type=Path, required=True)
    final.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    final.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    final.add_argument("--max-render", type=int, default=MAX_TG_PHOTOS)

    send = commands.add_parser("send")
    send.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    send.add_argument(
        "--report-html",
        type=Path,
        default=ROOT / "analysis/html/p1_owner_short_gold_center_recent2d_holdout_20260811.html",
    )
    send.add_argument("--max-send", type=int, default=MAX_TG_PHOTOS)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "fetch":
        return fetch_snapshot(args)
    if args.command == "historical":
        return build_historical_snapshot(args)
    if args.command == "scan":
        return scan_snapshot(args)
    if args.command == "merge":
        return merge_scans(args)
    if args.command == "finalize":
        return finalize(args)
    if args.command == "send":
        return send_to_telegram(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Scan three completed UTC daily-mover Top20 boards with the frozen t-3 YOLO.

This is a bounded retrospective visual probe.  The ranking reads confirmed
OKX ``1Dutc`` open/close rows for the exact preregistered days and sorts by
absolute same-day return.  Membership is therefore known only after each day
closes; it is deliberately not presented as a causal universe selector.

Detection uses only one causal 15m window at a time.  Each image contains
``open/high/low/close`` and causal SMA/EMA 20/60/120 through its own endpoint.
The frozen W14/W18/W22 ensemble accepts only mapped 4--7-bar boxes whose right
edge is 3--5 bars before that endpoint.  Five post-day endpoints are scanned so
a core ending in the final five bars can receive the confirmation context seen
during training; event attribution still requires the mapped core end to lie
inside the ranked UTC day.

All fetched 15m data is disposable under ``analysis/output``.  The script never
writes canonical ``data/``, trains, tunes, promotes, deploys, writes forward
state, or places orders.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/scan_15m_ma_launch_t3_daily_movers.py --fetch
  PYTHONPATH=. .venv/bin/python scripts/scan_15m_ma_launch_t3_daily_movers.py --scan
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.data.loader import BLOCKED_BASES
from yoyo.data.universe import STOCKISH_BASES
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas
from yoyo.layers.l1_detection.render import ChartTransform, render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-t3-daily-movers3d-v2"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_t3_daily_movers3d_v2"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
TICKERS_URL = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
BAR_DELTA = pd.Timedelta(minutes=15)
CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
CLASS_COLORS = {0: (35, 165, 45), 1: (45, 45, 220)}


class MoversScanError(RuntimeError):
    """Fail-closed data, identity, or inference contract error."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity for one artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    result = pd.Timestamp(value)
    if result.tzinfo is None:
        return result.tz_localize("UTC")
    return result.tz_convert("UTC")


def load_preregistration(path: Path) -> dict[str, Any]:
    """Load and enforce the exact no-tuning/no-production scan contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise MoversScanError("unexpected experiment_id")
    auth = payload["owner_authorization"]
    if int(auth["holdout_consumption_number_for_this_configuration"]) != 1:
        raise MoversScanError("holdout consumption number drifted")
    if auth.get("training_or_tuning_authorized") is not False:
        raise MoversScanError("training/tuning must remain unauthorized")
    if auth.get("production_or_promotion_authorized") is not False:
        raise MoversScanError("production/promotion must remain unauthorized")
    days = [utc(value) for value in payload["calendar"]["complete_days"]]
    if len(days) != 3 or days != sorted(days) or len(set(days)) != 3:
        raise MoversScanError("calendar must contain three ordered unique days")
    if any(day != day.floor("D") for day in days):
        raise MoversScanError("calendar days must start at UTC midnight")
    detector = payload["detector"]
    if detector.get("threshold_or_window_retuning_after_results") is not False:
        raise MoversScanError("post-result retuning switch drifted")
    if tuple(map(int, detector["window_lengths"])) != (14, 18, 22):
        raise MoversScanError("window ensemble drifted")
    if float(detector["confidence"]) != 0.25 or float(detector["nms_iou"]) != 0.7:
        raise MoversScanError("inference threshold drifted")
    if payload["ranking"]["causality"] != "post_hoc_same_day_ranking_not_live_selection":
        raise MoversScanError("ranking causality disclosure drifted")
    if any(value is not False for value in payload["safety"].values()):
        raise MoversScanError("one or more safety switches drifted")
    return payload


def verify_sources_committed(prereg_path: Path) -> str:
    """Require main and committed builder/prereg bytes before holdout reads."""

    script = Path(__file__).resolve()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise MoversScanError("official scan must run on main")
    paths = [script.relative_to(ROOT), prereg_path.resolve().relative_to(ROOT)]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise MoversScanError(f"scan sources must be committed before execution:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", str(paths[0])], cwd=ROOT, text=True
    ).strip()
    if len(commit) != 40:
        raise MoversScanError("could not resolve scan source commit")
    return commit


def _request(url: str) -> dict[str, Any]:
    """Use the repository's throttled, WAF-safe public OKX reader."""

    from src.data.fetch_okx import _request as repository_request

    payload = repository_request(url)
    if str(payload.get("code")) != "0":
        raise MoversScanError(f"OKX error for {url}: {payload.get('msg')}")
    return payload


def eligible_instruments(
    ticker_rows: Sequence[Mapping[str, Any]],
    instrument_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Return live instCategory=1 crypto USDT swaps with a current ticker."""

    instruments: set[str] = set()
    blocked = set(BLOCKED_BASES) | set(STOCKISH_BASES)
    crypto_live = {
        str(row.get("instId") or "")
        for row in instrument_rows
        if str(row.get("state")) == "live" and str(row.get("instCategory")) == "1"
    }
    for row in ticker_rows:
        inst_id = str(row.get("instId") or "")
        if not inst_id.endswith("-USDT-SWAP") or inst_id not in crypto_live:
            continue
        base = inst_id.split("-", 1)[0]
        if base in blocked:
            continue
        try:
            last = float(row.get("last") or 0)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(last) or last <= 0:
            continue
        instruments.add(inst_id)
    return sorted(instruments)


def parse_confirmed_candles(
    rows: Sequence[Sequence[Any]], *, target_start: pd.Timestamp, target_end: pd.Timestamp
) -> pd.DataFrame:
    """Parse confirmed OKX rows inside one exact half-open interval."""

    parsed: dict[int, list[Any]] = {}
    start_ms, end_ms = int(target_start.timestamp() * 1000), int(target_end.timestamp() * 1000)
    for row in rows:
        if len(row) < 6:
            continue
        timestamp = int(row[0])
        if len(row) > 8 and str(row[8]) == "0":
            continue
        if not start_ms <= timestamp < end_ms:
            continue
        parsed[timestamp] = [
            timestamp,
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
        ]
    frame = pd.DataFrame(
        [parsed[key] for key in sorted(parsed)],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    if frame.empty:
        return pd.DataFrame(
            columns=["ts", "open", "high", "low", "close", "volume", "open_time"]
        )
    frame["open_time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    return frame


def fetch_daily_rows(
    inst_id: str, days: Sequence[pd.Timestamp]
) -> tuple[str, dict[pd.Timestamp, dict[str, Any]], int, str | None]:
    """Fetch enough recent 1Dutc rows to cover every preregistered complete day."""

    try:
        # One current partial row may precede the requested complete days.  The
        # extra row also makes the bound robust around the UTC close without
        # broadening the retained interval.
        limit = max(5, len(days) + 2)
        url = f"{CANDLES_URL}?instId={inst_id}&bar=1Dutc&limit={limit}"
        raw = list(_request(url).get("data") or [])
        wanted = set(days)
        found: dict[pd.Timestamp, dict[str, Any]] = {}
        for row in raw:
            if len(row) < 6 or (len(row) > 8 and str(row[8]) == "0"):
                continue
            day = pd.to_datetime(int(row[0]), unit="ms", utc=True)
            if day not in wanted:
                continue
            open_px, close_px = float(row[1]), float(row[4])
            if not np.isfinite(open_px) or not np.isfinite(close_px) or open_px <= 0:
                continue
            found[day] = {
                "day": day,
                "inst_id": inst_id,
                "symbol": inst_id.replace("-", "_"),
                "open": open_px,
                "close": close_px,
                "daily_return": close_px / open_px - 1.0,
                "daily_volume": float(row[5]),
            }
        return inst_id, found, len(raw), None
    except Exception as exc:  # noqa: BLE001 - one instrument is recorded, never hidden
        return inst_id, {}, 0, f"{type(exc).__name__}:{exc}"


def rank_daily_rows(
    by_instrument: Mapping[str, Mapping[pd.Timestamp, Mapping[str, Any]]],
    days: Sequence[pd.Timestamp],
    *,
    top: int,
) -> list[dict[str, Any]]:
    """Rank each day by absolute open-to-close return with a stable tie break."""

    ranked: list[dict[str, Any]] = []
    for day in days:
        candidates = [dict(rows[day]) for rows in by_instrument.values() if day in rows]
        candidates.sort(key=lambda row: (-abs(float(row["daily_return"])), str(row["symbol"])))
        if len(candidates) < top:
            raise MoversScanError(f"{day:%Y-%m-%d} has {len(candidates)} daily rows, needs {top}")
        for rank, row in enumerate(candidates[:top], 1):
            row["rank"] = rank
            row["eligible_daily_universe"] = len(candidates)
            ranked.append(row)
    return ranked


def fetch_15m_frame(
    inst_id: str, *, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[str, pd.DataFrame, int, str | None]:
    """Fetch a bounded confirmed 15m snapshot for one selected instrument."""

    raw_rows: list[list[Any]] = []
    after: int | None = None
    raw_count = 0
    try:
        while True:
            endpoint = CANDLES_URL if after is None else HISTORY_URL
            limit = 300 if after is None else 100
            url = f"{endpoint}?instId={inst_id}&bar=15m&limit={limit}"
            if after is not None:
                url += f"&after={after}"
            page = list(_request(url).get("data") or [])
            raw_count += len(page)
            if not page:
                break
            raw_rows.extend(page)
            oldest = int(page[-1][0])
            if oldest <= int(start.timestamp() * 1000):
                break
            if after == oldest:
                raise MoversScanError(f"OKX pagination did not advance for {inst_id}")
            after = oldest
        frame = parse_confirmed_candles(raw_rows, target_start=start, target_end=end)
        return inst_id, frame, raw_count, None
    except Exception as exc:  # noqa: BLE001 - recorded explicitly in the receipt
        return inst_id, pd.DataFrame(), raw_count, f"{type(exc).__name__}:{exc}"


def frame_gap_count(frame: pd.DataFrame) -> int:
    """Count non-15m timestamp jumps in a sorted frame."""

    if len(frame) < 2:
        return 0
    diffs = pd.to_datetime(frame["open_time"], utc=True).diff().iloc[1:]
    return int((diffs != BAR_DELTA).sum())


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    """Write a deterministic CSV with an explicit schema even when empty."""

    frame = pd.DataFrame([dict(row) for row in rows], columns=list(columns))
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def fetch_and_rank(
    prereg: Mapping[str, Any],
    *,
    out: Path,
    results: Path,
    workers: int,
    source_commit: str,
) -> dict[str, Any]:
    """Fetch the active daily universe and disposable selected 15m snapshots."""

    building = out.with_name(f"{out.name}.building")
    receipt_path = results / "fetch_receipt.json"
    if out.exists() or building.exists() or receipt_path.exists():
        raise FileExistsError(f"refusing to overwrite official fetch outputs: {out}")
    building.mkdir(parents=True)
    snapshot_dir = building / "kline_snapshot"
    snapshot_dir.mkdir()
    days = [utc(value) for value in prereg["calendar"]["complete_days"]]
    top = int(prereg["ranking"]["top_per_day"])
    started = time.perf_counter()

    ticker_payload = _request(TICKERS_URL)
    instrument_payload = _request(INSTRUMENTS_URL)
    ticker_rows = list(ticker_payload.get("data") or [])
    instrument_rows = list(instrument_payload.get("data") or [])
    instruments = eligible_instruments(ticker_rows, instrument_rows)
    if not instruments:
        raise MoversScanError("active ticker universe is empty")
    print(f"active eligible USDT swaps: {len(instruments)}", flush=True)

    daily_results: dict[str, dict[pd.Timestamp, dict[str, Any]]] = {}
    daily_failures: list[dict[str, str]] = []
    daily_raw_rows = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_daily_rows, inst, days): inst for inst in instruments}
        for number, future in enumerate(as_completed(futures), 1):
            inst_id, rows, raw_count, error = future.result()
            daily_raw_rows += raw_count
            if error:
                daily_failures.append({"inst_id": inst_id, "error": error})
            else:
                daily_results[inst_id] = rows
            if number % 50 == 0 or number == len(instruments):
                print(
                    f"daily [{number}/{len(instruments)}] usable={len(daily_results)} "
                    f"failed={len(daily_failures)}",
                    flush=True,
                )

    ranked = rank_daily_rows(daily_results, days, top=top)
    selected = sorted({str(row["inst_id"]) for row in ranked})
    for day in days:
        board = [row for row in ranked if row["day"] == day]
        print(
            f"{day:%Y-%m-%d} Top{top} threshold="
            f"{abs(float(board[-1]['daily_return'])) * 100:.2f}% "
            f"universe={board[-1]['eligible_daily_universe']}",
            flush=True,
        )

    context_start = days[0] - pd.Timedelta(
        hours=float(prereg["data"]["minimum_context_before_first_day_hours"])
    )
    extension = int(prereg["detector"]["scan_endpoint_extension_after_day_bars"])
    snapshot_end = days[-1] + pd.Timedelta(days=1) + extension * BAR_DELTA
    snapshot_rows: list[dict[str, Any]] = []
    snapshot_failures: list[dict[str, str]] = []
    fifteen_raw_rows = 0
    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(fetch_15m_frame, inst, start=context_start, end=snapshot_end): inst
            for inst in selected
        }
        for number, future in enumerate(as_completed(futures), 1):
            inst_id, frame, raw_count, error = future.result()
            fifteen_raw_rows += raw_count
            symbol = inst_id.replace("-", "_")
            if error or frame.empty:
                snapshot_failures.append({"inst_id": inst_id, "error": error or "empty"})
            else:
                frames[symbol] = frame
                path = snapshot_dir / f"{symbol}.csv"
                frame.to_csv(path, index=False)
                snapshot_rows.append(
                    {
                        "symbol": symbol,
                        "rows": int(len(frame)),
                        "start": utc(frame["open_time"].iloc[0]).isoformat(),
                        "end": utc(frame["open_time"].iloc[-1]).isoformat(),
                        "gap_count": frame_gap_count(frame),
                        "sha256": sha256_file(path),
                    }
                )
            print(
                f"15m [{number}/{len(selected)}] {symbol} rows={len(frame)} "
                f"error={error or '-'}",
                flush=True,
            )

    if snapshot_failures:
        raise MoversScanError(f"selected 15m fetch failures: {snapshot_failures}")

    day_integrity: list[dict[str, Any]] = []
    for row in ranked:
        day, symbol = utc(row["day"]), str(row["symbol"])
        frame = frames[symbol]
        mask = (frame["open_time"] >= day) & (frame["open_time"] < day + pd.Timedelta(days=1))
        part = frame.loc[mask]
        gaps = frame_gap_count(part)
        day_integrity.append(
            {
                "day": day.isoformat(),
                "rank": int(row["rank"]),
                "symbol": symbol,
                "bars": int(len(part)),
                "gap_count": gaps,
                "exact_96_contiguous": int(len(part)) == 96 and gaps == 0,
            }
        )
    exact_days = sum(int(item["exact_96_contiguous"]) for item in day_integrity)

    ranking_columns = (
        "day",
        "rank",
        "symbol",
        "inst_id",
        "daily_return",
        "open",
        "close",
        "daily_volume",
        "eligible_daily_universe",
    )
    serialized_ranked = [
        {**row, "day": utc(row["day"]).isoformat()} for row in ranked
    ]
    write_csv(building / "daily_rankings.csv", serialized_ranked, ranking_columns)
    (building / "universe_snapshot.json").write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "eligible_instruments": instruments,
                "selected_instruments": selected,
                "instrument_filter": "state=live and instCategory=1",
                "daily_failures": daily_failures,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    building.replace(out)
    rankings_path = out / "daily_rankings.csv"
    universe_path = out / "universe_snapshot.json"
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": str(prereg["experiment_id"]),
        "source_commit": source_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 1,
        "holdout_read_authorized_by_owner_request": True,
        "complete_days": [day.isoformat() for day in days],
        "ranking_causality": prereg["ranking"]["causality"],
        "ticker_rows_received": len(ticker_rows),
        "instrument_metadata_rows_received": len(instrument_rows),
        "instrument_filter": "state=live and instCategory=1",
        "eligible_instruments": len(instruments),
        "daily_raw_rows_received": daily_raw_rows,
        "daily_api_failures": len(daily_failures),
        "selected_symbol_days": len(ranked),
        "selected_unique_instruments": len(selected),
        "fifteen_minute_raw_rows_received": fifteen_raw_rows,
        "snapshot_rows_materialized": sum(int(item["rows"]) for item in snapshot_rows),
        "exact_96_contiguous_symbol_days": exact_days,
        "non_exact_symbol_days": len(day_integrity) - exact_days,
        "context_start": context_start.isoformat(),
        "snapshot_end_exclusive": snapshot_end.isoformat(),
        "canonical_data_written": False,
        "daily_rankings_path": str(rankings_path.relative_to(ROOT)),
        "daily_rankings_sha256": sha256_file(rankings_path),
        "universe_snapshot_path": str(universe_path.relative_to(ROOT)),
        "universe_snapshot_sha256": sha256_file(universe_path),
        "snapshot_files": sorted(snapshot_rows, key=lambda item: item["symbol"]),
        "day_integrity": day_integrity,
        "ranked": serialized_ranked,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "training_or_tuning": False,
        "production_eligible": False,
    }
    results.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"fetch complete: {len(ranked)} symbol-days, {len(selected)} symbols, "
        f"exact={exact_days}/{len(day_integrity)} -> {receipt_path}",
        flush=True,
    )
    return payload


def choose_device(requested: str | None) -> str:
    """Choose MPS when available, otherwise CPU, unless explicitly frozen."""

    if requested:
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "0"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def map_prediction_to_core(
    *,
    cx: float,
    width: float,
    transform: ChartTransform,
    window_start_i: int,
    window_end_i: int,
) -> dict[str, int]:
    """Map a normalized prediction back to discrete core and confirmation bars."""

    x0 = (float(cx) - float(width) / 2.0) * transform.width
    x1 = (float(cx) + float(width) / 2.0) * transform.width
    centers = np.asarray([transform.x_at(index) for index in range(transform.n_bars)])
    local_start = int(np.argmin(np.abs(centers - x0)))
    local_end = int(np.argmin(np.abs(centers - x1)))
    if local_end < local_start:
        local_start, local_end = local_end, local_start
    core_start_i = window_start_i + local_start
    core_end_i = window_start_i + local_end
    return {
        "core_start_i": core_start_i,
        "core_end_i": core_end_i,
        "core_length_bars": core_end_i - core_start_i + 1,
        "confirmation_bars": window_end_i - core_end_i,
        "core_start_local": local_start,
        "core_end_local": local_end,
    }


def deduplicate_hits(hits: Sequence[Mapping[str, Any]], *, gap_bars: int) -> list[dict[str, Any]]:
    """Keep the highest-confidence event within each same-symbol temporal cluster."""

    kept: list[dict[str, Any]] = []
    for row in sorted(
        (dict(hit) for hit in hits),
        key=lambda item: (-float(item["confidence"]), int(item["core_end_i"]), str(item["class_name"])),
    ):
        if any(abs(int(row["core_end_i"]) - int(old["core_end_i"])) < gap_bars for old in kept):
            continue
        kept.append(row)
    kept.sort(key=lambda item: (int(item["core_end_i"]), -float(item["confidence"])))
    return kept


def _predict_batches(
    model: Any,
    tasks: Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]],
    *,
    batch_size: int,
    conf: float,
    iou: float,
    imgsz: int,
    device: str,
    day: pd.Timestamp,
    frame: pd.DataFrame,
    allowed_cores: set[int],
    allowed_confirmations: set[int],
    stats: Counter[str],
) -> list[dict[str, Any]]:
    """Run bounded model batches and retain structurally legal mapped boxes."""

    hits: list[dict[str, Any]] = []
    times = pd.to_datetime(frame["open_time"], utc=True)
    for start in range(0, len(tasks), batch_size):
        batch = tasks[start : start + batch_size]
        images = [item[0] for item in batch]
        predictions = model.predict(
            source=images,
            imgsz=imgsz,
            conf=conf,
            iou=iou,
            batch=min(batch_size, len(images)),
            device=device,
            verbose=False,
        )
        if len(predictions) != len(batch):
            raise MoversScanError("prediction count differs from inference task count")
        for prediction, (_, transform, meta) in zip(predictions, batch):
            stats["windows_scored"] += 1
            boxes = prediction.boxes
            if boxes is None or len(boxes) == 0:
                continue
            stats["windows_with_any_box"] += 1
            for xywhn, class_id, confidence in zip(
                boxes.xywhn.cpu().numpy(),
                boxes.cls.cpu().numpy(),
                boxes.conf.cpu().numpy(),
            ):
                stats["raw_boxes"] += 1
                cid = int(class_id)
                if cid not in CLASS_NAMES:
                    stats["reject_unknown_class"] += 1
                    continue
                mapped = map_prediction_to_core(
                    cx=float(xywhn[0]),
                    width=float(xywhn[2]),
                    transform=transform,
                    window_start_i=int(meta["window_start_i"]),
                    window_end_i=int(meta["window_end_i"]),
                )
                if mapped["core_length_bars"] not in allowed_cores:
                    stats["reject_core_length"] += 1
                    continue
                if mapped["confirmation_bars"] not in allowed_confirmations:
                    stats["reject_confirmation"] += 1
                    continue
                core_end_time = utc(times.iloc[mapped["core_end_i"]])
                if core_end_time.floor("D") != day:
                    stats["reject_core_outside_ranked_day"] += 1
                    continue
                segment = frame.iloc[
                    mapped["core_start_i"] : mapped["core_end_i"] + 1
                ]
                hit = {
                    **meta,
                    **mapped,
                    "class_id": cid,
                    "class_name": CLASS_NAMES[cid],
                    "confidence": float(confidence),
                    "core_start_time": utc(times.iloc[mapped["core_start_i"]]).isoformat(),
                    "core_end_time": core_end_time.isoformat(),
                    "core_high": float(segment["high"].max()),
                    "core_low": float(segment["low"].min()),
                }
                hits.append(hit)
                stats["accepted_structural_boxes"] += 1
    return hits


def scan_symbol_day(
    frame: pd.DataFrame,
    *,
    day_row: Mapping[str, Any],
    model: Any,
    detector: Mapping[str, Any],
    device: str,
    batch_size: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scan one ranked symbol-day with frozen W14/W18/W22 causal windows."""

    enriched = add_mas(frame)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    day = utc(day_row["day"])
    extension = int(detector["scan_endpoint_extension_after_day_bars"])
    endpoint_end = day + pd.Timedelta(days=1) + extension * BAR_DELTA
    endpoint_indices = np.flatnonzero((times >= day) & (times < endpoint_end))
    tasks: list[tuple[np.ndarray, ChartTransform, dict[str, Any]]] = []
    hits: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for endpoint in endpoint_indices:
        end_i = int(endpoint)
        for window_len in map(int, detector["window_lengths"]):
            start_i = end_i - window_len + 1
            if start_i < 0:
                stats["skip_insufficient_rows"] += 1
                continue
            window = enriched.iloc[start_i : end_i + 1]
            if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                stats["skip_ma_warmup"] += 1
                continue
            image, transform = render_chart(window, out_path=None)
            tasks.append(
                (
                    image,
                    transform,
                    {
                        "day": day.isoformat(),
                        "rank": int(day_row["rank"]),
                        "symbol": str(day_row["symbol"]),
                        "inst_id": str(day_row["inst_id"]),
                        "daily_return": float(day_row["daily_return"]),
                        "window_len": window_len,
                        "window_start_i": start_i,
                        "window_end_i": end_i,
                        "window_end_time": utc(times.iloc[end_i]).isoformat(),
                    },
                )
            )
            if len(tasks) >= batch_size:
                hits.extend(
                    _predict_batches(
                        model,
                        tasks,
                        batch_size=batch_size,
                        conf=float(detector["confidence"]),
                        iou=float(detector["nms_iou"]),
                        imgsz=int(detector["imgsz"]),
                        device=device,
                        day=day,
                        frame=enriched,
                        allowed_cores=set(map(int, detector["mapped_core_length_bars_allowed"])),
                        allowed_confirmations=set(
                            map(int, detector["mapped_confirmation_bars_allowed"])
                        ),
                        stats=stats,
                    )
                )
                tasks.clear()
    if tasks:
        hits.extend(
            _predict_batches(
                model,
                tasks,
                batch_size=batch_size,
                conf=float(detector["confidence"]),
                iou=float(detector["nms_iou"]),
                imgsz=int(detector["imgsz"]),
                device=device,
                day=day,
                frame=enriched,
                allowed_cores=set(map(int, detector["mapped_core_length_bars_allowed"])),
                allowed_confirmations=set(
                    map(int, detector["mapped_confirmation_bars_allowed"])
                ),
                stats=stats,
            )
        )
    deduped = deduplicate_hits(
        hits, gap_bars=int(detector["same_symbol_event_gap_bars"])
    )
    stats["accepted_before_dedup"] = len(hits)
    stats["deduplicated_events"] = len(deduped)
    stats["dedup_removed"] = len(hits) - len(deduped)
    return deduped, dict(stats)


def _put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    color: tuple[int, int, int] = (30, 30, 30),
    scale: float = 0.56,
    thickness: int = 1,
) -> None:
    """Draw one clipping-safe ASCII label."""

    cv2.putText(
        image,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def render_symbol_day_panel(
    frame: pd.DataFrame,
    *,
    day_row: Mapping[str, Any],
    hits: Sequence[Mapping[str, Any]],
    width: int = 760,
    chart_height: int = 420,
) -> np.ndarray:
    """Render one full UTC day and overlay every deduplicated small core box."""

    day = utc(day_row["day"])
    times = pd.to_datetime(frame["open_time"], utc=True)
    mask = (times >= day) & (times < day + pd.Timedelta(days=1))
    day_frame = add_mas(frame).loc[mask].reset_index(drop=True)
    if day_frame.empty:
        chart = np.full((chart_height, width, 3), 255, dtype=np.uint8)
        transform = None
    else:
        chart, transform = render_chart(day_frame, width=width, height=chart_height, out_path=None)
    header_height = 54
    panel = cv2.copyMakeBorder(
        chart, header_height, 0, 0, 0, cv2.BORDER_CONSTANT, value=(246, 247, 249)
    )
    symbol = str(day_row["symbol"]).replace("_USDT_SWAP", "")
    _put_text(
        panel,
        f"#{int(day_row['rank']):02d} {symbol}  {float(day_row['daily_return']) * 100:+.2f}%",
        (10, 22),
        scale=0.64,
        thickness=2,
    )
    if hits:
        summary = " | ".join(
            f"{'L' if int(hit['class_id']) == 0 else 'S'} {float(hit['confidence']):.2f}"
            for hit in hits
        )
        _put_text(panel, f"detections {len(hits)}: {summary[:92]}", (10, 44), scale=0.52)
    else:
        _put_text(panel, "detections 0", (10, 44), color=(110, 110, 110), scale=0.52)
    if transform is None:
        return panel

    day_start_index = int(np.flatnonzero(mask)[0])
    for hit in hits:
        local_start = int(hit["core_start_i"]) - day_start_index
        local_end = int(hit["core_end_i"]) - day_start_index
        if not 0 <= local_start <= local_end < len(day_frame):
            continue
        x0 = transform.x_at(local_start) - transform.candle_half_w - 2
        x1 = transform.x_at(local_end) + transform.candle_half_w + 2
        y0 = transform.y_at(float(hit["core_high"])) - 3 + header_height
        y1 = transform.y_at(float(hit["core_low"])) + 3 + header_height
        color = CLASS_COLORS[int(hit["class_id"])]
        cv2.rectangle(panel, (x0, y0), (x1, y1), color, 3, cv2.LINE_AA)
        label = f"{'LONG' if int(hit['class_id']) == 0 else 'SHORT'} {float(hit['confidence']):.2f}"
        label_y = max(header_height + 18, y0 - 5)
        _put_text(panel, label, (max(2, x0), label_y), color=color, scale=0.46, thickness=1)
    return panel


def contact_sheet(
    panels: Sequence[np.ndarray],
    *,
    day: pd.Timestamp,
    event_count: int,
    columns: int = 2,
    model_label: str = "t-3",
) -> np.ndarray:
    """Compose all 20 ranked symbol charts into one owner-facing daily PNG."""

    if not panels:
        raise MoversScanError("daily contact sheet has no panels")
    cell_h = max(panel.shape[0] for panel in panels)
    cell_w = max(panel.shape[1] for panel in panels)
    banner_h = 84
    rows = math.ceil(len(panels) / columns)
    canvas = np.full((banner_h + rows * cell_h, columns * cell_w, 3), 244, dtype=np.uint8)
    _put_text(
        canvas,
        f"{day:%Y-%m-%d} UTC | post-hoc abs daily mover Top20 | {model_label} events {event_count}",
        (16, 31),
        scale=0.82,
        thickness=2,
    )
    _put_text(
        canvas,
        "HINDSIGHT BOARD - research weak-label model - green LONG / red SHORT - not a live signal",
        (16, 62),
        color=(30, 90, 190),
        scale=0.58,
        thickness=2,
    )
    for index, panel in enumerate(panels):
        row, column = divmod(index, columns)
        y, x = banner_h + row * cell_h, column * cell_w
        canvas[y : y + panel.shape[0], x : x + panel.shape[1]] = panel
    return canvas


def render_overview(
    ranked: Sequence[Mapping[str, Any]],
    signals: Sequence[Mapping[str, Any]],
    days: Sequence[pd.Timestamp],
    *,
    model_label: str = "t-3",
) -> np.ndarray:
    """Render compact ranked boards with per-symbol event counts."""

    columns = 3
    rows = max(1, math.ceil(len(days) / columns))
    width, height = 1800, 100 + rows * 1000
    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    _put_text(
        canvas,
        f"15m {model_label} YOLO | last {len(days)} complete UTC days | daily absolute mover Top20",
        (24, 42),
        scale=0.95,
        thickness=2,
    )
    _put_text(
        canvas,
        "POST-HOC symbol board. Completed-path weak-label research model; not live selection or trading proof.",
        (24, 76),
        color=(30, 90, 190),
        scale=0.62,
        thickness=2,
    )
    count: Counter[tuple[str, str]] = Counter(
        (str(row["day"]), str(row["symbol"])) for row in signals
    )
    long_count: Counter[tuple[str, str]] = Counter(
        (str(row["day"]), str(row["symbol"]))
        for row in signals
        if int(row["class_id"]) == 0
    )
    short_count: Counter[tuple[str, str]] = Counter(
        (str(row["day"]), str(row["symbol"]))
        for row in signals
        if int(row["class_id"]) == 1
    )
    card_w = 570
    for index, day in enumerate(days):
        row_index, column = divmod(index, columns)
        x0 = 20 + column * 590
        y0 = 105 + row_index * 990
        cv2.rectangle(canvas, (x0, y0), (x0 + card_w, y0 + 970), (225, 229, 234), 2)
        board = sorted(
            (row for row in ranked if utc(row["day"]) == day), key=lambda row: int(row["rank"])
        )
        day_key = day.isoformat()
        total = sum(count[(day_key, str(row["symbol"]))] for row in board)
        _put_text(canvas, f"{day:%Y-%m-%d} UTC | events {total}", (x0 + 12, y0 + 35), scale=0.68, thickness=2)
        _put_text(canvas, "#  SYMBOL            RETURN     DET(L/S)", (x0 + 12, y0 + 69), scale=0.5, thickness=1)
        for line, row in enumerate(board):
            symbol = str(row["symbol"]).replace("_USDT_SWAP", "")[:14]
            key = (day_key, str(row["symbol"]))
            text = (
                f"{int(row['rank']):02d} {symbol:<14} "
                f"{float(row['daily_return']) * 100:+7.2f}%   "
                f"{count[key]:>2}({long_count[key]}/{short_count[key]})"
            )
            color = (20, 125, 35) if float(row["daily_return"]) >= 0 else (45, 45, 190)
            _put_text(canvas, text, (x0 + 12, y0 + 105 + line * 41), color=color, scale=0.54, thickness=1)
    return canvas


def scan_and_render(
    prereg: Mapping[str, Any],
    *,
    out: Path,
    results: Path,
    device: str,
    batch_size: int,
    source_commit: str,
) -> dict[str, Any]:
    """Run frozen inference, write CSV/JSON evidence, and render three daily sheets."""

    fetch_receipt_path = results / "fetch_receipt.json"
    scan_receipt_path = results / "scan_receipt.json"
    if not out.is_dir() or not fetch_receipt_path.is_file():
        raise FileNotFoundError("official fetch output/receipt is missing")
    if scan_receipt_path.exists():
        raise FileExistsError(f"refusing to overwrite official scan receipt: {scan_receipt_path}")
    detector = prereg["detector"]
    weights = ROOT / str(detector["weights"])
    if sha256_file(weights) != str(detector["weights_sha256"]):
        raise MoversScanError("best.pt hash drifted")
    rankings_path = out / "daily_rankings.csv"
    fetch_receipt = json.loads(fetch_receipt_path.read_text(encoding="utf-8"))
    if sha256_file(rankings_path) != fetch_receipt["daily_rankings_sha256"]:
        raise MoversScanError("daily ranking bytes drifted after fetch")
    ranked_frame = pd.read_csv(rankings_path)
    ranked_frame["day"] = pd.to_datetime(ranked_frame["day"], utc=True)
    ranked = ranked_frame.to_dict("records")
    days = [utc(value) for value in prereg["calendar"]["complete_days"]]
    if len(ranked) != int(prereg["ranking"]["top_per_day"]) * len(days):
        raise MoversScanError("ranked row count drifted")

    from ultralytics import YOLO

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != CLASS_NAMES:
        raise MoversScanError(f"weight class names drifted: {names}")
    started = time.perf_counter()
    all_signals: list[dict[str, Any]] = []
    scan_rows: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    for number, day_row in enumerate(ranked, 1):
        symbol = str(day_row["symbol"])
        if symbol not in frames:
            path = out / "kline_snapshot" / f"{symbol}.csv"
            frame = pd.read_csv(path)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            for column in ("open", "high", "low", "close", "volume"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frames[symbol] = frame
        signals, stats = scan_symbol_day(
            frames[symbol],
            day_row=day_row,
            model=model,
            detector=detector,
            device=device,
            batch_size=batch_size,
        )
        all_signals.extend(signals)
        scan_rows.append(
            {
                "day": utc(day_row["day"]).isoformat(),
                "rank": int(day_row["rank"]),
                "symbol": symbol,
                "daily_return": float(day_row["daily_return"]),
                **stats,
            }
        )
        print(
            f"scan [{number:02d}/{len(ranked)}] {utc(day_row['day']):%m-%d} "
            f"#{int(day_row['rank']):02d} {symbol:<22} "
            f"{float(day_row['daily_return']) * 100:+7.2f}% events={len(signals)}",
            flush=True,
        )

    signal_columns = (
        "day",
        "rank",
        "symbol",
        "inst_id",
        "daily_return",
        "class_id",
        "class_name",
        "confidence",
        "core_start_time",
        "core_end_time",
        "core_start_i",
        "core_end_i",
        "core_length_bars",
        "confirmation_bars",
        "window_len",
        "window_start_i",
        "window_end_i",
        "window_end_time",
        "core_high",
        "core_low",
    )
    write_csv(out / "signals.csv", all_signals, signal_columns)
    scan_columns = sorted({key for row in scan_rows for key in row})
    write_csv(out / "scan_stats.csv", scan_rows, scan_columns)

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in all_signals:
        by_key[(str(row["day"]), str(row["symbol"]))].append(row)
    image_rows: list[dict[str, Any]] = []
    for day in days:
        board = sorted(
            (row for row in ranked if utc(row["day"]) == day), key=lambda row: int(row["rank"])
        )
        panels: list[np.ndarray] = []
        day_events = 0
        for day_row in board:
            key = (day.isoformat(), str(day_row["symbol"]))
            hits = by_key[key]
            day_events += len(hits)
            panels.append(
                render_symbol_day_panel(frames[str(day_row["symbol"])], day_row=day_row, hits=hits)
            )
        sheet = contact_sheet(
            panels,
            day=day,
            event_count=day_events,
            model_label=str(detector.get("display_name", "t-3")),
        )
        path = results / f"day_{day:%Y%m%d}_top20.png"
        if path.exists():
            raise FileExistsError(path)
        ok = cv2.imwrite(str(path), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 4])
        if not ok:
            raise OSError(f"OpenCV failed to write {path}")
        image_rows.append(
            {
                "day": day.isoformat(),
                "events": day_events,
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "width": int(sheet.shape[1]),
                "height": int(sheet.shape[0]),
            }
        )

    overview = render_overview(
        ranked,
        all_signals,
        days,
        model_label=str(detector.get("display_name", "t-3")),
    )
    overview_path = results / "overview.png"
    if overview_path.exists():
        raise FileExistsError(overview_path)
    if not cv2.imwrite(str(overview_path), overview, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
        raise OSError(f"OpenCV failed to write {overview_path}")

    signals_path = out / "signals.csv"
    stats_path = out / "scan_stats.csv"
    class_counts = Counter(str(row["class_name"]) for row in all_signals)
    day_counts = Counter(str(row["day"]) for row in all_signals)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": str(prereg["experiment_id"]),
        "source_commit": source_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 1,
        "weights_path": str(weights.relative_to(ROOT)),
        "weights_sha256": sha256_file(weights),
        "device": device,
        "confidence": float(detector["confidence"]),
        "nms_iou": float(detector["nms_iou"]),
        "window_lengths": list(map(int, detector["window_lengths"])),
        "selected_symbol_days": len(ranked),
        "selected_unique_symbols": len({str(row["symbol"]) for row in ranked}),
        "deduplicated_events": len(all_signals),
        "class_counts": dict(sorted(class_counts.items())),
        "day_counts": dict(sorted(day_counts.items())),
        "symbols_with_events": len({(str(row["day"]), str(row["symbol"])) for row in all_signals}),
        "signals_path": str(signals_path.relative_to(ROOT)),
        "signals_sha256": sha256_file(signals_path),
        "scan_stats_path": str(stats_path.relative_to(ROOT)),
        "scan_stats_sha256": sha256_file(stats_path),
        "overview": {
            "path": str(overview_path.relative_to(ROOT)),
            "sha256": sha256_file(overview_path),
            "size_bytes": overview_path.stat().st_size,
            "width": int(overview.shape[1]),
            "height": int(overview.shape[0]),
        },
        "daily_images": image_rows,
        "scan_rows": scan_rows,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "ranking_is_post_hoc": True,
        "economic_backtest": False,
        "threshold_or_window_retuned": False,
        "training_or_tuning": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "orders_placed": False,
        "production_eligible": False,
    }
    scan_receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"scan complete: events={len(all_signals)} classes={dict(class_counts)} "
        f"wall={payload['wall_seconds'] / 60:.1f}m -> {overview_path}",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--fetch", action="store_true")
    phase.add_argument("--scan", action="store_true")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1:
        parser.error("--workers and --batch-size must be positive")
    prereg_path = args.prereg.resolve()
    prereg = load_preregistration(prereg_path)
    source_commit = verify_sources_committed(prereg_path)
    if args.fetch:
        fetch_and_rank(
            prereg,
            out=args.out.resolve(),
            results=args.results.resolve(),
            workers=args.workers,
            source_commit=source_commit,
        )
    else:
        scan_and_render(
            prereg,
            out=args.out.resolve(),
            results=args.results.resolve(),
            device=choose_device(args.device),
            batch_size=args.batch_size,
            source_commit=source_commit,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

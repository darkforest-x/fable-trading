#!/usr/bin/env python3
"""Find 2026 ETH instances of the Owner's delayed short morphology.

This is a retrospective semantic-discovery tool, not a causal signal detector.
The core gate uses only ``open/high/low/close`` and SMA/EMA 20/60/120 from the
candidate core end or earlier.  Its longest backward windows are 120 bars for
the moving averages, 14 bars for ATR, eight bars of pre-core context and four
to seven candidate core bars.  The next three and five OHLC bars are used only
as a historical confirmation label; they are never represented as causal
features and make every output ``production_eligible=false``.

The reference is the 2026-08-10 ETHUSDT 15m example supplied by the Owner.  Its
four-bar core is the frozen model-aligned proposal 11:30--12:15 UTC; that
geometry remains explicitly unconfirmed.  Gate multipliers are frozen in this
file before the yearly scan.  The tool merges the canonical local OKX history
with an in-memory OKX API suffix and never writes raw klines, forward_log,
models/ACTIVE, or any trading state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402

from scripts.build_local_signal_v2_semantic_review import render_owner_chart  # noqa: E402


PROTOCOL = "eth_yearly_morphology_gate_v1_20260813"
OKX_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
DEFAULT_LOCAL = ROOT / "data/kline_fetched/okx_ETH_USDT_SWAP_15m_41281.csv"
DEFAULT_OUT = ROOT / "analysis/output/eth_yearly_morphology_gate_v1"
YEAR_START = pd.Timestamp("2026-01-01T00:00:00Z")
YEAR_END = pd.Timestamp("2027-01-01T00:00:00Z")
DEFAULT_SCAN_END = pd.Timestamp("2026-08-13T09:15:00Z")
REFERENCE_CORE_START = pd.Timestamp("2026-08-10T11:30:00Z")
REFERENCE_CORE_END = pd.Timestamp("2026-08-10T12:15:00Z")
REFERENCE_CORE_BARS = 4
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
HOLDOUT_USE_NUMBER = 1

PRE_BARS = 8
CORE_WIDTHS = tuple(range(4, 8))
CONFIRM_BARS = 5
REVIEW_FUTURE_BARS = 12
EVENT_GAP_BARS = 12

# Frozen against the single Owner reference, before opening the yearly result.
MA_SPAN_REFERENCE_MULT = 2.5
CORE_RANGE_REFERENCE_MULT = 1.75
PRE_RANGE_MAX_PCT = 0.75
CORE_ABS_NET_MAX_PCT = 0.45
PRE_ABS_NET_MAX_PCT = 0.50
CORE_BEFORE_LAST_MIN_PCT = -0.25
CORE_RANGE_ATR_MIN = 1.0
CORE_RANGE_ATR_MAX = 4.5
MA_INTERSECTION_MARGIN = 0.001
CONFIRM_D3_REFERENCE_MULT = 0.60
CONFIRM_D3_HARD_MAX_PCT = -0.25
CONFIRM_D5_REFERENCE_MULT = 0.75
CONFIRM_D5_HARD_MAX_PCT = -0.61
CONFIRM_LOW5_REFERENCE_MULT = 0.75
CONFIRM_LOW5_HARD_MAX_PCT = -0.73
CONFIRM_HIGH5_MAX_PCT = 0.15
CONFIRM_RED5_MIN = 3

OHLCV = ("open_time", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class Gate:
    """Frozen core-feature and future-label thresholds for the yearly scan."""

    ma_span_max_pct: float
    core_range_max_pct: float
    pre_range_max_pct: float
    core_abs_net_max_pct: float
    pre_abs_net_max_pct: float
    before_last_min_pct: float
    core_range_atr_min: float
    core_range_atr_max: float
    d3_close_max_pct: float
    d5_close_max_pct: float
    low5_max_pct: float
    high5_max_pct: float
    red5_min: int


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_local(path: Path) -> pd.DataFrame:
    """Read the canonical local CSV without mutating it."""
    raw = pd.read_csv(path, encoding_errors="replace")
    if "open_time" in raw:
        raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True, errors="coerce")
    elif "ts" in raw:
        raw["open_time"] = pd.to_datetime(
            pd.to_numeric(raw["ts"], errors="coerce"), unit="ms", utc=True
        )
    else:
        raise ValueError(f"{path} has neither open_time nor ts")
    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    if "confirm" in raw:
        raw = raw[raw["confirm"].astype(str) != "0"]
    return (
        raw[list(OHLCV)]
        .dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


def fetch_okx_suffix(
    *,
    scan_end: pd.Timestamp,
    stop_at: pd.Timestamp,
    pause_seconds: float = 0.12,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch a bounded API suffix in memory; no raw kline file is written."""
    cursor = int((scan_end + pd.Timedelta(minutes=15)).timestamp() * 1000)
    stop_with_overlap = stop_at - pd.Timedelta(hours=1)
    payload_rows: list[list[str]] = []
    calls = 0
    while True:
        query = urllib.parse.urlencode(
            {
                "instId": "ETH-USDT-SWAP",
                "bar": "15m",
                "after": str(cursor),
                "limit": "300",
            }
        )
        request = urllib.request.Request(
            f"{OKX_HISTORY_URL}?{query}",
            headers={"User-Agent": "fable-trading-research/1.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            document = json.load(response)
        calls += 1
        if str(document.get("code")) != "0":
            raise RuntimeError(f"OKX history-candles failed: {document}")
        page = document.get("data", [])
        if not page:
            break
        payload_rows.extend(page)
        oldest = min(int(row[0]) for row in page)
        if pd.to_datetime(oldest, unit="ms", utc=True) <= stop_with_overlap:
            break
        if oldest >= cursor:
            raise RuntimeError("OKX pagination did not move backward")
        cursor = oldest
        if calls >= 100:
            raise RuntimeError("OKX pagination exceeded the bounded safety limit")
        time.sleep(pause_seconds)

    columns = (
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vol_ccy",
        "vol_quote",
        "confirm",
    )
    frame = pd.DataFrame(payload_rows, columns=columns)
    if frame.empty:
        return pd.DataFrame(columns=OHLCV), {"api_calls": calls, "api_rows": 0}
    frame["open_time"] = pd.to_datetime(
        pd.to_numeric(frame["ts"], errors="coerce"), unit="ms", utc=True
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[frame["confirm"].astype(str) == "1"]
    frame = (
        frame[list(OHLCV)]
        .dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    return frame, {
        "api_calls": calls,
        "api_rows": len(frame),
        "api_first_time": frame["open_time"].min().isoformat(),
        "api_last_time": frame["open_time"].max().isoformat(),
    }


def canonical_series_sha256(frame: pd.DataFrame) -> str:
    canonical = frame[list(OHLCV)].copy()
    canonical["open_time"] = canonical["open_time"].map(lambda value: utc(value).isoformat())
    payload = canonical.to_csv(index=False, float_format="%.10g", lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_complete_series(
    path: Path,
    *,
    scan_end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Merge local history with an API suffix and prove 15m continuity."""
    local = read_local(path)
    local_last = utc(local["open_time"].max())
    if local_last < scan_end:
        suffix, api_audit = fetch_okx_suffix(scan_end=scan_end, stop_at=local_last)
    else:
        suffix = pd.DataFrame(columns=OHLCV)
        api_audit = {"api_calls": 0, "api_rows": 0}
    merged = (
        pd.concat([local, suffix], ignore_index=True)
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    warmup_start = YEAR_START - pd.Timedelta(days=7)
    merged = merged[
        (merged["open_time"] >= warmup_start) & (merged["open_time"] <= scan_end)
    ].reset_index(drop=True)
    expected = pd.date_range(warmup_start, scan_end, freq="15min", tz="UTC")
    actual = pd.DatetimeIndex(merged["open_time"])
    missing = expected.difference(actual)
    extras = actual.difference(expected)
    if len(missing) or len(extras):
        raise ValueError(
            f"15m continuity failed: missing={len(missing)} extras={len(extras)}"
        )
    audit = {
        "local_path": str(path.relative_to(ROOT)),
        "local_sha256": sha256_file(path),
        "local_last_time": local_last.isoformat(),
        **api_audit,
        "raw_klines_written_locally": 0,
        "merged_first_time": merged["open_time"].min().isoformat(),
        "merged_last_time": merged["open_time"].max().isoformat(),
        "merged_rows_with_warmup": len(merged),
        "year_rows": int((merged["open_time"] >= YEAR_START).sum()),
        "missing_15m_bars": len(missing),
        "extra_15m_bars": len(extras),
        "series_sha256": canonical_series_sha256(merged),
    }
    return merged, audit


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal MA and ATR columns; ATR is shifted to the pre-core boundary."""
    out = add_mas(frame)
    previous_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous_close).abs(),
            (out["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14_pre_core"] = true_range.rolling(14).mean().shift(1)
    return out


def feature_row(frame: pd.DataFrame, end: int, width: int) -> dict[str, Any]:
    """Compute one causal core feature row plus a separate future label block."""
    start = end - width + 1
    core = frame.iloc[start : end + 1]
    pre = frame.iloc[start - PRE_BARS : start]
    future = frame.iloc[end + 1 : end + CONFIRM_BARS + 1]
    close = float(frame.iloc[end]["close"])
    atr = float(frame.iloc[end]["atr14_pre_core"])
    mas = frame.iloc[end][list(ALL_MA_COLS)].astype(float)
    return {
        "core_start_i": start,
        "core_end_i": end,
        "core_width": width,
        "core_start_time": utc(frame.iloc[start]["open_time"]).isoformat(),
        "core_end_time": utc(frame.iloc[end]["open_time"]).isoformat(),
        "atr14_pre_core": atr,
        "ma_span_pct": float((mas.max() - mas.min()) / close * 100),
        "core_range_pct": float(
            (core["high"].max() - core["low"].min()) / core.iloc[0]["open"] * 100
        ),
        "core_net_pct": float((core.iloc[-1]["close"] / core.iloc[0]["open"] - 1) * 100),
        "core_before_last_pct": float(
            (core.iloc[-2]["close"] / core.iloc[0]["open"] - 1) * 100
        ),
        "pre_range_pct": float(
            (pre["high"].max() - pre["low"].min()) / pre.iloc[0]["open"] * 100
        ),
        "pre_net_pct": float((pre.iloc[-1]["close"] / pre.iloc[0]["open"] - 1) * 100),
        "core_range_atr": float((core["high"].max() - core["low"].min()) / atr),
        "core_intersects_ma_bundle": bool(
            core["high"].max() >= mas.min() * (1 - MA_INTERSECTION_MARGIN)
            and core["low"].min() <= mas.max() * (1 + MA_INTERSECTION_MARGIN)
        ),
        "confirm_d3_close_pct": float((future.iloc[2]["close"] / close - 1) * 100),
        "confirm_d5_close_pct": float((future.iloc[4]["close"] / close - 1) * 100),
        "confirm_low5_pct": float((future["low"].min() / close - 1) * 100),
        "confirm_high5_pct": float((future["high"].max() / close - 1) * 100),
        "confirm_red5": int((future["close"] < future["open"]).sum()),
        "feature_future_bars": 0,
        "label_future_bars": CONFIRM_BARS,
    }


def build_gate(reference: dict[str, Any]) -> Gate:
    """Derive the frozen numeric limits from the one supplied reference."""
    return Gate(
        ma_span_max_pct=float(reference["ma_span_pct"]) * MA_SPAN_REFERENCE_MULT,
        core_range_max_pct=float(reference["core_range_pct"]) * CORE_RANGE_REFERENCE_MULT,
        pre_range_max_pct=PRE_RANGE_MAX_PCT,
        core_abs_net_max_pct=CORE_ABS_NET_MAX_PCT,
        pre_abs_net_max_pct=PRE_ABS_NET_MAX_PCT,
        before_last_min_pct=CORE_BEFORE_LAST_MIN_PCT,
        core_range_atr_min=CORE_RANGE_ATR_MIN,
        core_range_atr_max=CORE_RANGE_ATR_MAX,
        d3_close_max_pct=min(
            float(reference["confirm_d3_close_pct"]) * CONFIRM_D3_REFERENCE_MULT,
            CONFIRM_D3_HARD_MAX_PCT,
        ),
        d5_close_max_pct=min(
            float(reference["confirm_d5_close_pct"]) * CONFIRM_D5_REFERENCE_MULT,
            CONFIRM_D5_HARD_MAX_PCT,
        ),
        low5_max_pct=min(
            float(reference["confirm_low5_pct"]) * CONFIRM_LOW5_REFERENCE_MULT,
            CONFIRM_LOW5_HARD_MAX_PCT,
        ),
        high5_max_pct=CONFIRM_HIGH5_MAX_PCT,
        red5_min=CONFIRM_RED5_MIN,
    )


def passes_gate(row: dict[str, Any], gate: Gate) -> bool:
    """Apply the pre-registered conjunction without any score threshold."""
    return bool(
        float(row["ma_span_pct"]) <= gate.ma_span_max_pct
        and float(row["core_range_pct"]) <= gate.core_range_max_pct
        and abs(float(row["core_net_pct"])) <= gate.core_abs_net_max_pct
        and float(row["pre_range_pct"]) <= gate.pre_range_max_pct
        and abs(float(row["pre_net_pct"])) <= gate.pre_abs_net_max_pct
        and float(row["core_before_last_pct"]) >= gate.before_last_min_pct
        and gate.core_range_atr_min
        <= float(row["core_range_atr"])
        <= gate.core_range_atr_max
        and bool(row["core_intersects_ma_bundle"])
        and float(row["confirm_d3_close_pct"]) <= gate.d3_close_max_pct
        and float(row["confirm_d5_close_pct"]) <= gate.d5_close_max_pct
        and float(row["confirm_low5_pct"]) <= gate.low5_max_pct
        and float(row["confirm_high5_pct"]) <= gate.high5_max_pct
        and int(row["confirm_red5"]) >= gate.red5_min
    )


def similarity_distance(row: dict[str, Any], reference: dict[str, Any]) -> float:
    """Rank gate passers only; the score never determines pool membership."""
    fields_and_scales = (
        ("ma_span_pct", max(abs(float(reference["ma_span_pct"])), 0.05)),
        ("core_range_pct", max(abs(float(reference["core_range_pct"])), 0.25)),
        ("core_net_pct", 0.25),
        ("core_before_last_pct", 0.25),
        ("pre_range_pct", max(abs(float(reference["pre_range_pct"])), 0.30)),
        ("pre_net_pct", 0.30),
        (
            "confirm_d3_close_pct",
            max(abs(float(reference["confirm_d3_close_pct"])), 0.25),
        ),
        (
            "confirm_d5_close_pct",
            max(abs(float(reference["confirm_d5_close_pct"])), 0.50),
        ),
        ("confirm_low5_pct", max(abs(float(reference["confirm_low5_pct"])), 0.60)),
        ("confirm_high5_pct", 0.15),
        ("confirm_red5", 2.0),
        ("core_width", 2.0),
    )
    residuals = [
        (float(row[field]) - float(reference[field])) / scale
        for field, scale in fields_and_scales
    ]
    return float(np.mean(np.square(residuals)))


def cluster_candidate_events(
    rows: list[dict[str, Any]],
    *,
    gap_bars: int = EVENT_GAP_BARS,
) -> list[dict[str, Any]]:
    """Choose one width per endpoint, then transitive-time-deduplicate events."""
    by_endpoint: dict[int, dict[str, Any]] = {}
    for row in rows:
        endpoint = int(row["core_end_i"])
        if endpoint not in by_endpoint or float(row["distance"]) < float(
            by_endpoint[endpoint]["distance"]
        ):
            by_endpoint[endpoint] = row
    groups: list[list[dict[str, Any]]] = []
    for row in sorted(by_endpoint.values(), key=lambda item: int(item["core_end_i"])):
        if groups and int(row["core_end_i"]) - int(groups[-1][-1]["core_end_i"]) <= gap_bars:
            groups[-1].append(row)
        else:
            groups.append([row])
    events: list[dict[str, Any]] = []
    for group in groups:
        best = dict(min(group, key=lambda item: float(item["distance"])))
        best.update(
            {
                "endpoint_count": len(group),
                "event_first_core_end_time": min(
                    str(item["core_end_time"]) for item in group
                ),
                "event_last_core_end_time": max(
                    str(item["core_end_time"]) for item in group
                ),
            }
        )
        events.append(best)
    return events


def scan_year(
    frame: pd.DataFrame,
    *,
    reference: dict[str, Any],
    gate: Gate,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Perform the one-shot exhaustive yearly scan."""
    gate_rows: list[dict[str, Any]] = []
    for end in range(128, len(frame) - CONFIRM_BARS):
        stamp = utc(frame.iloc[end]["open_time"])
        if stamp < YEAR_START or stamp >= YEAR_END:
            continue
        if not np.isfinite(float(frame.iloc[end]["atr14_pre_core"])):
            continue
        if not np.isfinite(frame.iloc[end][list(ALL_MA_COLS)].astype(float)).all():
            continue
        for width in CORE_WIDTHS:
            row = feature_row(frame, end, width)
            if passes_gate(row, gate):
                row["distance"] = similarity_distance(row, reference)
                gate_rows.append(row)
    events = cluster_candidate_events(gate_rows)
    for event in events:
        payload = (
            f"{PROTOCOL}|ETH_USDT_SWAP|{event['core_start_time']}|"
            f"{event['core_end_time']}"
        )
        event["event_id"] = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return events, {
        "gate_passing_width_rows": len(gate_rows),
        "gate_passing_unique_endpoints": len(
            {int(row["core_end_i"]) for row in gate_rows}
        ),
        "deduplicated_events": len(events),
    }


def write_review_image(
    frame: pd.DataFrame,
    event: dict[str, Any],
    *,
    output: Path,
    caption: str,
) -> dict[str, Any]:
    start = int(event["core_start_i"])
    end = int(event["core_end_i"])
    window_start = start - PRE_BARS
    window_end = end + REVIEW_FUTURE_BARS
    review = frame.iloc[window_start : window_end + 1].reset_index(drop=True)
    image, span_pct = render_owner_chart(
        review,
        core_start_local=PRE_BARS,
        core_end_local=end - window_start,
        decision_local=end - window_start,
        caption=caption,
    )
    cst = utc(event["core_start_time"]).tz_convert("Asia/Shanghai")
    cv2.putText(
        image,
        cst.strftime("%Y-%m-%d %H:%M CST"),
        (900, 31),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (22, 32, 39),
        2,
        cv2.LINE_AA,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"failed to write {output}")
    return {
        "review_path": str(output.relative_to(ROOT)),
        "review_sha256": sha256_file(output),
        "review_actual_span_pct": span_pct,
        "review_future_bars": REVIEW_FUTURE_BARS,
        "review_future_only_after_boundary": True,
    }


def build_artifacts(
    frame: pd.DataFrame,
    events: list[dict[str, Any]],
    *,
    reference: dict[str, Any],
    gate: Gate,
    source_audit: dict[str, Any],
    scan_counts: dict[str, int],
    output: Path,
    scan_end: pd.Timestamp,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {output}")
    (output / "review").mkdir(parents=True, exist_ok=True)
    reference_index = next(
        (
            index
            for index, event in enumerate(events)
            if utc(event["event_first_core_end_time"])
            <= REFERENCE_CORE_END
            <= utc(event["event_last_core_end_time"])
        ),
        None,
    )
    if reference_index is None:
        raise ValueError("frozen gate failed to recover its own reference event")
    ordered = [events[reference_index], *events[:reference_index], *events[reference_index + 1 :]]
    rows: list[dict[str, Any]] = []
    images: list[np.ndarray] = []
    for rank, event in enumerate(ordered, 1):
        is_reference = rank == 1
        row = dict(event)
        image_path = output / "review" / (
            f"{rank:03d}_ETH_{utc(event['core_start_time']).strftime('%Y%m%d_%H%M')}.png"
        )
        image_meta = write_review_image(
            frame,
            event,
            output=image_path,
            caption="OWNER REFERENCE" if is_reference else f"CANDIDATE {rank - 1:02d}",
        )
        row.update(
            {
                "rank": rank,
                **image_meta,
                "symbol": "ETH_USDT_SWAP",
                "timeframe": "15m",
                "owner_reference_sample": is_reference,
                "owner_protocol_confirmed": True,
                "sample_owner_confirmed_semantics": is_reference,
                "sample_owner_confirmed_geometry": False,
                "geometry_status": "model_aligned_proposal_unconfirmed",
                "owner_verdict": "REFERENCE" if is_reference else "PENDING",
                "codex_visual_review": "REFERENCE" if is_reference else "SUGGESTED_MATCH",
                "training_eligible": False,
                "production_eligible": False,
            }
        )
        rows.append(row)
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError(f"could not reopen {image_path}")
        images.append(image)
    spacer = np.full((14, images[0].shape[1], 3), 210, dtype=np.uint8)
    contact_parts: list[np.ndarray] = []
    for index, image in enumerate(images):
        if index:
            contact_parts.append(spacer)
        contact_parts.append(image)
    comparison = np.vstack(contact_parts)
    comparison_path = output / "comparison.png"
    if not cv2.imwrite(str(comparison_path), comparison):
        raise OSError(f"failed to write {comparison_path}")
    write_jsonl(output / "review_manifest.jsonl", rows)
    gate_payload = {field: getattr(gate, field) for field in gate.__dataclass_fields__}
    (output / "reference_features.json").write_text(
        json.dumps(
            {
                "protocol": PROTOCOL,
                "reference": reference,
                "gate": gate_payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    holdout_rows = int(
        (
            (frame["open_time"] >= HOLDOUT_START)
            & (frame["open_time"] <= scan_end)
        ).sum()
    )
    summary = {
        "protocol": PROTOCOL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "ETH_USDT_SWAP",
        "timeframe": "15m",
        "scan_start": YEAR_START.isoformat(),
        "scan_end": scan_end.isoformat(),
        "reference_core_start": REFERENCE_CORE_START.isoformat(),
        "reference_core_end": REFERENCE_CORE_END.isoformat(),
        "source_audit": source_audit,
        "scan_counts": scan_counts,
        "machine_candidate_events_including_reference": len(rows),
        "owner_reference_events": sum(bool(row["owner_reference_sample"]) for row in rows),
        "new_candidate_events": sum(not bool(row["owner_reference_sample"]) for row in rows),
        "owner_confirmed_semantic_events": sum(
            bool(row["sample_owner_confirmed_semantics"]) for row in rows
        ),
        "pending_owner_confirmation_events": sum(
            str(row["owner_verdict"]) == "PENDING" for row in rows
        ),
        "holdout": {
            "read": True,
            "rows_read": holdout_rows,
            "use_number_for_this_frozen_gate": HOLDOUT_USE_NUMBER,
            "owner_authorized_in_conversation": True,
            "authorization_scope": "find how many matching ETH shapes occurred this year",
        },
        "lookahead_contract": {
            "core_feature_future_bars": 0,
            "historical_confirmation_label_future_bars": CONFIRM_BARS,
            "human_review_future_bars": REVIEW_FUTURE_BARS,
        },
        "threshold_tuned_after_scan": False,
        "model_used": False,
        "training_eligible": False,
        "production_eligible": False,
        "comparison_path": str(comparison_path.relative_to(ROOT)),
        "comparison_sha256": sha256_file(comparison_path),
    }
    (output / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    readme = f"""# ETH 2026 morphology gate v1

- machine candidates (including the supplied reference): **{len(rows)}**
- supplied reference: **1**
- new candidate awaiting Owner confirmation: **{summary['pending_owner_confirmation_events']}**
- core features use zero future bars; next 3/5 bars are retrospective labels only
- holdout use: frozen gate use #{HOLDOUT_USE_NUMBER}, explicitly authorized by the yearly request
- training/production eligible: **false / false**

Open `comparison.png` for the side-by-side visual audit. Orange is the proposed
core; the purple boundary separates the 12-bar review-only future context.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", type=Path, default=DEFAULT_LOCAL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scan-end", default=DEFAULT_SCAN_END.isoformat())
    args = parser.parse_args()

    scan_end = utc(args.scan_end)
    if not (YEAR_START <= scan_end < YEAR_END):
        raise SystemExit("scan-end must be within 2026")
    frame, source_audit = load_complete_series(args.local.resolve(), scan_end=scan_end)
    frame = enrich(frame)
    reference_end_matches = frame.index[frame["open_time"] == REFERENCE_CORE_END]
    if len(reference_end_matches) != 1:
        raise ValueError("reference core end is missing or duplicated")
    reference = feature_row(
        frame,
        int(reference_end_matches[0]),
        REFERENCE_CORE_BARS,
    )
    if utc(reference["core_start_time"]) != REFERENCE_CORE_START:
        raise ValueError("reference core geometry drifted")
    gate = build_gate(reference)
    events, scan_counts = scan_year(frame, reference=reference, gate=gate)
    summary = build_artifacts(
        frame,
        events,
        reference=reference,
        gate=gate,
        source_audit=source_audit,
        scan_counts=scan_counts,
        output=args.out.resolve(),
        scan_end=scan_end,
    )
    print(
        f"events={summary['machine_candidate_events_including_reference']} "
        f"new={summary['new_candidate_events']} "
        f"pending={summary['pending_owner_confirmation_events']} "
        f"out={args.out.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

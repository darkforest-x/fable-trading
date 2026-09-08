"""Read-only public-history review of four already exposed owner screenshots.

Source: owner's ETH/ZEC/XAU 4H and SOPH 30m screenshots on 2026-09-08.
Only OKX public history-candles GETs are used, with an independent 2 req/s
client. Native candles are confirmed, UTC-aligned, unique and gap-free. Each
case requests 1,200 bars ending at its fixed exclusive UTC window end; no
latest quote, credentials, service writes or outcome-dependent selection is
used. Feature windows and decision-time availability are documented in
imacd_startup_quality.build_features. Every release in the fixed window is
reported, including price differences from the screenshot reference; those
differences do not verify that the screenshot and event share a timestamp.

These selected, previously exposed examples are semantic reviews, never an
independent validation sample. The runner requires its source dependency
closure to match git HEAD before requests or outputs, and exclusively creates
new case directories. It neither overwrites research results nor changes the
existing monitor's data, Pine settings, notifications or execution behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.monitor.okx import MarketError, OKX
from .imacd_startup_quality import build_features


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-startup-quality-20260908-v1"
DATA = ROOT / "data/imacd_startup_quality_20260908_v1"
BAR_COUNT = 1200
MIN_WARMUP = 340
PERIOD_MS = {"4H": 14_400_000, "30m": 1_800_000}
ENDPOINT = "/api/v5/market/history-candles"
FROZEN_PATHS = (
    "yoyo/evaluation/imacd_startup_cases.py",
    "yoyo/evaluation/imacd_startup_quality.py",
    "yoyo/monitor/__init__.py",
    "yoyo/monitor/signals.py",
    "yoyo/monitor/okx.py",
    "experiments/active/exp-imacd-startup-quality-20260908-v1/PROJECT_PLAN.md",
)


@dataclass(frozen=True)
class Case:
    symbol: str
    timeframe: str
    start: str
    end: str
    reference_price: float


CASES = (
    Case("ETH-USDT-SWAP", "4H", "2026-08-18", "2026-08-21", 1922.23),
    Case("ZEC-USDT-SWAP", "4H", "2026-08-17", "2026-08-21", 506.26),
    Case("XAU-USDT-SWAP", "4H", "2026-08-04", "2026-08-08", 4173.8),
    Case("SOPH-USDT-SWAP", "30m", "2026-09-06", "2026-09-08", .004524),
)


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    """Parse fixed UTC bounds without consulting the host's current clock."""
    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _milliseconds(value: str | pd.Timestamp) -> int:
    return int(_utc(value).value // 1_000_000)


def _parse_page(raw_rows: list, period: int, end_ms: int) -> tuple[list[dict], int | None]:
    """Validate public row shape and closed OHLCV; return its raw oldest cursor.

    Confirmation is raw[8]; unconfirmed bars cannot supply a candle. Cursor
    progression still sees their timestamps. Closed bars beyond the fixed end
    are ignored, never admitted through a newer server response.
    """
    if not isinstance(raw_rows, list):
        raise MarketError("invalid_history_payload")
    parsed = []
    stamps = []
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) < 9:
            raise MarketError("invalid_history_row_shape")
        try:
            stamp = int(raw[0])
        except (TypeError, ValueError, OverflowError):
            raise MarketError("invalid_history_timestamp") from None
        if isinstance(raw[0], float) and stamp != raw[0]:
            raise MarketError("invalid_history_timestamp")
        if stamp < 0 or stamp % period:
            raise MarketError("unaligned_history_timestamp")
        stamps.append(stamp)
        flag = str(raw[8])
        if flag not in ("0", "1"):
            raise MarketError("invalid_history_confirmation")
        if flag != "1" or stamp + period > end_ms:
            continue
        try:
            o, h, l, c, v = (float(x) for x in raw[1:6])
        except (TypeError, ValueError, OverflowError):
            raise MarketError("invalid_history_number") from None
        if not all(math.isfinite(x) for x in (o, h, l, c, v)):
            raise MarketError("nonfinite_history_number")
        if min(o, h, l, c) <= 0 or v < 0 or h < max(o, l, c) or l > min(o, h, c):
            raise MarketError("invalid_history_ohlcv")
        parsed.append(dict(ts=stamp, open=o, high=h, low=l, close=c, volume=v))
    return parsed, min(stamps) if stamps else None


def fetch_history(client, case: Case, count: int = BAR_COUNT, max_pages: int = 30) -> tuple[pd.DataFrame, dict]:
    """Page backward from the frozen end, deduplicate, and reject gaps.

    The native exchange timeframe is never changed or synthesized. No source
    fallback or missing-bar interpolation is allowed. A short continuous
    history is returned with explicit metadata rather than padded to count.
    """
    if case.timeframe not in PERIOD_MS or not case.symbol.endswith("-USDT-SWAP"):
        raise ValueError("unsupported case instrument or native timeframe")
    if count < 1 or max_pages < 1:
        raise ValueError("count and max_pages must be positive")
    end_ms = _milliseconds(case.end)
    period = PERIOD_MS[case.timeframe]
    if _milliseconds(case.start) >= end_ms or end_ms % period:
        raise ValueError("invalid case time window")
    cursor = end_ms
    collected = {}
    pages = 0
    exhausted = False
    while pages < max_pages and len(collected) < count:
        raw = client.get(ENDPOINT, {"instId": case.symbol, "bar": case.timeframe,
                                    "after": str(cursor), "limit": "100"})
        pages += 1
        parsed, oldest = _parse_page(raw, period, end_ms)
        if oldest is None:
            exhausted = True
            break
        if oldest >= cursor:
            raise MarketError("history_pagination_did_not_advance")
        for row in parsed:
            previous = collected.get(row["ts"])
            if previous is not None and previous != row:
                raise MarketError("conflicting_confirmed_history_candle")
            collected[row["ts"]] = row
        cursor = oldest
    if pages >= max_pages and len(collected) < count and not exhausted:
        raise MarketError("history_page_budget_exhausted")
    rows = [collected[t] for t in sorted(collected)[-count:]]
    stamps = [r["ts"] for r in rows]
    if any(b - a != period for a, b in zip(stamps, stamps[1:])):
        raise MarketError("confirmed_history_gap")
    # A continuous but old response is not complete coverage of the target end.
    if stamps and stamps[-1] + period != end_ms:
        raise MarketError("history_missing_window_end")
    bars = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    bars.index = pd.DatetimeIndex(pd.to_datetime(bars.pop("ts"), unit="ms", utc=True), name="open_time")
    return bars, {"pages": pages, "requested_bars": count, "received_bars": len(bars),
                  "history_complete": len(bars) == count, "source_exhausted": exhausted,
                  "start_open_utc": bars.index[0].isoformat() if len(bars) else None,
                  "end_close_utc": (bars.index[-1] + pd.Timedelta(milliseconds=period)).isoformat() if len(bars) else None}


def review_case(bars: pd.DataFrame, case: Case) -> dict:
    """List every release in [start,end) without choosing by price or outcome."""
    result = {"symbol": case.symbol, "timeframe": case.timeframe,
              "window_start_utc": _utc(case.start).isoformat(),
              "window_end_exclusive_utc": _utc(case.end).isoformat(),
              "window_basis": "bar_open_utc; bar_close_utc <= window_end",
              "reference_price": case.reference_price,
              "screenshot_timestamp_verified": False,
              "same_timestamp_claim": "Not verified; all releases in the predeclared window are listed.",
              "bars": len(bars), "releases": [], "release_count": 0}
    if len(bars) < MIN_WARMUP:
        result["status"] = "insufficient_warmup"
        return result
    period = pd.Timedelta(milliseconds=PERIOD_MS[case.timeframe])
    features = build_features(bars)
    selected = features.loc[(features.index >= _utc(case.start))
                            & (features.index < _utc(case.end))
                            & (features.index + period <= _utc(case.end))
                            & features.release_side.ne(0)]
    for stamp, row in selected.iterrows():
        close = float(bars.loc[stamp, "close"])
        delta = close - case.reference_price
        record = {"bar_open_utc": stamp.isoformat(), "bar_close_utc": (stamp + period).isoformat(),
                  "side": "long" if row.release_side > 0 else "short", "close": close,
                  "near_zero_bars": int(row.near_zero_bars),
                  "keep_contraction": bool(row.keep_contraction),
                  "keep_proximity": bool(row.keep_proximity),
                  "keep_separation": bool(row.keep_separation),
                  "dense": bool(row.dense),
                  "reference_price_delta": delta,
                  "reference_price_delta_pct": delta / case.reference_price * 100}
        for name in ("contraction_ratio", "proximity_atr", "separation_delta", "release_band"):
            record[name] = float(row[name]) if pd.notna(row[name]) and math.isfinite(row[name]) else None
        result["releases"].append(record)
    result["release_count"] = len(result["releases"])
    result["status"] = "ok" if len(bars) >= BAR_COUNT else "partial_history"
    return result


def frozen_sources(root: Path = ROOT) -> dict:
    """Fail before data access if any declared builder source differs from HEAD."""
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sources = []
    for relative in FROZEN_PATHS:
        try:
            frozen = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=root,
                                             stderr=subprocess.PIPE)
            working = (root / relative).read_bytes()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise RuntimeError(f"source must be committed before case requests: {relative}") from exc
        if frozen != working:
            raise RuntimeError(f"source differs from frozen HEAD: {relative}")
        sources.append({"path": relative, "sha256": hashlib.sha256(working).hexdigest()})
    return {"builder_commit": commit, "source_files": sources}


def run() -> Path:
    """Fetch once into dedicated research paths after source/output preflight."""
    frozen = frozen_sources()
    result_dir = EXP / "results/cases"
    data_dir = DATA / "cases"
    if result_dir.exists() or data_dir.exists():
        raise FileExistsError("case output already exists; inspect it instead of overwriting")
    result_dir.mkdir(parents=True, exist_ok=False)
    data_dir.mkdir(parents=True, exist_ok=False)
    client = OKX(rate=2.0)
    manifest = {"schema_version": 1, "study": "exposed_owner_screenshot_cases_v1",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(), **frozen,
                "source": "OKX public history-candles", "endpoint": ENDPOINT,
                "rate_requests_per_second": 2.0, "target_bars_per_case": BAR_COUNT,
                "evaluation_role": "previously exposed semantic cases; no held-out selection evidence",
                "history_seed_limit": "Recurrences start at these 1,200 bars; TradingView's longer history may change marginal states.",
                "holdout_case_review_number": 1, "training_eligible": False,
                "production_eligible": False, "monitor_mutations": False, "cases": []}
    for case in CASES:
        try:
            bars, metadata = fetch_history(client, case)
            review = review_case(bars, case)
            output = data_dir / f"{case.symbol}_{case.timeframe}.csv"
            saved = bars.copy()
            saved.insert(0, "ts", saved.index.asi8 // 1_000_000)
            saved["open_time"] = saved.index.strftime("%Y-%m-%dT%H:%M:%SZ")
            saved["confirm"] = 1
            saved.to_csv(output, index=False, mode="x")
            manifest["cases"].append({**review, "history": metadata,
                                      "source_path": str(output.relative_to(ROOT)),
                                      "source_sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
        except (MarketError, ValueError) as exc:
            manifest["cases"].append({"symbol": case.symbol, "timeframe": case.timeframe,
                                      "window_start_utc": _utc(case.start).isoformat(),
                                      "window_end_exclusive_utc": _utc(case.end).isoformat(),
                                      "status": "source_or_validation_failed", "error": str(exc),
                                      "screenshot_timestamp_verified": False})
    manifest["request_attempts"] = client.requests
    path = result_dir / "manifest.json"
    with path.open("x") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return path


if __name__ == "__main__":
    print(run())

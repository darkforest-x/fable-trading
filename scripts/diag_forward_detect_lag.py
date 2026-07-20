#!/usr/bin/env python3
"""Diagnose live YOLO tip-firing lag for forward_log rows (EDEN / KAITO etc.).

Walks the tip forward from each signal bar and records the earliest tip index
where `scan_series_with_yolo(..., mode="live")` returns that signal bar.

This answers: "was the detector silent at the true tip, or did live pipeline
drop a detectable box?"

Usage (on Mac or VPS with .venv + owner_best.pt + kline):
  PYTHONPATH=. .venv/bin/python scripts/diag_forward_detect_lag.py \\
      --symbol EDEN_USDT_SWAP --max-lag-bars 80
  PYTHONPATH=. .venv/bin/python scripts/diag_forward_detect_lag.py --from-log

Does NOT touch holdout. Read-only over data + weights.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    DEFAULT_WEIGHTS,
    WINDOW,
    load_yolo_model,
    scan_series_with_yolo,
)


def _parse_ts(s: str) -> pd.Timestamp:
    ts = pd.Timestamp(s)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def earliest_live_hit(
    frame: pd.DataFrame,
    signal_i: int,
    model,
    *,
    conf: float = DEFAULT_CONF,
    max_lag_bars: int = 80,
    start_from_i: int | None = None,
) -> dict:
    """Return first tip index (>= signal_i+1) where live scan includes signal_i."""
    n = len(frame)
    # Tip must leave room for entry bar at least.
    tip0 = min(n - 1, max(signal_i + 1, signal_i))
    tip1 = min(n - 1, signal_i + 1 + max_lag_bars)
    first_hit = None
    conf_at_first = None
    # Sample every bar for first 8, then every 2 bars (cost control).
    tips = list(range(tip0, min(tip0 + 8, tip1 + 1)))
    tips += list(range(tip0 + 8, tip1 + 1, 2))
    tips = sorted(set(t for t in tips if t > signal_i and t < n))
    for tip in tips:
        # Simulate "frame only known up to tip" (closed bars through tip).
        sub = frame.iloc[: tip + 1].copy()
        sf = start_from_i
        if sf is not None:
            sf = min(sf, tip)
        hits = scan_series_with_yolo(
            sub,
            model,
            conf=conf,
            start_from_i=sf,
            mode="live",
        )
        if signal_i in hits:
            first_hit = tip
            break
    lag_bars = (first_hit - signal_i) if first_hit is not None else None
    lag_min = lag_bars * 15 if lag_bars is not None else None
    return {
        "signal_i": signal_i,
        "first_tip_i": first_hit,
        "lag_bars": lag_bars,
        "lag_min": lag_min,
        "tip_fire": first_hit is not None and first_hit <= signal_i + 2,
        "scanned_tips": len(tips),
        "conf": conf,
    }


def load_frame(symbol: str) -> pd.DataFrame:
    """Load 15m OHLCV for one SWAP symbol from kline_fetched (or cache)."""
    groups = list_series(bar="15m")
    paths = None
    for (_source, sym), plist in groups.items():
        if sym == symbol:
            paths = plist
            break
    if not paths:
        raise FileNotFoundError(f"no kline series for {symbol}")
    df = load_series(paths)
    if df.empty:
        raise FileNotFoundError(f"empty series for {symbol}")
    return df


def rows_from_log(path: Path, symbol: str | None) -> list[dict]:
    rows = list(csv.DictReader(path.open()))
    out = []
    for r in rows:
        if symbol and r.get("symbol") != symbol:
            continue
        out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="", help="e.g. EDEN_USDT_SWAP")
    ap.add_argument("--from-log", action="store_true", help="use data/forward_log.csv rows")
    ap.add_argument("--log", type=Path, default=PROJECT / "data" / "forward_log.csv")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--max-lag-bars", type=int, default=80)
    ap.add_argument("--out", type=Path, default=PROJECT / "analysis" / "output" / "diag_detect_lag.json")
    args = ap.parse_args()

    if not args.weights.exists():
        print(f"weights missing: {args.weights}", file=sys.stderr)
        return 2

    model = load_yolo_model(args.weights)
    targets: list[dict] = []
    if args.from_log:
        if not args.log.exists():
            print(f"log missing: {args.log}", file=sys.stderr)
            return 2
        targets = rows_from_log(args.log, args.symbol or None)
        if not targets:
            print("no matching rows in log", file=sys.stderr)
            return 1
    elif args.symbol:
        # single symbol: use last log row for that symbol if any, else tip-1
        if args.log.exists():
            targets = rows_from_log(args.log, args.symbol)
        if not targets:
            targets = [{"symbol": args.symbol, "signal_time": "", "signal_i": ""}]
    else:
        print("pass --symbol or --from-log", file=sys.stderr)
        return 2

    results = []
    for rec in targets:
        sym = rec["symbol"]
        print(f"=== {sym} signal_time={rec.get('signal_time')} ===", flush=True)
        frame = load_frame(sym)
        if "open_time" in frame.columns:
            times = pd.to_datetime(frame["open_time"], utc=True)
        else:
            raise SystemExit(f"{sym}: no open_time")

        if rec.get("signal_i") not in ("", None):
            signal_i = int(float(rec["signal_i"]))
        elif rec.get("signal_time"):
            st = _parse_ts(rec["signal_time"])
            hits = (times == st).to_numpy().nonzero()[0]
            if len(hits) == 0:
                # nearest
                diffs = (times - st).abs()
                signal_i = int(diffs.argmin())
            else:
                signal_i = int(hits[0])
        else:
            signal_i = len(frame) - 5

        # Align with live FORWARD_START filter loosely: allow scanning from
        # signal_i-5 so we don't blank the tip.
        start_from_i = max(0, signal_i - 5)
        r = earliest_live_hit(
            frame,
            signal_i,
            model,
            conf=args.conf,
            max_lag_bars=args.max_lag_bars,
            start_from_i=start_from_i,
        )
        r["symbol"] = sym
        r["signal_time"] = str(times.iloc[signal_i])
        r["log_detected_at"] = rec.get("detected_at", "")
        r["log_lag_min"] = None
        if rec.get("detected_at") and rec.get("signal_time"):
            try:
                lag = (_parse_ts(rec["detected_at"]) - _parse_ts(rec["signal_time"])).total_seconds() / 60
                r["log_lag_min"] = round(lag, 1)
            except Exception:
                pass
        r["window"] = WINDOW
        print(
            f"  first_live_hit lag_bars={r['lag_bars']} lag_min={r['lag_min']} "
            f"tip_fire={r['tip_fire']} log_lag_min={r['log_lag_min']}",
            flush=True,
        )
        results.append(r)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(args.weights),
        "conf": args.conf,
        "results": results,
        "n_tip_fire": sum(1 for r in results if r.get("tip_fire")),
        "n_total": len(results),
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

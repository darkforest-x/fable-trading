#!/usr/bin/env python3
"""Diagnose live/tip YOLO tip-firing lag for forward_log rows (EDEN / KAITO etc.).

Walks the tip forward from each signal bar and records the earliest tip index
where `scan_series_with_yolo` returns that signal bar.

Also supports a forced tip-only smoke (`--tip-smoke`) over log symbols (or a
symbol list) at the current tip: tip_fire rate under tip mode + optional TIP_CONF.

Usage (Mac or VPS with .venv + owner_best.pt + kline):
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
      scripts/diag_forward_detect_lag.py --from-log --compare
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
      scripts/diag_forward_detect_lag.py --from-log --mode tip --tip-conf 0.22
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
      scripts/diag_forward_detect_lag.py --tip-smoke --from-log --tip-conf 0.22

Does NOT touch holdout. Read-only over data + weights. Does not write forward_log.
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


def earliest_hit(
    frame: pd.DataFrame,
    signal_i: int,
    model,
    *,
    conf: float = DEFAULT_CONF,
    tip_conf: float | None = None,
    mode: str = "live",
    max_lag_bars: int = 80,
    start_from_i: int | None = None,
) -> dict:
    """Return first tip index (>= signal_i) where scan includes signal_i."""
    n = len(frame)
    tip0 = min(n - 1, max(signal_i, signal_i))
    tip1 = min(n - 1, signal_i + 1 + max_lag_bars)
    first_hit = None
    # Sample every bar for first 8, then every 2 bars (cost control).
    tips = list(range(tip0, min(tip0 + 8, tip1 + 1)))
    tips += list(range(tip0 + 8, tip1 + 1, 2))
    tips = sorted(set(t for t in tips if t >= signal_i and t < n))
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
            tip_conf=tip_conf,
            start_from_i=sf,
            mode=mode,
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
        "lag_le_30": lag_min is not None and lag_min <= 30,
        "scanned_tips": len(tips),
        "conf": conf,
        "tip_conf": tip_conf,
        "mode": mode,
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


def _summarize(results: list[dict]) -> dict:
    lags = [r["lag_min"] for r in results if r.get("lag_min") is not None]
    return {
        "n_total": len(results),
        "n_tip_fire": sum(1 for r in results if r.get("tip_fire")),
        "n_lag_le_30": sum(1 for r in results if r.get("lag_le_30")),
        "n_hit": sum(1 for r in results if r.get("lag_bars") is not None),
        "n_miss": sum(1 for r in results if r.get("lag_bars") is None),
        "lag_min_median": float(pd.Series(lags).median()) if lags else None,
        "lag_min_min": float(min(lags)) if lags else None,
    }


def tip_smoke(
    symbols: list[str],
    model,
    *,
    conf: float,
    tip_conf: float | None,
    mode: str = "tip",
) -> dict:
    """Force tip-only (or live) scan at each series' current tip; count fires."""
    rows = []
    for sym in symbols:
        try:
            frame = load_frame(sym)
        except FileNotFoundError as exc:
            rows.append({"symbol": sym, "error": str(exc), "fired": False})
            continue
        if len(frame) < 500:
            rows.append({"symbol": sym, "error": "too_short", "fired": False})
            continue
        tip_i = len(frame) - 1
        start_from_i = max(0, tip_i - 5)
        hits = scan_series_with_yolo(
            frame,
            model,
            conf=conf,
            tip_conf=tip_conf,
            start_from_i=start_from_i,
            mode=mode,
        )
        tipish = [i for i in hits if i >= tip_i - 1]
        rows.append(
            {
                "symbol": sym,
                "tip_i": tip_i,
                "n_hits": len(hits),
                "tipish_hits": tipish,
                "fired": bool(tipish),
                "open_time": str(frame["open_time"].iloc[tip_i])
                if "open_time" in frame.columns
                else "",
            }
        )
        print(
            f"  smoke {sym}: fired={bool(tipish)} tipish={tipish} n_hits={len(hits)}",
            flush=True,
        )
    return {
        "mode": mode,
        "conf": conf,
        "tip_conf": tip_conf,
        "n_symbols": len(rows),
        "n_fired": sum(1 for r in rows if r.get("fired")),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="", help="e.g. EDEN_USDT_SWAP")
    ap.add_argument("--from-log", action="store_true", help="use forward_log.csv rows")
    ap.add_argument("--log", type=Path, default=PROJECT / "data" / "forward_log.csv")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument(
        "--tip-conf",
        type=float,
        default=None,
        help="tip-window conf floor (e.g. 0.22); default None = shared --conf",
    )
    ap.add_argument(
        "--mode",
        choices=("live", "tip"),
        default="live",
        help="scan mode for lag walk (default live)",
    )
    ap.add_argument(
        "--compare",
        action="store_true",
        help="run both live@conf and tip@tip-conf (or tip@conf) and summarize",
    )
    ap.add_argument(
        "--tip-smoke",
        action="store_true",
        help="force tip scan at current tip for log/list symbols (no lag walk)",
    )
    ap.add_argument("--max-lag-bars", type=int, default=80)
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "analysis" / "output" / "diag_detect_lag.json",
    )
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
        if args.log.exists():
            targets = rows_from_log(args.log, args.symbol)
        if not targets:
            targets = [{"symbol": args.symbol, "signal_time": "", "signal_i": ""}]
    else:
        print("pass --symbol or --from-log", file=sys.stderr)
        return 2

    tip_conf = args.tip_conf
    payload: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(args.weights),
        "conf": args.conf,
        "tip_conf": tip_conf,
    }

    if args.tip_smoke:
        syms = sorted({r["symbol"] for r in targets})
        print(f"=== tip-smoke mode=tip symbols={len(syms)} tip_conf={tip_conf} ===", flush=True)
        smoke_tip = tip_smoke(syms, model, conf=args.conf, tip_conf=tip_conf, mode="tip")
        print(f"=== tip-smoke mode=live (control) ===", flush=True)
        smoke_live = tip_smoke(syms, model, conf=args.conf, tip_conf=None, mode="live")
        payload["tip_smoke"] = {"tip": smoke_tip, "live": smoke_live}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(
            f"smoke tip fired={smoke_tip['n_fired']}/{smoke_tip['n_symbols']} "
            f"live fired={smoke_live['n_fired']}/{smoke_live['n_symbols']}"
        )
        print(f"wrote {args.out}")
        return 0

    modes = [("live", args.conf, None), ("tip", args.conf, tip_conf)] if args.compare else [
        (args.mode, args.conf, tip_conf if args.mode == "tip" else None)
    ]

    by_mode: dict[str, list[dict]] = {}
    for mode, conf, tconf in modes:
        results = []
        print(f"=== lag-walk mode={mode} conf={conf} tip_conf={tconf} ===", flush=True)
        for rec in targets:
            sym = rec["symbol"]
            print(f"--- {sym} signal_time={rec.get('signal_time')} ---", flush=True)
            frame = load_frame(sym)
            if "open_time" not in frame.columns:
                raise SystemExit(f"{sym}: no open_time")
            times = pd.to_datetime(frame["open_time"], utc=True)

            if rec.get("signal_i") not in ("", None):
                # Prefer signal_time (stable); signal_i is tail-relative on VPS.
                if rec.get("signal_time"):
                    st = _parse_ts(rec["signal_time"])
                    hits = (times == st).to_numpy().nonzero()[0]
                    if len(hits) == 0:
                        diffs = (times - st).abs()
                        signal_i = int(diffs.argmin())
                    else:
                        signal_i = int(hits[0])
                else:
                    signal_i = int(float(rec["signal_i"]))
            elif rec.get("signal_time"):
                st = _parse_ts(rec["signal_time"])
                hits = (times == st).to_numpy().nonzero()[0]
                if len(hits) == 0:
                    diffs = (times - st).abs()
                    signal_i = int(diffs.argmin())
                else:
                    signal_i = int(hits[0])
            else:
                signal_i = len(frame) - 5

            start_from_i = max(0, signal_i - 5)
            r = earliest_hit(
                frame,
                signal_i,
                model,
                conf=conf,
                tip_conf=tconf,
                mode=mode,
                max_lag_bars=args.max_lag_bars,
                start_from_i=start_from_i,
            )
            r["symbol"] = sym
            r["signal_time"] = str(times.iloc[signal_i])
            r["log_detected_at"] = rec.get("detected_at", "")
            r["log_lag_min"] = None
            if rec.get("detected_at") and rec.get("signal_time"):
                try:
                    lag = (
                        _parse_ts(rec["detected_at"]) - _parse_ts(rec["signal_time"])
                    ).total_seconds() / 60
                    r["log_lag_min"] = round(lag, 1)
                except Exception:
                    pass
            r["window"] = WINDOW
            print(
                f"  mode={mode} lag_bars={r['lag_bars']} lag_min={r['lag_min']} "
                f"tip_fire={r['tip_fire']} lag_le_30={r['lag_le_30']} "
                f"log_lag_min={r['log_lag_min']}",
                flush=True,
            )
            results.append(r)
        by_mode[mode] = results

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload["results_by_mode"] = by_mode
    payload["summary_by_mode"] = {m: _summarize(rs) for m, rs in by_mode.items()}
    # Back-compat flat fields for the primary mode.
    primary = args.mode if not args.compare else "tip"
    primary_results = by_mode.get(primary) or next(iter(by_mode.values()))
    payload["results"] = primary_results
    payload["n_tip_fire"] = sum(1 for r in primary_results if r.get("tip_fire"))
    payload["n_total"] = len(primary_results)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    for m, s in payload["summary_by_mode"].items():
        print(
            f"summary {m}: tip_fire={s['n_tip_fire']}/{s['n_total']} "
            f"lag<=30={s['n_lag_le_30']} hit={s['n_hit']} miss={s['n_miss']} "
            f"lag_med={s['lag_min_median']}",
            flush=True,
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

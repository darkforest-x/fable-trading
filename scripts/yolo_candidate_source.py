"""YOLO-as-candidate-generator: build a judgment dataset whose candidate
signal bars come from the DETECTOR's boxes instead of the rule scan.

The A/B fairness contract: everything downstream (labeling, features,
train/val split, backtest, costs) is byte-identical to the rule-scan path
for the chosen side. ONLY the candidate source differs. For `--side short`,
features go through align_short_feature_rows (directional remaps) and labels
use label_short_candidate — same as build_dataset --side short.

Usage: PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --weights models/owner_best.pt --out data/judgment_yolo_swap.csv

Short-only (2026-07-24 owner pipeline; do not overwrite long pools):
  PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --side short --weights runs/detect/runs/detect/owner_side_short_v1/weights/best.pt \
    --out data/judgment_yolo_owner_side_short.csv

Time-bounded pilot (signal_time in [start, end); warmup bars before start still render):
  PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --side short --weights .../best.pt \
    --months 6 --end-before 2026-05-04 \
    --symbols-file analysis/output/yolo_short_10hv_6m_symbols.txt \
    --out data/judgment_yolo_owner_side_short_10hv_6m.csv

Optional: --workers N  (process pool; each worker loads its own YOLO weights).
On 16GB Macs prefer workers=1 + --resume + --chunk-series (see
scripts/run_yolo_short_pool_chunked.sh) — full-history scans OOM if one
process holds all series / writes only at the end.
Does not promote weights / does not touch holdout / does not change ACTIVE.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import list_series, load_series
from src.data.universe import is_stockish
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows_for_side
from src.judgment.labeling import HORIZON_BARS, label_candidate, label_short_candidate
from src.judgment.yolo_candidates import DEFAULT_CONF, load_yolo_model, scan_series_with_yolo

# Set in worker processes only.
_WORKER_MODEL = None
_WORKER_SIDE = "long"
_WORKER_SIGNAL_LO: pd.Timestamp | None = None
_WORKER_SIGNAL_HI: pd.Timestamp | None = None


def _list_swap_jobs(*, min_bars: int = 500) -> list[tuple[str, str, list[str]]]:
    """(source, symbol, path_strs) for OKX USDT-m swaps, non-stockish."""
    jobs: list[tuple[str, str, list[str]]] = []
    for (source, symbol), paths in sorted(list_series(bar="15m").items()):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        jobs.append((source, symbol, [str(p) for p in paths]))
    # Filter by min_bars after load would be ideal; quick path-count skip is ok —
    # workers re-check after load_series.
    _ = min_bars
    return jobs


def _done_path(out: Path) -> Path:
    return out.with_suffix(out.suffix + ".done_symbols")


def _load_done_symbols(out: Path) -> set[str]:
    """Symbols already scanned (incl. zero-candidate). Sidecar + CSV symbols."""
    done: set[str] = set()
    side = _done_path(out)
    if side.exists():
        done.update(
            ln.strip() for ln in side.read_text(encoding="utf-8").splitlines() if ln.strip()
        )
    if out.exists() and out.stat().st_size > 0:
        try:
            cols = pd.read_csv(out, usecols=["symbol"])
            done.update(cols["symbol"].astype(str).unique().tolist())
        except (ValueError, pd.errors.EmptyDataError):
            pass
    return done


def _mark_done(out: Path, symbol: str) -> None:
    side = _done_path(out)
    side.parent.mkdir(parents=True, exist_ok=True)
    with side.open("a", encoding="utf-8") as f:
        f.write(f"{symbol}\n")


def _append_records(out: Path, records: list[dict]) -> None:
    if not records:
        return
    df = pd.DataFrame(records)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out.exists() or out.stat().st_size == 0
    df.to_csv(out, mode="a", header=write_header, index=False)


def _parse_utc_ts(raw: str | None) -> pd.Timestamp | None:
    if raw is None or str(raw).strip() == "":
        return None
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _resolve_signal_window(args: argparse.Namespace) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Return (lo inclusive, hi exclusive). Prefer explicit --start/--end-time."""
    lo = _parse_utc_ts(getattr(args, "start_time", None))
    hi = _parse_utc_ts(getattr(args, "end_time", None))
    end_before = _parse_utc_ts(getattr(args, "end_before", None))
    months = int(getattr(args, "months", 0) or 0)
    if end_before is not None:
        if hi is not None and hi != end_before:
            raise SystemExit("--end-time and --end-before disagree")
        hi = end_before
    if months > 0:
        if hi is None:
            raise SystemExit("--months requires --end-before or --end-time")
        derived_lo = hi - pd.DateOffset(months=months)
        if lo is not None and lo != derived_lo:
            raise SystemExit("--start-time disagrees with --months/--end-before")
        lo = derived_lo
    return lo, hi


def _load_symbol_allowlist(args: argparse.Namespace) -> set[str] | None:
    """Optional symbol allowlist from --symbols and/or --symbols-file."""
    syms: set[str] = set()
    raw = getattr(args, "symbols", None)
    if raw:
        for part in str(raw).split(","):
            s = part.strip()
            if s:
                syms.add(s)
    path = getattr(args, "symbols_file", None)
    if path:
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"--symbols-file missing: {p}")
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                syms.add(s)
    return syms or None


def _process_one(
    source: str,
    symbol: str,
    frame: pd.DataFrame,
    model,
    *,
    side: str = "long",
    signal_time_lo: pd.Timestamp | None = None,
    signal_time_hi: pd.Timestamp | None = None,
) -> list[dict]:
    enriched_ind = add_indicators(frame)
    featured = add_features(enriched_ind)
    deduped = [
        si
        for si in scan_series_with_yolo(
            frame,
            model,
            conf=DEFAULT_CONF,
            signal_time_lo=signal_time_lo,
            signal_time_hi=signal_time_hi,
        )
        if si + 1 + HORIZON_BARS < len(frame)
    ]
    if not deduped:
        print(f"  {symbol}: 0 YOLO候选", flush=True)
        return []
    feat_rows = extract_feature_rows_for_side(featured, deduped, side)
    label_fn = label_short_candidate if side == "short" else label_candidate
    records: list[dict] = []
    for pos, signal_i in enumerate(deduped):
        # Keep historical YOLO pool barriers (TP5/SL2); do not change without owner.
        o = label_fn(enriched_ind, signal_i, tp_mult=5.0, sl_mult=2.0)
        if o is None:
            continue
        sig_t = enriched_ind["open_time"].iloc[signal_i]
        # Belt-and-suspenders: signal_time must land in [lo, hi).
        ts = pd.Timestamp(sig_t)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        if signal_time_lo is not None and ts < signal_time_lo:
            continue
        if signal_time_hi is not None and ts >= signal_time_hi:
            continue
        rec = {
            "source": source,
            "symbol": symbol,
            "side": side,
            "signal_i": signal_i,
            "signal_time": sig_t,
            "label": o.label,
            "outcome": o.outcome,
            "exit_offset": o.exit_offset,
            "entry_price": o.entry_price,
            "realized_ret": o.realized_ret,
        }
        rec.update(feat_rows.iloc[pos].to_dict())
        records.append(rec)
    print(f"  {symbol}: {len(records)} YOLO候选 ({side})", flush=True)
    return records


def _worker_init(
    weights: str,
    side: str,
    signal_lo_iso: str | None,
    signal_hi_iso: str | None,
) -> None:
    global _WORKER_MODEL, _WORKER_SIDE, _WORKER_SIGNAL_LO, _WORKER_SIGNAL_HI
    _WORKER_MODEL = load_yolo_model(weights)
    _WORKER_SIDE = side
    _WORKER_SIGNAL_LO = _parse_utc_ts(signal_lo_iso)
    _WORKER_SIGNAL_HI = _parse_utc_ts(signal_hi_iso)


def _worker_job(job: tuple[str, str, list[str]]) -> tuple[str, list[dict]]:
    source, symbol, path_strs = job
    frame = load_series([Path(p) for p in path_strs])
    if frame is None or len(frame) < 500:
        return symbol, []
    assert _WORKER_MODEL is not None
    return symbol, _process_one(
        source,
        symbol,
        frame,
        _WORKER_MODEL,
        side=_WORKER_SIDE,
        signal_time_lo=_WORKER_SIGNAL_LO,
        signal_time_hi=_WORKER_SIGNAL_HI,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="models/owner_best.pt")
    ap.add_argument(
        "--side",
        choices=("long", "short"),
        default="long",
        help="long = label_candidate; short = label_short_candidate (same TP5/SL2).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Default: data/judgment_yolo_swap.csv (long) or "
        "data/judgment_yolo_owner_side_short.csv (short).",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="process pool size (1 = sequential). M4: try 4–6. "
        "On 16GB prefer 1 + --resume/--chunk-series.",
    )
    ap.add_argument(
        "--max-series",
        type=int,
        default=0,
        help="If >0, consider only the first N swap series (sorted). 0 = all. "
        "Applied after --symbols/--symbols-file filter.",
    )
    ap.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated OKX swap symbols to scan (e.g. BTC_USDT_SWAP,ETH_USDT_SWAP).",
    )
    ap.add_argument(
        "--symbols-file",
        type=Path,
        default=None,
        help="Text file of symbols (one per line; # comments ok).",
    )
    ap.add_argument(
        "--start-time",
        default=None,
        help="Inclusive UTC lower bound for signal_time (ISO). Warmup may use earlier bars.",
    )
    ap.add_argument(
        "--end-time",
        default=None,
        help="Exclusive UTC upper bound for signal_time (ISO).",
    )
    ap.add_argument(
        "--end-before",
        default=None,
        help="Alias for --end-time (exclusive). Use with --months for a trailing window.",
    )
    ap.add_argument(
        "--months",
        type=int,
        default=0,
        help="If >0 with --end-before/--end-time: start-time = end - N months.",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip symbols already in out CSV / .done_symbols sidecar; "
        "append per-symbol instead of rewriting the whole file.",
    )
    ap.add_argument(
        "--chunk-series",
        type=int,
        default=0,
        help="If >0, process at most N not-yet-done series then exit "
        "(for outer chunked drivers that spawn a fresh Python each chunk).",
    )
    ap.add_argument(
        "--finalize",
        action="store_true",
        help="Sort out CSV by signal_time and print summary JSON "
        "(no scanning). Use after a resumed/chunked run completes.",
    )
    args = ap.parse_args()
    weights = str(Path(args.weights).resolve())
    out = args.out
    if out is None:
        out = (
            PROJECT_DIR / "data" / "judgment_yolo_owner_side_short.csv"
            if args.side == "short"
            else PROJECT_DIR / "data" / "judgment_yolo_swap.csv"
        )
    signal_lo, signal_hi = _resolve_signal_window(args)
    allow = _load_symbol_allowlist(args)

    if args.finalize:
        if not out.exists() or out.stat().st_size == 0:
            print(json.dumps({"side": args.side, "out": str(out), "candidates": 0}))
            return 0
        df = pd.read_csv(out)
        if len(df) and "signal_time" in df.columns:
            df = df.sort_values("signal_time").reset_index(drop=True)
            df.to_csv(out, index=False)
        print(
            json.dumps(
                {
                    "side": args.side,
                    "weights": weights,
                    "candidates": len(df),
                    "symbols": int(df["symbol"].nunique()) if len(df) else 0,
                    "pos_rate": round(float(df["label"].mean()), 4) if len(df) else None,
                    "out": str(out),
                    "features": list(FEATURE_COLUMNS),
                    "finalize": True,
                    "signal_time_lo": str(signal_lo) if signal_lo is not None else None,
                    "signal_time_hi": str(signal_hi) if signal_hi is not None else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    jobs = _list_swap_jobs()
    if allow is not None:
        jobs = [j for j in jobs if j[1] in allow]
        missing = sorted(allow - {j[1] for j in jobs})
        if missing:
            print(f"warn: symbols not in swap universe: {missing}", flush=True)
    if int(args.max_series) > 0:
        jobs = jobs[: int(args.max_series)]
    done_syms: set[str] = _load_done_symbols(out) if args.resume else set()
    if done_syms:
        before = len(jobs)
        jobs = [j for j in jobs if j[1] not in done_syms]
        print(
            f"resume: skip {before - len(jobs)} done symbols "
            f"({len(done_syms)} marked); remain {len(jobs)}",
            flush=True,
        )
    if int(args.chunk_series) > 0:
        jobs = jobs[: int(args.chunk_series)]
    n_series = len(jobs)
    print(
        f"swap series to scan: {n_series}  workers={max(1, int(args.workers))}  "
        f"side={args.side}  resume={bool(args.resume)}  "
        f"signal_window=[{signal_lo}, {signal_hi})",
        flush=True,
    )
    if n_series == 0:
        print(json.dumps({"side": args.side, "series": 0, "out": str(out), "noop": True}))
        return 0

    workers = max(1, int(args.workers))
    # Checkpoint path (resume or chunk): append per symbol so Jetsam ≠ zero progress.
    checkpoint = bool(args.resume) or int(args.chunk_series) > 0
    records: list[dict] = []
    scanned = 0

    if workers == 1:
        model = load_yolo_model(weights)
        for source, symbol, path_strs in jobs:
            frame = load_series([Path(p) for p in path_strs])
            if frame is None or len(frame) < 500:
                if checkpoint:
                    _mark_done(out, symbol)
                scanned += 1
                continue
            recs = _process_one(
                source,
                symbol,
                frame,
                model,
                side=args.side,
                signal_time_lo=signal_lo,
                signal_time_hi=signal_hi,
            )
            if checkpoint:
                _append_records(out, recs)
                _mark_done(out, symbol)
                del frame, recs
                gc.collect()
            else:
                records.extend(recs)
            scanned += 1
            if scanned % 10 == 0 or scanned == n_series:
                print(f"  … progress {scanned}/{n_series}", flush=True)
    else:
        if checkpoint:
            raise SystemExit(
                "--resume/--chunk-series require --workers 1 "
                "(pool path does not per-symbol checkpoint yet)"
            )
        # macOS spawn: re-import module; init loads YOLO once per worker.
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(
                weights,
                args.side,
                str(signal_lo) if signal_lo is not None else None,
                str(signal_hi) if signal_hi is not None else None,
            ),
        ) as pool:
            futs = {pool.submit(_worker_job, job): job for job in jobs}
            done = 0
            for fut in as_completed(futs):
                _symbol, recs = fut.result()
                records.extend(recs)
                done += 1
                if done % 10 == 0 or done == n_series:
                    print(f"  … progress {done}/{n_series}", flush=True)

    summary_extra = {
        "signal_time_lo": str(signal_lo) if signal_lo is not None else None,
        "signal_time_hi": str(signal_hi) if signal_hi is not None else None,
        "symbols_filter": sorted(allow) if allow is not None else None,
    }

    if checkpoint:
        n_cand = 0
        n_sym = 0
        pos_rate = None
        if out.exists() and out.stat().st_size > 0:
            df_all = pd.read_csv(out)
            n_cand = len(df_all)
            n_sym = int(df_all["symbol"].nunique()) if n_cand else 0
            pos_rate = round(float(df_all["label"].mean()), 4) if n_cand else None
        print(
            json.dumps(
                {
                    "side": args.side,
                    "weights": weights,
                    "series_this_chunk": n_series,
                    "candidates_so_far": n_cand,
                    "symbols_so_far": n_sym,
                    "pos_rate": pos_rate,
                    "out": str(out),
                    "workers": workers,
                    "checkpoint": True,
                    **summary_extra,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    df = pd.DataFrame(records)
    if len(df):
        df = df.sort_values("signal_time").reset_index(drop=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(
        json.dumps(
            {
                "side": args.side,
                "weights": weights,
                "series": n_series,
                "candidates": len(df),
                "symbols": int(df["symbol"].nunique()) if len(df) else 0,
                "pos_rate": round(float(df["label"].mean()), 4) if len(df) else None,
                "out": str(out),
                "workers": workers,
                "features": list(FEATURE_COLUMNS),
                **summary_extra,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    # Required for ProcessPoolExecutor on some platforms when launched via -u.
    raise SystemExit(main())

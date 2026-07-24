"""YOLO-as-candidate-generator: build a judgment dataset whose candidate
signal bars come from the DETECTOR's boxes instead of the rule scan.

The A/B fairness contract: everything downstream (labeling, features,
train/val split, backtest, costs) is byte-identical to the rule-scan path
for the chosen side. ONLY the candidate source differs.

Usage: PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --weights models/owner_best.pt --out data/judgment_yolo_swap.csv

Short-only (2026-07-24 owner pipeline; do not overwrite long pools):
  PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --side short --weights runs/detect/runs/detect/owner_side_short_v1/weights/best.pt \
    --out data/judgment_yolo_owner_side_short.csv

Optional: --workers N  (process pool; each worker loads its own YOLO weights).
Does not promote weights / does not touch holdout / does not change ACTIVE.
"""
from __future__ import annotations

import argparse
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
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import HORIZON_BARS, label_candidate, label_short_candidate
from src.judgment.yolo_candidates import DEFAULT_CONF, load_yolo_model, scan_series_with_yolo

# Set in worker processes only.
_WORKER_MODEL = None
_WORKER_SIDE = "long"


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


def _process_one(
    source: str, symbol: str, frame: pd.DataFrame, model, *, side: str = "long"
) -> list[dict]:
    enriched_ind = add_indicators(frame)
    featured = add_features(enriched_ind)
    deduped = [
        si
        for si in scan_series_with_yolo(frame, model, conf=DEFAULT_CONF)
        if si + 1 + HORIZON_BARS < len(frame)
    ]
    if not deduped:
        print(f"  {symbol}: 0 YOLO候选", flush=True)
        return []
    feat_rows = extract_feature_rows(featured, deduped)
    label_fn = label_short_candidate if side == "short" else label_candidate
    records: list[dict] = []
    for pos, signal_i in enumerate(deduped):
        # Keep historical YOLO pool barriers (TP5/SL2); do not change without owner.
        o = label_fn(enriched_ind, signal_i, tp_mult=5.0, sl_mult=2.0)
        if o is None:
            continue
        rec = {
            "source": source,
            "symbol": symbol,
            "side": side,
            "signal_i": signal_i,
            "signal_time": enriched_ind["open_time"].iloc[signal_i],
            "label": o.label,
            "outcome": o.outcome,
            "exit_offset": o.exit_offset,
            "entry_price": o.entry_price,
            "realized_ret": o.realized_ret,
        }
        rec.update(feat_rows.iloc[pos].to_dict())
        records.append(rec)
    print(f"  {symbol}: {len(deduped)} YOLO候选 ({side})", flush=True)
    return records


def _worker_init(weights: str, side: str) -> None:
    global _WORKER_MODEL, _WORKER_SIDE
    _WORKER_MODEL = load_yolo_model(weights)
    _WORKER_SIDE = side


def _worker_job(job: tuple[str, str, list[str]]) -> tuple[str, list[dict]]:
    source, symbol, path_strs = job
    frame = load_series([Path(p) for p in path_strs])
    if frame is None or len(frame) < 500:
        return symbol, []
    assert _WORKER_MODEL is not None
    return symbol, _process_one(source, symbol, frame, _WORKER_MODEL, side=_WORKER_SIDE)


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
        help="process pool size (1 = sequential). M4: try 4–6.",
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

    jobs = _list_swap_jobs()
    n_series = len(jobs)
    print(
        f"swap series to scan: {n_series}  workers={max(1, int(args.workers))}  "
        f"side={args.side}",
        flush=True,
    )

    records: list[dict] = []
    workers = max(1, int(args.workers))
    if workers == 1:
        model = load_yolo_model(weights)
        for source, symbol, path_strs in jobs:
            frame = load_series([Path(p) for p in path_strs])
            if frame is None or len(frame) < 500:
                continue
            records.extend(_process_one(source, symbol, frame, model, side=args.side))
    else:
        # macOS spawn: re-import module; init loads YOLO once per worker.
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(weights, args.side),
        ) as pool:
            futs = {pool.submit(_worker_job, job): job for job in jobs}
            done = 0
            for fut in as_completed(futs):
                _symbol, recs = fut.result()
                records.extend(recs)
                done += 1
                if done % 10 == 0 or done == n_series:
                    print(f"  … progress {done}/{n_series}", flush=True)

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
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    # Required for ProcessPoolExecutor on some platforms when launched via -u.
    raise SystemExit(main())

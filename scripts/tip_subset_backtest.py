"""Tip-subset backtest: what does the mainline val-window backtest earn if we
only keep signals the LIVE pipeline could have caught at the tip?

Owner-authorized overnight offline work 2026-07-20. Question: the published
baseline (analysis/output/p_weight_centric_val.json, baseline_binary) books
val net +141% on 10 units / PF 7.63 / win 81.6%, but the v11 pool's signals
come from a strided full-history scan — many boxes sit mid-window, i.e. they
were only detectable in hindsight. Live trading can only enter signals the
detector fires ON the tip bar (realtime tip path + 30-min freshness gate).
This experiment filters the SAME eligible signals down to "v12 fires with the
box right edge on the tip bar when the window ends exactly at the signal bar"
and re-runs the identical portfolio simulation. Single variable: the sample
subset. Threshold / costs / exits / sizing (w=1) / capital untouched.

Tip protocol (mirrors live geometry, cf. scripts/tip_detectability.py):
  re-render the 200-bar window whose LAST bar is the signal bar, with MAs
  computed on the full series before slicing (identical to live
  scan_series_with_yolo semantics, unlike tip_detectability's slice-then-MA),
  predict with the mainline v12 weights at conf=0.30, and map each box right
  edge to a bar via right_edge_to_bar. Recorded per signal:
    - tip_hit_strict: some box's right edge maps to the tip bar itself
      (bar 199) — the only geometry that passes the 30-min freshness gate
      (bar 198 is already 31..38 min old at accounting time);
    - tip_hit_92: cx + w/2 >= 0.92 (tip_detectability.py protocol, ~last 16
      bars) — reported as a loose sensitivity bound only.

Three stages, three PROCESSES (lightgbm imported before the first ultralytics
predict segfaults this venv — docs/learnings/lightgbm-import-before-
ultralytics-predict-segfaults.md):
  1. score    (lightgbm): frozen v11 artifact scores the v11 pool, writes all
               eligible (score >= val-q90, entry < HOLDOUT_START) signals.
  2. rerender (torch only, NO lightgbm anywhere in the import chain): tip
               re-render + v12 predict for every eligible signal, sequential,
               one symbol at a time, predict batch=1 / workers=0, checkpoint
               CSV after each symbol (resume-safe; 16GB RSS discipline).
  3. backtest (pandas/lightgbm ok): same simulate()/window_metrics() as
               scripts/weight_centric_backtest.py on full vs tip subsets.

Scope guards: entries strictly < 2026-05-04 (holdout never simulated), accept
window never touched, no production defaults changed.

Usage:
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=. \
        .venv/bin/python scripts/tip_subset_backtest.py --stage score
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=. \
        .venv/bin/python scripts/tip_subset_backtest.py --stage rerender [--limit N]
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=. \
        .venv/bin/python scripts/tip_subset_backtest.py --stage backtest
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"
ELIGIBLE_CSV = OUTPUT_DIR / "tip_subset_eligible.csv"
RERENDER_CSV = OUTPUT_DIR / "tip_subset_rerender.csv"
META_JSON = OUTPUT_DIR / "tip_subset_meta.json"
RESULT_JSON = OUTPUT_DIR / "p_tip_subset_backtest.json"

WINDOW = 200
TIP_CONF = 0.30  # == DEFAULT_CONF used by the live scan
WEIGHTS = PROJECT_DIR / "models" / "owner_best.pt"  # mainline v12 (H-TIP)
COSTS = (0.002, 0.003)


# --------------------------------------------------------------------------
# stage 1: score (lightgbm process)
# --------------------------------------------------------------------------

def stage_score() -> int:
    from scripts.weight_centric_backtest import build_scored_pool
    from src.judgment.frozen import default_config, latest_artifact
    from src.judgment.train import HOLDOUT_START

    signals, threshold, _train, val = build_scored_pool()
    eligible = signals[signals["score"] >= threshold].copy()
    keep = [
        "source", "symbol", "signal_i", "signal_time", "entry_time",
        "exit_time", "score", "outcome", "exit_offset", "realized_ret",
    ]
    eligible = eligible[keep].sort_values(["entry_time", "score"], ascending=[True, False])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    eligible.to_csv(ELIGIBLE_CSV, index=False)

    artifact = latest_artifact(default_config())
    assert artifact is not None
    val_start = val["signal_time"].min() + pd.Timedelta(minutes=15)
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frozen_artifact": artifact.metadata_path.name,
        "dataset": artifact.relative_dataset_path,
        "threshold_val_q90": threshold,
        "val_start": str(val_start),
        "holdout_start": str(HOLDOUT_START),
        "n_eligible": int(len(eligible)),
        "n_eligible_val": int((eligible["entry_time"] >= val_start).sum()),
        "yolo_weights": str(WEIGHTS),
    }
    META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# stage 2: rerender (torch-only process — no lightgbm in the import chain)
# --------------------------------------------------------------------------

def _locate_signal(frame: pd.DataFrame, signal_i: int, signal_time: pd.Timestamp) -> int | None:
    """Trust signal_i if open_time matches; otherwise re-locate by timestamp
    (data files are append-only, but be defensive)."""
    if 0 <= signal_i < len(frame) and frame["open_time"].iloc[signal_i] == signal_time:
        return signal_i
    hits = np.flatnonzero((frame["open_time"] == signal_time).to_numpy())
    return int(hits[0]) if len(hits) else None


def stage_rerender(limit: int) -> int:
    import gc
    import os

    from src.data.loader import list_series, load_series
    from src.detection.data import add_mas
    from src.detection.render import render_chart
    from src.judgment.yolo_candidates import load_yolo_model, right_edge_to_bar

    def score_one_png(model, png: Path, tf) -> dict:
        """Single-image predict (batch=1) to keep RSS under ~4GB on 16GB machines."""
        res = model.predict(
            str(png), conf=TIP_CONF, verbose=False, device="cpu", workers=0
        )[0]
        n_boxes = 0
        max_right_bar = -1
        max_right_norm = 0.0
        max_conf = 0.0
        tip_conf = 0.0
        if res.boxes is not None and len(res.boxes):
            n_boxes = len(res.boxes)
            xywhn = res.boxes.xywhn.cpu().numpy()
            confs = res.boxes.conf.cpu().numpy()
            for (cx, _, w, _), c in zip(xywhn, confs):
                bar = right_edge_to_bar(float(cx), float(w), tf, n_bars=WINDOW)
                max_right_bar = max(max_right_bar, bar)
                max_right_norm = max(max_right_norm, float(cx + w / 2))
                max_conf = max(max_conf, float(c))
                if bar >= WINDOW - 1:
                    tip_conf = max(tip_conf, float(c))
        return {
            "rerender_ok": True,
            "skip_reason": "",
            "n_boxes": n_boxes,
            "max_right_bar": max_right_bar,
            "max_right_norm": round(max_right_norm, 4),
            "max_conf": round(max_conf, 4),
            "tip_conf": round(tip_conf, 4),
            "tip_hit_strict": bool(max_right_bar >= WINDOW - 1),
            "tip_hit_92": bool(max_right_norm >= 0.92),
        }

    eligible = pd.read_csv(ELIGIBLE_CSV, parse_dates=["signal_time", "entry_time", "exit_time"])
    if limit > 0:
        eligible = eligible.head(limit)

    # Resume: skip (source, symbol, signal_i) already on disk from a prior run.
    done_keys: set[tuple[str, str, int]] = set()
    rows: list[dict] = []
    if RERENDER_CSV.exists():
        prev = pd.read_csv(RERENDER_CSV)
        if len(prev):
            rows = prev.to_dict("records")
            done_keys = {
                (str(r["source"]), str(r["symbol"]), int(r["signal_i"])) for r in rows
            }
            print(f"  resume: {len(done_keys)} signals already on disk", flush=True)

    remaining = eligible[
        ~eligible.apply(
            lambda r: (r["source"], r["symbol"], int(r["signal_i"])) in done_keys, axis=1
        )
    ]
    if remaining.empty:
        print("  nothing left to rerender", flush=True)
        out = pd.DataFrame(rows)
    else:
        series_paths = {
            (src, sym): [str(p) for p in paths]
            for (src, sym), paths in list_series(bar="15m").items()
        }
        model = load_yolo_model(WEIGHTS)
        # Per-pid tmp so a crashed sibling cannot leave thousands of PNGs shared.
        tmp_dir = PROJECT_DIR / "data" / f"_tip_subset_tmp_{os.getpid()}"
        if tmp_dir.exists():
            for stale in tmp_dir.glob("*.png"):
                stale.unlink(missing_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        groups = list(remaining.groupby(["source", "symbol"], sort=True))
        n_groups_total = eligible.groupby(["source", "symbol"]).ngroups
        for gi, ((source, symbol), grp) in enumerate(groups, 1):
            key = (source, symbol)
            sym_rows: list[dict] = []
            if key not in series_paths:
                sym_rows.extend(
                    {"source": source, "symbol": symbol, "signal_i": int(r.signal_i),
                     "rerender_ok": False, "skip_reason": "series_missing"}
                    for r in grp.itertuples()
                )
            else:
                frame = load_series([Path(p) for p in series_paths[key]])
                enriched = add_mas(frame)  # full-series MAs, then slice == live semantics
                for r in grp.itertuples():
                    base = {"source": source, "symbol": symbol, "signal_i": int(r.signal_i)}
                    pos = _locate_signal(frame, int(r.signal_i), r.signal_time)
                    if pos is None:
                        sym_rows.append({**base, "rerender_ok": False,
                                         "skip_reason": "signal_time_mismatch"})
                        continue
                    start = pos - WINDOW + 1
                    if start < 0:
                        sym_rows.append({**base, "rerender_ok": False,
                                         "skip_reason": "window_short"})
                        continue
                    sub = enriched.iloc[start : pos + 1]
                    png = tmp_dir / f"{symbol}_{pos}.png"
                    try:
                        _, tf = render_chart(sub, out_path=png)
                        scored = score_one_png(model, png, tf)
                        sym_rows.append({**base, **scored})
                    except Exception as exc:  # noqa: BLE001 — keep the sweep alive
                        sym_rows.append({**base, "rerender_ok": False,
                                         "skip_reason": f"render_or_predict:{exc}"})
                    finally:
                        png.unlink(missing_ok=True)
                del frame, enriched
                gc.collect()

            rows.extend(sym_rows)
            # Checkpoint after every symbol — OOM mid-run must not lose progress.
            pd.DataFrame(rows).to_csv(RERENDER_CSV, index=False)
            if gi % 5 == 0 or gi == len(groups):
                hit = sum(1 for r in rows if r.get("tip_hit_strict") in (True, "True", 1))
                n_png = len(list(tmp_dir.glob("*.png")))
                print(
                    f"  … {gi}/{len(groups)} remaining symbols "
                    f"(~{n_groups_total} total), {len(rows)} signals, "
                    f"strict_hits={hit}, tmp_png={n_png}",
                    flush=True,
                )

        # Best-effort cleanup of per-pid tmp.
        for stale in tmp_dir.glob("*.png"):
            stale.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        out = pd.DataFrame(rows)

    ok = out[out["rerender_ok"] == True]  # noqa: E712
    summary = {
        "n_signals": int(len(out)),
        "n_rerendered": int(len(ok)),
        "n_skipped": int(len(out) - len(ok)),
        "tip_hit_strict": int(ok["tip_hit_strict"].astype(bool).sum()) if len(ok) else 0,
        "tip_hit_92": int(ok["tip_hit_92"].astype(bool).sum()) if len(ok) else 0,
        "strict_rate": round(float(ok["tip_hit_strict"].astype(bool).mean()), 4) if len(ok) else None,
        "rate_92": round(float(ok["tip_hit_92"].astype(bool).mean()), 4) if len(ok) else None,
        "weights": str(WEIGHTS),
        "conf": TIP_CONF,
        "predict_batch": 1,
        "workers": 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# stage 3: backtest (same machinery as weight_centric_backtest)
# --------------------------------------------------------------------------

def stage_backtest() -> int:
    from scripts.weight_centric_backtest import monthly_net, simulate, window_metrics

    meta = json.loads(META_JSON.read_text(encoding="utf-8"))
    threshold = float(meta["threshold_val_q90"])
    val_start = pd.Timestamp(meta["val_start"])
    holdout_start = pd.Timestamp(meta["holdout_start"])

    eligible = pd.read_csv(ELIGIBLE_CSV, parse_dates=["signal_time", "entry_time", "exit_time"])
    rer = pd.read_csv(RERENDER_CSV)
    merged = eligible.merge(
        rer, on=["source", "symbol", "signal_i"], how="left", validate="one_to_one"
    )
    assert merged["entry_time"].max() < holdout_start, "holdout leak — refuse to run"
    merged["tip_hit_strict"] = merged["tip_hit_strict"].fillna(False).astype(bool)
    merged["tip_hit_92"] = merged["tip_hit_92"].fillna(False).astype(bool)
    train_start = merged["entry_time"].min()

    subsets = {
        "full_baseline": merged,
        "tip_strict": merged[merged["tip_hit_strict"]],
        "tip_92_loose": merged[merged["tip_hit_92"]],
    }
    n_val = {
        name: int((sub["entry_time"] >= val_start).sum()) for name, sub in subsets.items()
    }
    results: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "eligible_counts": {
            "pre_holdout_total": int(len(merged)),
            "val_window": n_val["full_baseline"],
            "rerender_skipped": int((merged["rerender_ok"] != True).sum()),  # noqa: E712
            "tip_strict_total": int(len(subsets["tip_strict"])),
            "tip_strict_val": n_val["tip_strict"],
            "tip_92_total": int(len(subsets["tip_92_loose"])),
            "tip_92_val": n_val["tip_92"],
        },
        "variants": {},
    }
    for name, sub in subsets.items():
        trades = simulate(sub, threshold, np.ones(len(sub)))
        results["variants"][name] = {
            "val": {
                f"cost_{c:.3f}": window_metrics(trades, c, val_start, holdout_start)
                for c in COSTS
            },
            "train_insample": {
                f"cost_{c:.3f}": window_metrics(trades, c, train_start, val_start)
                for c in COSTS
            },
            "val_monthly_net_cost_0.003": monthly_net(trades, 0.003, val_start, holdout_start),
        }
        trades.to_csv(OUTPUT_DIR / f"p_tip_subset_{name}_trades.csv", index=False)

    RESULT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=["score", "rerender", "backtest"], required=True)
    ap.add_argument("--limit", type=int, default=0, help="rerender smoke-test cap (0 = all)")
    args = ap.parse_args()
    if args.stage == "score":
        return stage_score()
    if args.stage == "rerender":
        return stage_rerender(args.limit)
    return stage_backtest()


if __name__ == "__main__":
    raise SystemExit(main())

"""Expand the frozen IMACD/YOLO experiment to an existing 54-symbol pool.

Source: exp-imacd-yolo-expanded-20260908-v1/PROJECT_PLAN.md. Only evaluation
coverage changes. Candidate features use the entire available OHLCV prefix,
through each signal close; inference and decisions reuse the frozen native
timeframe engine. The two chronological cohorts have separate follow-up ends.
No training, economic exits, parameter search or production changes occur.

Each symbol/timeframe/cohort shard is written before a hashed completion
receipt. Resume accepts only complete, unchanged shards with the identical
source manifest and input prefix. Incomplete shards fail closed; they must be
reviewed explicitly rather than silently replayed or mixed into a summary.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from . import imacd_yolo_timeframes as frozen

ROOT = frozen.ROOT
EXP = ROOT / "experiments/active/exp-imacd-yolo-expanded-20260908-v1"
DATA = ROOT / "data/imacd_yolo_expanded_20260908_v1"
START = pd.Timestamp("2026-01-01", tz="UTC")
CUT = pd.Timestamp("2026-05-04", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
FOLDS = {"pre_holdout": (START, CUT), "holdout_review": (CUT, END)}
PERIODS = (60, 240)
CANDIDATE_COLUMNS = frozen.CANDIDATE_COLUMNS + ["fold"]
digest = frozen.digest


def candidates(bars, features, symbol, minutes, fold):
    """Use causal focus state at p; retain censored events at cohort boundaries.

    Columns: OHLCV bars and release_side/focus_start_i/near_zero_bars from the
    frozen IMACD features through p. Cohort end only determines the offline
    availability of p+9 confirmation and p+10 entry; it is never a feature.
    """
    start, end = FOLDS[fold]
    frozen.ensure_grid(bars, minutes)
    step = pd.Timedelta(minutes=minutes)
    if len(bars) and bars.index[-1] + step > end:
        raise ValueError("truncate bar prefix at cohort end")
    rows = []
    for p in np.flatnonzero(features.release_side.to_numpy()):
        available = bars.index[p] + step
        if not start <= available < end:
            continue
        row = features.iloc[p]
        a = int(row.focus_start_i)
        if not 0 <= a < p or int(row.near_zero_bars) != p-a:
            raise ValueError("invalid frozen setup")
        rows.append(dict(event_id=f"{symbol}_{minutes}_{int(bars.index[p].timestamp())}",
            symbol=symbol, timeframe_min=minutes, signal_i=int(p),
            side=int(row.release_side), setup_start_i=a, setup_bars=p-a,
            signal_open_at=bars.index[p], signal_available_at=available,
            signal_close=float(bars.close.iloc[p]),
            complete_followup=p+frozen.MAX_WAIT+1 < len(bars), fold=fold))
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def stats_for(frame, **identity):
    """Describe gate retention and entry displacement, never return or accuracy."""
    full = frame.loc[frame.complete_followup.eq(True)]
    kept = full.loc[full.status.eq("confirmed")]
    delayed = kept.loc[kept.delay_bars.gt(0)]
    def metric(column, which="median", group=kept):
        return float(getattr(group[column], which)()) if len(group) else None
    return dict(**identity, arrows=len(frame), complete=len(full), confirmed=len(kept),
        invalidated=int(full.status.eq("invalidated").sum()),
        expired=int(full.status.eq("expired").sum()), censored=len(frame)-len(full),
        pass_rate_pct=100*len(kept)/len(full) if len(full) else None,
        confirmed_long=int(kept.side.eq(1).sum()), confirmed_short=int(kept.side.eq(-1).sum()),
        immediate=int(kept.delay_bars.eq(0).sum()), delayed=len(delayed),
        confirmed_symbols=int(kept.symbol.nunique()),
        delay_median_bars=metric("delay_bars"), delay_max_bars=metric("delay_bars", "max"),
        displacement_median_bp=metric("displacement_bp"),
        displacement_max_bp=metric("displacement_bp", "max"),
        delayed_displacement_median_bp=metric("displacement_bp", group=delayed),
        adverse_delays=int(delayed.displacement_bp.gt(0).sum()),
        favorable_delays=int(delayed.displacement_bp.lt(0).sum()))


def grouped_stats(frame, columns):
    return [stats_for(g, **dict(zip(columns, key if isinstance(key, tuple) else (key,))))
            for key, g in frame.groupby(columns, sort=True)]


def direction_nulls(decisions):
    """Four predeclared cohort/period tests, Holm corrected as one family."""
    results = {}
    for fold in FOLDS:
        for minutes in PERIODS:
            frame = decisions.loc[decisions.fold.eq(fold) & decisions.timeframe_min.eq(minutes)]
            results[f"{minutes}_{fold}"] = frozen.original.direction_null(frame)
    ordered = sorted(results, key=lambda key: results[key]["p_one_sided"])
    previous = 0.
    for rank, key in enumerate(ordered):
        previous = max(previous, min(1., (len(ordered)-rank)*results[key]["p_one_sided"]))
        results[key]["p_holm"] = previous
    return results


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    temporary.replace(path)


def load_completed(key, identity):
    """Resume only verified shards; reject a partial or changed evaluation."""
    receipt_path = DATA/f"{key}_receipt.json"
    existing = list(DATA.glob(f"{key}_*"))
    if not receipt_path.exists():
        if existing:
            raise ValueError(f"incomplete shard requires explicit review: {key}")
        return None
    receipt = json.loads(receipt_path.read_text())
    if receipt["identity"] != identity:
        raise ValueError(f"resume identity mismatch: {key}")
    expected = {f"{key}_{suffix}" for suffix in (
        "candidates.csv", "decisions.csv", "proposals.csv.gz", "trace.csv.gz")}
    if set(receipt["files"]) != expected:
        raise ValueError(f"incomplete shard manifest: {key}")
    for name, sha in receipt["files"].items():
        if digest(DATA/name) != sha:
            raise ValueError(f"changed shard file: {name}")
    return receipt


def run():
    """Run/resume this single preregistered expanded evaluation on Mac MPS."""
    import torch
    from ultralytics import YOLO
    manifest_path = EXP/"source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for relative, sha in manifest["files"].items():
        if digest(ROOT/relative) != sha:
            raise ValueError(f"source changed: {relative}")
    own = [str(Path(__file__).relative_to(ROOT)),
           str((EXP/"PROJECT_PLAN.md").relative_to(ROOT)), str(manifest_path.relative_to(ROOT))]
    if subprocess.check_output(["git", "status", "--porcelain", "--", *own], cwd=ROOT, text=True).strip():
        raise ValueError("commit source and plan before evaluation")
    universe = json.loads((EXP/"universe.json").read_text())
    if len(universe["sources"]) != 54 or len({s["symbol"] for s in universe["sources"]}) != 54:
        raise ValueError("frozen 54-symbol pool required")
    if digest(frozen.MODEL_PATH) != frozen.MODEL_SHA256 or not torch.backends.mps.is_available():
        raise ValueError("pinned model and MPS required")
    DATA.mkdir(parents=True, exist_ok=True)
    results_dir = EXP/"results"
    results_dir.mkdir(parents=True, exist_ok=True)
    started_path = results_dir/"run_started.json"
    source_sha = digest(manifest_path)
    if started_path.exists():
        started = json.loads(started_path.read_text())
        if started["source_manifest_sha256"] != source_sha:
            raise ValueError("cannot resume with different source")
    else:
        started = dict(started_at=pd.Timestamp.now(tz="UTC").isoformat(),
            source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            source_manifest_sha256=source_sha, owner_request="多测试一点数据啊",
            authorization="任何时间段数据都可以使用不要有任何限制",
            holdout_consumptions={"60":2, "240":2}, this_expansion_holdout_passes=1,
            start=START.isoformat(), end_exclusive=END.isoformat(),
            note="Second holdout evaluation of each unchanged timeframe configuration; no blind claim.")
        with started_path.open("x") as handle:
            json.dump(started, handle, indent=2, ensure_ascii=False)
    model = YOLO(str(frozen.MODEL_PATH))
    if model.names != {0:"dense_long", 1:"dense_short"}:
        raise ValueError("unexpected model classes")
    inputs, receipts, total = {}, {}, 54*len(PERIODS)*len(FOLDS)
    for source in universe["sources"]:
        symbol, path = source["symbol"], ROOT/source["path"]
        if digest(path) != source["sha256"]:
            raise ValueError(f"source bytes changed: {symbol}")
        raw = frozen.read_prefix(path, end=END)
        if raw.index[-1] + pd.Timedelta(minutes=15) != END:
            raise ValueError(f"incomplete requested source coverage: {symbol}")
        values = raw[["open", "high", "low", "close", "volume"]].to_numpy(float)
        if (not np.isfinite(values).all() or (values[:, :4] <= 0).any()
                or (values[:, 4] < 0).any() or (raw.high < raw[["open", "close", "low"]].max(axis=1)).any()
                or (raw.low > raw[["open", "close", "high"]].min(axis=1)).any()):
            raise ValueError(f"invalid OHLCV: {symbol}")
        raw_sha = hashlib.sha256(raw.to_csv().encode()).hexdigest()
        for minutes in PERIODS:
            all_bars = frozen.aggregate(raw, minutes)
            frozen.ensure_grid(all_bars, minutes)
            all_features = frozen.build_features(all_bars)
            for fold, (start, end) in FOLDS.items():
                key = f"{symbol}_{minutes}_{fold}"
                bars = all_bars.loc[all_bars.index < end]
                features = all_features.iloc[:len(bars)]
                identity = dict(source_manifest_sha256=source_sha,
                    raw_bounded_ohlcv_sha256=raw_sha, key=key)
                receipt = load_completed(key, identity)
                if receipt is None:
                    events = candidates(bars, features, symbol, minutes, fold)
                    print(json.dumps(dict(group=key, group_number=len(receipts)+1, total=total,
                        arrows=len(events), phase="inference")), flush=True)
                    proposals, timing = frozen.infer(bars, events, symbol, model, "mps", minutes)
                    for name in ("windows_scored", "windows_with_boxes", "raw_boxes", "structural_boxes"):
                        timing.setdefault(name, 0)
                    proposals["fold"] = fold
                    decisions = frozen.decisions_for(events, proposals, bars, features, minutes)
                    decisions["fold"] = fold
                    first_bad = []
                    for event in decisions.itertuples(index=False):
                        p = int(event.signal_i)
                        md = features.md.iloc[p:p+10].to_numpy()
                        bad = np.flatnonzero(~np.isfinite(md) | (event.side*md <= 0))
                        first_bad.append(p+int(bad[0]) if len(bad) else None)
                    decisions["first_invalid_i"] = first_bad
                    trace = frozen.trace_for(events, bars, features)
                    trace["fold"] = fold
                    validation = frozen.validate_selection(decisions, proposals, minutes)
                    files = {}
                    for suffix, frame in (("candidates.csv", events), ("decisions.csv", decisions),
                                          ("proposals.csv.gz", proposals), ("trace.csv.gz", trace)):
                        target = DATA/f"{key}_{suffix}"
                        frame.to_csv(target, index=False)
                        files[target.name] = digest(target)
                    metadata = dict(raw_path=source["path"], minutes=minutes, fold=fold,
                        raw_rows=len(raw), raw_bounded_ohlcv_sha256=raw_sha,
                        parsed_rows=len(bars), parsed_first=bars.index[0].isoformat(),
                        parsed_last=bars.index[-1].isoformat(),
                        evaluation_bars=int(((bars.index+pd.Timedelta(minutes=minutes) >= start)
                                              & (bars.index+pd.Timedelta(minutes=minutes) < end)).sum()),
                        evaluation_ready_bars=int((features.ready &
                            (bars.index+pd.Timedelta(minutes=minutes) >= start) &
                            (bars.index+pd.Timedelta(minutes=minutes) < end)).sum()),
                        bounded_ohlcv_sha256=hashlib.sha256(bars.to_csv().encode()).hexdigest(),
                        discarded_partial_source_rows=len(raw)-len(all_bars)*(minutes//15), **timing)
                    receipt = dict(identity=identity, files=files, input=metadata, validation=validation,
                        table=stats_for(decisions, symbol=symbol, timeframe_min=minutes, fold=fold),
                        completed_at=pd.Timestamp.now(tz="UTC").isoformat())
                    atomic_json(DATA/f"{key}_receipt.json", receipt)
                receipts[key] = receipt
                inputs[key] = receipt["input"]
                atomic_json(results_dir/"progress.json", dict(completed_groups=len(receipts), total_groups=total,
                    completed_windows=sum(r["input"]["windows_scored"] for r in receipts.values()),
                    completed_arrows=sum(r["table"]["arrows"] for r in receipts.values()),
                    last_group=key, updated_at=pd.Timestamp.now(tz="UTC").isoformat()))
    combined = {}
    for suffix in ("candidates.csv", "decisions.csv", "proposals.csv.gz", "trace.csv.gz"):
        frame = pd.concat([pd.read_csv(DATA/f"{key}_{suffix}") for key in receipts], ignore_index=True)
        target_name = "traces.csv.gz" if suffix == "trace.csv.gz" else suffix
        frame.to_csv(DATA/target_name, index=False)
        combined[suffix] = frame
    decisions = combined["decisions.csv"]
    if decisions.event_id.duplicated().any() or combined["proposals.csv.gz"].detection_id.duplicated().any():
        raise ValueError("cross-shard identity collision")
    decisions["month"] = pd.to_datetime(decisions.signal_available_at, utc=True).dt.strftime("%Y-%m")
    print(json.dumps(dict(phase="direction_nulls", arrows=len(decisions))), flush=True)
    summary = dict(**started, model_sha256=frozen.MODEL_SHA256, max_wait_bars=9,
        symbol_count=54, symbols=[s["symbol"] for s in universe["sources"]],
        inputs=inputs, table=[r["table"] for r in receipts.values()],
        by_timeframe=grouped_stats(decisions, ["timeframe_min"]),
        by_fold_timeframe=grouped_stats(decisions, ["fold", "timeframe_min"]),
        by_month_timeframe=grouped_stats(decisions, ["month", "timeframe_min"]),
        by_direction_timeframe=grouped_stats(decisions, ["side", "timeframe_min"]),
        direction_null=direction_nulls(decisions),
        validation={key:r["validation"] for key,r in receipts.items()},
        versions={p:importlib.metadata.version(p) for p in ("torch","ultralytics","numpy","pandas")},
        files={str(p.relative_to(ROOT)):digest(p) for p in sorted(DATA.glob("*")) if p.is_file()},
        economic_evaluation=False, training_eligible=False, production_eligible=False,
        completed_at=pd.Timestamp.now(tz="UTC").isoformat())
    atomic_json(results_dir/"summary.json", summary)
    print(json.dumps(dict(by_timeframe=summary["by_timeframe"], direction_null=summary["direction_null"])), flush=True)


if __name__ == "__main__":
    run()

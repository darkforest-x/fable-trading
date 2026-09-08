"""Frozen native-chart timeframe transfer of the existing IMACD/YOLO gate.

Source: exp-imacd-yolo-timeframes-20260908-v1/PROJECT_PLAN.md. The only research
change from the frozen 15m pilot is applying the same per-bar rules to complete
UTC 1H and 4H candles. Each interval recomputes its MAs and IMACD from its own
full available history. Images use W18/W19 rows ending on the actual decision
candle. The nine-local-bar wait is 9h/36h, not a fixed 135-minute wait.

The candidate, decision and infer implementations below are literal, frozen
adaptations of imacd_yolo_confirmation.py, with explicit minutes, timeframe
identity, UTC clocks and additional audit traces. The old experiment source is
unchanged. No outcome, profit, training, parameter search or live notification
is computed. New saved traces include md[p:p+9] and next-open observations for
independent verification; they do not enter image inference or selection.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from . import imacd_yolo_confirmation as original
from .imacd_formation_research import read_prefix, aggregate
from .imacd_startup_quality import build_features

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-timeframes-20260908-v1"
DATA = ROOT / "data/imacd_yolo_timeframes_20260908_v1"
START, END = original.START, original.END
MAX_WAIT, SEED, SYMBOLS = original.MAX_WAIT, original.SEED, original.SYMBOLS
MODEL_PATH, MODEL_SHA256 = original.MODEL_PATH, original.MODEL_SHA256
PROPOSAL_COLUMNS = original.PROPOSAL_COLUMNS + ["timeframe_min"]
PERIODS = (60, 240)
CANDIDATE_COLUMNS = ["event_id", "symbol", "timeframe_min", "signal_i", "side",
    "setup_start_i", "setup_bars", "signal_open_at", "signal_available_at",
    "signal_close", "complete_followup"]
DECISION_EXTRA = ["status", "can_long", "can_short", "model_detection_id", "confirmation_i",
    "confirmation_available_at", "delay_bars", "model_confidence", "baseline_next_open",
    "confirmed_next_open", "displacement_bp", "core_start_i", "core_end_i", "overlap_bars",
    "core_overlap_fraction", "core_end_from_arrow_bars", "core_identity"]
digest = original.digest
prepare_window = original.prepare_window


def ensure_period(events, proposals, minutes):
    """Fail closed on mixed period ledgers; 15 is only for synthetic parity."""
    if minutes not in (15, 60, 240):
        raise ValueError("unsupported candle period")
    for frame in (events, proposals):
        if frame is not None and not frame.empty:
            if not frame.timeframe_min.eq(minutes).all():
                raise ValueError("mixed timeframe ledgers")


def ensure_grid(bars, minutes):
    """Every row is a complete, contiguous UTC bar from aggregate()."""
    if str(bars.index.tz) != "UTC" or not bars.index.equals(bars.index.floor(f"{minutes}min")):
        raise ValueError("expected UTC-aligned bars")
    if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("invalid clock order")
    if len(bars)>1 and not bars.index.to_series().diff().iloc[1:].eq(pd.Timedelta(minutes=minutes)).all():
        raise ValueError("candle gap")


def eligible_pool(event, proposals, md):
    """Keep timeframe in the join; reuse the old dimensionless event gate."""
    if proposals.empty:
        return proposals.reindex(columns=PROPOSAL_COLUMNS).copy()
    own = proposals.loc[proposals.timeframe_min.eq(event.timeframe_min)]
    return original.eligible_pool(event, own, md)

def candidates(bars: pd.DataFrame, features: pd.DataFrame, symbol: str, minutes: int) -> pd.DataFrame:
    """Freeze setup boundaries using focus state known on signal close p."""
    step = pd.Timedelta(minutes=minutes)
    ensure_grid(bars, minutes)
    rows = []
    for p in np.flatnonzero(features.release_side.to_numpy()):
        available = bars.index[p] + step
        if not START <= available < END:
            continue
        row = features.iloc[p]
        start = int(row.focus_start_i)
        if start < 0 or start >= p or int(row.near_zero_bars) != p-start:
            raise ValueError("invalid frozen setup boundaries")
        rows.append(dict(
            event_id=f"{symbol}_{minutes}_{int(bars.index[p].timestamp())}", symbol=symbol, timeframe_min=minutes,
            signal_i=int(p), side=int(row.release_side), setup_start_i=start,
            setup_bars=int(row.near_zero_bars), signal_open_at=bars.index[p],
            signal_available_at=available, signal_close=float(bars.close.iloc[p]),
            complete_followup=p+MAX_WAIT+1 < len(bars),
        ))
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)



def decisions_for(events: pd.DataFrame, proposals: pd.DataFrame,
                  bars: pd.DataFrame, features: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Record earliest valid confirmation and its legal next-open price.

    Displacement is signed in signal direction: positive is a less favourable
    fill after waiting. It is not profit or realized slippage. Fill lookup is a
    separate retrospective observation and cannot affect signal selection.
    """
    step = pd.Timedelta(minutes=minutes)
    ensure_period(events, proposals, minutes)
    ensure_grid(bars, minutes)
    if events.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS+DECISION_EXTRA)
    rows = []
    md = features.md.to_numpy(float)
    for event in events.itertuples(index=False):
        row = event._asdict()
        row.update(status="censored_end", can_long=False, can_short=False,
                   model_detection_id=None, confirmation_i=None,
                   confirmation_available_at=None, delay_bars=None,
                   model_confidence=None, baseline_next_open=None,
                   confirmed_next_open=None, displacement_bp=None,
                   core_start_i=None, core_end_i=None, overlap_bars=None,
                   core_overlap_fraction=None, core_end_from_arrow_bars=None,
                   core_identity=None)
        if not event.complete_followup:
            rows.append(row)
            continue
        p, side = int(event.signal_i), int(event.side)
        pool = eligible_pool(event, proposals, md)
        row["can_long"] = bool(pool.direction.eq("long").any())
        row["can_short"] = bool(pool.direction.eq("short").any())
        direction = "long" if side == 1 else "short"
        valid = pool.loc[pool.direction.eq(direction)]
        invalid = np.flatnonzero(~np.isfinite(md[p:p+MAX_WAIT+1])
                                | (side*md[p:p+MAX_WAIT+1] <= 0))
        row["status"] = "invalidated" if len(invalid) else "expired"
        row["baseline_next_open"] = float(bars.open.iloc[p+1])
        if not valid.empty:
            first = valid.iloc[0]
            e = int(first.window_end_i)
            available = bars.index[e]+step
            if pd.Timestamp(first.available_at) != available:
                raise ValueError("detector availability differs from consumed window")
            row.update(status="confirmed", model_detection_id=first.detection_id,
                       confirmation_i=e, confirmation_available_at=available,
                       delay_bars=e-p, model_confidence=float(first.confidence),
                       confirmed_next_open=float(bars.open.iloc[e+1]))
            a, b = int(first.core_start_i), int(first.core_end_i)
            overlap = min(b, p)-max(a, int(event.setup_start_i))+1
            row.update(core_start_i=a, core_end_i=b, overlap_bars=overlap,
                       core_overlap_fraction=overlap/(b-a+1),
                       core_end_from_arrow_bars=b-p,
                       core_identity=f"{event.symbol}_{minutes}_{event.side}_{a}_{b}")
            row["displacement_bp"] = side*(row["confirmed_next_open"] /
                                           row["baseline_next_open"]-1)*10000
        rows.append(row)
    return pd.DataFrame(rows)



def infer(raw: pd.DataFrame, events: pd.DataFrame, symbol: str,
          model, device: str, minutes: int) -> tuple[pd.DataFrame, dict]:
    """Infer distinct endpoints only; fixed batch16 preserves old runtime."""
    step = pd.Timedelta(minutes=minutes)
    ensure_period(events, None, minutes)
    ensure_grid(raw, minutes)
    from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
    enriched = add_candidate_features(raw.reset_index(names="open_time"))
    endpoints = sorted({p+d for p in events.loc[events.complete_followup, "signal_i"]
                        for d in range(MAX_WAIT+1)})
    tasks, rows = [], []
    stats = Counter(candidate_endpoints=len(endpoints), events=len(events))
    start = time.monotonic()

    def flush():
        predictions = model.predict(source=[t[0] for t in tasks], imgsz=1280,
            conf=.25, iou=.70, batch=len(tasks), device=device, verbose=False,
            rect=True, half=False, agnostic_nms=False, max_det=300, save=False)
        if len(predictions) != len(tasks):
            raise ValueError("detector output count differs from input count")
        for prediction, (image, transform, meta) in zip(predictions, tasks):
            stats["windows_scored"] += 1
            boxes = prediction.boxes
            if boxes is None or len(boxes) == 0:
                continue
            stats["windows_with_boxes"] += 1
            pixel_sha = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()
            centers = np.asarray([transform.x_at(k) for k in range(transform.n_bars)])
            for xywh, cls, confidence in zip(boxes.xywhn.cpu().numpy(),
                    boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy()):
                cx, cy, width, height = map(float, xywh)
                cls = int(cls)
                if cls not in (0, 1):
                    raise ValueError("unknown model class")
                a = int(np.argmin(np.abs(centers-(cx-width/2)*transform.width)))
                b = int(np.argmin(np.abs(centers-(cx+width/2)*transform.width)))
                a, b = sorted((a+meta["window_start_i"], b+meta["window_start_i"]))
                core, post = b-a+1, meta["window_end_i"]-b
                row = dict(symbol=symbol, timeframe_min=minutes, direction="long" if cls == 0 else "short",
                    confidence=float(confidence), **meta,
                    available_at=raw.index[meta["window_end_i"]]+step,
                    core_start_i=a, core_end_i=b, core_length_bars=core,
                    confirmation_bars=post, structural_pass=core in (4, 5) and 2 <= post <= 9,
                    input_pixel_sha256=pixel_sha, prediction_cx_norm=cx,
                    prediction_cy_norm=cy, prediction_w_norm=width, prediction_h_norm=height)
                identity = [MODEL_SHA256, symbol, minutes, meta, cls, [round(float(x), 10) for x in xywh]]
                row["detection_id"] = "yolo_"+hashlib.sha256(
                    json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
                rows.append(row)
                stats["raw_boxes"] += 1
                stats["structural_boxes"] += int(row["structural_pass"])
        tasks.clear()
        if stats["windows_scored"] % 320 == 0:
            print(json.dumps(dict(symbol=symbol, windows_scored=stats["windows_scored"],
                                  windows_total=2*len(endpoints))), flush=True)

    for e in endpoints:
        for w in (18, 19):
            tasks.append(prepare_window(enriched, e, w))
            if len(tasks) == 16:
                flush()
    if tasks:
        flush()
    stats["elapsed_seconds"] = round(time.monotonic()-start, 3)
    return pd.DataFrame(rows, columns=PROPOSAL_COLUMNS), dict(stats)



def validate_selection(decisions: pd.DataFrame, proposals: pd.DataFrame, minutes: int) -> dict:
    """Independent ledger identities and timestamp/bar-overlap invariants."""
    step = pd.Timedelta(minutes=minutes)
    ensure_period(decisions, proposals, minutes)
    accepted = decisions.loc[decisions.status.eq("confirmed")]
    if decisions.event_id.duplicated().any() or proposals.detection_id.duplicated().any():
        raise ValueError("duplicate identity")
    indexed = proposals.set_index("detection_id")
    for r in accepted.itertuples():
        q = indexed.loc[r.model_detection_id]
        assert r.symbol == q.symbol
        assert (r.side == 1) == (q.direction == "long")
        assert q.structural_pass and q.core_length_bars in (4, 5)
        assert 2 <= q.confirmation_bars <= 9
        assert q.core_start_i <= r.signal_i and q.core_end_i >= r.setup_start_i
        assert 0 <= r.delay_bars <= MAX_WAIT
        assert int(q.window_end_i) == r.confirmation_i == r.signal_i+r.delay_bars
        assert pd.Timestamp(q.available_at) == pd.Timestamp(r.confirmation_available_at)
        assert pd.Timestamp(r.confirmation_available_at) == (
            pd.Timestamp(r.signal_available_at)+r.delay_bars*step)
        assert r.complete_followup
        assert np.isclose(r.displacement_bp, r.side*(r.confirmed_next_open /
                                                   r.baseline_next_open-1)*10000)
    return dict(passed=True, confirmed_events_checked=len(accepted),
                identities_unique=True, clock_geometry_price_invariants=True)


def trace_for(events, bars, features):
    """Save local p..p+10 clocks/md/opens, including the entry-only last row.

    md through p+9 drives cancellation. The extra p+10 open is only needed
    when the last allowed confirmation is at p+9. Saving this row never makes
    it an inference input or an earlier observation.
    """
    rows = []
    for event in events.itertuples(index=False):
        p = int(event.signal_i)
        for i in range(p, min(p+MAX_WAIT+2, len(bars))):
            rows.append(dict(event_id=event.event_id, symbol=event.symbol,
                timeframe_min=event.timeframe_min, bar_i=i, bar_open_at=bars.index[i],
                md=float(features.md.iloc[i]), open=float(bars.open.iloc[i]),
                close=float(bars.close.iloc[i])))
    return pd.DataFrame(rows, columns=["event_id", "symbol", "timeframe_min", "bar_i",
                                       "bar_open_at", "md", "open", "close"])


def nulls_by_timeframe(decisions):
    """Use two separate fixed symbol/month shuffles, then Holm over two tests."""
    results = {}
    for minutes in PERIODS:
        g = decisions.loc[decisions.timeframe_min.eq(minutes)].copy()
        if g.empty:
            results[str(minutes)] = dict(observed=0, permutations=10000, seed=SEED,
                null_mean=0., null_sd=0., p_one_sided=1., scope="no candidates; no evidence")
        else:
            results[str(minutes)] = original.direction_null(g)
    order = sorted(results, key=lambda k:results[k]["p_one_sided"])
    previous = 0.
    for rank, key in enumerate(order):
        previous = max(previous, min(1., (len(order)-rank)*results[key]["p_one_sided"]))
        results[key]["p_holm"] = previous
    return results


def run():
    """One source-frozen authorized evaluation per new timeframe configuration."""
    import torch
    from ultralytics import YOLO
    manifest = json.loads((EXP/"source_manifest.json").read_text())
    for relative, expected in manifest["files"].items():
        if digest(ROOT/relative) != expected:
            raise ValueError(f"source changed: {relative}")
    own = [str(Path(__file__).relative_to(ROOT)), str((EXP/"PROJECT_PLAN.md").relative_to(ROOT)),
           str((EXP/"source_manifest.json").relative_to(ROOT))]
    if subprocess.check_output(["git", "status", "--porcelain", "--", *own], cwd=ROOT, text=True).strip():
        raise ValueError("commit source/plan before evaluation")
    if digest(MODEL_PATH) != MODEL_SHA256 or not torch.backends.mps.is_available():
        raise ValueError("pinned weight and MPS required")
    results_dir = EXP/"results"
    results_dir.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with (results_dir/"run_started.json").open("x") as handle:
        json.dump(dict(started_at=pd.Timestamp.now(tz="UTC").isoformat(), source_commit=commit,
            owner_request="1h 4h也试试", authorization="Owner此前明确任何时间段数据都可以使用不要有任何限制",
            holdout_consumptions={"60":1, "240":1}, timeframe_minutes=list(PERIODS),
            start=START.isoformat(), end_exclusive=END.isoformat()), handle, indent=2, ensure_ascii=False)
    model = YOLO(str(MODEL_PATH))
    if model.names != {0:"dense_long", 1:"dense_short"}:
        raise ValueError("incorrect model classes")
    old_summary = json.loads((original.EXP/"results/summary.json").read_text())
    all_events, all_proposals, all_decisions, all_traces = [], [], [], []
    inputs, validations, table = {}, {}, []
    for symbol in SYMBOLS:
        path = ROOT/f"data/kline_deep/okx_{symbol}_USDT_SWAP_15m_158499.csv"
        raw = read_prefix(path, end=END)
        raw_sha = hashlib.sha256(raw.to_csv().encode()).hexdigest()
        if raw_sha != old_summary["inputs"][symbol]["bounded_ohlcv_sha256"]:
            raise ValueError("data changed from the previous timeframe comparison")
        for minutes in PERIODS:
            key = f"{symbol}_{minutes}"
            bars = aggregate(raw, minutes)
            ensure_grid(bars, minutes)
            features = build_features(bars)
            events = candidates(bars, features, symbol, minutes)
            print(json.dumps(dict(group=key, arrows=len(events), phase="inference")), flush=True)
            proposals, stats = infer(bars, events, symbol, model, "mps", minutes)
            for name in ("windows_scored", "windows_with_boxes", "raw_boxes", "structural_boxes"):
                stats.setdefault(name, 0)
            decisions = decisions_for(events, proposals, bars, features, minutes)
            trace = trace_for(events, bars, features)
            invalid_indexes = []
            for event in decisions.itertuples(index=False):
                p = int(event.signal_i)
                md = features.md.iloc[p:p+MAX_WAIT+1].to_numpy()
                bad = np.flatnonzero(~np.isfinite(md) | (event.side*md <= 0))
                invalid_indexes.append(p+int(bad[0]) if len(bad) else None)
            decisions["first_invalid_i"] = invalid_indexes
            validations[key] = validate_selection(decisions, proposals, minutes)
            events.to_csv(DATA/f"{key}_candidates.csv", index=False)
            decisions.to_csv(DATA/f"{key}_decisions.csv", index=False)
            proposals.to_csv(DATA/f"{key}_proposals.csv.gz", index=False)
            trace.to_csv(DATA/f"{key}_trace.csv.gz", index=False)
            inputs[key] = dict(raw_path=str(path.relative_to(ROOT)), minutes=minutes,
                raw_rows=len(raw), raw_bounded_ohlcv_sha256=raw_sha,
                parsed_rows=len(bars), parsed_first=bars.index[0].isoformat(),
                parsed_last=bars.index[-1].isoformat(),
                bounded_ohlcv_sha256=hashlib.sha256(bars.to_csv().encode()).hexdigest(),
                discarded_partial_source_rows=len(raw)-len(bars)*(minutes//15), **stats)
            complete = decisions.loc[decisions.complete_followup.eq(True)]
            kept = complete.loc[complete.status.eq("confirmed")]
            table.append(dict(symbol=symbol, timeframe_min=minutes, max_wait_hours=MAX_WAIT*minutes/60,
                arrows=len(decisions), complete=len(complete), confirmed=len(kept),
                invalidated=int(complete.status.eq("invalidated").sum()),
                expired=int(complete.status.eq("expired").sum()), censored=len(decisions)-len(complete),
                pass_rate_pct=100*len(kept)/len(complete) if len(complete) else None,
                delay_median_minutes=float(kept.delay_bars.median()*minutes) if len(kept) else None,
                displacement_median_bp=float(kept.displacement_bp.median()) if len(kept) else None,
                displacement_max_bp=float(kept.displacement_bp.max()) if len(kept) else None))
            all_events.append(events); all_proposals.append(proposals)
            all_decisions.append(decisions); all_traces.append(trace)
    events = pd.concat(all_events, ignore_index=True)
    proposals = pd.concat(all_proposals, ignore_index=True)
    decisions = pd.concat(all_decisions, ignore_index=True)
    traces = pd.concat(all_traces, ignore_index=True)
    if events.event_id.duplicated().any() or proposals.detection_id.duplicated().any():
        raise ValueError("cross-timeframe identity collision")
    events.to_csv(DATA/"candidates.csv", index=False)
    decisions.to_csv(DATA/"decisions.csv", index=False)
    traces.to_csv(DATA/"traces.csv.gz", index=False)
    summary = dict(source_commit=commit, model_sha256=MODEL_SHA256,
        start=START.isoformat(), end_exclusive=END.isoformat(), max_wait_bars=MAX_WAIT,
        inputs=inputs, table=table, validation=validations,
        direction_null=nulls_by_timeframe(decisions), holdout_consumptions={"60":1,"240":1},
        versions={p:importlib.metadata.version(p) for p in ("torch","ultralytics","numpy","pandas")},
        files={str(p.relative_to(ROOT)):digest(p) for p in sorted(DATA.glob("*")) if p.is_file()},
        economic_evaluation=False, training_eligible=False, production_eligible=False,
        prior_15m_summary_sha256=digest(original.EXP/"results/summary.json"),
        completed_at=pd.Timestamp.now(tz="UTC").isoformat())
    (results_dir/"summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(dict(table=table, validation=validations)), flush=True)


if __name__ == "__main__":
    run()

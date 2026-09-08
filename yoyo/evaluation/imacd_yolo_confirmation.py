"""Frozen, research-only IMACD to native-15m YOLO confirmation pilot.

Source: exp-imacd-yolo-confirmation-20260908-v1/PROJECT_PLAN.md. Signals use
the existing monitor-parity focusRelease features (34/9, 12 bars, .10 ATR).
After signal p closes, W18/W19 windows ending p through p+9 may confirm it.
Inputs to each image use OHLCV and trailing six close-source MAs through that
endpoint only. Core4/5, post2..9 and direction are the detector's old contract.
The detected core must overlap the frozen setup plus arrow [focus_start,p]
by an actual candle, and md must stay strictly on the signal side through the
confirmation close. The first eligible confirmation wins, never a later one
with a better price or confidence. This is observed temporal association, not
human-verified identity of the detected shape.

This module computes feasibility, delay and next-open price displacement,
not returns, trade outcomes, profit optimisation or live notifications. The
timestamp-strict source reader never parses OHLCV at or after the fixed end.
Owner's existing unrestricted-date authorization covers this configuration's
first holdout evaluation. No training, threshold search or deployment occurs.
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

from .imacd_formation_research import read_prefix
from .imacd_startup_quality import build_features

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-confirmation-20260908-v1"
DATA = ROOT / "data/imacd_yolo_confirmation_20260908_v1"
START = pd.Timestamp("2026-05-04", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
BAR = pd.Timedelta(minutes=15)
MAX_WAIT = 9
SYMBOLS = ("BTC", "ETH")
SEED = 20260908
MODEL_PATH = ROOT / ("analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/"
                    "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt")
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
PROPOSAL_COLUMNS = ["detection_id", "symbol", "direction", "confidence", "window_len",
    "window_start_i", "window_end_i", "available_at", "core_start_i", "core_end_i",
    "core_length_bars", "confirmation_bars", "structural_pass", "input_pixel_sha256",
    "prediction_cx_norm", "prediction_cy_norm", "prediction_w_norm", "prediction_h_norm"]


def digest(path: Path) -> str:
    """Hash existing bytes for identity; never infer a dataset split from SHA."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidates(bars: pd.DataFrame, features: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Freeze setup boundaries using focus state known on signal close p."""
    rows = []
    for p in np.flatnonzero(features.release_side.to_numpy()):
        available = bars.index[p] + BAR
        if not START <= available < END:
            continue
        row = features.iloc[p]
        start = int(row.focus_start_i)
        if start < 0 or start >= p or int(row.near_zero_bars) != p-start:
            raise ValueError("invalid frozen setup boundaries")
        rows.append(dict(
            event_id=f"{symbol}_15_{int(bars.index[p].timestamp())}", symbol=symbol,
            signal_i=int(p), side=int(row.release_side), setup_start_i=start,
            setup_bars=int(row.near_zero_bars), signal_open_at=bars.index[p],
            signal_available_at=available, signal_close=float(bars.close.iloc[p]),
            complete_followup=p+MAX_WAIT+1 < len(bars),
        ))
    return pd.DataFrame(rows)


def prepare_window(raw: pd.DataFrame, endpoint: int, window_len: int):
    """Render only trailing 18/19 enriched rows; raw MAs must be causal."""
    from yoyo.layers.l1_detection.data import ALL_MA_COLS
    from yoyo.layers.l1_detection.render import render_chart
    start = endpoint-window_len+1
    if start < 0 or endpoint >= len(raw):
        raise ValueError("incomplete input window")
    window = raw.iloc[start:endpoint+1]
    if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
        raise ValueError("MA warmup missing")
    image, transform = render_chart(window, out_path=None)
    return image, transform, dict(window_len=window_len, window_start_i=start,
                                  window_end_i=endpoint)


def eligible_pool(event, proposals: pd.DataFrame, md: np.ndarray) -> pd.DataFrame:
    """Causal spatial/time pool, independent of predicted direction.

    md[p:e] uses only the original direction's state up to observed e. A reset
    invalidates this pending event permanently, even if md later recovers.
    Integer core intervals require shared bars, not touching close/open times.
    """
    p, side = int(event.signal_i), int(event.side)
    if proposals.empty:
        return proposals.reindex(columns=PROPOSAL_COLUMNS).copy()
    q = proposals.loc[
        proposals.symbol.eq(event.symbol) & proposals.structural_pass.eq(True)
        & proposals.core_length_bars.isin([4, 5])
        & proposals.confirmation_bars.between(2, 9)
        & proposals.window_end_i.between(p, p+MAX_WAIT)
        & proposals.core_start_i.le(p)
        & proposals.core_end_i.ge(int(event.setup_start_i))
    ].copy()
    if q.empty:
        return q
    invalid = np.flatnonzero(~np.isfinite(md[p:p+MAX_WAIT+1])
                            | (side*md[p:p+MAX_WAIT+1] <= 0))
    if len(invalid):
        q = q.loc[q.window_end_i.lt(p+int(invalid[0]))]
    return q.sort_values(["window_end_i", "confidence", "window_len", "detection_id"],
                         ascending=[True, False, True, True])


def decisions_for(events: pd.DataFrame, proposals: pd.DataFrame,
                  bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Record earliest valid confirmation and its legal next-open price.

    Displacement is signed in signal direction: positive is a less favourable
    fill after waiting. It is not profit or realized slippage. Fill lookup is a
    separate retrospective observation and cannot affect signal selection.
    """
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
            available = bars.index[e]+BAR
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
                       core_identity=f"{event.symbol}_{event.side}_{a}_{b}")
            row["displacement_bp"] = side*(row["confirmed_next_open"] /
                                           row["baseline_next_open"]-1)*10000
        rows.append(row)
    return pd.DataFrame(rows)


def direction_null(decisions: pd.DataFrame) -> dict:
    """Shuffle arrow directions within symbol/month, preserving fixed pools.

    This null measures conditional label agreement only. Original md-valid
    time pools remain fixed; there is no claim about unconditional specificity,
    trend prediction, noise rejection or trading alpha. No prices are accessed.
    """
    d = decisions.loc[decisions.complete_followup].reset_index(drop=True)
    side = d.side.to_numpy(int)
    positive = d.can_long.to_numpy(bool)
    negative = d.can_short.to_numpy(bool)
    observed = int(np.where(side == 1, positive, negative).sum())
    groups = d.groupby([d.symbol, pd.to_datetime(d.signal_available_at, utc=True)
                         .dt.strftime("%Y-%m")]).indices
    rng = np.random.default_rng(SEED)
    counts = np.zeros(10000, dtype=int)
    for j in range(len(counts)):
        shuffled = side.copy()
        for indexes in groups.values():
            shuffled[indexes] = rng.permutation(side[indexes])
        counts[j] = np.where(shuffled == 1, positive, negative).sum()
    return dict(observed=observed, permutations=len(counts), seed=SEED,
                null_mean=float(counts.mean()), null_sd=float(counts.std()),
                p_one_sided=float((1+(counts >= observed).sum())/(len(counts)+1)),
                scope="conditional direction agreement only; not profitability")


def infer(raw: pd.DataFrame, events: pd.DataFrame, symbol: str,
          model, device: str) -> tuple[pd.DataFrame, dict]:
    """Infer distinct endpoints only; fixed batch16 preserves old runtime."""
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
                row = dict(symbol=symbol, direction="long" if cls == 0 else "short",
                    confidence=float(confidence), **meta,
                    available_at=raw.index[meta["window_end_i"]]+BAR,
                    core_start_i=a, core_end_i=b, core_length_bars=core,
                    confirmation_bars=post, structural_pass=core in (4, 5) and 2 <= post <= 9,
                    input_pixel_sha256=pixel_sha, prediction_cx_norm=cx,
                    prediction_cy_norm=cy, prediction_w_norm=width, prediction_h_norm=height)
                identity = [MODEL_SHA256, symbol, meta, cls, [round(float(x), 10) for x in xywh]]
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


def validate_selection(decisions: pd.DataFrame, proposals: pd.DataFrame) -> dict:
    """Independent ledger identities and timestamp/bar-overlap invariants."""
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
            pd.Timestamp(r.signal_available_at)+r.delay_bars*BAR)
        assert r.complete_followup
        assert np.isclose(r.displacement_bp, r.side*(r.confirmed_next_open /
                                                   r.baseline_next_open-1)*10000)
    return dict(passed=True, confirmed_events_checked=len(accepted),
                identities_unique=True, clock_geometry_price_invariants=True)


def run():
    """One immutable pilot run; refuse overwrites and uncommitted source."""
    import torch
    from ultralytics import YOLO
    plan = EXP / "PROJECT_PLAN.md"
    receipt = EXP / "source_manifest.json"
    frozen = json.loads(receipt.read_text())
    for relative, expected in frozen["files"].items():
        if digest(ROOT/relative) != expected:
            raise ValueError(f"source drift: {relative}")
    tracked = [str(Path(__file__).resolve().relative_to(ROOT)),
               str(plan.relative_to(ROOT)), str(receipt.relative_to(ROOT))]
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *tracked],
                                     cwd=ROOT, text=True)
    if dirty.strip():
        raise ValueError("commit pilot source, plan and receipt before running")
    if (EXP / "results/run_started.json").exists():
        raise ValueError("immutable evaluation already started; inspect prior receipt")
    if digest(MODEL_PATH) != MODEL_SHA256:
        raise ValueError("wrong model weight identity")
    if not torch.backends.mps.is_available():
        raise ValueError("frozen pilot requires available MPS; no silent fallback")
    device = "mps"
    (EXP / "results").mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (EXP / "results/run_started.json").write_text(json.dumps(dict(
        started_at=pd.Timestamp.now(tz="UTC").isoformat(), source_commit=commit,
        holdout_consumption_this_configuration=1, start=START.isoformat(),
        end_exclusive=END.isoformat(), device=device,
        authorization="Owner: 任何时间段数据都可以使用不要有任何限制"), indent=2, ensure_ascii=False))
    model = YOLO(str(MODEL_PATH))
    if model.names != {0: "dense_long", 1: "dense_short"}:
        raise ValueError("model classes changed")
    inputs, all_events, all_proposals, all_decisions = {}, [], [], []
    for symbol in SYMBOLS:
        path = ROOT/f"data/kline_deep/okx_{symbol}_USDT_SWAP_15m_158499.csv"
        raw = read_prefix(path, end=END)
        features = build_features(raw)
        events = candidates(raw, features, symbol)
        print(json.dumps(dict(symbol=symbol, arrows=len(events), phase="inference")), flush=True)
        proposals, stats = infer(raw, events, symbol, model, device)
        decisions = decisions_for(events, proposals, raw, features)
        proposals.to_csv(DATA/f"{symbol}_proposals.csv.gz", index=False)
        decisions.to_csv(DATA/f"{symbol}_decisions.csv", index=False)
        inputs[symbol] = dict(path=str(path.relative_to(ROOT)), parsed_rows=len(raw),
            parsed_first=raw.index[0].isoformat(), parsed_last=raw.index[-1].isoformat(),
            bounded_ohlcv_sha256=hashlib.sha256(raw.to_csv().encode()).hexdigest(), **stats)
        all_events.append(events)
        all_proposals.append(proposals)
        all_decisions.append(decisions)
    events = pd.concat(all_events, ignore_index=True)
    proposals = pd.concat(all_proposals, ignore_index=True)
    decisions = pd.concat(all_decisions, ignore_index=True)
    events.to_csv(DATA/"candidates.csv", index=False)
    decisions.to_csv(DATA/"decisions.csv", index=False)
    validation = validate_selection(decisions, proposals)
    table = []
    for symbol, g in decisions.groupby("symbol", sort=True):
        full = g.loc[g.complete_followup]
        kept = full.loc[full.status.eq("confirmed")]
        table.append(dict(symbol=symbol, arrows=len(g), complete=len(full), confirmed=len(kept),
            invalidated=int(full.status.eq("invalidated").sum()),
            expired=int(full.status.eq("expired").sum()), censored=len(g)-len(full),
            pass_rate_pct=100*len(kept)/len(full) if len(full) else None,
            delay_median_minutes=float(kept.delay_bars.median()*15) if len(kept) else None,
            displacement_median_bp=float(kept.displacement_bp.median()) if len(kept) else None,
            displacement_max_bp=float(kept.displacement_bp.max()) if len(kept) else None))
    result = dict(source_commit=commit, model_sha256=digest(MODEL_PATH),
        start=START.isoformat(), end_exclusive=END.isoformat(), max_wait_bars=MAX_WAIT,
        inputs=inputs, table=table, validation=validation, direction_null=direction_null(decisions),
        versions={p: importlib.metadata.version(p) for p in ("torch", "ultralytics", "numpy", "pandas")},
        reused_core_confirmations=int(decisions.loc[decisions.status.eq("confirmed"),
                                                     "core_identity"].duplicated().sum()),
        files={str(p.relative_to(ROOT)): digest(p) for p in sorted(DATA.glob("*")) if p.is_file()},
        holdout_consumption_this_configuration=1, economic_evaluation=False,
        training_eligible=False, production_eligible=False,
        completed_at=pd.Timestamp.now(tz="UTC").isoformat())
    (EXP/"results/summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(dict(table=table, validation=validation), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run()

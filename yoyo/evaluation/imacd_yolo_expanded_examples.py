"""Reproduce causal input/audit/context examples from frozen expanded ledgers.

Selection uses only confirmed signal time/event ID: first per 1H/4H x long/
short, plus the first one-candle-overlap event if present, deduplicated. No
future return or YOLO inference is used. OHLCV is parsed strictly before each
cohort end, then its aggregate SHA is checked. Each model input stops at the
saved confirmation endpoint and must reproduce the saved input pixel SHA.
The separate six-MA/IMACD context also stops at that endpoint. Original input
pixels are never annotated; audit graphics are saved as a separate copy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from .imacd_formation_research import aggregate, read_prefix
from .imacd_startup_quality import MA_COLUMNS, build_features
from .imacd_yolo_confirmation import prepare_window
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-expanded-20260908-v1"
DATA = ROOT / "data/imacd_yolo_expanded_20260908_v1"
ENDS = {"pre_holdout": pd.Timestamp("2026-05-04", tz="UTC"),
        "holdout_review": pd.Timestamp("2026-07-01", tz="UTC")}


def select_examples(decisions):
    """Choose at most five identities by the fixed time/direction rule."""
    kept = decisions.loc[decisions.status.eq("confirmed")].copy()
    kept["signal_available_at"] = pd.to_datetime(kept.signal_available_at, utc=True)
    kept = kept.sort_values(["signal_available_at", "event_id"])
    chosen = kept.groupby(["timeframe_min", "side"], sort=True).head(1)
    extra = kept.loc[kept.overlap_bars.eq(1)].head(1)
    return pd.concat([chosen, extra]).drop_duplicates("event_id").sort_values(
        ["signal_available_at", "event_id"])


def context_image(path, bars, features, row):
    """Show only candles at/before actual confirmation, with a visible zero axis."""
    p, e = int(row.signal_i), int(row.confirmation_i)
    start = max(0, min(int(row.setup_start_i) - 20, p - 100))
    b, f = bars.iloc[start:e+1], features.iloc[start:e+1]
    xs = np.arange(len(b))
    fig, (ax, osc) = plt.subplots(2, 1, figsize=(12, 6.5), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]}, layout="constrained")
    for axis in (ax, osc):
        axis.set_facecolor("#fafbfc")
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", color="#dfe5eb", alpha=.5)
        axis.axvspan(int(row.setup_start_i)-start-.5, p-start-.5, color="#d9a744", alpha=.10)
    for i, candle in enumerate(b.itertuples()):
        color = "#159b8d" if candle.close >= candle.open else "#d75768"
        ax.vlines(i, candle.low, candle.high, color=color, linewidth=.7)
        ax.add_patch(Rectangle((i-.32, min(candle.open, candle.close)), .64,
            max(abs(candle.close-candle.open), candle.open*.00001), color=color, linewidth=0))
    for name, color in zip(MA_COLUMNS, ["#63a8a3", "#438d87", "#83a6cd", "#6888ba", "#9299a1", "#606a74"]):
        ax.plot(xs, f[name], color=color, linewidth=.8)
    ax.axvline(p-start, color="#db9846", linewidth=1.4, label="IMACD arrow")
    ax.axvline(e-start, color="#008a80", linestyle="--", linewidth=1.2, label="YOLO confirmation candle")
    ax.axvspan(int(row.core_start_i)-start-.5, int(row.core_end_i)-start+.5, color="#00a494", alpha=.14)
    ax.set_title(f"{row.symbol} / {int(row.timeframe_min)//60}H / {'LONG' if row.side == 1 else 'SHORT'}"
        f" | delay {row.delay_bars*row.timeframe_min/60:g}h | core overlap {int(row.overlap_bars)} bars", loc="left", fontsize=11)
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    osc.plot(xs, f.md, color="#527ed0", linewidth=1.2)
    osc.plot(xs, f.sb, color="#d39a3d", linewidth=1.1)
    osc.axhline(0, color="#8a939d", linewidth=.7)
    ticks = np.linspace(0, len(b)-1, 6).astype(int)
    osc.set_xticks(ticks, [t.strftime("%m-%d\n%H:%M") for t in b.index[ticks]])
    osc.set_xlabel(f"UTC candle OPEN; last candle closes at {pd.Timestamp(row.confirmation_available_at).isoformat()}")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run():
    """Generate examples only after this source is tracked and committed."""
    source = str(Path(__file__).resolve().relative_to(ROOT))
    subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    if subprocess.check_output(["git", "status", "--porcelain", "--", source], cwd=ROOT, text=True).strip():
        raise ValueError("commit example source before rendering")
    summary_path = EXP / "results/summary.json"
    summary = json.loads(summary_path.read_text())
    def saved_csv(path):
        if hashlib.sha256(path.read_bytes()).hexdigest() != summary["files"][str(path.relative_to(ROOT))]:
            raise ValueError(f"saved ledger changed: {path.name}")
        return pd.read_csv(path)
    decisions = saved_csv(DATA / "decisions.csv")
    if decisions.event_id.duplicated().any() or len(summary["inputs"]) != 216:
        raise ValueError("require complete unique frozen expanded results")
    selected = select_examples(decisions)
    directory = EXP / "results/examples"
    directory.mkdir(parents=True, exist_ok=True)
    cache, records = {}, []
    for row in selected.itertuples(index=False):
        minutes, key = int(row.timeframe_min), f"{row.symbol}_{int(row.timeframe_min)}_{row.fold}"
        info = summary["inputs"][key]
        if key not in cache:
            bars = aggregate(read_prefix(ROOT / info["raw_path"], end=ENDS[row.fold]), minutes)
            if hashlib.sha256(bars.to_csv().encode()).hexdigest() != info["bounded_ohlcv_sha256"]:
                raise ValueError(f"aggregate source mismatch: {key}")
            proposals = saved_csv(DATA / f"{key}_proposals.csv.gz").set_index("detection_id", verify_integrity=True)
            cache[key] = bars, add_candidate_features(bars.reset_index(names="open_time")), proposals, build_features(bars)
        bars, enriched, proposals, features = cache[key]
        proposal = proposals.loc[row.model_detection_id]
        p, e = int(row.signal_i), int(row.confirmation_i)
        available = bars.index[e] + pd.Timedelta(minutes=minutes)
        if (proposal.symbol != row.symbol or proposal.timeframe_min != minutes or proposal.fold != row.fold
                or int(proposal.window_end_i) != e or available != pd.Timestamp(row.confirmation_available_at)
                or available != pd.Timestamp(proposal.available_at)):
            raise ValueError("example identity/confirmation endpoint mismatch")
        image, transform, _ = prepare_window(enriched, e, int(proposal.window_len))
        pixel_sha = hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()
        if pixel_sha != proposal.input_pixel_sha256:
            raise ValueError(f"model input pixel mismatch: {row.event_id}")
        paths = {kind: directory / f"{row.event_id}_{kind}.png" for kind in ("input", "audit", "context")}
        audit = image.copy()
        cx, cy, w, h = (float(proposal[name]) for name in ("prediction_cx_norm", "prediction_cy_norm", "prediction_w_norm", "prediction_h_norm"))
        height, width = image.shape[:2]
        cv2.rectangle(audit, (int((cx-w/2)*width), int((cy-h/2)*height)),
                      (int((cx+w/2)*width), int((cy+h/2)*height)), (130, 155, 0), 3)
        if 0 <= p-int(proposal.window_start_i) < transform.n_bars:
            x = int(transform.x_at(p-int(proposal.window_start_i)))
            cv2.line(audit, (x, 15), (x, height-15), (50, 115, 230), 2)
        cv2.putText(audit, "AUDIT COPY | IMACD orange | YOLO core teal | no future candles",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .6, (60, 60, 60), 1, cv2.LINE_AA)
        for kind, pixels in (("input", image), ("audit", audit)):
            if not cv2.imwrite(str(paths[kind]), pixels):
                raise OSError(f"cannot write {kind}")
        context_image(paths["context"], bars, features, row)
        records.append(dict(event_id=row.event_id, symbol=row.symbol, timeframe_min=minutes, side=int(row.side),
            fold=row.fold, overlap_bars=int(row.overlap_bars), signal_available_at=str(row.signal_available_at),
            model_available_at=str(available), context_last_open_at=str(bars.index[e]),
            aggregate_sha256_verified=info["bounded_ohlcv_sha256"], pixel_sha256_verified=pixel_sha,
            paths={kind: str(path.relative_to(ROOT)) for kind, path in paths.items()},
            file_sha256={kind: hashlib.sha256(path.read_bytes()).hexdigest() for kind, path in paths.items()}))
    manifest = dict(selection="Earliest signal per timeframe/side, plus earliest one-bar core overlap; deduplicated, no outcomes.",
        summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(), examples=records, model_inference_runs=0)
    (EXP / "results/example_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"examples": len(records), "manifest": str(EXP / "results/example_manifest.json")}))


if __name__ == "__main__":
    run()

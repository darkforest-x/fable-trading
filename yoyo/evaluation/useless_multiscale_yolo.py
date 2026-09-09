"""Fixed-window causal YOLO case audit for USELESS on 2026-08-31.

Owner authorized this dated historical case on 2026-09-09. This is an
illustrative holdout read, not a detector/strategy validation or a threshold
search. Reuse the pinned Spike weight, renderer, class mapping and proposal
geometry. Only the research adapter adds a 3-minute duration; the live
monitor, notifications, trained weights and execution state are untouched.

Features use t/o/h/l/c/v through each closed endpoint, with close-source
SMA/EMA 20/60/120 and a fixed common history origin. An inference image sees
only its last 18/19 bars. Human review overlays are written separately from
the exact model input. A model score is not a probability of profitable
trading, and structural_pass does not establish IMACD setup association.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import time

import cv2
import numpy as np
import pandas as pd

from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart
from yoyo.monitor.yolo_detector import (
    MA_COLUMNS, MODEL_CLASSES, MODEL_PATH, MODEL_SHA256, PREDICT_PARAMETERS,
    RenderedWindow, parse_prediction, prepare_windows,
)

SYMBOL = "USELESS-USDT-SWAP"
DURATIONS = {"3m": 180_000, "5m": 300_000}


def sha_file(path: Path) -> str:
    """Hash actual bytes, not the provenance narrative."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value: str) -> int:
    """Convert an explicit zoned datetime to milliseconds; reject naive time."""
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("A clock must explicitly include its UTC offset")
    return int(result.timestamp() * 1000)


def local_time(value: int) -> str:
    return pd.Timestamp(value, unit="ms", tz="UTC").tz_convert("Asia/Shanghai").isoformat()


def load_frame(path: Path, timeframe: str) -> pd.DataFrame:
    """Read ordinary exchange bars; all rolling features end on their row."""
    frame = pd.read_csv(path).rename(columns={
        "t": "ts", "o": "open", "h": "high", "l": "low",
        "c": "close", "v": "volume", "timestamp": "ts",
    })
    if "ts" not in frame and "open_time" in frame:
        frame["ts"] = pd.to_datetime(frame["open_time"], utc=True).astype("int64") // 1_000_000
    fields = ["ts", "open", "high", "low", "close", "volume"]
    if not set(fields).issubset(frame.columns):
        raise ValueError(f"Missing ordinary OHLCV fields in {path}")
    for column in fields:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    values = frame[fields].to_numpy()
    if not np.isfinite(values).all():
        raise ValueError("Non-finite candle data")
    if not np.equal(frame.ts, np.floor(frame.ts)).all():
        raise ValueError("Non-integer candle timestamp")
    frame.ts = frame.ts.astype("int64")
    duration = DURATIONS[timeframe]
    if not (frame.ts % duration == 0).all() or not (frame.ts.diff().dropna() == duration).all():
        raise ValueError("Input must be chronological, aligned, unique and gap-free")
    if (frame.low <= 0).any() or (frame.volume < 0).any():
        raise ValueError("Invalid positive price/nonnegative volume")
    if (frame.high < frame[["open", "close"]].max(axis=1)).any() or (frame.low > frame[["open", "close"]].min(axis=1)).any():
        raise ValueError("Invalid OHLC geometry")
    if "confirm" in frame and not frame["confirm"].astype(str).isin(["1", "True"]).all():
        raise ValueError("Unconfirmed input candle")
    return add_mas(frame).reset_index(drop=True)


def case_windows(frame: pd.DataFrame, timeframe: str, available_at_ms: int) -> list[RenderedWindow]:
    """Read only bars closed by decision time, with frozen W18/W19 geometry."""
    duration = DURATIONS[timeframe]
    prefix = frame.loc[frame.ts + duration <= available_at_ms]
    if len(prefix) < 340 or int(prefix.ts.iloc[-1]) + duration != available_at_ms:
        raise ValueError("Insufficient warmup or missing exact closed endpoint")
    result = []
    for length in (18, 19):
        rows = prefix.tail(length)
        if not np.isfinite(rows[list(MA_COLUMNS)].to_numpy()).all():
            raise ValueError("Unready moving averages")
        pixels, transform = render_chart(rows, out_path=None)
        result.append(RenderedWindow(pixels, transform, tuple(int(x) for x in rows.ts),
            hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest()))
    return result


def verify_windows(frame: pd.DataFrame, timeframe: str, decision: int) -> dict:
    """Future mutation plus 5m parity against the unmodified monitor adapter."""
    original = case_windows(frame, timeframe, decision)
    altered = frame.copy()
    future = altered.ts + DURATIONS[timeframe] > decision
    # Mutate future source prices, then recompute every MA from the same origin.
    altered.loc[future, ["open", "high", "low", "close"]] *= 100
    altered.loc[future, "volume"] *= 999
    mutated = case_windows(add_mas(altered), timeframe, decision)
    hashes = [window.input_pixel_sha256 for window in original]
    if hashes != [window.input_pixel_sha256 for window in mutated]:
        raise AssertionError("Future data changed a causal image")
    monitor_parity = None
    if timeframe == "5m":
        candles = frame.rename(columns={"ts": "t", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}).to_dict("records")
        live = prepare_windows(candles, timeframe, decision - DURATIONS[timeframe])
        monitor_parity = hashes == [window.input_pixel_sha256 for window in live]
        if not monitor_parity:
            raise AssertionError("Research pixels differ from monitor pixels")
    return {"timeframe": timeframe, "decision": local_time(decision),
            "future_mutation_pass": True, "monitor_pixel_parity": monitor_parity,
            "input_pixel_hashes": hashes}


def phase(close_ms: int, decisions: list[int]) -> str:
    if close_ms <= decisions[0]:
        return "by_first_decision"
    if close_ms <= decisions[-1]:
        return "between_decisions"
    return "after_final_decision"


def save_review(out: Path, frame: pd.DataFrame, timeframe: str, row: dict) -> list[dict]:
    """Store raw causal inputs unchanged, then draw separately identified boxes."""
    windows = case_windows(frame, timeframe, row["available_at_ms"])
    artifacts = []
    for window in windows:
        name = f"{timeframe}_{pd.Timestamp(row['available_at_ms'], unit='ms', tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y%m%d_%H%M')}_w{len(window.times)}"
        raw_path, overlay_path = out / "model_inputs" / f"{name}.png", out / "human_review" / f"{name}.png"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(raw_path), window.image):
            raise OSError("Cannot write exact model image")
        overlay = window.image.copy()
        candidates = [p for p in row["proposals"] if p["window_len"] == len(window.times)]
        for proposal in candidates:
            x, y, w, h = [proposal[f"prediction_{key}_norm"] for key in ("cx", "cy", "w", "h")]
            a = (round((x - w / 2) * overlay.shape[1]), round((y - h / 2) * overlay.shape[0]))
            b = (round((x + w / 2) * overlay.shape[1]), round((y + h / 2) * overlay.shape[0]))
            color = (120, 110, 15) if proposal["side"] == "long" else (75, 70, 190)
            cv2.rectangle(overlay, a, b, color, 2)
            caption = f"{proposal['side']} {proposal['confidence']:.3f} core={proposal['core_length_bars']} post={proposal['post_bars']} pass={proposal['structural_pass']}"
            cv2.putText(overlay, caption, (max(12, a[0]), max(35, a[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, .55, color, 1, cv2.LINE_AA)
        title = f"{timeframe} | data closed by {local_time(row['available_at_ms'])} | {'NO BOX' if not candidates else str(len(candidates)) + ' boxes'}"
        # A header is added after inference; it never enters the model.
        overlay = cv2.copyMakeBorder(overlay, 55, 0, 0, 0, cv2.BORDER_CONSTANT, value=(245, 247, 249))
        cv2.putText(overlay, title, (12, 33), cv2.FONT_HERSHEY_SIMPLEX, .65, (55, 55, 55), 1, cv2.LINE_AA)
        cv2.imwrite(str(overlay_path), overlay)
        artifacts.append({"timeframe": timeframe, "available_at": local_time(row["available_at_ms"]),
            "window_len": len(window.times), "model_input": str(raw_path),
            "human_review": str(overlay_path), "input_pixel_sha256": window.input_pixel_sha256,
            "input_file_sha256": sha_file(raw_path), "selection": row["selection"]})
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles3m", type=Path, required=True)
    parser.add_argument("--candles5m", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--from-close", default="2026-08-31T06:00:00+08:00")
    parser.add_argument("--until-close", default="2026-08-31T10:00:00+08:00")
    parser.add_argument("--decision-close", action="append", default=[])
    parser.add_argument("--setup-start", help="Frozen 1H setup interval start, explicit zoned datetime")
    parser.add_argument("--setup-end", help="Frozen 1H setup interval end (exclusive), explicit zoned datetime")
    args = parser.parse_args()
    if bool(args.setup_start) != bool(args.setup_end):
        raise ValueError("Supply both 1H setup edges or neither")
    setup = (stamp(args.setup_start), stamp(args.setup_end)) if args.setup_start else None
    if setup and setup[0] >= setup[1]:
        raise ValueError("Setup interval must be nonempty")
    decisions = sorted(stamp(x) for x in (args.decision_close or ["2026-08-31T08:00:00+08:00", "2026-08-31T09:00:00+08:00"]))
    first, last = stamp(args.from_close), stamp(args.until_close)
    if first > min(decisions) or last < max(decisions):
        raise ValueError("All decision snapshots must lie inside the prespecified scan")
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("Refusing to overwrite a prior case run")
    if sha_file(MODEL_PATH) != MODEL_SHA256:
        raise ValueError("Pinned model bytes do not match")
    frames = {tf: load_frame(path, tf) for tf, path in (("3m", args.candles3m), ("5m", args.candles5m))}
    checks = [verify_windows(frame, tf, decision) for tf, frame in frames.items() for decision in decisions]
    import torch
    from ultralytics import YOLO
    model = YOLO(str(MODEL_PATH))
    if model.names != MODEL_CLASSES:
        raise ValueError("Pinned classes do not match")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    args.out.mkdir(parents=True, exist_ok=True)
    rows, artifacts = [], []
    started = time.monotonic()
    for tf, frame in frames.items():
        duration = DURATIONS[tf]
        ends = (frame.ts + duration).loc[(frame.ts + duration >= first) & (frame.ts + duration <= last)].to_list()
        if ends != list(range(first, last + 1, duration)):
            raise ValueError("Scan endpoint grid is incomplete")
        first_pass = set()
        for available in ends:
            available = int(available)
            windows = case_windows(frame, tf, available)
            predictions = model.predict(source=[w.image for w in windows], batch=2, device=device, **PREDICT_PARAMETERS)
            if len(predictions) != 2:
                raise ValueError("Unexpected prediction count")
            proposals = []
            for prediction, window in zip(predictions, windows):
                boxes = prediction.boxes
                if boxes is not None and len(boxes):
                    proposals.extend(parse_prediction(boxes.xywhn.cpu().numpy(), boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy(), window, SYMBOL, tf))
            for proposal in proposals:
                proposal.update(core_start_bj=local_time(proposal["core_start_ms"]),
                    core_end_bj=local_time(proposal["core_end_ms"]),
                    observed_at_bj=local_time(available),
                    setup_overlap_seconds=(max(0, min(proposal["core_end_ms"] + duration, setup[1]) - max(proposal["core_start_ms"], setup[0])) / 1000 if setup else None))
            passed = [p for p in proposals if p["side"] == "long" and p["structural_pass"]]
            item = {"timeframe": tf, "available_at_ms": available, "available_at_bj": local_time(available),
                "phase": phase(available, decisions), "long_structural_pass": bool(passed),
                "any_long_proposal": any(p["side"] == "long" for p in proposals),
                "max_long_pass_confidence": max((p["confidence"] for p in passed), default=None),
                "input_pixel_hashes": [w.input_pixel_sha256 for w in windows], "proposals": proposals}
            selections = []
            if available in decisions:
                selections.append("exact_prespecified_decision")
            if passed and item["phase"] not in first_pass:
                first_pass.add(item["phase"])
                selections.append("first_long_structure_in_phase")
            if selections:
                item["selection"] = selections
                artifacts.extend(save_review(args.out, frame, tf, item))
            rows.append(item)
        print(json.dumps({"timeframe": tf, "endpoints_complete": len(ends), "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
    counts = {}
    for tf in DURATIONS:
        counts[tf] = {}
        for label in ("by_first_decision", "between_decisions", "after_final_decision"):
            group = [row for row in rows if row["timeframe"] == tf and row["phase"] == label]
            passed = [row for row in group if row["long_structural_pass"]]
            counts[tf][label] = {"endpoints": len(group), "long_structure_endpoints": len(passed),
                "any_long_proposal_endpoints": sum(row["any_long_proposal"] for row in group),
                "first_long_structure_bj": passed[0]["available_at_bj"] if passed else None}
    receipt = {"symbol": SYMBOL, "illustrative_case_only": True, "holdout_configuration_consumption": 1,
        "owner_authorization": "2026-09-09 explicit USELESS 2026-08-31 multi-timeframe case request",
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "builder_sha256": sha_file(Path(__file__)), "weight_path": str(MODEL_PATH), "weight_sha256": MODEL_SHA256,
        "renderer_sha256": sha_file(Path(__file__).resolve().parents[1] / "layers/l1_detection/render.py"),
        "predict_parameters": PREDICT_PARAMETERS, "device": device,
        "packages": {name: importlib.metadata.version(name) for name in ("torch", "ultralytics", "numpy", "pandas")},
        "source_files": {tf: {"path": str(path), "sha256": sha_file(path), "rows": len(frames[tf]),
            "history_origin_bj": local_time(int(frames[tf].ts.iloc[0]))} for tf, path in (("3m", args.candles3m), ("5m", args.candles5m))},
        "from_close": local_time(first), "until_close": local_time(last), "decision_closes": list(map(local_time, decisions)),
        "setup_interval_bj_exclusive_right": list(map(local_time, setup)) if setup else None,
        "causal_checks": checks, "counts": counts, "artifacts": artifacts,
        "limitations": ["Single successful case; no economic/false-positive validation.",
            "3m is an unvalidated timeframe extrapolation of the 15m-trained detector.",
            "Long structural pass is not proof of association to the 1H frozen IMACD setup.",
            "Post-core candles are visible only when closed by the actual detector decision time."]}
    (args.out / "endpoints.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.out), "counts": counts, "artifacts": len(artifacts)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

"""YOLO detector as judgment-layer candidate source (mainline after 2026-07-15).

Replaces rule `scan_candidates` / `forward_candidate_indices` for the critical
path. Downstream labeling, features, LightGBM freeze, and TP5/SL2 exits are
unchanged — only *which bars* are proposed as signals differs.

Requires ultralytics/torch (use `.venv/bin/python` for any path that calls
`scan_series_with_yolo`).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.detection.data import add_mas
from src.detection.render import render_chart
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS
from src.judgment.labeling import HORIZON_BARS

PROJECT_DIR = Path(__file__).resolve().parents[2]
WINDOW = 200
STRIDE = 50
DEFAULT_CONF = 0.30
DEFAULT_WEIGHTS = PROJECT_DIR / "models" / "owner_best.pt"
# Base temp dir; each predict call uses a unique filename (thread-safe live scan).
_TMP_DIR = PROJECT_DIR / "data"

_model_cache: dict[str, Any] = {}
_predict_lock = threading.Lock()
_predict_device: str | None = None


def _resolve_predict_device() -> str:
    """Prefer CUDA on VPS; fall back to CPU (MPS has hung multi-series scans)."""
    global _predict_device
    if _predict_device is not None:
        return _predict_device
    forced = os.environ.get("FABLE_YOLO_DEVICE", "").strip()
    if forced:
        _predict_device = forced
        return _predict_device
    try:
        import torch

        if torch.cuda.is_available():
            _predict_device = "0"
            return _predict_device
    except Exception:  # noqa: BLE001
        pass
    _predict_device = "cpu"
    return _predict_device


def right_edge_to_bar(cx: float, w: float, tf, *, n_bars: int) -> int:
    """Normalized box right edge -> bar index within the window."""
    right_px = (cx + w / 2) * tf.width
    if tf.plot_w <= 0:
        return n_bars - 1
    idx = round((right_px - tf.left) / tf.plot_w * (tf.n_bars - 1))
    return int(min(max(idx, 0), tf.n_bars - 1))


def load_yolo_model(weights: str | Path | None = None):
    """Lazy-load and cache YOLO weights (heavy import kept local)."""
    path = str(Path(weights) if weights is not None else DEFAULT_WEIGHTS)
    if path not in _model_cache:
        from ultralytics import YOLO

        if not Path(path).exists():
            raise FileNotFoundError(f"YOLO weights missing: {path}")
        _model_cache[path] = YOLO(path)
    return _model_cache[path]


def scan_series_with_yolo(
    frame: pd.DataFrame,
    model=None,
    *,
    conf: float = DEFAULT_CONF,
    window: int = WINDOW,
    stride: int = STRIDE,
    min_gap: int = MIN_GAP_BARS,
    tmp_png: Path | None = None,
    start_from_i: int | None = None,
    mode: str = "full",
) -> list[int]:
    """Return sorted signal bar indices for one OHLCV frame (causal at each bar).

    mode:
      - "full": offline dataset build (stride over history)
      - "live": forward/mainline — only windows near the right edge (and
        covering start_from_i..end). Avoids multi-hour full-history scans.
    """
    if model is None:
        model = load_yolo_model()
    if len(frame) < WARMUP_BARS + window + 2:
        return []
    enriched_ma = add_mas(frame)
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Unique path per call (thread id + pid) so parallel live scans never clobber.
    if tmp_png is None:
        tmp_png = _TMP_DIR / f"_yolo_cand_tmp_{os.getpid()}_{threading.get_ident()}.png"
    last_start = len(frame) - window
    first_start = WARMUP_BARS
    if start_from_i is not None:
        first_start = max(first_start, int(start_from_i) - window + 1)
    if mode == "live":
        # Live schedule (2026-07-20): pin the tip and two bars back, then
        # coarse stride for context — at most 6 windows. The 14-window
        # "tip-dense" schedule (backs 0..21 + half-stride walk) rested on a
        # false premise: a box's right edge maps to ANY bar inside the window
        # (right_edge_to_bar), so recent-but-not-tip bars are already
        # discoverable from the tip window itself (EDEN 2026-07-19: the tip
        # window's mid-window box mapped 35 bars back). Its real effect was
        # 14/6 x predict cost: pulses went 6->25 min wall, the 15-min cadence
        # degraded to 25 min, and rows landed older than the 30-min freshness
        # gate — the dense schedule destroyed the very tip-latency it chased.
        starts_set: set[int] = set()
        for back in (0, 1, 2):
            s = last_start - back
            if s >= first_start:
                starts_set.add(s)
        s = last_start - stride
        while s >= first_start and len(starts_set) < 6:
            starts_set.add(s)
            s -= stride
        starts = sorted(starts_set, reverse=True)
    else:
        starts = list(range(first_start, last_start + 1, stride))

    chosen: list[int] = []
    device = _resolve_predict_device()
    n_fail = 0
    last_err: str | None = None
    # Render all windows first, then ONE batched predict per series: on CPU
    # the per-call setup (source pipeline, backend checks) dominated with 6
    # single-image calls under the global lock (2026-07-20 telemetry: discover
    # ~500s/pulse ≈ all render+predict). Order of results matches input order.
    rendered: list[tuple[int, object, Path]] = []
    for k, start in enumerate(starts):
        sub = enriched_ma.iloc[start : start + window]
        win_png = tmp_png.with_name(f"{tmp_png.stem}_{k}.png")
        try:
            _, tf = render_chart(sub, out_path=win_png)
        except Exception as exc:  # noqa: BLE001 — keep series alive; count failures
            n_fail += 1
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        rendered.append((start, tf, win_png))
    results = []
    if rendered:
        try:
            # Serialize predict: ultralytics is not reliably thread-safe.
            with _predict_lock:
                results = model.predict(
                    [str(p) for _, _, p in rendered], conf=conf, verbose=False, device=device
                )
        except Exception as exc:  # noqa: BLE001
            n_fail += len(rendered)
            last_err = f"{type(exc).__name__}: {exc}"
            results = []
    for (start, tf, _), res in zip(rendered, results):
        boxes = res.boxes
        if boxes is None:
            continue
        for b in boxes.xywhn.cpu().numpy():
            cx, _, w, _ = map(float, b[:4])
            bar_in_win = right_edge_to_bar(cx, w, tf, n_bars=window)
            signal_i = start + bar_in_win
            if signal_i < WARMUP_BARS or signal_i >= len(frame):
                continue
            # Offline dataset builds need the entry bar for labels; the live
            # path must NOT wait for it -- the tip bar is the whole point of
            # real-time detection (entry fields backfill next pulse).
            if mode != "live" and signal_i + 1 >= len(frame):
                continue
            if start_from_i is not None and signal_i < start_from_i:
                continue
            chosen.append(int(signal_i))
    if n_fail and n_fail >= len(starts):
        # Only noisy when the whole series failed (data/render/device issue).
        print(f"yolo_live: all {n_fail} windows failed last={last_err}", flush=True)
    if not chosen:
        return []
    chosen = sorted(set(chosen))
    deduped: list[int] = []
    for si in chosen:
        if not deduped or si - deduped[-1] >= min_gap:
            deduped.append(si)
    return deduped


def dedupe_indices(indices: list[int], min_gap: int = MIN_GAP_BARS) -> list[int]:
    out: list[int] = []
    for si in sorted(indices):
        if not out or si - out[-1] >= min_gap:
            out.append(int(si))
    return out

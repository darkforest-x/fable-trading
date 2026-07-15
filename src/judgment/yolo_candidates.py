"""YOLO detector as judgment-layer candidate source (mainline after 2026-07-15).

Replaces rule `scan_candidates` / `forward_candidate_indices` for the critical
path. Downstream labeling, features, LightGBM freeze, and TP5/SL2 exits are
unchanged — only *which bars* are proposed as signals differs.

Requires ultralytics/torch (use `.venv/bin/python` for any path that calls
`scan_series_with_yolo`).
"""
from __future__ import annotations

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
TMP_PNG = PROJECT_DIR / "data" / "_yolo_cand_tmp.png"

_model_cache: dict[str, Any] = {}


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
    tmp_png: Path = TMP_PNG,
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
    tmp_png.parent.mkdir(parents=True, exist_ok=True)
    last_start = len(frame) - window
    first_start = WARMUP_BARS
    if start_from_i is not None:
        first_start = max(first_start, int(start_from_i) - window + 1)
    if mode == "live":
        # at most ~6 windows walking back from the tip (≈ stride*5 + one tip)
        starts = []
        s = last_start
        while s >= first_start and len(starts) < 6:
            starts.append(s)
            s -= stride
    else:
        starts = list(range(first_start, last_start + 1, stride))

    chosen: list[int] = []
    for start in starts:
        sub = enriched_ma.iloc[start : start + window]
        try:
            _, tf = render_chart(sub, out_path=tmp_png)
            # device=cpu: MPS predict has hung mid multi-series forward scans on
            # M4 Air; CPU is slower per call but finishes reliably for live mode.
            res = model.predict(str(tmp_png), conf=conf, verbose=False, device="cpu")
        except Exception:
            continue
        if not res:
            continue
        boxes = res[0].boxes
        if boxes is None:
            continue
        for b in boxes.xywhn.cpu().numpy():
            cx, _, w, _ = map(float, b[:4])
            bar_in_win = right_edge_to_bar(cx, w, tf, n_bars=window)
            signal_i = start + bar_in_win
            if signal_i < WARMUP_BARS or signal_i + 1 >= len(frame):
                continue
            if start_from_i is not None and signal_i < start_from_i:
                continue
            chosen.append(int(signal_i))
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

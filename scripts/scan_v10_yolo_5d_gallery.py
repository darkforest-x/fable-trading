#!/usr/bin/env python3
"""Scan last N days of OKX USDT-SWAP 15m with YOLO v10 only; HTML gallery with boxes.

No LightGBM / no TP-SL overlays — detector boxes + conf only.

  PYTHONPATH=/path/to/yoyo-trading:. \\
    .venv/bin/python scripts/scan_v10_yolo_5d_gallery.py --days 5

Requires ultralytics + models/owner_short_star_v10.pt and yoyo L1 candidates
(or PYTHONPATH including yoyo-trading after 2026-08 restructure).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
# L1 lives in this repository: yoyo/layers/l1_detection/
from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402

try:
    from yoyo.layers.l1_detection.candidates import (  # noqa: E402
        DEFAULT_CONF,
        WINDOW,
        load_yolo_model,
        map_box_to_signal,
        right_edge_to_bar,
    )
except ImportError:  # pragma: no cover
    from src.judgment.yolo_candidates import (  # type: ignore
        DEFAULT_CONF,
        WINDOW,
        load_yolo_model,
        right_edge_to_bar,
    )

    map_box_to_signal = None  # type: ignore

WEIGHTS = PROJECT / "models" / "owner_short_star_v10.pt"
OUT = PROJECT / "analysis" / "output" / "v10_yolo_5d_gallery"
# Box label + HTML header used to hardcode "v10", so a run with --weights
# pointing elsewhere produced a gallery that still claimed v10. Both are now
# derived from the weights actually loaded; main() overwrites them.
MODEL_TAG = "short_star_v10"
WEIGHTS_NAME = "models/owner_short_star_v10.pt"
TIP_EDGE = 2  # tip / tip-1 / tip-2
MIN_GAP_BARS = 18  # same-symbol de-dupe (~4.5h)
# Review chart: keep signal near horizontal center (before ≈ after)
LOOKBACK_BARS = 100   # bars before signal
LOOKAHEAD_BARS = 100  # bars after signal → signal roughly in the middle


def _device() -> str:
    import os

    forced = (os.environ.get("FABLE_YOLO_DEVICE") or "").strip()
    if forced:
        return forced
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "0"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def scan_symbol_range(
    fr: pd.DataFrame,
    model,
    *,
    i_lo: int,
    i_hi: int,
    conf: float,
    device: str,
    tmp_png: Path,
    stride: int = 1,
) -> list[dict]:
    """Causal windows ending at each bar in [i_lo, i_hi]; tip-edge boxes only."""
    hits: list[dict] = []
    n = len(fr)
    if n < WINDOW or i_hi < WINDOW - 1:
        return hits
    i_lo = max(i_lo, WINDOW - 1)
    i_hi = min(i_hi, n - 1)
    stride = max(1, int(stride))
    for end_i in range(i_lo, i_hi + 1, stride):
        start_i = end_i - WINDOW + 1
        win = fr.iloc[start_i : end_i + 1]
        try:
            img, tf = render_chart(win, out_path=None)
            tmp_png.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(tmp_png), img)
            res = model.predict(str(tmp_png), conf=conf, verbose=False, device=device)
        except Exception:  # noqa: BLE001
            continue
        r0 = res[0] if res else None
        if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
            continue
        best = None
        for row, cf in zip(r0.boxes.xywhn.cpu().numpy(), r0.boxes.conf.cpu().numpy()):
            cx, cy, w, h = map(float, row)
            if map_box_to_signal is not None:
                m = map_box_to_signal(
                    cx=cx,
                    w=w,
                    tf=tf,
                    window_start_i=start_i,
                    n_bars=WINDOW,
                    frame_length=n,
                    latest_closed_i=end_i,
                    tip_edge_bars=TIP_EDGE,
                    apply_tip_edge=True,
                    max_global_tip_age_bars=TIP_EDGE,
                    allow_pending_entry=True,
                )
                if not m.accepted:
                    continue
                sig_i = m.mapped_signal_i
                b1 = m.bar_in_window
            else:
                b1 = right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
                if b1 < WINDOW - 1 - TIP_EDGE:
                    continue
                sig_i = start_i + b1
            b0 = right_edge_to_bar(cx - w / 2, 0.0, tf, n_bars=WINDOW)
            cand = {
                "signal_i": int(sig_i),
                "window_end_i": int(end_i),
                "window_start_i": int(start_i),
                "bar0": int(b0),
                "bar1": int(b1),
                "conf": float(cf),
                "cx": cx,
                "cy": cy,
                "bw": w,
                "bh": h,
            }
            if best is None or cand["conf"] > best["conf"]:
                best = cand
        if best is not None:
            hits.append(best)
    # de-dupe by min_gap, keep highest conf
    hits.sort(key=lambda h: -h["conf"])
    kept: list[dict] = []
    used: list[int] = []
    for h in hits:
        si = h["signal_i"]
        if any(abs(si - u) < MIN_GAP_BARS for u in used):
            continue
        used.append(si)
        kept.append(h)
    kept.sort(key=lambda h: h["signal_i"])
    return kept


def _flat_pad_rows(template: pd.Series, n: int, *, side: str) -> pd.DataFrame:
    """Build n synthetic flat bars so the signal can stay at a fixed mid-slot.

    Review-only padding (never used for detection). Uses last/first close so
    render_chart still draws candles; volume=0 marks them as non-real.
    """
    if n <= 0:
        return pd.DataFrame(columns=template.index)
    px = float(template["close"] if side == "right" else template["open"])
    rows = []
    base_t = pd.Timestamp(template["open_time"])
    step = pd.Timedelta(minutes=15)
    for k in range(n):
        row = template.copy()
        for c in ("open", "high", "low", "close"):
            if c in row.index:
                row[c] = px
        if "volume" in row.index:
            row["volume"] = 0.0
        if side == "right":
            row["open_time"] = base_t + step * (k + 1)
        else:
            row["open_time"] = base_t - step * (n - k)
        rows.append(row)
    return pd.DataFrame(rows)


def draw_box_chart(
    fr: pd.DataFrame,
    hit: dict,
    *,
    symbol: str,
    out_path: Path,
    lookback: int = LOOKBACK_BARS,
    lookahead: int = LOOKAHEAD_BARS,
) -> None:
    """Global review chart: signal ALWAYS at horizontal center.

    Fixed layout: lookback bars left + signal + lookahead bars right.
    Real klines when the series has them; flat pad only when tip/start cuts
    the series short — never shift the window so the box ends on the right edge.
    Detection stays causal; padding is review-only.
    """
    sig_i = int(hit["signal_i"])
    if "sma20" not in fr.columns:
        fr = add_mas(fr)

    n = len(fr)
    half_l, half_r = int(lookback), int(lookahead)
    # Real slice around signal — do NOT shift left when after-data is short.
    i0 = max(0, sig_i - half_l)
    i1 = min(n - 1, sig_i + half_r)
    before_real = sig_i - i0
    after_real = i1 - sig_i
    pad_left = half_l - before_real
    pad_right = half_r - after_real

    view = fr.iloc[i0 : i1 + 1].copy()
    if pad_left > 0:
        view = pd.concat(
            [_flat_pad_rows(view.iloc[0], pad_left, side="left"), view],
            ignore_index=True,
        )
    if pad_right > 0:
        view = pd.concat(
            [view, _flat_pad_rows(view.iloc[-1], pad_right, side="right")],
            ignore_index=True,
        )
    # Recompute MAs on padded view so lines continue; pad is flat so MAs converge.
    if "sma20" in view.columns:
        view = add_mas(view)

    img, tf = render_chart(view, out_path=None)
    n_local = len(view)
    loc_sig = half_l  # fixed mid-slot by construction

    # Detector box in absolute bar indices → local coords (pad_left offset)
    win_end = int(hit.get("window_end_i", sig_i))
    win_start = int(hit.get("window_start_i", win_end - WINDOW + 1))
    abs_b0 = win_start + int(hit.get("bar0", WINDOW - 1 - TIP_EDGE))
    abs_b1 = win_start + int(hit.get("bar1", WINDOW - 1))
    abs_b0 = max(0, min(abs_b0, n - 1))
    abs_b1 = max(abs_b0, min(abs_b1, n - 1))
    # map absolute → local: local = abs - i0 + pad_left
    loc0 = int(abs_b0 - i0 + pad_left)
    loc1 = int(abs_b1 - i0 + pad_left)
    loc0 = max(0, min(loc0, n_local - 1))
    loc1 = max(loc0, min(loc1, n_local - 1))

    hi = float(fr["high"].iloc[abs_b0 : abs_b1 + 1].max())
    lo = float(fr["low"].iloc[abs_b0 : abs_b1 + 1].min())
    y1 = tf.y_at(hi)
    y2 = tf.y_at(lo)
    x1 = tf.x_at(loc0)
    x2 = tf.x_at(loc1)
    if x2 < x1:
        x1, x2 = x2, x1
    if x2 - x1 < 6:
        x2 = x1 + 6
    color = (0, 180, 255)  # BGR orange
    cv2.rectangle(img, (x1, min(y1, y2)), (x2, max(y1, y2)), color, 2, cv2.LINE_AA)

    xs = tf.x_at(min(max(loc_sig, 0), n_local - 1))
    cv2.line(img, (xs, 0), (xs, img.shape[0] - 1), (220, 220, 220), 1, cv2.LINE_AA)
    # Shade the whole right half (after signal) so center is obvious
    if loc_sig < n_local - 1:
        x_after = tf.x_at(min(loc_sig + 1, n_local - 1))
        x_end = tf.x_at(n_local - 1)
        overlay = img.copy()
        cv2.rectangle(
            overlay,
            (x_after, 0),
            (x_end, img.shape[0] - 1),
            (40, 40, 20),
            -1,
        )
        cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)
        # If we padded the tip, dim the synthetic flat zone further
        if pad_right > 0:
            x_pad0 = tf.x_at(min(loc_sig + after_real + 1, n_local - 1))
            overlay2 = img.copy()
            cv2.rectangle(
                overlay2,
                (x_pad0, 0),
                (x_end, img.shape[0] - 1),
                (60, 60, 60),
                -1,
            )
            cv2.addWeighted(overlay2, 0.25, img, 0.75, 0, img)
            cv2.putText(
                img,
                "no future bars (padded)",
                (min(x_pad0 + 6, img.shape[1] - 220), 76),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (160, 160, 160),
                1,
                cv2.LINE_AA,
            )

    st = pd.Timestamp(fr["open_time"].iloc[sig_i])
    label = f"{MODEL_TAG} conf={hit['conf']:.3f}"
    cv2.putText(
        img, label, (x1, max(18, min(y1, y2) - 6)),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA,
    )
    pad_note = ""
    if pad_right > 0 or pad_left > 0:
        pad_note = f"  |  pad L{pad_left}/R{pad_right}"
    title = (
        f"{symbol}  signal={st}  |  -{before_real} real / +{after_real} real after"
        f"  |  mid {half_l}+1+{half_r}{pad_note}"
    )
    cv2.putText(
        img, title, (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA,
    )
    cv2.putText(
        img, "signal", (xs + 4, 40),
        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA,
    )
    if after_real > 0:
        cv2.putText(
            img, "after signal (real klines)", (min(xs + 12, img.shape[1] - 200), 58),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 200, 120), 1, cv2.LINE_AA,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


# Review bands — fixed thresholds (causal features; review-only, not L2).
CONF_BANDS: list[tuple[str, float, float]] = [
    ("0.90–1.00", 0.90, 1.01),
    ("0.80–0.89", 0.80, 0.90),
    ("0.70–0.79", 0.70, 0.80),
    ("0.60–0.69", 0.60, 0.70),
    ("0.50–0.59", 0.50, 0.60),
    ("0.30–0.49", 0.30, 0.50),
]
# 动能: ret_16 = close/close.shift(16)-1  (16×15m ≈ 4h)
MOM_BANDS: list[tuple[str, float, float]] = [
    ("强涨≥+2%", 0.02, 10.0),
    ("弱涨+0.5~2%", 0.005, 0.02),
    ("横盘±0.5%", -0.005, 0.005),
    ("弱跌-2~-0.5%", -0.02, -0.005),
    ("强跌≤-2%", -10.0, -0.02),
]
# 成交量: volume_ratio = vol / rolling_mean(20)  (candidates_v206)
VOL_BANDS: list[tuple[str, float, float]] = [
    ("放量≥2.0x", 2.0, 100.0),
    ("偏放1.2~2x", 1.2, 2.0),
    ("常态0.8~1.2x", 0.8, 1.2),
    ("缩量<0.8x", 0.0, 0.8),
]


def _band_id(value: float, bands: list[tuple[str, float, float]], *, default: str = "other") -> str:
    if value is None or (isinstance(value, float) and (value != value)):  # NaN
        return default
    v = float(value)
    for label, lo, hi in bands:
        if lo <= v < hi:
            return label
    return default


def conf_band_id(conf: float) -> str:
    return _band_id(conf, CONF_BANDS)


def mom_band_id(ret16: float) -> str:
    return _band_id(ret16, MOM_BANDS)


def vol_band_id(vol_ratio: float) -> str:
    return _band_id(vol_ratio, VOL_BANDS)


def _count_bands(cards: list[dict], key: str, bands: list[tuple[str, float, float]]) -> dict[str, int]:
    out = {lab: 0 for lab, _, _ in bands}
    out["other"] = 0
    for c in cards:
        raw = c.get(key)
        if raw is None:
            out["other"] += 1
            continue
        lab = _band_id(float(raw), bands)
        out[lab] = out.get(lab, 0) + 1
    return out


def write_html(cards: list[dict], out_html: Path, *, days: int, conf: float) -> None:
    """2-up gallery: conf × 动能(ret16) × 成交量(vol_ratio) filters (AND)."""
    cards_sorted = sorted(
        cards,
        key=lambda c: (-float(c["conf"]), str(c.get("signal_time", "")), str(c.get("symbol", ""))),
    )
    conf_counts = _count_bands(cards_sorted, "conf", CONF_BANDS)
    # attach band labels for counting mom/vol even if missing
    for c in cards_sorted:
        c["_conf_band"] = conf_band_id(float(c["conf"]))
        c["_mom_band"] = (
            mom_band_id(float(c["ret_16"])) if c.get("ret_16") is not None else "other"
        )
        c["_vol_band"] = (
            vol_band_id(float(c["volume_ratio"]))
            if c.get("volume_ratio") is not None
            else "other"
        )
    mom_counts: dict[str, int] = {lab: 0 for lab, _, _ in MOM_BANDS}
    mom_counts["other"] = 0
    vol_counts: dict[str, int] = {lab: 0 for lab, _, _ in VOL_BANDS}
    vol_counts["other"] = 0
    for c in cards_sorted:
        mom_counts[c["_mom_band"]] = mom_counts.get(c["_mom_band"], 0) + 1
        vol_counts[c["_vol_band"]] = vol_counts.get(c["_vol_band"], 0) + 1

    has_mv = any(c.get("ret_16") is not None for c in cards_sorted)

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>",
        f"<title>v10 YOLO last {days}d · manifest {len(cards)}</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;background:#0e1116;color:#e6edf3;margin:20px 24px 48px}",
        "h1{font-size:1.35rem;margin:0 0 8px}",
        ".meta{color:#8b949e;margin:0 0 12px;font-size:14px;line-height:1.5}",
        ".filter-dock{position:sticky;top:0;z-index:5;background:#0e1116f2;"
        "padding:10px 0 12px;backdrop-filter:blur(6px);border-bottom:1px solid #30363d;"
        "margin:0 0 18px}",
        ".frow{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:6px 0}",
        ".flabel{min-width:4.5rem;color:#8b949e;font-size:12px;font-weight:600}",
        ".frow button{border:1px solid #30363d;background:#21262d;color:#e6edf3;"
        "border-radius:999px;padding:5px 11px;font-size:12.5px;cursor:pointer}",
        ".frow button:hover{border-color:#58a6ff}",
        ".frow button.active{background:#1f6feb;border-color:#1f6feb;color:#fff}",
        ".frow button .n{opacity:.85;margin-left:4px;font-variant-numeric:tabular-nums}",
        ".sec{margin:28px 0 10px;font-size:1.05rem;color:#c9d1d9;border-bottom:1px solid #30363d;"
        "padding-bottom:6px}",
        ".sec .n{color:#8b949e;font-weight:400;font-size:0.9rem}",
        ".sec.hidden,.grid.hidden{display:none}",
        ".grid{display:grid;grid-template-columns:1fr 1fr;gap:20px 16px;"
        "max-width:1800px;margin:0 auto}",
        "@media(max-width:1100px){.grid{grid-template-columns:1fr}}",
        ".card{border:1px solid #30363d;border-radius:12px;overflow:hidden;background:#161b22;"
        "box-shadow:0 8px 24px rgba(0,0,0,.35);margin:0}",
        ".card.hidden{display:none}",
        ".card img{width:100%;max-height:none;height:auto;display:block;background:#000;"
        "image-rendering:auto}",
        ".card .cap{padding:10px 12px;font-size:13px;line-height:1.45}",
        ".tag{display:inline-block;background:#1f6feb33;color:#58a6ff;padding:2px 8px;"
        "border-radius:999px;font-size:12px;margin:0 4px 0 0}",
        ".tag.mom{background:#3fb95033;color:#3fb950}",
        ".tag.vol{background:#d2992233;color:#d29922}",
        "a.open{color:#58a6ff;font-size:13px;margin-left:8px}",
        "#count{color:#58a6ff}",
        "</style></head><body>",
        f"<h1>YOLO {MODEL_TAG} · OKX USDT-SWAP 15m · last {days} days</h1>",
        f"<p class='meta'>weights=<code>{WEIGHTS_NAME}</code> · scan conf≥{conf} · "
        f"tip-edge≤{TIP_EDGE} · min_gap={MIN_GAP_BARS} bars · "
        f"<b>manifest only: <span id='count'>{len(cards)}</span> / {len(cards)}</b> · "
        "L2 <b>not</b> used<br>"
        "筛选三维可叠加（AND）：<b>conf</b> · <b>动能 ret_16（约4h）</b> · "
        "<b>成交量 volume/mean20</b>（与 candidates_v206 同定义，仅审图）。"
        "「分组」切换 section 轴。点「原图」全尺寸。</p>",
        "<div class='filter-dock'>",
        # group-by axis
        "<div class='frow' id='group-by'>",
        "<span class='flabel'>分组</span>",
        "<button type='button' class='active' data-group='conf'>按 conf</button>",
        "<button type='button' data-group='mom'>按 动能</button>",
        "<button type='button' data-group='vol'>按 成交量</button>",
        "</div>",
        # conf
        "<div class='frow' id='filt-conf'>",
        "<span class='flabel'>conf</span>",
        f"<button type='button' class='active' data-dim='conf' data-val='all'>全部"
        f"<span class='n'>{len(cards)}</span></button>",
    ]
    for lab, _, _ in CONF_BANDS:
        n = conf_counts.get(lab, 0)
        if not n:
            continue
        parts.append(
            f"<button type='button' data-dim='conf' data-val='{html.escape(lab)}'>"
            f"{html.escape(lab)}<span class='n'>{n}</span></button>"
        )
    parts.append("</div>")

    # momentum
    parts.append("<div class='frow' id='filt-mom'><span class='flabel'>动能</span>")
    parts.append(
        f"<button type='button' class='active' data-dim='mom' data-val='all'>全部"
        f"<span class='n'>{len(cards)}</span></button>"
    )
    for lab, _, _ in MOM_BANDS:
        n = mom_counts.get(lab, 0)
        if not n and has_mv:
            continue
        if not has_mv:
            n = 0
        parts.append(
            f"<button type='button' data-dim='mom' data-val='{html.escape(lab)}'>"
            f"{html.escape(lab)}<span class='n'>{n}</span></button>"
        )
    if mom_counts.get("other"):
        parts.append(
            f"<button type='button' data-dim='mom' data-val='other'>"
            f"未知<span class='n'>{mom_counts['other']}</span></button>"
        )
    parts.append("</div>")

    # volume
    parts.append("<div class='frow' id='filt-vol'><span class='flabel'>成交量</span>")
    parts.append(
        f"<button type='button' class='active' data-dim='vol' data-val='all'>全部"
        f"<span class='n'>{len(cards)}</span></button>"
    )
    for lab, _, _ in VOL_BANDS:
        n = vol_counts.get(lab, 0)
        if not n and has_mv:
            continue
        parts.append(
            f"<button type='button' data-dim='vol' data-val='{html.escape(lab)}'>"
            f"{html.escape(lab)}<span class='n'>{n}</span></button>"
        )
    if vol_counts.get("other"):
        parts.append(
            f"<button type='button' data-dim='vol' data-val='other'>"
            f"未知<span class='n'>{vol_counts['other']}</span></button>"
        )
    parts.append("</div></div>")  # end filter-dock

    def _metric_tags(c: dict) -> str:
        bits = [f"<span class='tag'>conf {float(c['conf']):.3f}</span>"]
        if c.get("ret_16") is not None:
            r = float(c["ret_16"]) * 100
            bits.append(f"<span class='tag mom'>ret16 {r:+.2f}%</span>")
        if c.get("volume_ratio") is not None:
            bits.append(f"<span class='tag vol'>vol {float(c['volume_ratio']):.2f}x</span>")
        return "".join(bits)

    def _emit_card(c: dict) -> str:
        return (
            f"<figure class='card' data-conf='{html.escape(c['_conf_band'])}' "
            f"data-mom='{html.escape(c['_mom_band'])}' "
            f"data-vol='{html.escape(c['_vol_band'])}' "
            f"data-conf-v='{float(c['conf']):.4f}'>"
            f"<a href='{html.escape(c['rel_img'])}' target='_blank' rel='noopener'>"
            f"<img src='{html.escape(c['rel_img'])}' loading='lazy'/></a>"
            "<figcaption class='cap'>"
            f"<b>{html.escape(c['symbol'])}</b> {_metric_tags(c)}"
            f"<a class='open' href='{html.escape(c['rel_img'])}' target='_blank'>原图</a><br>"
            f"{html.escape(str(c['signal_time']))} UTC · signal_i={c.get('signal_i', '')}"
            "</figcaption></figure>"
        )

    # --- section layouts (all cards present in each; JS shows one axis) ---
    # conf sections
    for lab, lo, hi in CONF_BANDS:
        band_cards = [c for c in cards_sorted if lo <= float(c["conf"]) < hi]
        if not band_cards:
            continue
        parts.append(
            f"<h2 class='sec' data-sec-axis='conf' data-sec-val='{html.escape(lab)}'>"
            f"conf {html.escape(lab)} <span class='n'>({len(band_cards)})</span></h2>"
        )
        parts.append(
            f"<div class='grid' data-sec-axis='conf' data-sec-val='{html.escape(lab)}'>"
        )
        for c in band_cards:
            parts.append(_emit_card(c))
        parts.append("</div>")

    # mom sections
    for lab, lo, hi in MOM_BANDS:
        band_cards = [c for c in cards_sorted if c["_mom_band"] == lab]
        if not band_cards:
            continue
        parts.append(
            f"<h2 class='sec hidden' data-sec-axis='mom' data-sec-val='{html.escape(lab)}'>"
            f"动能 {html.escape(lab)} <span class='n'>({len(band_cards)})</span></h2>"
        )
        parts.append(
            f"<div class='grid hidden' data-sec-axis='mom' data-sec-val='{html.escape(lab)}'>"
        )
        for c in band_cards:
            parts.append(_emit_card(c))
        parts.append("</div>")
    other_mom = [c for c in cards_sorted if c["_mom_band"] == "other"]
    if other_mom:
        parts.append(
            f"<h2 class='sec hidden' data-sec-axis='mom' data-sec-val='other'>"
            f"动能 未知 <span class='n'>({len(other_mom)})</span></h2>"
        )
        parts.append("<div class='grid hidden' data-sec-axis='mom' data-sec-val='other'>")
        for c in other_mom:
            parts.append(_emit_card(c))
        parts.append("</div>")

    # vol sections
    for lab, lo, hi in VOL_BANDS:
        band_cards = [c for c in cards_sorted if c["_vol_band"] == lab]
        if not band_cards:
            continue
        parts.append(
            f"<h2 class='sec hidden' data-sec-axis='vol' data-sec-val='{html.escape(lab)}'>"
            f"成交量 {html.escape(lab)} <span class='n'>({len(band_cards)})</span></h2>"
        )
        parts.append(
            f"<div class='grid hidden' data-sec-axis='vol' data-sec-val='{html.escape(lab)}'>"
        )
        for c in band_cards:
            parts.append(_emit_card(c))
        parts.append("</div>")
    other_vol = [c for c in cards_sorted if c["_vol_band"] == "other"]
    if other_vol:
        parts.append(
            f"<h2 class='sec hidden' data-sec-axis='vol' data-sec-val='other'>"
            f"成交量 未知 <span class='n'>({len(other_vol)})</span></h2>"
        )
        parts.append("<div class='grid hidden' data-sec-axis='vol' data-sec-val='other'>")
        for c in other_vol:
            parts.append(_emit_card(c))
        parts.append("</div>")

    parts.append(
        """
<script>
(function(){
  const state = { conf: 'all', mom: 'all', vol: 'all', group: 'conf' };
  const countEl = document.getElementById('count');
  const total = """
        + str(len(cards))
        + """;

  function matchCard(el){
    const okC = state.conf === 'all' || el.dataset.conf === state.conf;
    const okM = state.mom === 'all' || el.dataset.mom === state.mom;
    const okV = state.vol === 'all' || el.dataset.vol === state.vol;
    return okC && okM && okV;
  }

  function apply(){
    // which section axis is visible
    document.querySelectorAll('[data-sec-axis]').forEach(node => {
      const onAxis = node.getAttribute('data-sec-axis') === state.group;
      node.classList.toggle('hidden', !onAxis);
    });
    // cards: only those under active axis, and matching filters
    let shown = 0;
    document.querySelectorAll('.grid[data-sec-axis]').forEach(grid => {
      const onAxis = grid.getAttribute('data-sec-axis') === state.group;
      if (!onAxis) return;
      let any = false;
      grid.querySelectorAll('.card').forEach(el => {
        const ok = matchCard(el);
        el.classList.toggle('hidden', !ok);
        if (ok) { shown++; any = true; }
      });
      const secVal = grid.getAttribute('data-sec-val');
      const sec = document.querySelector(
        'h2.sec[data-sec-axis="'+state.group+'"][data-sec-val="'+CSS.escape(secVal)+'"]'
      );
      if (sec) sec.classList.toggle('hidden', !any);
      grid.classList.toggle('hidden', !any);
    });
    if (countEl) countEl.textContent = shown;
  }

  document.querySelectorAll('.frow button[data-dim]').forEach(btn => {
    btn.addEventListener('click', () => {
      const dim = btn.dataset.dim;
      const val = btn.dataset.val;
      state[dim] = val;
      document.querySelectorAll('.frow button[data-dim="'+dim+'"]').forEach(b => {
        b.classList.toggle('active', b.dataset.val === val);
      });
      apply();
    });
  });
  document.querySelectorAll('#group-by button').forEach(btn => {
    btn.addEventListener('click', () => {
      state.group = btn.dataset.group;
      document.querySelectorAll('#group-by button').forEach(b => {
        b.classList.toggle('active', b.dataset.group === state.group);
      });
      apply();
    });
  });
  apply();
})();
</script>
</body></html>"""
    )
    out_html.write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--n-symbols", type=int, default=0, help="0=all SWAP")
    ap.add_argument("--weights", type=Path, default=WEIGHTS)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument(
        "--stride",
        type=int,
        default=2,
        help="bars between window ends (1=every 15m; 2≈2x faster, may miss some)",
    )
    args = ap.parse_args()

    if not args.weights.is_file():
        raise SystemExit(f"missing weights {args.weights}")

    global MODEL_TAG, WEIGHTS_NAME
    stem = args.weights.stem
    if stem in ("best", "last"):  # runs/detect/.../<run_name>/weights/best.pt
        stem = args.weights.parent.parent.name
    MODEL_TAG = stem[len("owner_"):] if stem.startswith("owner_") else stem
    try:
        WEIGHTS_NAME = str(args.weights.resolve().relative_to(PROJECT))
    except ValueError:
        WEIGHTS_NAME = str(args.weights)

    out_dir: Path = args.out_dir
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    for p in img_dir.glob("*.png"):
        p.unlink()

    device = _device()
    print(f"device={device} weights={args.weights} conf={args.conf} days={args.days}", flush=True)
    model = load_yolo_model(args.weights)

    # Prefer this repo's kline tree (loader default may point at yoyo-trading).
    kline_dir = PROJECT / "data" / "kline_fetched"
    if not kline_dir.is_dir():
        raise SystemExit(f"missing kline dir {kline_dir}")
    groups = list_series(kline_dir, bar="15m")
    series = []
    for (src, sym), paths in groups.items():
        if src != "okx" or not str(sym).endswith("_USDT_SWAP"):
            continue
        if is_stockish(sym):
            continue
        series.append((sym, paths))
    series.sort(key=lambda x: x[0])
    if args.n_symbols > 0:
        series = series[: args.n_symbols]
    print(f"symbols={len(series)} kline_dir={kline_dir}", flush=True)

    # global time window from latest available bar across a sample
    now_candidates = []
    for sym, paths in series[:20]:
        fr = load_series(paths)
        if not fr.empty:
            now_candidates.append(pd.to_datetime(fr["open_time"], utc=True).max())
    if not now_candidates:
        raise SystemExit("no kline data")
    t_hi = max(now_candidates)
    t_lo = t_hi - pd.Timedelta(days=args.days)
    print(f"time window UTC {t_lo} .. {t_hi}", flush=True)
    print(
        f"chart view: -{LOOKBACK_BARS} bars before signal, "
        f"+{LOOKAHEAD_BARS} bars after (review path)",
        flush=True,
    )

    cards: list[dict] = []
    tmp = out_dir / "_tmp_win.png"
    t0 = time.time()
    for i, (sym, paths) in enumerate(series, 1):
        fr = load_series(paths)
        if fr.empty or len(fr) < WINDOW:
            continue
        fr = add_mas(fr)
        times = pd.to_datetime(fr["open_time"], utc=True)
        mask = (times >= t_lo) & (times <= t_hi)
        idxs = np.flatnonzero(mask.to_numpy())
        if len(idxs) == 0:
            continue
        i_lo, i_hi = int(idxs[0]), int(idxs[-1])
        hits = scan_symbol_range(
            fr,
            model,
            i_lo=i_lo,
            i_hi=i_hi,
            conf=args.conf,
            device=device,
            tmp_png=tmp,
            stride=args.stride,
        )
        for h in hits:
            st = pd.Timestamp(times.iloc[h["signal_i"]])
            fname = f"{sym}_{st.strftime('%Y%m%d_%H%M')}_c{h['conf']:.2f}.png"
            # sanitize
            fname = fname.replace("/", "_")
            draw_box_chart(fr, h, symbol=sym, out_path=img_dir / fname)
            cards.append(
                {
                    "symbol": sym,
                    "signal_time": str(st),
                    "signal_i": h["signal_i"],
                    "conf": round(h["conf"], 4),
                    "rel_img": f"images/{fname}",
                }
            )
        if i % 20 == 0 or hits:
            print(
                f"[{i}/{len(series)}] {sym} hits={len(hits)} total_cards={len(cards)} "
                f"elapsed={time.time()-t0:.0f}s",
                flush=True,
            )
        # incremental HTML so browser can open before full run finishes
        if i % 10 == 0 or hits:
            preview = sorted(cards, key=lambda c: c["signal_time"], reverse=True)
            write_html(preview, out_dir / "index.html", days=args.days, conf=args.conf)

    cards.sort(key=lambda c: c["signal_time"], reverse=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "days": args.days,
                "conf": args.conf,
                "weights": str(args.weights),
                "device": device,
                "t_lo": str(t_lo),
                "t_hi": str(t_hi),
                "n_symbols": len(series),
                "n_signals": len(cards),
                "cards": cards,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_html(cards, out_dir / "index.html", days=args.days, conf=args.conf)
    print(f"DONE signals={len(cards)} -> {out_dir / 'index.html'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

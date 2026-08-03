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
# restructure: L1 lives in yoyo-trading
_YOYO = Path.home() / "yoyo-trading"
if _YOYO.is_dir():
    sys.path.insert(0, str(_YOYO))

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
TIP_EDGE = 2  # tip / tip-1 / tip-2
MIN_GAP_BARS = 18  # same-symbol de-dupe (~4.5h)
# Review chart: context before signal + path after signal (not used for detection)
LOOKBACK_BARS = 120   # ~1.25d before
LOOKAHEAD_BARS = 96   # ~1d after (or whatever history has)


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


def draw_box_chart(
    fr: pd.DataFrame,
    hit: dict,
    *,
    symbol: str,
    out_path: Path,
    lookback: int = LOOKBACK_BARS,
    lookahead: int = LOOKAHEAD_BARS,
) -> None:
    """Global review chart: history before signal + path after, with YOLO box.

    Detection stays causal (box from tip window). This view is for human review
    only and intentionally shows bars after signal_i.
    """
    sig_i = int(hit["signal_i"])
    if "sma20" not in fr.columns:
        fr = add_mas(fr)

    i0 = max(0, sig_i - lookback)
    i1 = min(len(fr) - 1, sig_i + lookahead)
    view = fr.iloc[i0 : i1 + 1].copy()
    img, tf = render_chart(view, out_path=None)
    n_local = len(view)
    loc_sig = sig_i - i0

    # Absolute bar span of the detector box (from tip window geometry)
    win_end = int(hit.get("window_end_i", sig_i))
    win_start = int(hit.get("window_start_i", win_end - WINDOW + 1))
    abs_b0 = win_start + int(hit.get("bar0", WINDOW - 1 - TIP_EDGE))
    abs_b1 = win_start + int(hit.get("bar1", WINDOW - 1))
    abs_b0 = max(0, min(abs_b0, len(fr) - 1))
    abs_b1 = max(abs_b0, min(abs_b1, len(fr) - 1))
    loc0 = max(0, abs_b0 - i0)
    loc1 = min(n_local - 1, abs_b1 - i0)

    # Price bounds of the boxed segment for y mapping
    hi = float(fr["high"].iloc[abs_b0 : abs_b1 + 1].max())
    lo = float(fr["low"].iloc[abs_b0 : abs_b1 + 1].min())
    # Prefer original normalized box y if present (scale into this chart)
    if all(k in hit for k in ("cy", "bh")):
        # map using view price range via tf — use segment hi/lo as primary
        pass
    y1 = tf.y_at(hi)
    y2 = tf.y_at(lo)
    x1 = tf.x_at(loc0)
    x2 = tf.x_at(loc1)
    if x2 < x1:
        x1, x2 = x2, x1
    # minimum box width ~ half a candle
    if x2 - x1 < 6:
        x2 = x1 + 6
    color = (0, 180, 255)  # BGR orange
    cv2.rectangle(img, (x1, min(y1, y2)), (x2, max(y1, y2)), color, 2, cv2.LINE_AA)

    # Signal bar vertical (where the trade is dated)
    xs = tf.x_at(min(max(loc_sig, 0), n_local - 1))
    cv2.line(img, (xs, 0), (xs, img.shape[0] - 1), (220, 220, 220), 1, cv2.LINE_AA)
    # Shade post-signal region lightly so "after" is obvious
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

    # Labels
    st = pd.Timestamp(fr["open_time"].iloc[sig_i])
    after_n = i1 - sig_i
    before_n = sig_i - i0
    label = f"v10 conf={hit['conf']:.3f}"
    cv2.putText(
        img, label, (x1, max(18, min(y1, y2) - 6)),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA,
    )
    title = f"{symbol}  signal={st}  |  -{before_n} bars  |  +{after_n} bars after"
    cv2.putText(
        img, title, (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA,
    )
    cv2.putText(
        img, "signal", (xs + 4, 40),
        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA,
    )
    if after_n > 0:
        cv2.putText(
            img, "after signal", (min(xs + 12, img.shape[1] - 120), 58),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 200, 120), 1, cv2.LINE_AA,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def write_html(cards: list[dict], out_html: Path, *, days: int, conf: float) -> None:
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>",
        f"<title>v10 YOLO last {days}d signals</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;background:#0e1116;color:#e6edf3;margin:20px 24px 48px}",
        "h1{font-size:1.35rem;margin:0 0 8px}",
        ".meta{color:#8b949e;margin:0 0 20px;font-size:14px;line-height:1.5}",
        ".grid{display:flex;flex-direction:column;gap:28px;max-width:1400px;margin:0 auto}",
        ".card{border:1px solid #30363d;border-radius:12px;overflow:hidden;background:#161b22;"
        "box-shadow:0 8px 24px rgba(0,0,0,.35)}",
        ".card img{width:100%;max-height:none;height:auto;display:block;background:#000;"
        "image-rendering:auto}",
        ".card .cap{padding:12px 16px;font-size:14px;line-height:1.5}",
        ".tag{display:inline-block;background:#1f6feb33;color:#58a6ff;padding:2px 8px;"
        "border-radius:999px;font-size:12px;margin-left:6px}",
        "a.open{color:#58a6ff;font-size:13px;margin-left:12px}",
        "</style></head><body>",
        f"<h1>YOLO v10 · OKX USDT-SWAP 15m · last {days} days</h1>",
        f"<p class='meta'>weights=<code>owner_short_star_v10.pt</code> · conf≥{conf} · "
        f"tip-edge≤{TIP_EDGE} · min_gap={MIN_GAP_BARS} bars · "
        f"<b>{len(cards)}</b> signals · L2 judgment <b>not</b> used<br>"
        "大图单列展示；点「原图」可全尺寸打开 PNG。</p>",
        "<div class='grid'>",
    ]
    for c in cards:
        parts.append(
            "<figure class='card'>"
            f"<a href='{html.escape(c['rel_img'])}' target='_blank' rel='noopener'>"
            f"<img src='{html.escape(c['rel_img'])}' loading='lazy'/></a>"
            "<figcaption class='cap'>"
            f"<b>{html.escape(c['symbol'])}</b> "
            f"<span class='tag'>conf {c['conf']:.3f}</span>"
            f"<a class='open' href='{html.escape(c['rel_img'])}' target='_blank'>原图</a><br>"
            f"{html.escape(c['signal_time'])} UTC · signal_i={c['signal_i']}"
            "</figcaption></figure>"
        )
    parts.append("</div></body></html>")
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

#!/usr/bin/env python3
"""Render every saved 4h YOLO event with the complete frozen future visible.

This is a delivery-only view over an immutable scan.  It never runs YOLO and
does not modify ``summary.json`` or the canonical ``charts/`` that the offline
verifier replays.  The large panel renders every frozen confirmed 4h candle for
the symbol, while the inset replays the exact W18/W19 model input and verifies
its saved pixel hash before any output is written.

Chart contract:
- Question: what happened after the event was first observable, through the
  common frozen snapshot end?
- Surface: standalone 1920x1400 PNGs plus a local filterable HTML gallery.
- Main panel: the complete per-symbol snapshot; solid FIRST SIGNAL boundary,
  dashed representative detection boundary, and an explicit future-bar count.
- Inset: exact causal model input with the unchanged raw YOLO rectangle.
- Palette: one direction color plus neutral boundaries; labels and line styles
  keep the states readable without relying on color alone.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd

from scripts import scan_4h_ma_launch_yolo_latest as base
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1"
CHART_DIRNAME = "charts_global_future"
GALLERY_NAME = "all_global_future_charts.html"
RECEIPT_NAME = "global_future_gallery_receipt.json"


def bar_index(frame: pd.DataFrame, value: object) -> int:
    """Return the exact causal bar index for a UTC timestamp."""

    target = base.utc(value)
    times = pd.to_datetime(frame["open_time"], utc=True)
    matches = np.flatnonzero(times.eq(target).to_numpy())
    if len(matches) != 1:
        raise base.FourHourYoloError(
            f"expected one bar for {target.isoformat()}, found {len(matches)}"
        )
    return int(matches[0])


def future_bar_count(frame: pd.DataFrame, event: Mapping[str, Any]) -> int:
    """Count confirmed bars strictly after the event's first detected bar."""

    return len(frame) - bar_index(frame, event["first_detection_bar_open_time"]) - 1


def event_chart_filename(event: Mapping[str, Any], order: int) -> str:
    """Keep a source filename when present, otherwise derive a stable one.

    Semantic-gate results are created directly from the accepted-candidate
    ledger and intentionally have no canonical ``charts/`` artifact.  Their
    delivery-only global-future filename therefore comes from event order,
    symbol, and side without mutating the frozen scan summary.
    """

    source = str(event.get("chart") or "").strip()
    if source:
        return Path(source).name
    symbol = str(event["symbol"]).replace("_USDT_SWAP", "")
    side = "LONG" if int(event["class_id"]) == 0 else "SHORT"
    return f"{int(order):03d}_{symbol}_{side}.png"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp_cst(value: object) -> str:
    return base.utc(value).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M")


def _draw_global_panel(
    event: Mapping[str, Any], frame: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, Any, dict[str, int]]:
    """Render all frozen bars and add explicit causal/future boundaries."""

    main, tf = render_chart(
        frame,
        width=base.MAIN_WIDTH,
        height=base.MAIN_HEIGHT,
        out_path=None,
    )
    first_i = bar_index(frame, event["first_detection_bar_open_time"])
    last_i = bar_index(frame, event["last_detection_bar_open_time"])
    representative_i = int(event["window_end_i"])
    if not 0 <= representative_i < len(frame):
        raise base.FourHourYoloError("representative endpoint is outside frozen frame")
    if base.utc(frame.iloc[representative_i]["open_time"]) != base.utc(event["window_end_time"]):
        raise base.FourHourYoloError("representative endpoint timestamp drifted")
    if not first_i <= last_i <= representative_i:
        raise base.FourHourYoloError(
            f"invalid event chronology: first={first_i} last={last_i} representative={representative_i}"
        )

    first_x = base.x_at_float(tf, first_i)
    representative_x = base.x_at_float(tf, representative_i)
    future_bars = len(frame) - first_i - 1

    # Make the observed-future region locatable without obscuring its candles.
    if future_bars:
        future_start_x = base.x_at_float(tf, first_i + 1)
        tint = main.copy()
        cv2.rectangle(
            tint,
            (future_start_x, 0),
            (base.MAIN_WIDTH - 1, base.MAIN_HEIGHT - 1),
            (248, 245, 236),
            -1,
        )
        main = cv2.addWeighted(tint, 0.11, main, 0.89, 0)
        arrow_y = 38
        cv2.arrowedLine(
            main,
            (min(base.MAIN_WIDTH - 28, future_start_x + 8), arrow_y),
            (base.MAIN_WIDTH - 30, arrow_y),
            (80, 80, 80),
            2,
            cv2.LINE_AA,
            tipLength=0.012,
        )
        label_x = min(max(12, future_start_x + 18), base.MAIN_WIDTH - 360)
        base.put_text(
            main,
            f"OBSERVED FUTURE  {future_bars} BARS / {future_bars * 4 / 24:.1f} DAYS",
            (label_x, 29),
            scale=0.48,
            color=(65, 65, 65),
            thickness=2,
        )
    else:
        base.put_text(
            main,
            "NO OBSERVED FUTURE YET — SIGNAL IS ON THE FROZEN SNAPSHOT TIP",
            (base.MAIN_WIDTH - 620, 29),
            scale=0.48,
            color=(45, 45, 180),
            thickness=2,
        )

    # FIRST SIGNAL is the causal boundary.  REP DETECT identifies the exact
    # saved model input shown in the inset when an event spans several bars.
    cv2.line(main, (first_x, 8), (first_x, base.MAIN_HEIGHT - 15), (25, 25, 25), 3, cv2.LINE_AA)
    first_label_x = min(max(4, first_x + 6), base.MAIN_WIDTH - 150)
    base.put_text(main, "FIRST SIGNAL", (first_label_x, 62), scale=0.44, thickness=2)
    if representative_i != first_i:
        base.dashed_vertical(main, representative_x, 70, base.MAIN_HEIGHT - 15)
        rep_label_x = min(max(4, representative_x + 6), base.MAIN_WIDTH - 150)
        base.put_text(main, "REP DETECT", (rep_label_x, 88), scale=0.42, thickness=2)

    # Project the unchanged raw box into the global panel.  It can be narrow at
    # this scale, so a direction-colored top marker points to its true center.
    start_i = int(event["window_start_i"])
    end_i = int(event["window_end_i"])
    model_window = frame.iloc[start_i : end_i + 1]
    clean_input, input_tf = render_chart(model_window, out_path=None)
    if base.pixel_sha256(clean_input) != str(event["input_pixel_sha256"]):
        raise base.FourHourYoloError("model input pixel replay drifted")
    x0, y0, x1, y1 = base.project_raw_box(
        event,
        input_tf=input_tf,
        context_tf=tf,
        context_start_i=0,
    )
    direction_color = base.CLASS_COLORS[int(event["class_id"])]
    cv2.rectangle(main, (x0, y0), (x1, y1), direction_color, 4, cv2.LINE_AA)
    box_center = int(round((x0 + x1) / 2))
    marker = np.array(
        [[box_center, 94], [max(0, box_center - 9), 76], [min(base.MAIN_WIDTH - 1, box_center + 9), 76]],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(main, marker, direction_color, cv2.LINE_AA)
    return main, clean_input, tf, {
        "first_i": first_i,
        "last_i": last_i,
        "representative_i": representative_i,
        "future_bars": future_bars,
    }


def render_global_future_event(
    event: Mapping[str, Any],
    *,
    frame: pd.DataFrame,
    order: int,
    total: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build one full-snapshot image and return its delivery metadata."""

    main, clean_input, global_tf, indices = _draw_global_panel(event, frame)
    overlay = clean_input.copy()
    raw_x0, raw_y0, raw_x1, raw_y1 = base.normalized_box_corners(event)
    cv2.rectangle(
        overlay,
        (raw_x0, raw_y0),
        (raw_x1, raw_y1),
        base.CLASS_COLORS[int(event["class_id"])],
        4,
        cv2.LINE_AA,
    )

    canvas = np.full((base.CANVAS_HEIGHT, base.CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    direction = "LONG" if int(event["class_id"]) == 0 else "SHORT"
    symbol = str(event["symbol"]).replace("_USDT_SWAP", "")
    first_available = base.utc(event["first_available_at"])
    snapshot_last_open = base.utc(frame.iloc[-1]["open_time"])
    snapshot_last_available = snapshot_last_open + base.BAR_DELTA
    future_bars = indices["future_bars"]

    gate_label = " | SEMANTIC GATE PASS" if bool(event.get("semantic_gate_pass")) else ""
    base.put_text(
        canvas,
        f"{symbol}USDT.P 4h | GLOBAL FUTURE VIEW{gate_label} | {direction} conf {float(event['confidence']):.3f} | {order:03d}/{total:03d}",
        (24, 38),
        scale=0.68,
        thickness=2,
    )
    base.put_text(
        canvas,
        f"ALL {len(frame)} frozen confirmed bars | {_stamp_cst(frame.iloc[0]['open_time'])} -> {_stamp_cst(frame.iloc[-1]['open_time'])} CST | observed future after first signal: {future_bars} bars ({future_bars * 4 / 24:.1f} days)",
        (24, 72),
        scale=0.48,
        color=(55, 55, 55),
    )
    if bool(event.get("semantic_gate_pass")):
        gate_text = (
            "causal gate PASS | "
            f"MA envelope {float(event['semantic_ma_envelope_atr']):.2f} ATR | "
            f"close-to-MA {float(event['semantic_max_close_to_ma_envelope_atr']):.2f} ATR | "
            f"post2 {float(event['semantic_post2_progress_atr']):+.2f} ATR | "
            f"exact W{int(event['window_len'])} input retained at right"
        )
    else:
        gate_text = (
            f"first signal available {_stamp_cst(first_available)} CST | "
            f"snapshot fully available {_stamp_cst(snapshot_last_available)} CST | "
            f"exact W{int(event['window_len'])} input retained at right"
        )
    base.put_text(canvas, gate_text, (24, 102), scale=0.46, color=(75, 75, 75))
    canvas[
        base.MAIN_Y : base.MAIN_Y + base.MAIN_HEIGHT,
        base.MAIN_X : base.MAIN_X + base.MAIN_WIDTH,
    ] = main

    times = pd.to_datetime(frame["open_time"], utc=True).reset_index(drop=True)
    for local_i in np.linspace(0, len(frame) - 1, 7).round().astype(int):
        x = base.MAIN_X + base.x_at_float(global_tf, int(local_i))
        stamp = base.utc(times.iloc[int(local_i)]).tz_convert("Asia/Shanghai")
        base.put_text(
            canvas,
            f"{stamp:%m-%d %H:%M}",
            (max(0, x - 48), base.MAIN_Y + base.MAIN_HEIGHT + 24),
            scale=0.40,
            color=(80, 80, 80),
        )

    for fraction in np.linspace(0.08, 0.92, 5):
        price = global_tf.price_max - fraction * (global_tf.price_max - global_tf.price_min)
        y = base.MAIN_Y + int(round(global_tf.top + fraction * global_tf.plot_h))
        base.put_text(
            canvas,
            base.price_text(price),
            (base.CANVAS_WIDTH - 118, y),
            scale=0.40,
            color=(75, 75, 75),
        )

    footer_y = 926
    base.put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.64, thickness=2)
    base.put_text(
        canvas,
        "Top: every frozen 4h candle for this symbol. FIRST SIGNAL is the causal event boundary; all bars to its right are observed future.",
        (28, footer_y + 34),
        scale=0.43,
    )
    base.put_text(
        canvas,
        "Dashed REP DETECT is the exact representative input endpoint. Current-tip events honestly show 0 future bars; no candles are fabricated.",
        (28, footer_y + 64),
        scale=0.43,
    )
    base.put_text(
        canvas,
        "Review-only future context is physically separate from model input and does not alter detections, confidence, thresholds, or event IDs.",
        (28, footer_y + 94),
        scale=0.43,
        color=(45, 45, 180),
        thickness=2,
    )
    base.put_text(
        canvas,
        "EXACT CAUSAL MODEL INPUT",
        (base.CANVAS_WIDTH - base.INSET_WIDTH - 18, footer_y),
        scale=0.60,
        thickness=2,
    )
    inset = cv2.resize(
        overlay,
        (base.INSET_WIDTH, base.INSET_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )
    inset_x, inset_y = base.CANVAS_WIDTH - base.INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + base.INSET_HEIGHT, inset_x : inset_x + base.INSET_WIDTH] = inset
    cv2.rectangle(
        canvas,
        (inset_x, inset_y),
        (inset_x + base.INSET_WIDTH - 1, inset_y + base.INSET_HEIGHT - 1),
        (65, 65, 65),
        2,
    )
    return canvas, {
        "event_id": str(event["event_id"]),
        "symbol": str(event["symbol"]),
        "first_signal_bar_index": indices["first_i"],
        "representative_bar_index": indices["representative_i"],
        "snapshot_bars": len(frame),
        "future_bars_after_first_signal": future_bars,
        "future_days_after_first_signal": future_bars * 4 / 24,
        "first_signal_available_at": first_available.isoformat(),
        "snapshot_last_available_at": snapshot_last_available.isoformat(),
    }


def _gallery_document(rows: list[dict[str, Any]]) -> str:
    gated = bool(rows) and all(
        bool(row["event"].get("semantic_gate_pass")) for row in rows
    )
    title_prefix = "4h YOLO + 因果语义门" if gated else "4h YOLO"
    cards: list[str] = []
    for row in rows:
        event = row["event"]
        symbol = str(event["symbol"]).replace("_USDT_SWAP", "USDT.P")
        side = "LONG" if int(event["class_id"]) == 0 else "SHORT"
        status = "CURRENT" if bool(event["is_current_latest_bar"]) else "HISTORICAL"
        confidence = float(event["confidence"])
        future_bars = int(row["future_bars"])
        first = pd.Timestamp(event["first_available_at"]).tz_convert("Asia/Shanghai")
        path = str(row["chart"])
        gate_status = "GATED" if bool(event.get("semantic_gate_pass")) else ""
        searchable = f"{symbol} {side} {status} {gate_status}".lower()
        future_label = (
            f"未来 {future_bars} 根 / {future_bars * 4 / 24:.1f} 天"
            if future_bars
            else "未来 0 根（快照最右端）"
        )
        cards.append(
            f'''<article class="card" data-search="{html.escape(searchable)}" data-conf="{confidence:.9f}" data-future="{future_bars}" data-first="{first.isoformat()}">
  <header><strong>{html.escape(symbol)}</strong>
    <span class="{side.lower()}">{side}</span><span>{status}</span><span>{gate_status}</span>
    <b>conf {confidence:.4f}</b><span>首次可见 {first:%m-%d %H:%M} CST</span>
    <span class="future">{future_label}</span>
  </header>
  <a href="{html.escape(path)}" target="_blank" title="打开 1920×1400 原图">
    <img src="{html.escape(path)}" loading="lazy" alt="{html.escape(symbol)} {side} global future">
  </a>
</article>'''
        )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_prefix}全局未来 K 线（{len(rows)} 张）</title>
<style>
body{{margin:0;background:#111;color:#eee;font:14px/1.5 -apple-system,"PingFang SC",sans-serif}}
.toolbar{{position:sticky;top:0;z-index:5;padding:12px 16px;background:#181818f2;border-bottom:1px solid #444;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
h1{{font-size:18px;margin:0 14px 0 0}} input,select{{background:#262626;color:#fff;border:1px solid #555;border-radius:6px;padding:8px 10px}}
button{{background:#333;color:#fff;border:1px solid #666;border-radius:6px;padding:7px 10px;cursor:pointer}}
.note{{width:100%;color:#c9c9c9}} #gallery{{display:grid;grid-template-columns:1fr;gap:18px;padding:18px}}
.card{{background:#f7f7f7;color:#111;border-radius:8px;overflow:hidden}} header{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 14px;background:#e9e9e9}}
header strong{{font-size:18px}} .long{{color:#087f5b;font-weight:800}} .short{{color:#c92a2a;font-weight:800}} .future{{font-weight:800}}
img{{display:block;width:100%;height:auto}} .hidden{{display:none}} .hint{{color:#bbb}}
@media(min-width:1500px){{#gallery.cols2{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
</style></head><body>
<div class="toolbar"><h1>{title_prefix} 全局未来 K 线：<span id="shown">{len(rows)}</span> / {len(rows)}</h1>
<input id="query" placeholder="筛选币种 / LONG / SHORT / CURRENT">
<input id="minconf" type="number" min="0.25" max="1" step="0.05" value="0.25" title="最低 confidence">
<input id="minfuture" type="number" min="0" step="1" value="0" title="至少包含多少根未来K线">
<select id="sort"><option value="oldest">最早信号优先</option><option value="newest">最新信号优先</option><option value="future">未来根数最多</option><option value="confidence">置信度最高</option></select>
<button onclick="setCols(1)">单列大图</button><button onclick="setCols(2)">双列</button>
<span class="hint">点击图片打开 1920×1400 原图</span>
<div class="note">每张主图显示该币冻结快照的全部确认 4h K 线；实线是首次信号，右侧是当时尚未知、现在已观察到的全部未来。{('这里只展示通过冻结因果语义门的候选；' if gated else '')}快照最右端信号未来为 0。</div></div>
<main id="gallery">{''.join(cards)}</main>
<script>
const gallery=document.getElementById('gallery'),q=document.getElementById('query'),mc=document.getElementById('minconf'),mf=document.getElementById('minfuture'),sort=document.getElementById('sort');
const cards=[...document.querySelectorAll('.card')];
function refresh(){{const s=q.value.trim().toLowerCase(),c=parseFloat(mc.value||'0'),f=parseInt(mf.value||'0',10);let n=0;cards.forEach(x=>{{const ok=x.dataset.search.includes(s)&&parseFloat(x.dataset.conf)>=c&&parseInt(x.dataset.future,10)>=f;x.classList.toggle('hidden',!ok);if(ok)n++;}});const mode=sort.value;cards.sort((a,b)=>mode==='oldest'?a.dataset.first.localeCompare(b.dataset.first):mode==='newest'?b.dataset.first.localeCompare(a.dataset.first):mode==='future'?parseInt(b.dataset.future)-parseInt(a.dataset.future):parseFloat(b.dataset.conf)-parseFloat(a.dataset.conf));cards.forEach(x=>gallery.appendChild(x));document.getElementById('shown').textContent=n;}}
function setCols(n){{gallery.classList.toggle('cols2',n===2);}}
[q,mc,mf,sort].forEach(x=>x.addEventListener('input',refresh));refresh();
</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = args.output.resolve()
    summary_path = out / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    events = [dict(event) for event in summary["signals"]]
    target = out / CHART_DIRNAME
    building = out / f".{CHART_DIRNAME}.building"
    gallery_path = out / GALLERY_NAME
    receipt_path = out / RECEIPT_NAME
    for path in (target, building, gallery_path, receipt_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    building.mkdir()

    frames: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    try:
        for symbol in sorted({str(event["symbol"]) for event in events}):
            frame = pd.read_csv(out / "candles" / f"{symbol}.csv")
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            frames[symbol] = add_mas(frame)

        manifest: list[dict[str, Any]] = []
        for order, event in enumerate(events, 1):
            frame = frames[str(event["symbol"])]
            image, metadata = render_global_future_event(
                event,
                frame=frame,
                order=order,
                total=len(events),
            )
            filename = event_chart_filename(event, order)
            path = building / filename
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise base.FourHourYoloError(f"could not write global-future chart: {path}")
            chart_rel = f"{CHART_DIRNAME}/{filename}"
            metadata["chart"] = chart_rel
            metadata["chart_sha256"] = _sha256(path)
            manifest.append(metadata)
            rows.append(
                {
                    "event": event,
                    "chart": chart_rel,
                    "future_bars": metadata["future_bars_after_first_signal"],
                }
            )

        shutil.move(str(building), str(target))
        gallery_path.write_text(_gallery_document(rows), encoding="utf-8")
        receipt = {
            "verdict": "PASS",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_summary": "summary.json",
            "source_summary_sha256": _sha256(summary_path),
            "charts": len(manifest),
            "model_inference": 0,
            "network_reads": 0,
            "canonical_scan_artifacts_modified": False,
            "future_definition": "all frozen confirmed 4h bars strictly after first_detection_bar_open_time",
            "gallery": GALLERY_NAME,
            "chart_directory": CHART_DIRNAME,
            "events": manifest,
        }
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        if building.exists():
            shutil.rmtree(building)
        raise

    print(
        json.dumps(
            {
                "gallery": str(gallery_path),
                "charts": len(rows),
                "future_bars_min": min(
                    (int(row["future_bars"]) for row in rows), default=0
                ),
                "future_bars_max": max(
                    (int(row["future_bars"]) for row in rows), default=0
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

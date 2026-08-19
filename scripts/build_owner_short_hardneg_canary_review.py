#!/usr/bin/env python3
"""Build the 331-event Owner review surface for the hard-negative canary.

Each event keeps three physically separate artifacts: the exact causal model
input, an annotated copy of that same input, and review-only future context.
The page records browser-local Owner decisions but never writes training labels,
changes ACTIVE, reads holdout rows, or marks any sample training-eligible.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import (  # noqa: E402
    BG,
    CANDLE_GREEN,
    CANDLE_RED,
    IMG_HEIGHT,
    IMG_WIDTH,
    MA_COLORS,
    MARGIN,
    WICK,
    ChartTransform,
    render_chart,
)

from scripts.backtest_owner_short_gold_center_recent import (  # noqa: E402
    HOLDOUT_START,
    load_snapshot,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


PROTOCOL = "owner_short_hardneg_canary_review331_v3_20260811"
DEFAULT_EVENTS = (
    ROOT
    / "analysis/output/owner_short_gold_center_preholdout_canary_20260503_v1"
    / "merged_hardneg/events.jsonl"
)
DEFAULT_SNAPSHOT = (
    ROOT
    / "analysis/output/owner_short_gold_center_preholdout_canary_audit_20260503_v1"
    / "kline_snapshot"
)
DEFAULT_SNAPSHOT_SUMMARY = DEFAULT_SNAPSHOT.parent / "fetch_summary.json"
DEFAULT_OUT = (
    ROOT
    / "analysis/output/owner_short_gold_center_hardneg_canary_review331_v3"
)
DEFAULT_HTML = (
    ROOT
    / "analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_20260811.html"
)
FUTURE_BARS = 48
ORANGE = (20, 145, 225)
PURPLE = (244, 238, 255)
BOUNDARY = (180, 90, 120)
REVIEW_TOP = 50
REVIEW_PRICE_PAD = 0.06
REVIEW_RENDERER = "human_actual_ohlc_ma_autoscale_no_training_floor_v1"


def utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def timestamp_index(frame: pd.DataFrame) -> dict[pd.Timestamp, int]:
    return {
        utc(value): int(index)
        for index, value in enumerate(frame["open_time"])
    }


def future_bar_count(
    decision_time: object,
    available_end: object,
    *,
    requested: int = FUTURE_BARS,
) -> int:
    """Return review bars available before the physically bounded prefix end."""
    if requested < 0:
        raise ValueError("requested future bars must be non-negative")
    delta = (utc(available_end) - utc(decision_time)) / pd.Timedelta(minutes=15)
    return max(0, min(requested, int(delta)))


def draw_normalized_box(image: np.ndarray, row: dict[str, Any]) -> None:
    height, width = image.shape[:2]
    x1 = int(round(float(row["x1n"]) * width))
    y1 = int(round(float(row["y1n"]) * height))
    x2 = int(round(float(row["x2n"]) * width))
    y2 = int(round(float(row["y2n"]) * height))
    cv2.rectangle(image, (x1, y1), (x2, y2), ORANGE, 4, cv2.LINE_AA)


def box_rect(image: np.ndarray, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    xc, yc, box_w, box_h = box
    return (
        int(round((xc - box_w / 2) * width)),
        int(round((yc - box_h / 2) * height)),
        int(round((xc + box_w / 2) * width)),
        int(round((yc + box_h / 2) * height)),
    )


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(path)


def make_human_review_transform(
    frame: pd.DataFrame,
    *,
    width: int = IMG_WIDTH,
    height: int = IMG_HEIGHT,
) -> tuple[ChartTransform, float]:
    """Build an actual-range scale for future-only human review.

    Columns used: low/high plus the precomputed SMA/EMA 20/60/120 values for
    the supplied review window. Unlike the frozen YOLO renderer, this function
    deliberately has no 6% relative-span floor; it must never render training
    inputs because its sole purpose is to make modest future moves legible.
    """
    frame = frame.reset_index(drop=True)
    series = [frame["low"], frame["high"]]
    series.extend(frame[column] for column in ALL_MA_COLS if column in frame)
    values = pd.concat(series).dropna()
    if values.empty:
        raise ValueError("human review frame has no price values")
    actual_low = float(values.min())
    actual_high = float(values.max())
    midpoint = (actual_high + actual_low) / 2
    actual_span = actual_high - actual_low
    safe_span = max(actual_span, abs(midpoint) * 1e-6, 1e-12)
    actual_span_pct = actual_span / max(abs(midpoint), 1e-12) * 100
    price_min = actual_low - safe_span * REVIEW_PRICE_PAD
    price_max = actual_high + safe_span * REVIEW_PRICE_PAD
    plot_width = width - 2 * MARGIN
    plot_height = height - REVIEW_TOP - MARGIN
    candle_half_width = max(1, int(plot_width / max(len(frame), 1) * 0.34))
    return (
        ChartTransform(
            n_bars=len(frame),
            width=width,
            height=height,
            left=MARGIN,
            top=REVIEW_TOP,
            plot_w=plot_width,
            plot_h=plot_height,
            price_min=price_min,
            price_max=price_max,
            candle_half_w=candle_half_width,
        ),
        actual_span_pct,
    )


def render_human_review_chart(
    frame: pd.DataFrame,
    *,
    width: int = IMG_WIDTH,
    height: int = IMG_HEIGHT,
) -> tuple[np.ndarray, ChartTransform, float]:
    """Render review-only future context with its real visible price span."""
    frame = frame.reset_index(drop=True)
    transform, actual_span_pct = make_human_review_transform(
        frame, width=width, height=height
    )
    image = np.full((height, width, 3), BG, dtype=np.uint8)
    for index, row in frame.iterrows():
        x = transform.x_at(index)
        high_y = transform.y_at(row["high"])
        low_y = transform.y_at(row["low"])
        open_y = transform.y_at(row["open"])
        close_y = transform.y_at(row["close"])
        color = (
            CANDLE_GREEN
            if float(row["close"]) >= float(row["open"])
            else CANDLE_RED
        )
        cv2.line(image, (x, high_y), (x, low_y), WICK, 1, cv2.LINE_AA)
        body_top, body_bottom = min(open_y, close_y), max(open_y, close_y)
        if body_bottom - body_top < 2:
            body_bottom = body_top + 2
        cv2.rectangle(
            image,
            (x - transform.candle_half_w, body_top),
            (x + transform.candle_half_w, body_bottom),
            color,
            -1,
            cv2.LINE_AA,
        )
    for column in ALL_MA_COLS:
        if column not in frame:
            continue
        points = [
            (transform.x_at(index), transform.y_at(float(value)))
            for index, value in enumerate(frame[column])
            if pd.notna(value)
        ]
        if len(points) >= 2:
            cv2.polylines(
                image,
                [np.asarray(points, dtype=np.int32)],
                False,
                MA_COLORS[column],
                1,
                cv2.LINE_AA,
            )
    return image, transform, actual_span_pct


def render_event(
    event: dict[str, Any],
    frame: pd.DataFrame,
    output: Path,
    review_id: str,
) -> dict[str, Any]:
    enriched = add_mas(frame)
    by_time = timestamp_index(enriched)
    window_start = by_time[utc(event["window_start_time"])]
    decision = by_time[utc(event["decision_time"])]
    core_start = by_time[utc(event["core_start_time"])]
    core_end = by_time[utc(event["core_end_time"])]
    causal = enriched.iloc[window_start : decision + 1].reset_index(drop=True)
    if len(causal) != int(event["window_len"]):
        raise ValueError(f"causal length mismatch: {event['event_id']}")
    causal_image, _ = render_chart(causal, out_path=None)
    raw_path = output / "causal_input" / f"{review_id}_{event['event_id']}.png"
    write_image(raw_path, causal_image)

    annotated = causal_image.copy()
    draw_normalized_box(annotated, event)
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 42), (250, 250, 250), -1)
    cv2.putText(
        annotated,
        f"{review_id} | CAUSAL INPUT ONLY | first={float(event['conf']):.3f} peak={float(event['event_conf_max']):.3f}",
        (10, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (22, 32, 39),
        2,
        cv2.LINE_AA,
    )
    annotated_path = output / "causal_review" / f"{review_id}_{event['event_id']}.png"
    write_image(annotated_path, annotated)

    available_end = utc(enriched["open_time"].iloc[-1])
    future_bars = future_bar_count(event["decision_time"], available_end)
    future_end = min(len(enriched) - 1, decision + future_bars)
    review = enriched.iloc[window_start : future_end + 1].reset_index(drop=True)
    review_image, transform, actual_span_pct = render_human_review_chart(review)
    decision_local = decision - window_start
    core_start_local = core_start - window_start
    core_end_local = core_end - window_start
    core_box = yolo_box_from_bars(
        transform, review, core_start_local, core_end_local
    )
    if core_box is None:
        raise ValueError(f"future core box missing: {event['event_id']}")
    if future_bars:
        first_future = decision_local + 1
        boundary_x = (
            transform.x_at(decision_local) + transform.x_at(first_future)
        ) // 2
        tint = review_image.copy()
        cv2.rectangle(
            tint,
            (boundary_x, 42),
            (review_image.shape[1] - 1, review_image.shape[0] - 1),
            PURPLE,
            -1,
        )
        review_image[42:] = cv2.addWeighted(
            review_image[42:], 0.68, tint[42:], 0.32, 0
        )
        cv2.line(
            review_image,
            (boundary_x, 42),
            (boundary_x, review_image.shape[0] - 1),
            BOUNDARY,
            4,
            cv2.LINE_AA,
        )
    rect = box_rect(review_image, core_box)
    cv2.rectangle(
        review_image,
        (rect[0], rect[1]),
        (rect[2], rect[3]),
        ORANGE,
        4,
        cv2.LINE_AA,
    )
    cv2.rectangle(
        review_image,
        (0, 0),
        (review_image.shape[1], 42),
        (250, 250, 250),
        -1,
    )
    cv2.putText(
        review_image,
        f"{review_id} | AUTO-Y {actual_span_pct:.2f}% | FUTURE {future_bars} BARS REVIEW ONLY",
        (10, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (22, 32, 39),
        2,
        cv2.LINE_AA,
    )
    future_path = output / "future_review_only" / f"{review_id}_{event['event_id']}.png"
    write_image(future_path, review_image)

    item = dict(event)
    item.update(
        {
            "review_id": review_id,
            "causal_input_path": str(raw_path.relative_to(ROOT)),
            "causal_input_sha256": sha256_file(raw_path),
            "causal_review_path": str(annotated_path.relative_to(ROOT)),
            "causal_review_sha256": sha256_file(annotated_path),
            "future_review_path": str(future_path.relative_to(ROOT)),
            "future_review_sha256": sha256_file(future_path),
            "future_review_bars": future_bars,
            "future_review_actual_span_pct": actual_span_pct,
            "future_review_renderer": REVIEW_RENDERER,
            "future_review_end_time": utc(review["open_time"].iloc[-1]).isoformat(),
            "future_review_only": True,
            "future_data_in_causal_input": False,
            "owner_review_status": "unreviewed",
            "training_eligible": False,
            "production_eligible": False,
            "holdout_read": False,
        }
    )
    return item


def relative_image(path: str, output_html: Path) -> str:
    return Path(os.path.relpath(ROOT / path, output_html.parent)).as_posix()


def card(row: dict[str, Any], output_html: Path) -> str:
    review_id = html.escape(str(row["review_id"]), quote=True)
    event_id = html.escape(str(row["event_id"]), quote=True)
    symbol = html.escape(str(row["symbol"]), quote=True)
    causal = html.escape(relative_image(row["causal_review_path"], output_html), quote=True)
    future = html.escape(relative_image(row["future_review_path"], output_html), quote=True)
    decision = utc(row["decision_time"]).tz_convert("Asia/Shanghai")
    review_context = html.escape(str(row.get("review_context", "")), quote=True)
    context_text = f" · {review_context}" if review_context else ""
    return f"""
    <article class="card" id="card-{review_id}" data-id="{review_id}" data-choice="pending">
      <div class="head"><b>{review_id} · {symbol}</b><span class="chip">未确认</span></div>
      <div class="meta">event {event_id} · 决策 {decision:%m-%d %H:%M} CST · W{int(row['window_len'])} · 核心{int(row['predicted_core_bars'])}根 · 延迟{int(row['decision_delay_bars'])}根 · 对照真实波幅 {float(row['future_review_actual_span_pct']):.2f}% · first {float(row['conf']):.3f} · peak {float(row['event_conf_max']):.3f} · raw {int(row['raw_detection_count'])}{context_text}</div>
      <div class="pair">
        <button type="button" onclick="zoom('{review_id}','causal')"><span>模型当时可见输入＋预测框</span><img loading="lazy" data-role="causal" src="{causal}" alt="{review_id} causal"></button>
        <button type="button" onclick="zoom('{review_id}','future')"><span>人工审核未来（最多48根）</span><img loading="lazy" data-role="future" src="{future}" alt="{review_id} future"></button>
      </div>
      <div class="choices">
        <button type="button" data-value="target" onclick="choose('{review_id}','target')">1 · 对</button>
        <button type="button" data-value="rebox" onclick="choose('{review_id}','rebox')">2 · 框偏</button>
        <button type="button" data-value="hard_negative" onclick="choose('{review_id}','hard_negative')">3 · 不对</button>
      </div>
    </article>"""


def render_html(
    rows: list[dict[str, Any]],
    source: Path,
    output_html: Path,
    *,
    protocol: str = PROTOCOL,
    title: str = "331事件语义审核",
    heading: str = "Owner-short · 331个剩余事件逐张审核",
    description: str = "左图保持模型因果输入；右图按每张真实价差独立自动缩放，图头显示波幅，不再套用训练图6%下限。",
    notice: str = "形态和框都正确按1；形态正确但框偏按2；不是目标形态按3。默认331张全部未确认，审核结果只存在本机浏览器，仍不会自动开训。",
) -> str:
    ids = [str(row["review_id"]) for row in rows]
    js_ids = json.dumps(ids, ensure_ascii=False).replace("</", "<\\/")
    cards = "\n".join(card(row, output_html) for row in rows)
    source_hash = sha256_file(source)
    safe_title = html.escape(title)
    safe_heading = html.escape(heading)
    safe_description = html.escape(description)
    safe_notice = html.escape(notice)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>{safe_title}</title>
<style>:root{{--bg:#f3f6f8;--ink:#17232d;--muted:#60717f;--green:#198754;--orange:#d98700;--red:#d33;--blue:#1769aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header{{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #d8e0e6;padding:14px 20px;box-shadow:0 2px 10px #0001}}h1{{margin:0 0 6px;font-size:24px}}header p{{margin:4px 0;color:#435664}}.bar{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}}.bar button,.bar select{{padding:7px 11px;border:1px solid #b9c5ce;border-radius:8px;background:#fff;cursor:pointer}}.bar .copy{{background:var(--blue);color:#fff;border-color:var(--blue)}}#stats{{margin-left:auto;font-weight:800}}main{{max-width:1500px;margin:auto;padding:16px}}.notice{{background:#fff7df;border:1px solid #ebcb75;border-radius:10px;padding:11px 14px;margin-bottom:14px}}.hotkeys{{font-weight:800;color:#7c4f00}}.grid{{display:grid;grid-template-columns:1fr;gap:16px}}.card{{scroll-margin-top:138px;background:#fff;border:3px solid transparent;border-radius:11px;overflow:hidden;box-shadow:0 2px 10px #0001}}.card.current{{outline:4px solid var(--blue);outline-offset:2px}}.card[data-choice="target"]{{border-color:var(--green)}}.card[data-choice="rebox"]{{border-color:var(--orange)}}.card[data-choice="hard_negative"]{{border-color:var(--red)}}.head{{display:flex;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #e3e8ec}}.chip{{background:#edf1f4;border-radius:999px;padding:3px 8px;font-size:13px}}.meta{{padding:7px 12px;color:var(--muted);font-size:13px}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#dce3e8}}.pair button{{padding:0;border:0;background:#fff;cursor:zoom-in}}.pair span{{display:block;padding:6px 10px;text-align:left;font-weight:800;color:#344b5b;background:#edf3f6}}.pair img{{display:block;width:100%;height:auto}}.choices{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;padding:10px 12px}}.choices button{{padding:11px 5px;border:1px solid #c3cdd5;border-radius:8px;background:#fff;cursor:pointer;font-size:16px}}.choices button.active{{color:#fff;font-weight:800}}.choices [data-value="target"].active{{background:var(--green)}}.choices [data-value="rebox"].active{{background:var(--orange)}}.choices [data-value="hard_negative"].active{{background:var(--red)}}.hidden{{display:none}}textarea{{width:100%;min-height:150px;margin-top:16px}}dialog{{width:min(96vw,1500px);border:0;border-radius:10px}}dialog img{{width:100%}}@media(max-width:900px){{header{{position:static}}.card{{scroll-margin-top:10px}}.pair{{grid-template-columns:1fr}}#stats{{width:100%;margin:0}}}}</style></head>
<body><header><h1>{safe_heading}</h1><p>{safe_description}</p><p class="hotkeys">快捷键：1=对 · 2=框偏 · 3=不对 · Z=撤销；分类后自动跳到下一张。</p><div class="bar"><select id="filter" onchange="applyFilter()"><option value="all">全部</option><option value="pending">未确认</option><option value="target">对</option><option value="rebox">框偏</option><option value="hard_negative">不对</option></select><button onclick="clearAll()">清空选择</button><button class="copy" onclick="copyResults()">复制审核JSON</button><span id="stats"></span></div></header>
<main><div class="notice"><b>只判断三件事：</b>{safe_notice}</div><section class="grid">{cards}</section><textarea id="export" readonly placeholder="点击复制审核JSON"></textarea></main>
<dialog id="zoom"><button onclick="document.getElementById('zoom').close()">关闭</button><img id="zoom-img" alt="zoom"></dialog>
<script>
const IDS={js_ids};
const KEY="{PROTOCOL}:{source_hash}";
const LABELS={{pending:"未确认",target:"对",rebox:"框偏",hard_negative:"不对"}};
let d={{}},currentId=null,history=[];
try{{d=JSON.parse(localStorage.getItem(KEY)||"{{}}")||{{}}}}catch(_e){{d={{}}}}
function save(){{localStorage.setItem(KEY,JSON.stringify(d))}}
function setCurrent(id,scroll=true){{
  if(currentId)document.getElementById('card-'+currentId)?.classList.remove('current');
  currentId=id;
  const card=document.getElementById('card-'+id);
  card?.classList.add('current');
  if(scroll)card?.scrollIntoView({{behavior:'smooth',block:'start'}});
}}
function paint(id){{
  const c=document.getElementById('card-'+id),v=d[id]||'pending';
  c.dataset.choice=v;c.querySelector('.chip').textContent=LABELS[v];
  c.querySelectorAll('[data-value]').forEach(b=>b.classList.toggle('active',b.dataset.value===v));
}}
function counts(){{
  const c={{pending:0,target:0,rebox:0,hard_negative:0}};
  IDS.forEach(id=>c[d[id]||'pending']++);return c;
}}
function stats(){{
  const c=counts();
  document.getElementById('stats').textContent=`对 ${{c.target}} · 框偏 ${{c.rebox}} · 不对 ${{c.hard_negative}} · 未确认 ${{c.pending}}`;
}}
function applyFilter(resetCurrent=true){{
  const f=document.getElementById('filter').value;
  document.querySelectorAll('.card').forEach(c=>c.classList.toggle('hidden',f!=='all'&&c.dataset.choice!==f));
  const current=document.getElementById('card-'+currentId);
  if(resetCurrent&&(!current||current.classList.contains('hidden'))){{
    const first=document.querySelector('.card:not(.hidden)');if(first)setCurrent(first.dataset.id,false);
  }}
}}
function advance(id){{
  const start=IDS.indexOf(id);
  for(let step=1;step<=IDS.length;step++){{
    const candidate=IDS[(start+step)%IDS.length];
    if((d[candidate]||'pending')==='pending'){{
      const filter=document.getElementById('filter');
      if(filter.value!=='all'&&filter.value!=='pending')filter.value='all';
      applyFilter(false);setCurrent(candidate,true);return;
    }}
  }}
  setCurrent(id,true);
}}
function choose(id,v){{
  const previous=Object.prototype.hasOwnProperty.call(d,id)?d[id]:null;
  if(previous!==v){{history.push({{id,previous}});d[id]=v;save();paint(id);stats();applyFilter(false)}}
  advance(id);
}}
function undo(){{
  const last=history.pop();if(!last)return;
  if(last.previous===null)delete d[last.id];else d[last.id]=last.previous;
  save();paint(last.id);stats();document.getElementById('filter').value='all';applyFilter(false);setCurrent(last.id,true);
}}
function clearAll(){{
  if(!confirm('清空全部选择？'))return;
  d={{}};history=[];save();IDS.forEach(paint);stats();document.getElementById('filter').value='all';applyFilter(false);setCurrent(IDS[0],true);
}}
function payload(){{return{{protocol:"{protocol}",source_sha256:"{source_hash}",total:IDS.length,counts:counts(),decisions:Object.fromEntries(IDS.map(id=>[id,d[id]||'pending']))}}}}
async function copyResults(){{
  const t=JSON.stringify(payload(),null,2),b=document.getElementById('export');b.value=t;b.select();
  try{{await navigator.clipboard.writeText(t)}}catch(_e){{document.execCommand('copy')}}
}}
function zoom(id,role){{setCurrent(id,false);document.getElementById('zoom-img').src=document.querySelector(`#card-${{id}} img[data-role="${{role}}"]`).src;document.getElementById('zoom').showModal()}}
document.addEventListener('keydown',e=>{{
  if(e.metaKey||e.ctrlKey||e.altKey||e.target.matches('input,select,textarea'))return;
  const mapping={{'1':'target','2':'rebox','3':'hard_negative'}};
  if(mapping[e.key]&&currentId){{e.preventDefault();choose(currentId,mapping[e.key])}}
  else if(e.key.toLowerCase()==='z'){{e.preventDefault();undo()}}
}});
IDS.forEach(paint);stats();applyFilter();if(!currentId)setCurrent(IDS[0],false);
</script></body></html>"""


def build(
    events_path: Path,
    snapshot_dir: Path,
    snapshot_summary_path: Path,
    output: Path,
    output_html: Path,
) -> dict[str, Any]:
    events = read_jsonl(events_path)
    if len(events) != 331 or len({row["event_id"] for row in events}) != 331:
        raise ValueError(f"expected 331 unique events, got {len(events)}")
    snapshot_summary = json.loads(snapshot_summary_path.read_text(encoding="utf-8"))
    if int(snapshot_summary.get("holdout_rows_materialized", -1)) != 0:
        raise ValueError("snapshot summary does not prove zero holdout rows")
    if utc(snapshot_summary["max_materialized_time"]) >= HOLDOUT_START:
        raise ValueError("audit snapshot touches holdout")
    ranked = sorted(
        events,
        key=lambda row: (
            -float(row["event_conf_max"]),
            str(row["decision_time"]),
            str(row["event_id"]),
        ),
    )
    frames: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for number, event in enumerate(ranked, 1):
        symbol = str(event["symbol"])
        if symbol not in frames:
            frames[symbol] = load_snapshot(snapshot_dir / f"{symbol}.csv")
        rows.append(render_event(event, frames[symbol], output, f"C{number:03d}"))
        if number % 25 == 0 or number == len(ranked):
            print(f"review render [{number}/{len(ranked)}]", flush=True)
    write_jsonl(output / "review_manifest.jsonl", rows)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(render_html(rows, events_path, output_html), encoding="utf-8")
    summary = {
        "protocol": PROTOCOL,
        "events_source": str(events_path.relative_to(ROOT)),
        "events_source_sha256": sha256_file(events_path),
        "rows": len(rows),
        "symbols": len({row["symbol"] for row in rows}),
        "first_confidence": {
            "median": float(np.median([float(row["conf"]) for row in rows])),
            "p90": float(np.quantile([float(row["conf"]) for row in rows], 0.90)),
        },
        "peak_confidence": {
            "median": float(np.median([float(row["event_conf_max"]) for row in rows])),
            "p90": float(np.quantile([float(row["event_conf_max"]) for row in rows], 0.90)),
        },
        "future_bars": dict(sorted(Counter(int(row["future_review_bars"]) for row in rows).items())),
        "future_review_renderer": REVIEW_RENDERER,
        "future_review_actual_span_pct": {
            "p10": float(np.quantile([float(row["future_review_actual_span_pct"]) for row in rows], 0.10)),
            "median": float(np.median([float(row["future_review_actual_span_pct"]) for row in rows])),
            "p90": float(np.quantile([float(row["future_review_actual_span_pct"]) for row in rows], 0.90)),
        },
        "max_future_time": max(row["future_review_end_time"] for row in rows),
        "snapshot_max_materialized_time": snapshot_summary["max_materialized_time"],
        "holdout_rows_materialized": 0,
        "owner_decisions_preselected": 0,
        "training_eligible": 0,
        "html": str(output_html.relative_to(ROOT)),
        "quality_gates": {
            "exactly_331_unique_events": len(rows) == 331 and len({row["event_id"] for row in rows}) == 331,
            "all_three_images_exist": all(
                all((ROOT / row[key]).is_file() for key in ("causal_input_path", "causal_review_path", "future_review_path"))
                for row in rows
            ),
            "future_strictly_preholdout": all(utc(row["future_review_end_time"]) < HOLDOUT_START for row in rows),
            "causal_has_no_future": all(not row["future_data_in_causal_input"] for row in rows),
            "future_uses_human_actual_autoscale": all(
                row["future_review_renderer"] == REVIEW_RENDERER for row in rows
            ),
            "nothing_training_eligible": all(not row["training_eligible"] for row in rows),
            "no_label_directory": not (output / "labels").exists(),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(summary["quality_gates"])
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--snapshot-summary", type=Path, default=DEFAULT_SNAPSHOT_SUMMARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    args = parser.parse_args()
    summary = build(
        args.events,
        args.snapshot_dir,
        args.snapshot_summary,
        args.out,
        args.html,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

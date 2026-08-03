#!/usr/bin/env python3
"""Build a self-contained ETH 3m v10 prelabel review gallery.

The detector is evaluated causally at every historical bar: its input is exactly
the 200 completed candles ending at that bar.  Only boxes whose right edge lands
on tip/tip-1/tip-2 are retained.  The human review panel may show a fixed future
window, but that future is never present in the detector image or prediction.

This is deliberately an out-of-distribution behavior review: v10 was trained on
15m renders, not ETH 3m.  Results must not be used to tune v10, evaluate an
economic strategy, promote weights, or change live configuration.

Sources:
- an explicit ETH_USDT_SWAP 3m CSV supplied with --input;
- runs/detect/runs/detect/owner_short_star_v10/weights/best.pt.

Outputs are resumable and self-contained: the final HTML embeds every JPEG so it
can be sent as one mobile-openable file.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import math
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import ChartTransform, render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

DEFAULT_WEIGHTS = (
    PROJECT / "runs/detect/runs/detect/owner_short_star_v10/weights/best.pt"
)
DEFAULT_OUT = PROJECT / "analysis/output/eth_3m_v10_prelabels_3m"
MA_COLORS = {
    "sma20": "#277da1",
    "sma60": "#4d908e",
    "sma120": "#577590",
    "ema20": "#f94144",
    "ema60": "#f8961e",
    "ema120": "#9b5de5",
}


def load_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"open_time", "open", "high", "low", "close", "volume"}
    if missing := required - set(frame.columns):
        raise SystemExit(f"input is missing columns: {sorted(missing)}")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = (
        frame.dropna(subset=["open_time", "open", "high", "low", "close"])
        .sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )
    if len(frame) < WINDOW + 60:
        raise SystemExit(f"only {len(frame)} usable bars")
    gaps = frame["open_time"].diff().dropna()
    if not gaps.empty and gaps.max() > pd.Timedelta(minutes=3):
        raise SystemExit(f"3m input has a gap of {gaps.max()}; refusing a partial scan")
    return frame


def auto_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    if torch.cuda.is_available():
        return "0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _x_to_bar(norm_x: float, transform: ChartTransform) -> int:
    pixel = float(norm_x) * transform.width
    raw = (pixel - transform.left) / max(transform.plot_w, 1) * (transform.n_bars - 1)
    return int(np.clip(round(raw), 0, transform.n_bars - 1))


def _y_to_price(norm_y: float, transform: ChartTransform) -> float:
    pixel = float(norm_y) * transform.height
    frac = (pixel - transform.top) / max(transform.plot_h, 1)
    return float(transform.price_max - frac * (transform.price_max - transform.price_min))


def best_tip_box(result, transform: ChartTransform) -> dict | None:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None
    best = None
    for row, raw_conf in zip(boxes.xywhn.cpu().numpy(), boxes.conf.cpu().numpy()):
        cx, cy, width, height = map(float, row[:4])
        b1 = right_edge_to_bar(cx, width, transform, n_bars=WINDOW)
        if b1 < WINDOW - 1 - TIP_EDGE_BARS:
            continue
        b0 = _x_to_bar(cx - width / 2, transform)
        top = _y_to_price(cy - height / 2, transform)
        bottom = _y_to_price(cy + height / 2, transform)
        item = {
            "conf": float(raw_conf),
            "box_b0": min(b0, b1),
            "box_b1": max(b0, b1),
            "price_low": min(top, bottom),
            "price_high": max(top, bottom),
        }
        if best is None or item["conf"] > best["conf"]:
            best = item
    return best


def scan(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    *,
    model,
    anchors: list[int],
    conf: float,
    device: str,
    batch_size: int,
    out_dir: Path,
) -> list[dict]:
    checkpoint = out_dir / "scan_checkpoint.json"
    raw: list[dict] = []
    next_pos = 0
    signature = {
        "first_anchor": anchors[0],
        "last_anchor": anchors[-1],
        "anchor_count": len(anchors),
        "anchor_sum": int(sum(anchors)),
        "conf": conf,
    }
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("signature", {}) == signature:
            raw = saved.get("detections", [])
            next_pos = max(0, int(saved.get("next_pos", 0)))
            print(f"resume at anchor {next_pos}/{len(anchors)}, detections={len(raw)}", flush=True)

    total = len(anchors)
    for chunk_start in range(next_pos, total, batch_size):
        tips = anchors[chunk_start : chunk_start + batch_size]
        rendered: list[tuple[int, ChartTransform, np.ndarray]] = []
        for tip_i in tips:
            window = ma_frame.iloc[tip_i - WINDOW + 1 : tip_i + 1]
            image, transform = render_chart(window)
            rendered.append((tip_i, transform, image))
        # Ultralytics accepts OpenCV/BGR ndarrays directly.  Keeping each batch
        # in memory preserves the exact renderer pixels while avoiding tens of
        # thousands of temporary PNG encode/write/read/decode round trips.
        results = model.predict(
                [image for _, _, image in rendered],
                conf=conf,
                verbose=False,
                device=device,
            )
        for (tip_i, transform, _), result in zip(rendered, results):
            box = best_tip_box(result, transform)
            if box is None:
                continue
            signal_i = tip_i - (WINDOW - 1 - box["box_b1"])
            box_start_i = tip_i - (WINDOW - 1 - box["box_b0"])
            raw.append(
                {
                    "tip_i": int(tip_i),
                    "signal_i": int(signal_i),
                    "box_start_i": int(box_start_i),
                    "box_end_i": int(signal_i),
                    "conf": box["conf"],
                    "price_low": box["price_low"],
                    "price_high": box["price_high"],
                }
            )
        done_pos = chunk_start + len(tips)
        checkpoint.write_text(
            json.dumps(
                {
                    "signature": signature,
                    "next_pos": done_pos,
                    "detections": raw,
                }
            ),
            encoding="utf-8",
        )
        completed = done_pos
        if completed % max(batch_size * 20, batch_size) == 0 or done_pos >= total:
            print(
                f"scan {completed}/{total} ({completed / total:.1%}) raw_tip_fires={len(raw)}",
                flush=True,
            )
    return raw


def consolidate(raw: list[dict], *, min_gap: int, cluster_gap: int) -> tuple[list[dict], list[dict]]:
    ordered = sorted(raw, key=lambda row: (row["signal_i"], row["tip_i"], -row["conf"]))
    accepted: list[dict] = []
    for row in ordered:
        if accepted and row["signal_i"] - accepted[-1]["signal_i"] < min_gap:
            accepted[-1]["nearby_raw"] += 1
            accepted[-1]["max_conf"] = max(accepted[-1]["max_conf"], row["conf"])
            continue
        item = dict(row)
        item["nearby_raw"] = 1
        item["max_conf"] = row["conf"]
        accepted.append(item)

    events: list[dict] = []
    for row in accepted:
        if events and row["signal_i"] - events[-1]["last_signal_i"] <= cluster_gap:
            event = events[-1]
            event["last_signal_i"] = row["signal_i"]
            event["accepted_fires"] += 1
            event["raw_fires"] += row["nearby_raw"]
            event["max_conf"] = max(event["max_conf"], row["max_conf"])
            continue
        event = dict(row)
        event["first_signal_i"] = row["signal_i"]
        event["last_signal_i"] = row["signal_i"]
        event["accepted_fires"] = 1
        event["raw_fires"] = row["nearby_raw"]
        events.append(event)
    return accepted, events


def _draw_candles(ax, segment: pd.DataFrame, *, offset: int = 0) -> None:
    x = np.arange(len(segment)) + offset
    o = segment["open"].to_numpy(float)
    h = segment["high"].to_numpy(float)
    l = segment["low"].to_numpy(float)
    c = segment["close"].to_numpy(float)
    up = c >= o
    colors = np.where(up, "#26a69a", "#ef5350")
    ax.vlines(x, l, h, colors="#7d8790", lw=0.65, zorder=2)
    ax.bar(x, np.maximum(abs(c - o), 1e-9), 0.66, bottom=np.minimum(o, c), color=colors, zorder=3)
    for col in ALL_MA_COLS:
        if col in segment:
            ax.plot(x, segment[col].to_numpy(float), color=MA_COLORS.get(col, "#777"), lw=0.75, alpha=0.9)


def _time_ticks(ax, segment: pd.DataFrame, count: int = 7) -> None:
    if segment.empty:
        return
    positions = np.unique(np.linspace(0, len(segment) - 1, min(count, len(segment))).astype(int))
    labels = [pd.Timestamp(segment["open_time"].iloc[i]).strftime("%m-%d\n%H:%M") for i in positions]
    ax.set_xticks(positions, labels, fontsize=7)


def future_stats(frame: pd.DataFrame, event: dict, future_bars: int) -> dict:
    tip_i = event["tip_i"]
    future = frame.iloc[tip_i + 1 : tip_i + future_bars + 1]
    cut = float(frame["close"].iloc[tip_i])
    return {
        "future_return": float(future["close"].iloc[-1] / cut - 1),
        "max_drop": float(1 - future["low"].min() / cut),
        "max_rebound": float(future["high"].max() / cut - 1),
    }


def render_event(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    event: dict,
    *,
    future_bars: int,
    path: Path,
) -> None:
    tip_i = event["tip_i"]
    causal = ma_frame.iloc[tip_i - WINDOW + 1 : tip_i + 1].copy().reset_index(drop=True)
    review = ma_frame.iloc[tip_i - WINDOW + 1 : tip_i + future_bars + 1].copy().reset_index(drop=True)
    local_box_start = event["box_start_i"] - (tip_i - WINDOW + 1)
    local_box_end = event["box_end_i"] - (tip_i - WINDOW + 1)

    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(13.2, 8.2), dpi=105)
    fig.patch.set_facecolor("#0f1113")
    for ax in (ax0, ax1):
        ax.set_facecolor("#171a1e")
        ax.tick_params(colors="#aeb8c1")
        for spine in ax.spines.values():
            spine.set_color("#343b43")
        ax.grid(alpha=0.12, color="#7d8790")

    _draw_candles(ax0, causal)
    ax0.add_patch(
        Rectangle(
            (local_box_start - 0.4, event["price_low"]),
            max(0.8, local_box_end - local_box_start + 0.8),
            max(1e-9, event["price_high"] - event["price_low"]),
            fill=False,
            edgecolor="#ff5c5c",
            lw=2.0,
        )
    )
    ax0.axvline(WINDOW - 0.5, color="#ffb454", ls="--", lw=1.4)
    ax0.set_title(
        f"MODEL VIEW · causal 200 bars · v10 conf {event['conf']:.3f} · no future",
        color="#edf1f5",
        fontsize=10,
        loc="left",
    )
    _time_ticks(ax0, causal)

    _draw_candles(ax1, review)
    ax1.axvspan(WINDOW - 0.5, len(review) - 0.5, color="#ef6262", alpha=0.08)
    ax1.axvline(WINDOW - 0.5, color="#ffb454", ls="--", lw=1.5)
    ax1.text(WINDOW + 2, ax1.get_ylim()[1], " HUMAN-ONLY FUTURE", color="#ffb454", va="top", fontsize=8)
    ax1.add_patch(
        Rectangle(
            (local_box_start - 0.4, event["price_low"]),
            max(0.8, local_box_end - local_box_start + 0.8),
            max(1e-9, event["price_high"] - event["price_low"]),
            fill=False,
            edgecolor="#ff5c5c",
            lw=2.0,
        )
    )
    signal_time = pd.Timestamp(frame["open_time"].iloc[event["signal_i"]])
    ax1.set_title(
        f"HUMAN REVIEW · +{future_bars * 3 / 60:g}h future · signal {signal_time:%Y-%m-%d %H:%M} UTC",
        color="#edf1f5",
        fontsize=10,
        loc="left",
    )
    _time_ticks(ax1, review)
    ax0.set_ylabel("ETH price", color="#aeb8c1")
    ax1.set_ylabel("ETH price", color="#aeb8c1")
    fig.tight_layout(pad=1.15)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="jpeg", facecolor=fig.get_facecolor(), pil_kwargs={"quality": 72, "optimize": True})
    plt.close(fig)


def build_html(
    events: list[dict],
    *,
    frame: pd.DataFrame,
    raw_count: int,
    accepted_count: int,
    start: pd.Timestamp,
    end: pd.Timestamp,
    conf: float,
    future_bars: int,
    anchor_count: int,
    all_bar_count: int,
    out_dir: Path,
) -> Path:
    month_counts = Counter(event["month"] for event in events)
    months = sorted(month_counts)
    cards = []
    for event in sorted(events, key=lambda row: row["signal_i"], reverse=True):
        image_path = out_dir / event["image_rel"]
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        first = html.escape(event["signal_time"])
        month = html.escape(event["month"])
        cards.append(
            f"""
<article class="card" data-month="{month}" data-conf="{event['max_conf']:.6f}">
  <div class="meta"><span class="time">{first}</span><span class="badge">first {event['conf']:.3f}</span><span class="badge">max {event['max_conf']:.3f}</span><span class="badge">重复 {event['accepted_fires']}</span></div>
  <img loading="lazy" src="data:image/jpeg;base64,{encoded}" alt="ETH 3m v10 prelabel {first}">
  <div class="stats"><span>3h收盘 <b class="{'down' if event['future_return'] < 0 else 'up'}">{event['future_return']:+.2%}</b></span><span>最大下探 <b>{event['max_drop']:.2%}</b></span><span>最大反抽 <b>{event['max_rebound']:.2%}</b></span><span>raw框 {event['raw_fires']}</span></div>
</article>"""
        )

    month_options = "".join(f'<option value="{html.escape(m)}">{html.escape(m)} ({month_counts[m]})</option>' for m in months)
    month_rows = "".join(f"<tr><td>{html.escape(m)}</td><td>{month_counts[m]}</td></tr>" for m in months)
    payload = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETH 3m · v10 最近三个月预打标</title>
<style>
:root{{--bg:#0f1113;--panel:#171a1e;--line:#2d343c;--text:#edf1f5;--muted:#96a2ad;--teal:#80cbc4;--red:#ff6b6b;--green:#52c788;--orange:#ffb454}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:20px 16px 80px}}h1{{font-size:clamp(28px,5vw,48px);line-height:1.1;margin:.25em 0}}.sub{{color:var(--muted);max-width:850px}}.warn{{margin:18px 0;padding:14px 16px;background:#251b13;border-left:4px solid var(--orange);border-radius:8px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}.kpi{{padding:15px;border:1px solid var(--line);border-radius:10px;background:var(--panel)}}.kpi b{{display:block;font-size:25px}}.kpi span{{color:var(--muted)}}.controls{{position:sticky;top:0;z-index:5;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:11px;margin:18px 0;background:rgba(15,17,19,.94);border:1px solid var(--line);border-radius:10px}}select{{background:#20252a;color:var(--text);border:1px solid #3a434c;border-radius:7px;padding:7px 10px}}#shown{{margin-left:auto;color:var(--teal)}}.gallery{{display:grid;gap:16px}}.card{{border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow:hidden}}.card img{{display:block;width:100%;height:auto;background:#111}}.meta,.stats{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:11px 13px}}.meta{{border-bottom:1px solid var(--line)}}.time{{font-weight:720;margin-right:auto}}.badge{{padding:2px 8px;border-radius:999px;background:#263038;color:#cbd5dc;font-size:12px}}.stats{{color:var(--muted)}}.stats span{{margin-right:12px}}.stats b{{color:var(--text)}}.stats .down{{color:var(--green)}}.stats .up{{color:var(--red)}}details{{margin:18px 0;border:1px solid var(--line);border-radius:10px;background:var(--panel)}}summary{{padding:12px;cursor:pointer}}table{{width:100%;border-collapse:collapse}}td{{padding:8px 14px;border-top:1px solid var(--line)}}footer{{margin-top:35px;color:var(--muted)}}
@media(max-width:650px){{main{{padding:12px 9px 60px}}.kpis{{grid-template-columns:repeat(2,1fr)}}.card{{border-radius:8px}}.meta,.stats{{padding:9px}}.card img{{min-height:250px;object-fit:contain}}#shown{{margin-left:0;width:100%}}}}
</style></head><body><main>
<div style="color:var(--teal);font-weight:700;letter-spacing:.08em">YOYO TRADING · BEHAVIOR REVIEW</div>
<h1>ETH 3m · v10 最近三个月预打标</h1>
<p class="sub">最近三个月内等距抽取 {anchor_count} 个盘口锚点做 causal-tip 预览（全区间共 {all_bar_count} 个可扫 bar）；上图是 v10 真正看到的200根，绝无未来。下图右侧红色淡区是只给人工验真的未来3小时。</p>
<div class="warn"><b>重要：</b>v10 是15分钟模型，跑在3分钟图上属于分布外观察；本页不能证明精度、收益或可上线。窗口含 holdout，按 Owner 明确要求登记为第11次消耗，只能审图，不能据此调参或 promote。</div>
<div class="kpis"><div class="kpi"><b>{len(events)}</b><span>事件簇</span></div><div class="kpi"><b>{accepted_count}</b><span>去近邻后开火</span></div><div class="kpi"><b>{raw_count}</b><span>原始 tip 命中</span></div><div class="kpi"><b>{conf:.2f}</b><span>v10 conf 门槛</span></div></div>
<details><summary>月份分布与口径</summary><table>{month_rows}</table><div style="padding:12px;color:var(--muted)">范围 {start:%Y-%m-%d %H:%M} ～ {end:%Y-%m-%d %H:%M} UTC；{anchor_count}/{all_bar_count} 个 bar 等距抽样；tip门为最后3根；近邻去重8根（24分钟）；事件聚类20根（1小时）；未来观察窗 {future_bars} 根（3小时）。这是预览，不是逐根穷举。</div></details>
<div class="controls"><label>月份 <select id="month"><option value="all">全部</option>{month_options}</select></label><label>最低置信度 <select id="minconf"><option value="0">全部</option><option value="0.4">0.40+</option><option value="0.5">0.50+</option><option value="0.6">0.60+</option><option value="0.7">0.70+</option></select></label><span id="shown"></span></div>
<section class="gallery">{''.join(cards)}</section>
<footer>生成文件为单一自包含HTML，图片已内嵌。原始逐框记录见同目录 raw_detections.csv，事件记录见 events.csv。</footer>
</main><script>
const cards=[...document.querySelectorAll('.card')], month=document.querySelector('#month'), minconf=document.querySelector('#minconf'), shown=document.querySelector('#shown');
function apply(){{let n=0;cards.forEach(c=>{{const ok=(month.value==='all'||c.dataset.month===month.value)&&Number(c.dataset.conf)>=Number(minconf.value);c.style.display=ok?'':'none';if(ok)n++;}});shown.textContent=`显示 ${{n}} / ${{cards.length}}`;}}month.onchange=apply;minconf.onchange=apply;apply();
</script></body></html>"""
    target = out_dir / "index.html"
    target.write_text(payload, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start", default=None, help="inclusive UTC timestamp; default is max data time minus 3 calendar months")
    parser.add_argument("--end", default=None, help="inclusive UTC timestamp; default is max data time")
    parser.add_argument("--conf", type=float, default=0.30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--future-hours", type=float, default=3.0)
    parser.add_argument("--min-gap-bars", type=int, default=8)
    parser.add_argument("--cluster-gap-bars", type=int, default=20)
    parser.add_argument("--max-anchors", type=int, default=0, help="evenly sample at most this many causal tips; 0 scans every bar")
    args = parser.parse_args()

    if not args.weights.exists():
        raise SystemExit(f"weights not found: {args.weights}")
    args.out.mkdir(parents=True, exist_ok=True)
    frame = load_frame(args.input)
    ma_frame = add_mas(frame)
    data_end = frame["open_time"].max()
    start = pd.Timestamp(args.start, tz="UTC") if args.start else data_end - pd.DateOffset(months=3)
    end = min(pd.Timestamp(args.end, tz="UTC"), data_end) if args.end else data_end
    future_bars = int(round(args.future_hours * 60 / 3))
    scan_start_i = max(WINDOW - 1, int(frame["open_time"].searchsorted(start, side="left")))
    scan_end_i = min(len(frame) - future_bars - 1, int(frame["open_time"].searchsorted(end, side="right")) - 1)
    if scan_end_i < scan_start_i:
        raise SystemExit("no complete scan range after reserving the human future window")
    all_bar_count = scan_end_i - scan_start_i + 1
    if args.max_anchors and all_bar_count > args.max_anchors:
        anchors = np.unique(
            np.linspace(scan_start_i, scan_end_i, args.max_anchors).round().astype(int)
        ).tolist()
    else:
        anchors = list(range(scan_start_i, scan_end_i + 1))
    device = auto_device(args.device)
    print(
        f"data={len(frame)} bars {frame['open_time'].min()}..{data_end} scan={frame['open_time'].iloc[scan_start_i]}..{frame['open_time'].iloc[scan_end_i]} anchors={len(anchors)}/{all_bar_count} device={device}",
        flush=True,
    )
    model = load_yolo_model(str(args.weights))
    raw = scan(
        frame,
        ma_frame,
        model=model,
        anchors=anchors,
        conf=args.conf,
        device=device,
        batch_size=args.batch_size,
        out_dir=args.out,
    )
    accepted, events = consolidate(raw, min_gap=args.min_gap_bars, cluster_gap=args.cluster_gap_bars)
    raw_frame = pd.DataFrame(raw)
    if not raw_frame.empty:
        raw_frame["tip_time"] = raw_frame["tip_i"].map(frame["open_time"])
        raw_frame["signal_time"] = raw_frame["signal_i"].map(frame["open_time"])
    raw_frame.to_csv(args.out / "raw_detections.csv", index=False)

    images = args.out / "images"
    event_rows = []
    for serial, event in enumerate(events, 1):
        stats = future_stats(frame, event, future_bars)
        signal_time = pd.Timestamp(frame["open_time"].iloc[event["signal_i"]])
        image_rel = f"images/event_{serial:04d}_{signal_time:%Y%m%dT%H%MZ}.jpg"
        render_event(frame, ma_frame, event, future_bars=future_bars, path=args.out / image_rel)
        event.update(stats)
        event["signal_time"] = signal_time.isoformat()
        event["month"] = signal_time.strftime("%Y-%m")
        event["image_rel"] = image_rel
        event_rows.append(event)
        if serial % 25 == 0 or serial == len(events):
            print(f"review images {serial}/{len(events)}", flush=True)
    pd.DataFrame(event_rows).to_csv(args.out / "events.csv", index=False)
    target = build_html(
        event_rows,
        frame=frame,
        raw_count=len(raw),
        accepted_count=len(accepted),
        start=pd.Timestamp(frame["open_time"].iloc[scan_start_i]),
        end=pd.Timestamp(frame["open_time"].iloc[scan_end_i]),
        conf=args.conf,
        future_bars=future_bars,
        anchor_count=len(anchors),
        all_bar_count=all_bar_count,
        out_dir=args.out,
    )
    summary = {
        "weights": str(args.weights),
        "input": str(args.input),
        "scan_start": str(frame["open_time"].iloc[scan_start_i]),
        "scan_end": str(frame["open_time"].iloc[scan_end_i]),
        "raw_tip_fires": len(raw),
        "causal_anchors_scanned": len(anchors),
        "all_bars_in_range": all_bar_count,
        "accepted_after_24m_gap": len(accepted),
        "event_clusters_1h": len(events),
        "conf": args.conf,
        "future_hours_human_only": args.future_hours,
        "holdout_consumption": 11,
        "discipline": "owner-authorized visual review only; no tuning, evaluation, promotion, or live change",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {target} ({target.stat().st_size / 1024 / 1024:.1f} MiB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a holdout-safe ETH 3m dual-view calibration preview.

Owner-confirmed labeling contract (2026-07-29):

* the human review horizon is a fixed three hours (60 native 3m bars);
* shape/box labels and future outcome labels are stored separately;
* model pixels contain exactly 200 completed bars ending at the causal tip;
* future pixels appear only in the human review asset;
* the global holdout starts at 2026-05-04 UTC and is never read here.

This script creates a self-contained mobile HTML for approval before any Label
Studio import.  The 240 review tasks contain 216 unique mixed-source events and
24 blind repeats.  Candidate source and outcome numbers stay in the private
manifest and are deliberately absent from the review HTML.
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import math
import random
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_eth_short_tip_label_pack import (  # noqa: E402
    Candidate,
    downside_pool,
    numeric_pool,
    pick_diverse,
    random_pool,
)
from scripts.scan_eth_3m_v10_prelabels_html import (  # noqa: E402
    _draw_candles,
    _time_ticks,
    _x_to_bar,
    _y_to_price,
)
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import ChartTransform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import WINDOW, load_yolo_model, right_edge_to_bar  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
BAR_MINUTES = 3
FUTURE_BARS = 60
UNIQUE_TARGET = 216
REPEAT_TARGET = 24
TASK_TARGET = UNIQUE_TARGET + REPEAT_TARGET
SOURCE_QUOTAS = {"v10": 65, "numeric": 54, "downside": 43, "random": 54}
SEED = 20260729
DEFAULT_INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v10/weights/best.pt"
DEFAULT_OUT = PROJECT / "analysis/output/eth_3m_calibration240_preview"


def load_dev_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"open_time", "open", "high", "low", "close", "volume"}
    if missing := required - set(frame.columns):
        raise SystemExit(f"input missing columns: {sorted(missing)}")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = (
        frame.dropna(subset=["open_time", "open", "high", "low", "close"])
        .sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
    )
    # Physical holdout isolation happens before indicators, proposals, or
    # outcomes are computed.  The script never holds a post-boundary row.
    frame = frame[frame["open_time"] < HOLDOUT_START].reset_index(drop=True)
    gaps = frame["open_time"].diff().dropna()
    if frame.empty or (not gaps.empty and gaps.max() > pd.Timedelta(minutes=3)):
        raise SystemExit("pre-holdout ETH 3m data is empty or discontinuous")
    if len(frame) < WINDOW + FUTURE_BARS + UNIQUE_TARGET:
        raise SystemExit(f"only {len(frame)} usable pre-holdout bars")
    return frame


def safe_max_index(frame: pd.DataFrame) -> int:
    latest_tip = HOLDOUT_START - pd.Timedelta(minutes=BAR_MINUTES * FUTURE_BARS)
    # Strict contract: the final human-only future bar must be < holdout.
    return int(frame["open_time"].searchsorted(latest_tip, side="left")) - 1


def v10_exact_tip_pool(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    *,
    model,
    anchors: list[int],
    conf: float,
    batch_size: int,
    device: str,
    probe_limit: int,
) -> list[Candidate]:
    """Run v10 causally and retain only boxes ending on the exact tip."""
    anchors = list(dict.fromkeys(int(i) for i in anchors if i >= WINDOW - 1))
    anchors = anchors[:probe_limit]
    found: list[Candidate] = []
    for chunk_start in range(0, len(anchors), batch_size):
        chunk = anchors[chunk_start : chunk_start + batch_size]
        rendered: list[tuple[int, ChartTransform, np.ndarray]] = []
        for idx in chunk:
            image, transform = render_chart(ma_frame.iloc[idx - WINDOW + 1 : idx + 1])
            rendered.append((idx, transform, image))
        results = model.predict(
            [image for _, _, image in rendered],
            conf=conf,
            verbose=False,
            device=device,
        )
        for (idx, transform, _), result in zip(rendered, results):
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue
            best: tuple[float, tuple[float, float, float, float]] | None = None
            for row, raw_conf in zip(boxes.xywhn.cpu().numpy(), boxes.conf.cpu().numpy()):
                cx, cy, width, height = map(float, row[:4])
                if right_edge_to_bar(cx, width, transform, n_bars=WINDOW) != WINDOW - 1:
                    continue
                candidate = (float(raw_conf), (cx, cy, width, height))
                if best is None or candidate[0] > best[0]:
                    best = candidate
            if best is not None:
                found.append(
                    Candidate(
                        idx=idx,
                        source="v10",
                        score=best[0],
                        v10_conf=best[0],
                        v10_box=best[1],
                        group=pd.Timestamp(frame["open_time"].iloc[idx]).strftime("%Y-%m"),
                    )
                )
        done = min(chunk_start + len(chunk), len(anchors))
        if done % max(batch_size * 20, batch_size) == 0 or done == len(anchors):
            print(f"v10 probes {done}/{len(anchors)} exact-tip={len(found)}", flush=True)
        if len(found) >= SOURCE_QUOTAS["v10"] + 30:
            break
    return found


def choose_candidates(
    frame: pd.DataFrame,
    enriched: pd.DataFrame,
    ma_frame: pd.DataFrame,
    *,
    model,
    rng: random.Random,
    max_idx: int,
    conf: float,
    batch_size: int,
    device: str,
    probe_limit: int,
) -> tuple[list[Candidate], dict]:
    pools = {
        "numeric": [c for c in numeric_pool(frame, enriched) if c.idx <= max_idx],
        "downside": [
            c
            for c in downside_pool(
                frame,
                enriched,
                bar_minutes=BAR_MINUTES,
                limit=max(800, SOURCE_QUOTAS["downside"] * 10),
            )
            if c.idx <= max_idx
        ],
        "random": [c for c in random_pool(frame, enriched, rng=rng) if c.idx <= max_idx],
    }
    probe_ids = list(
        dict.fromkeys(
            [c.idx for c in pools["numeric"]]
            + [c.idx for c in pools["downside"][:1000]]
            + [c.idx for c in pools["random"][:1800]]
        )
    )
    rng.shuffle(probe_ids)
    pools["v10"] = v10_exact_tip_pool(
        frame,
        ma_frame,
        model=model,
        anchors=probe_ids,
        conf=conf,
        batch_size=batch_size,
        device=device,
        probe_limit=probe_limit,
    )

    selected_indices: list[int] = []
    chosen: list[Candidate] = []
    gap = 20  # 60 minutes on native 3m bars
    for source in ("v10", "numeric", "downside", "random"):
        picked = pick_diverse(
            pools[source],
            SOURCE_QUOTAS[source],
            selected_indices=selected_indices,
            gap=gap,
            rng=rng,
        )
        chosen.extend(picked)
        print(f"select {source}: {len(picked)}/{SOURCE_QUOTAS[source]}", flush=True)

    if len(chosen) < UNIQUE_TARGET:
        # v10 is a maximum share, not a required minimum.  Any shortage is
        # filled with blind backgrounds instead of relaxing exact-tip.
        fill = pick_diverse(
            pools["random"],
            UNIQUE_TARGET - len(chosen),
            selected_indices=selected_indices,
            gap=gap,
            rng=rng,
        )
        chosen.extend(replace(c, source="random_fill") for c in fill)
    if len(chosen) != UNIQUE_TARGET:
        raise RuntimeError(f"selected {len(chosen)} unique events, expected {UNIQUE_TARGET}")
    chosen.sort(key=lambda c: c.idx)
    inventory = {name: len(values) for name, values in pools.items()}
    return chosen, inventory


def box_geometry(box: tuple[float, float, float, float], transform: ChartTransform) -> tuple[float, float, float, float]:
    cx, cy, width, height = box
    x0 = _x_to_bar(cx - width / 2, transform)
    x1 = _x_to_bar(cx + width / 2, transform)
    p0 = _y_to_price(cy - height / 2, transform)
    p1 = _y_to_price(cy + height / 2, transform)
    return min(x0, x1), max(x0, x1), min(p0, p1), max(p0, p1)


def render_review(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    candidate: Candidate,
    *,
    show_prebox: bool,
    path: Path,
) -> None:
    idx = candidate.idx
    causal = ma_frame.iloc[idx - WINDOW + 1 : idx + 1].copy().reset_index(drop=True)
    review = ma_frame.iloc[idx - WINDOW + 1 : idx + FUTURE_BARS + 1].copy().reset_index(drop=True)
    _, transform = render_chart(causal)
    geometry = box_geometry(candidate.v10_box, transform) if show_prebox and candidate.v10_box else None

    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(11.5, 7.0), dpi=90)
    fig.patch.set_facecolor("#0f1113")
    for ax in (ax0, ax1):
        ax.set_facecolor("#171a1e")
        ax.tick_params(colors="#aeb8c1", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#343b43")
        ax.grid(alpha=0.12, color="#7d8790")

    _draw_candles(ax0, causal)
    ax0.axvline(WINDOW - 0.5, color="#ffb454", ls="--", lw=1.3)
    ax0.set_title("MODEL VIEW · causal 200 bars · no future", color="#edf1f5", fontsize=9, loc="left")
    _time_ticks(ax0, causal)

    _draw_candles(ax1, review)
    ax1.axvspan(WINDOW - 0.5, len(review) - 0.5, color="#ef6262", alpha=0.08)
    ax1.axvline(WINDOW - 0.5, color="#ffb454", ls="--", lw=1.4)
    ax1.text(WINDOW + 2, ax1.get_ylim()[1], "HUMAN-ONLY FUTURE", color="#ffb454", va="top", fontsize=7)
    ax1.set_title("HUMAN REVIEW · fixed +3h · never used as model pixels", color="#edf1f5", fontsize=9, loc="left")
    _time_ticks(ax1, review)

    if geometry is not None:
        x0, x1, low, high = geometry
        for ax in (ax0, ax1):
            ax.add_patch(
                Rectangle(
                    (x0 - 0.4, low),
                    max(0.8, x1 - x0 + 0.8),
                    max(1e-9, high - low),
                    fill=False,
                    edgecolor="#ff5c5c",
                    lw=1.8,
                )
            )
    ax0.set_ylabel("ETH", color="#aeb8c1")
    ax1.set_ylabel("ETH", color="#aeb8c1")
    fig.tight_layout(pad=1.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="jpeg", facecolor=fig.get_facecolor(), pil_kwargs={"quality": 58, "optimize": True})
    plt.close(fig)


def outcome_row(frame: pd.DataFrame, idx: int) -> dict[str, float]:
    close = float(frame["close"].iloc[idx])
    future = frame.iloc[idx + 1 : idx + FUTURE_BARS + 1]
    return {
        "outcome_return_1h": float(future["close"].iloc[19] / close - 1),
        "outcome_return_3h": float(future["close"].iloc[-1] / close - 1),
        "outcome_max_drop_3h": float(1 - future["low"].min() / close),
        "outcome_max_rebound_3h": float(future["high"].max() / close - 1),
    }


def build_html(rows: list[dict], out: Path, summary: dict) -> Path:
    cards = []
    for row in rows:
        encoded = base64.b64encode((out / row["review_image_rel"]).read_bytes()).decode("ascii")
        cards.append(
            f'''<article class="card" data-i="{row['task_id'] - 1}">
<div class="meta"><b>任务 {row['task_id']:03d} / {len(rows)}</b><span>ETH-USDT-SWAP · 3m</span></div>
<img loading="lazy" src="data:image/jpeg;base64,{encoded}" alt="ETH 3m calibration task {row['task_id']:03d}">
<div class="questions"><div><b>A · 形态</b><span>valid / invalid / uncertain + 框</span></div><div><b>B · 后续结果</b><span>strong drop / weak drop / fail / rebound</span></div></div>
</article>'''
        )
    payload = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETH 3m · 240张双视图校准包预览</title><style>
:root{{--bg:#0f1113;--panel:#171a1e;--line:#303840;--text:#edf1f5;--muted:#99a4ae;--teal:#80cbc4;--orange:#ffb454}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:18px 14px 90px}}h1{{font-size:clamp(27px,5vw,46px);line-height:1.1;margin:.25em 0}}.sub{{color:var(--muted);max-width:860px}}.warn{{margin:16px 0;padding:13px 15px;background:#251b13;border-left:4px solid var(--orange);border-radius:8px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:18px 0}}.kpi{{padding:13px;border:1px solid var(--line);border-radius:10px;background:var(--panel)}}.kpi b{{display:block;font-size:24px}}.kpi span{{color:var(--muted)}}.rules{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:15px 0}}.rule{{padding:13px;background:var(--panel);border:1px solid var(--line);border-radius:9px}}.rule b{{display:block;color:var(--teal)}}.nav{{position:sticky;top:0;z-index:5;display:flex;gap:8px;align-items:center;padding:10px;margin:16px 0;background:rgba(15,17,19,.95);border:1px solid var(--line);border-radius:10px}}button,input{{background:#22292f;color:var(--text);border:1px solid #45515c;border-radius:7px;padding:8px 11px}}button{{cursor:pointer}}input{{width:76px}}#where{{margin-left:auto;color:var(--teal)}}.card{{display:none;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}}.card.active{{display:block}}.card img{{display:block;width:100%;height:auto;background:#111}}.meta,.questions{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:11px 13px}}.meta{{border-bottom:1px solid var(--line)}}.meta b{{margin-right:auto}}.meta span{{color:var(--muted)}}.questions{{border-top:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr}}.questions div{{padding:8px;background:#121518;border-radius:7px}}.questions b,.questions span{{display:block}}.questions b{{color:var(--teal)}}.questions span{{color:var(--muted);font-size:12px}}footer{{margin-top:25px;color:var(--muted)}}
@media(max-width:650px){{main{{padding:11px 8px 70px}}.kpis{{grid-template-columns:repeat(2,1fr)}}.rules,.questions{{grid-template-columns:1fr}}.nav{{flex-wrap:wrap}}#where{{width:100%;margin-left:0}}.card{{border-radius:8px}}}}
</style></head><body><main><div style="color:var(--teal);font-weight:700;letter-spacing:.08em">FABLE TRADING · CALIBRATION PREVIEW</div>
<h1>ETH 3m · 240张双视图校准包</h1><p class="sub">这是导入 Label Studio 前的手机审版。上图严格只有信号时刻之前200根；下图固定增加未来3小时。候选来源、v10置信度和未来数值全部隐藏，避免锚定。</p>
<div class="warn"><b>开发期隔离：</b>所有图及其未来3小时均早于 2026-05-04，不读取 holdout。当前只确认视觉、任务结构和标签语义；尚未导入 Label Studio。</div>
<div class="kpis"><div class="kpi"><b>{len(rows)}</b><span>审阅任务</span></div><div class="kpi"><b>{summary['unique_events']}</b><span>独立事件</span></div><div class="kpi"><b>{summary['blind_repeats']}</b><span>盲重复</span></div><div class="kpi"><b>3h</b><span>固定人工未来窗</span></div></div>
<div class="rules"><div class="rule"><b>A · 形态标签</b>判断是不是目标空头密集启动，并修正框；不因后来失败而改成形态负例。</div><div class="rule"><b>B · 结果标签</b>单独记录 strong drop / weak drop / fail / rebound；未来结果不进入检测器图片。</div></div>
<div class="nav"><button id="prev">上一张</button><button id="next">下一张</button><label>跳到 <input id="jump" type="number" min="1" max="{len(rows)}" value="1"></label><span id="where"></span></div>
<section>{''.join(cards)}</section><footer>有些任务显示 v10 红色预框，有些刻意隐藏；盲重复也不会在页面标出。确认本页无问题后，才生成并导入 Label Studio 任务。</footer>
</main><script>const cards=[...document.querySelectorAll('.card')],where=document.querySelector('#where'),jump=document.querySelector('#jump');let i=0;function show(n,scroll=true){{i=Math.max(0,Math.min(cards.length-1,n));cards.forEach((c,j)=>c.classList.toggle('active',j===i));jump.value=i+1;where.textContent=`第 ${{i+1}} / ${{cards.length}} 张`;if(scroll)window.scrollTo({{top:document.querySelector('.nav').offsetTop-4,behavior:'smooth'}})}}document.querySelector('#prev').onclick=()=>show(i-1);document.querySelector('#next').onclick=()=>show(i+1);jump.onchange=()=>show(Number(jump.value)-1);show(0,false);</script></body></html>'''
    target = out / "index.html"
    target.write_text(payload, encoding="utf-8")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--v10-conf", type=float, default=0.05)
    ap.add_argument("--v10-probe-limit", type=int, default=3000)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output: {args.out}")
    if not args.input.exists() or not args.weights.exists():
        raise SystemExit("input data or v10 weights missing")
    args.out.mkdir(parents=True, exist_ok=True)
    review_dir = args.out / "review_images"
    review_dir.mkdir()

    rng = random.Random(args.seed)
    frame = load_dev_frame(args.input)
    max_idx = safe_max_index(frame)
    ma_frame = add_mas(frame)
    enriched = add_indicators(frame)
    model = load_yolo_model(args.weights)
    selected, inventory = choose_candidates(
        frame,
        enriched,
        ma_frame,
        model=model,
        rng=rng,
        max_idx=max_idx,
        conf=args.v10_conf,
        batch_size=args.batch_size,
        device=args.device,
        probe_limit=args.v10_probe_limit,
    )

    event_ids = {candidate.idx: f"eth3m_{i:04d}" for i, candidate in enumerate(selected, 1)}
    v10_events = [c for c in selected if c.source == "v10"]
    rng.shuffle(v10_events)
    visible_prebox_ids = {event_ids[c.idx] for c in v10_events[: math.ceil(len(v10_events) / 2)]}
    repeats = rng.sample(selected, REPEAT_TARGET)
    task_candidates = [(candidate, False) for candidate in selected] + [(candidate, True) for candidate in repeats]
    rng.shuffle(task_candidates)

    rows: list[dict] = []
    for task_id, (candidate, is_repeat) in enumerate(task_candidates, 1):
        event_id = event_ids[candidate.idx]
        show_prebox = event_id in visible_prebox_ids
        image_rel = f"review_images/task_{task_id:03d}.jpg"
        render_review(
            frame,
            ma_frame,
            candidate,
            show_prebox=show_prebox,
            path=args.out / image_rel,
        )
        ts = pd.Timestamp(frame["open_time"].iloc[candidate.idx])
        future_end = pd.Timestamp(frame["open_time"].iloc[candidate.idx + FUTURE_BARS])
        if future_end >= HOLDOUT_START:
            raise RuntimeError(f"holdout leak: task={task_id} future_end={future_end}")
        rows.append(
            {
                "task_id": task_id,
                "event_id": event_id,
                "repeat_group": event_id if is_repeat else "",
                "is_blind_repeat": is_repeat,
                "candidate_time": ts.isoformat(),
                "future_end": future_end.isoformat(),
                "source": candidate.source,
                "source_score": candidate.score,
                "v10_conf": candidate.v10_conf,
                "v10_prebox_visible": show_prebox,
                "review_image_rel": image_rel,
                "shape_label": "",
                "outcome_label": "",
                **outcome_row(frame, candidate.idx),
            }
        )
        if task_id % 25 == 0 or task_id == TASK_TARGET:
            print(f"review images {task_id}/{TASK_TARGET}", flush=True)

    manifest_path = args.out / "private_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(c.source for c in selected)
    summary = {
        "symbol": "ETH_USDT_SWAP",
        "timeframe": "3m",
        "tasks": TASK_TARGET,
        "unique_events": UNIQUE_TARGET,
        "blind_repeats": REPEAT_TARGET,
        "future_hours_human_only": 3,
        "model_window_bars": WINDOW,
        "source_counts_unique": dict(counts),
        "candidate_inventory": inventory,
        "visible_v10_preboxes_unique": len(visible_prebox_ids),
        "data_start": str(frame["open_time"].min()),
        "latest_safe_tip": str(frame["open_time"].iloc[max_idx]),
        "max_future_end": max(row["future_end"] for row in rows),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_consumed": False,
        "label_contract": {
            "shape": "valid / invalid / uncertain + box; detector target",
            "outcome": "strong_drop / weak_drop / fail / rebound; stored separately",
        },
        "status": "preview only; not imported to Label Studio",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    target = build_html(rows, args.out, summary)
    print(f"wrote {target} ({target.stat().st_size / 1024 / 1024:.1f} MiB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

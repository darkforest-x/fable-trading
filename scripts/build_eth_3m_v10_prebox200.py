#!/usr/bin/env python3
"""Build 200 ETH 3m review tasks that all contain a visible v10 prebox.

This is the simple owner-facing prelabel pack: no random/numeric/downside cards,
no hidden boxes, and no blind repeats.  v10 sees exactly 200 completed causal
bars.  A box is accepted only when its right edge lands on tip/tip-1/tip-2.
The human-only panel adds the owner-confirmed fixed three-hour future window.

All source rows are physically restricted to <2026-05-04 before inference or
outcome calculation.  Label Studio JSON is prepared but never imported here.
"""
from __future__ import annotations

import argparse
import base64
import bisect
import csv
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_eth_3m_dual_view_calibration import (  # noqa: E402
    FUTURE_BARS,
    HOLDOUT_START,
    box_geometry,
    load_dev_frame,
    outcome_row,
    safe_max_index,
)
from scripts.build_eth_short_tip_label_pack import Candidate, yolo_to_ls_box  # noqa: E402
from scripts.scan_eth_3m_v10_prelabels_html import _draw_candles, _time_ticks  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import ChartTransform, render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

TARGET = 200
SEED = 20260729
DEFAULT_INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v10/weights/best.pt"
DEFAULT_OUT = PROJECT / "datasets/eth_3m_v10_prebox200"


@dataclass(frozen=True)
class Detection:
    idx: int
    conf: float
    box: tuple[float, float, float, float]
    right_bar: int


def render_simple_review(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    candidate: Candidate,
    *,
    path: Path,
) -> None:
    """Render the owner reference style: one clean white chart, no trade lines."""
    idx = candidate.idx
    causal = ma_frame.iloc[idx - WINDOW + 1 : idx + 1].copy().reset_index(drop=True)
    review = ma_frame.iloc[idx - WINDOW + 1 : idx + FUTURE_BARS + 1].copy().reset_index(drop=True)
    _, transform = render_chart(causal)
    if candidate.v10_box is None:
        raise RuntimeError("visible v10 prebox is required")
    x0, x1, low, high = box_geometry(candidate.v10_box, transform)

    fig, ax = plt.subplots(figsize=(12.8, 6.6), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    _draw_candles(ax, review)
    ax.add_patch(
        Rectangle(
            (x0 - 0.4, low),
            max(0.8, x1 - x0 + 0.8),
            max(1e-9, high - low),
            fill=False,
            edgecolor="#c62828",
            lw=2.0,
        )
    )
    ax.axvline(WINDOW - 0.5, color="#50575e", ls="--", lw=1.1)
    ymax = ax.get_ylim()[1]
    ax.text(WINDOW + 2, ymax, "future +3h", color="#60676f", va="top", fontsize=8)
    ax.set_title("ETH_USDT_SWAP · 3m · v10 prebox", color="#20262b", fontsize=11, loc="left")
    ax.set_ylabel("Price", color="#30363b")
    _time_ticks(ax, review, count=8)
    ax.tick_params(colors="#4b535a", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#8b9298")
    ax.grid(color="#d9dde1", alpha=0.55, lw=0.6)
    fig.tight_layout(pad=1.1)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format="jpeg",
        facecolor="white",
        pil_kwargs={"quality": 68, "optimize": True},
    )
    plt.close(fig)


def anchor_order(start: int, end: int, *, primary: int, seed: int) -> list[int]:
    """Cover the full date range early, then fall back to every unscanned bar."""
    eligible = np.arange(start, end + 1, dtype=int)
    n_primary = min(primary, len(eligible))
    sampled = np.unique(np.linspace(start, end, n_primary).round().astype(int)).tolist()
    rng = random.Random(seed)
    rng.shuffle(sampled)
    sampled_set = set(sampled)
    remainder = [int(i) for i in eligible if int(i) not in sampled_set]
    rng.shuffle(remainder)
    return sampled + remainder


def far_enough(idx: int, selected: list[int], gap: int) -> bool:
    pos = bisect.bisect_left(selected, idx)
    return not (
        (pos > 0 and idx - selected[pos - 1] < gap)
        or (pos < len(selected) and selected[pos] - idx < gap)
    )


def scan_v10(
    ma_frame: pd.DataFrame,
    *,
    model,
    anchors: list[int],
    conf: float,
    batch_size: int,
    device: str,
    min_gap_bars: int,
    target: int,
    checkpoint: Path,
) -> list[Detection]:
    signature = {
        "first": min(anchors),
        "last": max(anchors),
        "count": len(anchors),
        "conf": conf,
        "min_gap_bars": min_gap_bars,
        "target": target,
    }
    selected: list[Detection] = []
    selected_indices: list[int] = []
    next_pos = 0
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        saved_signature = saved.get("signature", {})
        same_scan = all(
            saved_signature.get(key) == signature.get(key)
            for key in ("first", "last", "count", "conf", "target")
        )
        relaxed_gap = (
            same_scan
            and int(saved_signature.get("min_gap_bars", min_gap_bars)) >= min_gap_bars
        )
        if saved_signature == signature or relaxed_gap:
            selected = [
                Detection(
                    idx=int(row["idx"]),
                    conf=float(row["conf"]),
                    box=tuple(map(float, row["box"])),
                    right_bar=int(row["right_bar"]),
                )
                for row in saved.get("selected", [])
            ]
            selected_indices = sorted(det.idx for det in selected)
            next_pos = int(saved.get("next_pos", 0))
            print(
                f"resume anchors={next_pos}/{len(anchors)} selected={len(selected)} "
                f"gap={saved_signature.get('min_gap_bars')}->{min_gap_bars}",
                flush=True,
            )

    for chunk_start in range(next_pos, len(anchors), batch_size):
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
            if not far_enough(idx, selected_indices, min_gap_bars):
                continue
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue
            best: Detection | None = None
            for row, raw_conf in zip(boxes.xywhn.cpu().numpy(), boxes.conf.cpu().numpy()):
                cx, cy, width, height = map(float, row[:4])
                right_bar = right_edge_to_bar(cx, width, transform, n_bars=WINDOW)
                if right_bar < WINDOW - 1 - TIP_EDGE_BARS:
                    continue
                det = Detection(
                    idx=idx,
                    conf=float(raw_conf),
                    box=(cx, cy, width, height),
                    right_bar=right_bar,
                )
                if best is None or det.conf > best.conf:
                    best = det
            if best is not None:
                bisect.insort(selected_indices, idx)
                selected.append(best)
                if len(selected) >= target:
                    break
        done = chunk_start + len(chunk)
        checkpoint.write_text(
            json.dumps(
                {
                    "signature": signature,
                    "next_pos": done,
                    "selected": [
                        {"idx": d.idx, "conf": d.conf, "box": d.box, "right_bar": d.right_bar}
                        for d in selected
                    ],
                }
            ),
            encoding="utf-8",
        )
        if done % max(batch_size * 20, batch_size) == 0 or len(selected) >= target:
            print(f"v10 scan {done}/{len(anchors)} selected={len(selected)}/{target}", flush=True)
        if len(selected) >= target:
            break
    if len(selected) < target:
        raise RuntimeError(f"v10 produced only {len(selected)} usable preboxes")
    return selected[:target]


def label_config() -> str:
    return """<View>
  <View style="display:none">
    <Image name="causal" value="$causal_image"/>
    <RectangleLabels name="box" toName="causal">
      <Label value="short_start" background="#d32f2f"/>
    </RectangleLabels>
    <Choices name="shape" toName="causal" choice="single">
      <Choice value="valid"/>
      <Choice value="invalid"/>
      <Choice value="uncertain"/>
      <Choice value="bad_data"/>
    </Choices>
    <Choices name="error_reason" toName="causal" choice="multiple">
      <Choice value="not_dense"/>
      <Choice value="too_late"/>
      <Choice value="wrong_box"/>
      <Choice value="wrong_direction"/>
      <Choice value="repeated_trend"/>
      <Choice value="other"/>
    </Choices>
    <Choices name="outcome" toName="review" choice="single">
      <Choice value="strong_drop"/>
      <Choice value="weak_drop"/>
      <Choice value="fail"/>
      <Choice value="rebound"/>
      <Choice value="outcome_uncertain"/>
    </Choices>
    <TextArea name="note" toName="causal"/>
  </View>
  <Header value="ETH 3m · 这个红框是不是你要的做空形态？"/>
  <Choices name="is_target" toName="review" choice="single" required="true" showInline="true">
    <Choice value="是" hotkey="1"/>
    <Choice value="不是" hotkey="2"/>
  </Choices>
  <Header value="竖虚线左侧是模型当时可见范围，右侧是固定未来 3 小时；未来只供你判断。"/>
  <Image name="review" value="$review_image" zoom="true" zoomControl="true"/>
</View>
"""


def build_html(rows: list[dict], out: Path) -> Path:
    cards = []
    for row in rows:
        encoded = base64.b64encode((out / row["review_image_rel"]).read_bytes()).decode("ascii")
        cards.append(
            f'''<article class="card" data-i="{row['task_id'] - 1}"><div class="meta"><b>v10 预标 {row['task_id']:03d} / {len(rows)}</b><span>红框已显示 · ETH 3m</span></div><img loading="lazy" src="data:image/jpeg;base64,{encoded}" alt="v10 prebox {row['task_id']:03d}"><div class="hint">竖虚线左侧是模型当时可见范围，右侧是固定未来3小时。只检查红框；框不准就改，形态不对就删。</div></article>'''
        )
    payload = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ETH 3m · v10预标200张</title><style>
:root{{--bg:#0f1113;--panel:#171a1e;--line:#303840;--text:#edf1f5;--muted:#99a4ae;--teal:#80cbc4;--orange:#ffb454}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:18px 14px 80px}}h1{{font-size:clamp(28px,5vw,46px);line-height:1.1;margin:.25em 0}}.sub{{color:var(--muted)}}.warn{{margin:16px 0;padding:13px 15px;background:#251b13;border-left:4px solid var(--orange);border-radius:8px}}.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin:17px 0}}.kpi{{padding:13px;background:var(--panel);border:1px solid var(--line);border-radius:9px}}.kpi b{{display:block;font-size:25px}}.kpi span{{color:var(--muted)}}.card{{display:block;margin-bottom:16px;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}}.card img{{display:block;width:100%;height:auto}}.meta,.hint{{padding:11px 13px}}.meta{{display:flex;gap:10px;border-bottom:1px solid var(--line)}}.meta b{{margin-right:auto}}.meta span,.hint{{color:var(--muted)}}.hint{{border-top:1px solid var(--line)}}@media(max-width:650px){{main{{padding:10px 8px 60px}}.kpis{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main><div style="color:var(--teal);font-weight:700;letter-spacing:.08em">FABLE TRADING · V10 PRELABELS</div><h1>ETH 3m · v10预标200张</h1><p class="sub">本页只包含 v10 真正检出并画框的图片。200 张按顺序全部展开，直接向下滑动查看。</p><div class="warn"><b>看图口径：</b>单张白底图；竖虚线左侧是 v10 的200根因果输入，右侧固定增加未来3小时。保留K线与均线，不画入场、止盈、止损、成交量或背景填充。</div><div class="kpis"><div class="kpi"><b>200</b><span>全部有v10框</span></div><div class="kpi"><b>0.30</b><span>v10置信度门槛</span></div><div class="kpi"><b>3h</b><span>人工未来窗</span></div></div><section>{''.join(cards)}</section></main></body></html>'''
    target = out / "v10_prebox200_mobile.html"
    target.write_text(payload, encoding="utf-8")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--primary-anchors", type=int, default=12000)
    ap.add_argument("--min-gap-bars", type=int, default=10)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument(
        "--html-only",
        action="store_true",
        help="Rebuild the embedded mobile HTML from the existing manifest and review images.",
    )
    args = ap.parse_args()
    if args.html_only:
        manifest = args.out / "manifest.csv"
        if not manifest.exists():
            raise SystemExit(f"missing manifest for --html-only: {manifest}")
        rows = pd.read_csv(manifest).to_dict(orient="records")
        html = build_html(rows, args.out)
        print(html)
        return 0
    if args.out.exists():
        allowed_resume = {"scan_checkpoint.json", "review_images", "causal_images", "label_studio"}
        unexpected = [p for p in args.out.iterdir() if p.name not in allowed_resume]
        material = [
            p
            for name in ("review_images", "causal_images", "label_studio")
            for p in (args.out / name).glob("*")
            if (args.out / name).is_dir()
        ]
        if unexpected or material:
            raise SystemExit(f"refusing to overwrite material output: {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)
    review_dir = args.out / "review_images"
    causal_dir = args.out / "causal_images"
    ls_dir = args.out / "label_studio"
    for directory in (review_dir, causal_dir, ls_dir):
        directory.mkdir(exist_ok=True)

    frame = load_dev_frame(args.input)
    max_idx = safe_max_index(frame)
    ma_frame = add_mas(frame)
    anchors = anchor_order(WINDOW - 1, max_idx, primary=args.primary_anchors, seed=args.seed)
    model = load_yolo_model(args.weights)
    detections = scan_v10(
        ma_frame,
        model=model,
        anchors=anchors,
        conf=args.conf,
        batch_size=args.batch_size,
        device=args.device,
        min_gap_bars=args.min_gap_bars,
        target=TARGET,
        checkpoint=args.out / "scan_checkpoint.json",
    )
    rng = random.Random(args.seed + 1)
    rng.shuffle(detections)

    rows: list[dict] = []
    tasks: list[dict] = []
    for task_id, detection in enumerate(detections, 1):
        candidate = Candidate(
            idx=detection.idx,
            source="v10",
            score=detection.conf,
            v10_conf=detection.conf,
            v10_box=detection.box,
        )
        review_rel = f"review_images/task_{task_id:03d}.jpg"
        causal_rel = f"causal_images/task_{task_id:03d}.png"
        render_simple_review(frame, ma_frame, candidate, path=args.out / review_rel)
        render_chart(
            ma_frame.iloc[detection.idx - WINDOW + 1 : detection.idx + 1],
            out_path=args.out / causal_rel,
        )
        ts = pd.Timestamp(frame["open_time"].iloc[detection.idx])
        future_end = pd.Timestamp(frame["open_time"].iloc[detection.idx + FUTURE_BARS])
        if future_end >= HOLDOUT_START:
            raise RuntimeError(f"holdout leak: {future_end}")
        row = {
            "task_id": task_id,
            "candidate_time": ts.isoformat(),
            "future_end": future_end.isoformat(),
            "v10_conf": detection.conf,
            "box_cx": detection.box[0],
            "box_cy": detection.box[1],
            "box_w": detection.box[2],
            "box_h": detection.box[3],
            "box_right_bar": detection.right_bar,
            "causal_image_rel": causal_rel,
            "review_image_rel": review_rel,
            "shape_label": "",
            "outcome_label": "",
            **outcome_row(frame, detection.idx),
        }
        rows.append(row)
        tasks.append(
            {
                "data": {
                    "causal_image": f"/data/local-files/?d={args.out.name}/{causal_rel}",
                    "review_image": f"/data/local-files/?d={args.out.name}/{review_rel}",
                    "task_id": task_id,
                },
                "predictions": [
                    {
                        "model_version": "owner_short_star_v10_15m_ood_on_eth_3m",
                        "score": detection.conf,
                        "result": [
                            {
                                "id": f"v10_{task_id:03d}",
                                "type": "rectanglelabels",
                                "from_name": "box",
                                "to_name": "causal",
                                "original_width": 1280,
                                "original_height": 742,
                                "image_rotation": 0,
                                "value": {
                                    **yolo_to_ls_box(detection.box),
                                    "rectanglelabels": ["short_start"],
                                },
                            }
                        ],
                    }
                ],
            }
        )
        if task_id % 25 == 0 or task_id == TARGET:
            print(f"render {task_id}/{TARGET}", flush=True)

    with (args.out / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (ls_dir / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    (ls_dir / "label_config.xml").write_text(label_config(), encoding="utf-8")
    summary = {
        "tasks": TARGET,
        "all_have_visible_v10_prebox": True,
        "conf_floor": args.conf,
        "tip_gate": "tip/tip-1/tip-2",
        "min_gap_minutes": args.min_gap_bars * 3,
        "future_hours_human_only": 3,
        "max_future_end": max(row["future_end"] for row in rows),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_consumed": False,
        "label_studio_status": "prepared_not_imported",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    target = build_html(rows, args.out)
    print(f"wrote {target} ({target.stat().st_size / 1024 / 1024:.1f} MiB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

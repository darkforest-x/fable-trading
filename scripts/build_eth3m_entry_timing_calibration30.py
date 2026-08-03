#!/usr/bin/env python3
"""Build a 30-image ETH 3m short-entry timing calibration pack.

The source population is limited to the 93 Label Studio project-53 tasks the
owner marked ``是`` for shape.  Nearby tasks are grouped into >60-minute event
clusters, one representative is kept per event, and 15 representatives are
sampled from each future-aware diagnostic stratum (remaining move >= consumed
move versus consumed move > remaining move).  The stratum is used only to make
the calibration pack diverse; it is never shown to the owner or used as a
causal feature.

For each owner-confirmed shape, the proposed decision bar is the first bar
inside the original v10 box whose completed close is below all six rendered
moving averages.  This is deliberately a proposal for human calibration, not
an accepted trading rule.  The model-side image contains exactly 200 completed
bars ending at that proposal.  The review image adds a fixed 60-bar / 3-hour
future window, plus the later original v10 fire for comparison.

All OHLC rows are physically truncated to <2026-05-04 before calculations.
The script does not import Label Studio tasks, train a model, change thresholds,
or consume holdout.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_eth_3m_dual_view_calibration import (  # noqa: E402
    FUTURE_BARS,
    HOLDOUT_START,
    box_geometry,
    load_dev_frame,
)
from scripts.scan_eth_3m_v10_prelabels_html import _draw_candles, _time_ticks  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.yolo_candidates import WINDOW  # noqa: E402

TARGET = 30
PER_STRATUM = TARGET // 2
EVENT_GAP_MINUTES = 60
MIN_LEAD_BARS = 2
BAR_MINUTES = 3
SEED = 20260729
DEFAULT_INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_DETAIL = PROJECT / "analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv"
DEFAULT_OUT = PROJECT / "datasets/eth_3m_entry_timing_calibration30"

# Matplotlib's bundled DejaVu font has no CJK glyphs on this Mac.  Register a
# local system font explicitly so the annotations do not render as empty boxes.
for _font_path in (
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
):
    if _font_path.exists():
        font_manager.fontManager.addfont(str(_font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(
            fname=str(_font_path)
        ).get_name()
        plt.rcParams["axes.unicode_minus"] = False
        break


def assign_event_ids(rows: pd.DataFrame) -> pd.DataFrame:
    """Cluster chronologically adjacent owner-positive tasks into events."""
    out = rows.sort_values("candidate_time").copy()
    new_event = out["candidate_time"].diff() > pd.Timedelta(minutes=EVENT_GAP_MINUTES)
    if len(new_event):
        new_event.iloc[0] = True
    out["event_id"] = new_event.cumsum().astype(int)
    return out


def event_representatives(rows: pd.DataFrame) -> pd.DataFrame:
    """Keep one high-confidence, meaningfully earlier proposal per event."""
    eligible = rows[rows["first_below_all_mas_lag_bars"] >= MIN_LEAD_BARS].copy()
    reps = (
        eligible.sort_values(
            ["event_id", "v10_conf", "first_below_all_mas_lag_bars"],
            ascending=[True, False, False],
        )
        .groupby("event_id", as_index=False, sort=True)
        .head(1)
    )
    return reps.sort_values("candidate_time").reset_index(drop=True)


def evenly_spaced(rows: pd.DataFrame, n: int) -> pd.DataFrame:
    """Select n time-spread rows without introducing random split semantics."""
    ordered = rows.sort_values("candidate_time").reset_index(drop=True)
    if len(ordered) < n:
        raise ValueError(f"only {len(ordered)} event representatives for requested {n}")
    positions = np.linspace(0, len(ordered) - 1, n).round().astype(int)
    if len(set(map(int, positions))) != n:
        raise ValueError("even selection produced duplicate positions")
    return ordered.iloc[positions].copy()


def select_calibration_rows(rows: pd.DataFrame, *, seed: int = SEED) -> pd.DataFrame:
    """Return 30 unique events, balanced across the two diagnostic strata."""
    positives = rows[rows["owner_is_target"] == 1].copy()
    positives["candidate_time"] = pd.to_datetime(positives["candidate_time"], utc=True)
    positives["box_start_time"] = pd.to_datetime(positives["box_start_time"], utc=True)
    clustered = assign_event_ids(positives)
    reps = event_representatives(clustered)
    selected = []
    for value in (0, 1):
        selected.append(
            evenly_spaced(
                reps[reps["consumed_exceeds_remaining"] == value], PER_STRATUM
            )
        )
    out = pd.concat(selected, ignore_index=True)
    if out["event_id"].nunique() != TARGET:
        raise ValueError("calibration selection is not event-unique")
    # Shuffle presentation order so adjacent cards do not reveal the private
    # future-aware stratum.  The selected population itself remains fixed.
    order = list(range(len(out)))
    random.Random(seed).shuffle(order)
    return out.iloc[order].reset_index(drop=True)


def proposed_entry_index(
    row: Any,
    *,
    ma_frame: pd.DataFrame,
    position_by_time: pd.Series,
) -> tuple[int, int, int]:
    """Map the original box and return its first completed close below all MAs."""
    signal_i = int(position_by_time.loc[pd.Timestamp(row.candidate_time)])
    box_start_i = int(position_by_time.loc[pd.Timestamp(row.box_start_time)])
    box = ma_frame.iloc[box_start_i : signal_i + 1]
    ma_valid = box[list(ALL_MA_COLS)].notna().all(axis=1)
    below_all = ma_valid & (box["close"] < box[list(ALL_MA_COLS)].min(axis=1))
    hits = below_all[below_all].index
    if len(hits) == 0:
        raise ValueError(f"task {row.task_id}: no below-all-MA bar inside original box")
    entry_i = int(hits[0])
    if signal_i - entry_i < MIN_LEAD_BARS:
        raise ValueError(f"task {row.task_id}: proposal is not at least {MIN_LEAD_BARS} bars earlier")
    return entry_i, signal_i, box_start_i


def original_box_prices(
    row: Any,
    *,
    ma_frame: pd.DataFrame,
    signal_i: int,
) -> tuple[float, float]:
    original_causal = ma_frame.iloc[signal_i - WINDOW + 1 : signal_i + 1]
    transform = make_chart_transform(original_causal)
    _, _, low, high = box_geometry(
        (float(row.box_cx), float(row.box_cy), float(row.box_w), float(row.box_h)),
        transform,
    )
    return low, high


def render_review(
    frame: pd.DataFrame,
    ma_frame: pd.DataFrame,
    row: Any,
    *,
    entry_i: int,
    signal_i: int,
    box_start_i: int,
    path: Path,
) -> None:
    """Render one clean causal boundary plus fixed human-only three-hour future."""
    review_start = entry_i - WINDOW + 1
    review_end = entry_i + FUTURE_BARS
    review = ma_frame.iloc[review_start : review_end + 1].copy().reset_index(drop=True)
    if len(review) != WINDOW + FUTURE_BARS:
        raise ValueError(f"task {row.task_id}: incomplete review window")

    low, high = original_box_prices(row, ma_frame=ma_frame, signal_i=signal_i)
    local_box_start = box_start_i - review_start
    local_signal = signal_i - review_start
    lead_minutes = (signal_i - entry_i) * BAR_MINUTES

    fig, ax = plt.subplots(figsize=(12.8, 6.6), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    _draw_candles(ax, review)

    # The owner already approved this original v10 region as a shape.  Keep it
    # quiet and dashed so the only focal mark is the proposed causal boundary.
    ax.add_patch(
        Rectangle(
            (local_box_start - 0.4, low),
            max(0.8, local_signal - local_box_start + 0.8),
            max(1e-9, high - low),
            fill=False,
            edgecolor="#8b9298",
            lw=1.1,
            ls="--",
            alpha=0.8,
        )
    )
    ax.text(
        max(0, local_box_start),
        high,
        "原v10形态框",
        color="#747b82",
        fontsize=7,
        va="bottom",
    )

    # Decision happens after the completed entry_i bar.  Draw at the boundary
    # between that bar and the first human-only future bar.
    ax.axvline(WINDOW - 0.5, color="#e07a16", lw=2.0, zorder=6)
    ax.text(
        WINDOW - 2,
        ax.get_ylim()[1],
        "候选入场判断点",
        color="#c86408",
        fontsize=9,
        fontweight="bold",
        ha="right",
        va="top",
    )
    ax.axvline(local_signal + 0.5, color="#5f6871", lw=1.0, ls=":", zorder=5)
    ax.text(
        local_signal + 1.5,
        ax.get_ylim()[1],
        f"原v10开火 +{lead_minutes}min",
        color="#59626a",
        fontsize=8,
        va="top",
    )
    ax.text(
        WINDOW + 2,
        ax.get_ylim()[0],
        "右侧仅供人工查看：未来3小时",
        color="#7a8086",
        fontsize=8,
        va="bottom",
    )

    ax.set_title("ETH_USDT_SWAP · 3m · 提前入场线校准", color="#20262b", fontsize=11, loc="left")
    ax.set_ylabel("Price", color="#30363b")
    _time_ticks(ax, review, count=8)
    ax.tick_params(colors="#4b535a", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#8b9298")
    ax.grid(color="#d9dde1", alpha=0.45, lw=0.55)
    fig.tight_layout(pad=1.1)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format="jpeg",
        facecolor="white",
        pil_kwargs={"quality": 72, "optimize": True},
    )
    plt.close(fig)


def build_html(rows: list[dict[str, Any]], out: Path, summary: dict[str, Any]) -> Path:
    cards = []
    for row in rows:
        encoded = base64.b64encode((out / row["review_image_rel"]).read_bytes()).decode("ascii")
        cards.append(
            f'''<article class="card"><div class="meta"><b>校准 {row['task_id']:02d} / {len(rows)}</b><span>ETH 3m · owner形态=是</span></div><img loading="lazy" src="data:image/jpeg;base64,{encoded}" alt="ETH 3m entry timing calibration {row['task_id']:02d}"><p>只看橙色竖线：如果在这根收盘后准备做空，是否来得及？灰色虚线框与细线仅用于对照原 v10。</p></article>'''
        )
    payload = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ETH 3m · 提前入场线校准30张</title><style>
:root{{--bg:#0f1113;--panel:#171a1e;--line:#303840;--text:#edf1f5;--muted:#a7b0b8;--orange:#ffad55}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",system-ui,sans-serif}}main{{max-width:1080px;margin:auto;padding:16px 12px 70px}}.eyebrow{{color:#80cbc4;font-weight:700;letter-spacing:.08em}}h1{{font-size:clamp(27px,5vw,44px);line-height:1.12;margin:.25em 0}}.sub{{color:var(--muted);max-width:850px}}.rule{{margin:15px 0;padding:12px 14px;background:#261c13;border-left:4px solid var(--orange);border-radius:8px}}.stats{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 18px}}.stat{{padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}}.stat b{{font-size:20px;margin-right:5px}}.card{{margin:0 0 16px;background:var(--panel);border:1px solid var(--line);border-radius:11px;overflow:hidden}}.card img{{display:block;width:100%;height:auto;background:#fff}}.meta{{display:flex;gap:10px;padding:10px 12px;border-bottom:1px solid var(--line)}}.meta b{{margin-right:auto}}.meta span,.card p{{color:var(--muted)}}.card p{{margin:0;padding:10px 12px;border-top:1px solid var(--line)}}@media(max-width:650px){{main{{padding:9px 7px 55px}}.card{{border-radius:7px}}.meta{{font-size:12px}}}}
</style></head><body><main><div class="eyebrow">YOYO TRADING · ETH 3M SHORT</div><h1>提前入场线校准 · 30张</h1><p class="sub">这 30 张都来自你已经判断“形态是”的独立事件。页面没有按钮，直接往下滑；这里只校准橙色入场判断线，不重新判断形态。</p><div class="rule"><b>唯一问题：</b>橙色竖线左侧是模型当时能看到的 200 根，右侧是人工可看的未来 3 小时。如果在橙线处准备做空，这个时机来得及吗？</div><div class="stats"><div class="stat"><b>30</b>独立事件</div><div class="stat"><b>{summary['lead_minutes_median']:.0f}min</b>比原v10中位提前</div><div class="stat"><b>3h</b>固定人工未来窗</div></div>{''.join(cards)}</main></body></html>'''
    target = out / "eth3m_entry_timing_calibration30_mobile.html"
    target.write_text(payload, encoding="utf-8")
    return target


def label_config() -> str:
    return """<View>
  <Header value="ETH 3m · 橙色入场判断点来得及吗？"/>
  <Choices name="entry_timing" toName="review" choice="single" required="true" showInline="true">
    <Choice value="是" hotkey="1"/>
    <Choice value="不是" hotkey="2"/>
  </Choices>
  <Header value="橙线左侧是模型因果输入；右侧固定未来3小时，只供人工判断。"/>
  <Image name="review" value="$review_image" zoom="true" zoomControl="true"/>
</View>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"refusing to overwrite material output: {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)
    review_dir = args.out / "review_images"
    causal_dir = args.out / "causal_images"
    ls_dir = args.out / "label_studio"
    for directory in (review_dir, causal_dir, ls_dir):
        directory.mkdir(parents=True, exist_ok=True)

    frame = load_dev_frame(args.input)
    ma_frame = add_mas(frame)
    position_by_time = pd.Series(frame.index.to_numpy(), index=frame["open_time"])
    detail = pd.read_csv(args.detail)
    selected = select_calibration_rows(detail, seed=args.seed)

    rows: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for task_id, source in enumerate(selected.itertuples(index=False), 1):
        entry_i, signal_i, box_start_i = proposed_entry_index(
            source, ma_frame=ma_frame, position_by_time=position_by_time
        )
        future_end_i = entry_i + FUTURE_BARS
        future_end = pd.Timestamp(frame["open_time"].iloc[future_end_i])
        if future_end >= HOLDOUT_START:
            raise RuntimeError(f"task {source.task_id}: holdout leak {future_end}")
        causal_start = entry_i - WINDOW + 1
        if causal_start < 0:
            raise RuntimeError(f"task {source.task_id}: insufficient causal window")

        review_rel = f"review_images/task_{task_id:02d}.jpg"
        causal_rel = f"causal_images/task_{task_id:02d}.png"
        render_review(
            frame,
            ma_frame,
            source,
            entry_i=entry_i,
            signal_i=signal_i,
            box_start_i=box_start_i,
            path=args.out / review_rel,
        )
        render_chart(
            ma_frame.iloc[causal_start : entry_i + 1],
            out_path=args.out / causal_rel,
        )

        entry_close = float(frame["close"].iloc[entry_i])
        future = frame.iloc[entry_i + 1 : future_end_i + 1]
        row = {
            "task_id": task_id,
            "source_task_id": int(source.task_id),
            "event_id": int(source.event_id),
            "entry_candidate_time": pd.Timestamp(frame["open_time"].iloc[entry_i]).isoformat(),
            "original_v10_time": pd.Timestamp(frame["open_time"].iloc[signal_i]).isoformat(),
            "future_end": future_end.isoformat(),
            "lead_bars": int(signal_i - entry_i),
            "lead_minutes": int((signal_i - entry_i) * BAR_MINUTES),
            "selection_stratum": (
                "consumed_exceeds_remaining"
                if int(source.consumed_exceeds_remaining) == 1
                else "remaining_not_less_than_consumed"
            ),
            "proposal_rule": "first close below all six MAs inside owner-confirmed v10 box",
            "future_return_3h": float(future["close"].iloc[-1] / entry_close - 1),
            "future_max_drop_3h": float(1 - future["low"].min() / entry_close),
            "future_max_rebound_3h": float(future["high"].max() / entry_close - 1),
            "causal_image_rel": causal_rel,
            "review_image_rel": review_rel,
            "entry_timing_label": "",
        }
        rows.append(row)
        tasks.append(
            {
                "data": {
                    "review_image": f"/data/local-files/?d={args.out.name}/{review_rel}",
                    "causal_image": f"/data/local-files/?d={args.out.name}/{causal_rel}",
                    "task_id": task_id,
                    "source_task_id": int(source.task_id),
                }
            }
        )

    leads = pd.Series([row["lead_minutes"] for row in rows], dtype=float)
    summary = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "holdout_consumed": False,
        "holdout_start": HOLDOUT_START.isoformat(),
        "task_count": len(rows),
        "unique_event_count": len({row["event_id"] for row in rows}),
        "source_owner_yes_count": int((detail["owner_is_target"] == 1).sum()),
        "selection_strata": {
            key: sum(row["selection_stratum"] == key for row in rows)
            for key in ("remaining_not_less_than_consumed", "consumed_exceeds_remaining")
        },
        "entry_candidate_min": min(row["entry_candidate_time"] for row in rows),
        "entry_candidate_max": max(row["entry_candidate_time"] for row in rows),
        "future_end_max": max(row["future_end"] for row in rows),
        "lead_minutes_min": float(leads.min()),
        "lead_minutes_median": float(leads.median()),
        "lead_minutes_max": float(leads.max()),
        "proposal_rule": "first close below all six MAs inside owner-confirmed v10 box",
        "status": "preview_only_not_imported_not_trained",
    }

    with (args.out / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ls_dir / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ls_dir / "label_config.xml").write_text(label_config(), encoding="utf-8")
    html = build_html(rows, args.out, summary)
    print(json.dumps({"html": str(html), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

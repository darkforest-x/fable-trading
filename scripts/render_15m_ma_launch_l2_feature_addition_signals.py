#!/usr/bin/env python3
"""Render the 31 independent final q90 decisions with their realized outcomes.

The left side of every chart contains the 168 closed bars available to L2 at
``feature_bar_i``.  The right side is an explicitly shaded, review-only future
path of 72 bars used by the already-frozen TP5/SL2 label.  Future bars are never
fed back into either L1 or L2.  The preserved L1 rectangle is recovered from
its normalized detector coordinates and exact 1280x742 input transform.

Inputs are the frozen feature-addition score ledger, causal feature dataset and
global-context snapshot.  The renderer performs no inference, fitting,
threshold selection, network access, promotion, deployment, forward mutation,
Telegram send or order action.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_feature_group_ablation import bool_series
from scripts.research_15m_ma_launch_l2_global_context import (
    L2_CONTEXT_BARS,
    load_snapshot,
    normalized_box_corners,
    pixel_sha256,
    read_json,
    sha256_file,
    utc,
    write_json,
)
from yoyo.data.indicators import add_indicators
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import ChartTransform, render_chart

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-feature-addition-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_feature_addition_v1"
GALLERY_DIR = OUTPUT_DIR / "selected_q90_signal_gallery"
FEATURE_DATASET = OUTPUT_DIR / "l2_dataset_feature_addition.csv"
SCORED_FINAL = OUTPUT_DIR / "final_validation_feature_addition_scored.csv"

GLOBAL_EXPERIMENT_DIR = (
    ROOT / "experiments" / "active" / "exp-15m-ma-launch-l2-global-context-v1"
)
GLOBAL_PREREG = GLOBAL_EXPERIMENT_DIR / "preregistration.json"
GLOBAL_RESULTS = GLOBAL_EXPERIMENT_DIR / "results"
GLOBAL_OUTPUT = ROOT / "analysis" / "output" / "ma_launch_l2_global_context_v1"

HORIZON_BARS = 72
TP_ATR_MULTIPLE = 5.0
SL_ATR_MULTIPLE = 2.0
COST_FRACTION = 0.002
EXPECTED_SELECTED = 31
CHART_WIDTH = 1920
CHART_HEIGHT = 1080
HEADER_HEIGHT = 150
FOOTER_HEIGHT = 90
CANVAS_HEIGHT = HEADER_HEIGHT + CHART_HEIGHT + FOOTER_HEIGHT


class SignalGalleryError(RuntimeError):
    """Fail closed when signal identity, geometry or outcome semantics drift."""


def require(condition: bool, message: str) -> None:
    """Raise one explicit gallery-contract failure."""

    if not condition:
        raise SignalGalleryError(message)


def repo_relative(path: Path) -> str:
    """Return one repository-relative POSIX path."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def verify_file(path: Path, expected_sha256: str, label: str) -> None:
    """Require a byte-identical declared input."""

    require(path.is_file(), f"{label} missing: {path}")
    observed = sha256_file(path)
    require(observed == expected_sha256, f"{label} SHA drifted: {observed}")


def select_signal_rows(dataset: pd.DataFrame, scored: pd.DataFrame) -> pd.DataFrame:
    """Return only q90-kept dependency representatives, joined one-to-one."""

    required_scores = {
        "episode_id",
        "dependency_representative",
        "selected_keep",
        "selected_arm",
        "selected_score",
        "selected_percentile",
        "selected_threshold",
    }
    required_dataset = {
        "episode_id",
        "available_at",
        "split",
        "dependency_representative",
        "outcome",
        "label",
        "realized_ret",
        "net_ret",
    }
    require(required_scores <= set(scored), "score ledger columns drifted")
    require(required_dataset <= set(dataset), "feature dataset columns drifted")
    score_mask = bool_series(scored["dependency_representative"]) & bool_series(
        scored["selected_keep"]
    )
    selected = scored.loc[
        score_mask,
        [
            "episode_id",
            "selected_arm",
            "selected_score",
            "selected_percentile",
            "selected_threshold",
        ],
    ].copy()
    require(selected["episode_id"].is_unique, "selected score episode_id is not unique")
    source = dataset[dataset["episode_id"].isin(selected["episode_id"])].copy()
    source = source[
        (source["split"] == "final_validation")
        & bool_series(source["dependency_representative"])
    ]
    require(source["episode_id"].is_unique, "selected dataset episode_id is not unique")
    joined = source.merge(selected, on="episode_id", how="inner", validate="one_to_one")
    require(len(joined) == len(selected), "selected score rows did not join one-to-one")
    joined["net_profitable"] = joined["net_ret"].astype(float) > 0.0
    joined["barrier_positive"] = joined["label"].astype(int) == 1
    return joined.sort_values(["available_at", "episode_id"]).reset_index(drop=True)


def summarize_outcomes(rows: pd.DataFrame) -> dict[str, Any]:
    """Separate barrier labels from actual after-cost profitability."""

    outcomes = Counter(rows["outcome"].astype(str))
    profitable = int(rows["net_profitable"].sum())
    barrier_positive = int(rows["barrier_positive"].sum())
    return {
        "selected_independent_signals": len(rows),
        "net_profitable": profitable,
        "net_unprofitable": int(len(rows) - profitable),
        "barrier_positive_labels": barrier_positive,
        "barrier_negative_labels": int(len(rows) - barrier_positive),
        "outcomes": dict(sorted(outcomes.items())),
        "positive_timeouts": int(
            ((rows["outcome"].astype(str) == "timeout") & rows["net_profitable"]).sum()
        ),
        "negative_timeouts": int(
            ((rows["outcome"].astype(str) == "timeout") & ~rows["net_profitable"]).sum()
        ),
        "long": int((rows["side"].astype(str) == "long").sum()),
        "short": int((rows["side"].astype(str) == "short").sum()),
        "mean_gross_return": float(rows["realized_ret"].astype(float).mean()),
        "mean_net_return": float(rows["net_ret"].astype(float).mean()),
        "start_available_at": utc(rows["available_at"].min()).isoformat(),
        "end_available_at": utc(rows["available_at"].max()).isoformat(),
    }


def load_selected_signals() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load byte-pinned score/data inputs and validate the frozen count."""

    feature_receipt = read_json(RESULTS_DIR / "feature_dataset_receipt.json")
    training_receipt = read_json(RESULTS_DIR / "training_receipt.json")
    verify_file(
        FEATURE_DATASET, str(feature_receipt["dataset_sha256"]), "feature dataset"
    )
    verify_file(SCORED_FINAL, str(training_receipt["scored_sha256"]), "scored final")
    dataset = pd.read_csv(FEATURE_DATASET)
    scored = pd.read_csv(SCORED_FINAL)
    rows = select_signal_rows(dataset, scored)
    expected = int(training_receipt["metrics"]["selected"]["frozen_q90"]["n"])
    require(expected == EXPECTED_SELECTED, "training receipt selected count drifted")
    require(
        len(rows) == expected, f"selected row count drifted: {len(rows)} != {expected}"
    )
    summary = summarize_outcomes(rows)
    require(summary["net_profitable"] == 16, "after-cost profitable count drifted")
    require(summary["net_unprofitable"] == 15, "after-cost loss count drifted")
    require(
        summary["outcomes"] == {"sl": 14, "timeout": 4, "tp": 13},
        "outcome counts drifted",
    )
    require(summary["positive_timeouts"] == 3, "positive timeout count drifted")
    require(
        abs(summary["mean_net_return"] - 0.009870076455092053) < 1e-15,
        "mean net return drifted",
    )
    return rows, summary


def _inverse_x(transform: ChartTransform, pixel: int) -> float:
    return (pixel - transform.left) / transform.plot_w * (transform.n_bars - 1)


def _inverse_y(transform: ChartTransform, pixel: int) -> float:
    return transform.price_max - (pixel - transform.top) / transform.plot_h * (
        transform.price_max - transform.price_min
    )


def _x_float(transform: ChartTransform, index: float) -> int:
    return round(transform.left + index / (transform.n_bars - 1) * transform.plot_w)


def _draw_dashed_vertical(
    image: np.ndarray,
    x: int,
    color: tuple[int, int, int],
    *,
    width: int = 2,
    dash: int = 12,
) -> None:
    for y in range(0, image.shape[0], dash * 2):
        cv2.line(
            image,
            (x, y),
            (x, min(image.shape[0] - 1, y + dash)),
            color,
            width,
            cv2.LINE_AA,
        )


def _draw_dashed_horizontal(
    image: np.ndarray,
    y: int,
    x0: int,
    x1: int,
    color: tuple[int, int, int],
    *,
    width: int = 2,
    dash: int = 14,
) -> None:
    for x in range(max(0, x0), min(image.shape[1] - 1, x1), dash * 2):
        cv2.line(image, (x, y), (min(x1, x + dash), y), color, width, cv2.LINE_AA)


def _barrier_prices(
    row: Mapping[str, Any], enriched: pd.DataFrame
) -> tuple[float, float]:
    signal_i = int(row["feature_bar_i"])
    atr = float(enriched["atr14"].iloc[signal_i])
    entry = float(row["entry_price"])
    require(np.isfinite(atr) and atr > 0, "decision ATR is invalid")
    if str(row["side"]) == "long":
        return entry + TP_ATR_MULTIPLE * atr, entry - SL_ATR_MULTIPLE * atr
    return entry - TP_ATR_MULTIPLE * atr, entry + SL_ATR_MULTIPLE * atr


def render_signal_chart(
    row: Mapping[str, Any], frame: pd.DataFrame, order: int
) -> np.ndarray:
    """Render one high-resolution causal-input plus review-outcome chart."""

    enriched = add_indicators(add_mas(frame))
    signal_i = int(row["feature_bar_i"])
    context_start = signal_i - L2_CONTEXT_BARS + 1
    review_end = signal_i + HORIZON_BARS
    require(
        context_start >= 0 and review_end < len(enriched),
        "review context is incomplete",
    )
    review = enriched.iloc[context_start : review_end + 1]
    chart, review_tf = render_chart(
        review,
        width=CHART_WIDTH,
        height=CHART_HEIGHT,
        out_path=None,
    )

    exact_input = enriched.iloc[int(row["window_start_i"]) : signal_i + 1]
    exact_pixels, input_tf = render_chart(exact_input, out_path=None)
    require(
        pixel_sha256(exact_pixels) == str(row["input_pixel_sha256"]),
        f"exact L1 input pixels drifted: {row['episode_id']}",
    )
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(
        row, input_tf.width, input_tf.height
    )
    global_x0 = int(row["window_start_i"]) + _inverse_x(input_tf, raw_x0)
    global_x1 = int(row["window_start_i"]) + _inverse_x(input_tf, raw_x1)
    box_x0 = _x_float(review_tf, global_x0 - context_start)
    box_x1 = _x_float(review_tf, global_x1 - context_start)
    box_y0 = review_tf.y_at(_inverse_y(input_tf, raw_y0))
    box_y1 = review_tf.y_at(_inverse_y(input_tf, raw_y1))

    decision_local = signal_i - context_start
    entry_local = decision_local + 1
    exit_local = decision_local + int(row["exit_offset"])
    decision_x = review_tf.x_at(decision_local)
    entry_x = review_tf.x_at(entry_local)
    exit_x = review_tf.x_at(exit_local)
    future_x = round((decision_x + entry_x) / 2)

    overlay = chart.copy()
    cv2.rectangle(
        overlay, (future_x, 0), (CHART_WIDTH - 1, CHART_HEIGHT - 1), (230, 246, 250), -1
    )
    chart = cv2.addWeighted(overlay, 0.22, chart, 0.78, 0)
    if exit_x < CHART_WIDTH - 1:
        post = chart.copy()
        cv2.rectangle(
            post, (exit_x, 0), (CHART_WIDTH - 1, CHART_HEIGHT - 1), (220, 220, 220), -1
        )
        chart = cv2.addWeighted(post, 0.18, chart, 0.82, 0)

    _draw_dashed_vertical(chart, decision_x, (220, 170, 15), width=3)
    cv2.rectangle(
        chart,
        (min(box_x0, box_x1), min(box_y0, box_y1)),
        (max(box_x0, box_x1), max(box_y0, box_y1)),
        (30, 30, 225),
        5,
        cv2.LINE_AA,
    )
    tp_price, sl_price = _barrier_prices(row, enriched)
    tp_y, sl_y = review_tf.y_at(tp_price), review_tf.y_at(sl_price)
    if 0 <= tp_y < CHART_HEIGHT:
        _draw_dashed_horizontal(
            chart, tp_y, entry_x, CHART_WIDTH - 1, (50, 155, 50), width=2
        )
    if 0 <= sl_y < CHART_HEIGHT:
        _draw_dashed_horizontal(
            chart, sl_y, entry_x, CHART_WIDTH - 1, (45, 45, 220), width=2
        )
    entry_y = review_tf.y_at(float(row["entry_price"]))
    cv2.circle(chart, (entry_x, entry_y), 7, (0, 130, 245), -1, cv2.LINE_AA)
    outcome = str(row["outcome"])
    if outcome == "tp":
        exit_price = tp_price
    elif outcome == "sl":
        exit_price = sl_price
    else:
        exit_price = float(enriched["close"].iloc[signal_i + int(row["exit_offset"])])
    exit_y = review_tf.y_at(exit_price)
    profitable = bool(row["net_profitable"])
    outcome_color = (35, 155, 45) if profitable else (45, 45, 220)
    cv2.circle(chart, (exit_x, exit_y), 10, outcome_color, -1, cv2.LINE_AA)
    cv2.circle(chart, (exit_x, exit_y), 13, (20, 20, 20), 2, cv2.LINE_AA)

    canvas = np.full((CANVAS_HEIGHT, CHART_WIDTH, 3), 248, dtype=np.uint8)
    canvas[HEADER_HEIGHT : HEADER_HEIGHT + CHART_HEIGHT] = chart
    state = "NET WIN" if profitable else "NET LOSS"
    available = utc(row["available_at"])
    cst = available.tz_convert("Asia/Shanghai")
    title = (
        f"#{order:02d}/{EXPECTED_SELECTED} | {state} ({outcome.upper()}) | "
        f"{row['symbol']} | {str(row['side']).upper()}"
    )
    detail = (
        f"available {available:%Y-%m-%d %H:%M} UTC / {cst:%Y-%m-%d %H:%M} CST | "
        f"entry {float(row['entry_price']):.8g} | exit +{int(row['exit_offset'])} bars | "
        f"gross {float(row['realized_ret']):+.3%} | net {float(row['net_ret']):+.3%}"
    )
    score = (
        f"L2 {row['selected_arm']} | score {float(row['selected_score']):.6f} | "
        f"side percentile {float(row['selected_percentile']):.1%} | "
        f"gate {float(row['selected_threshold']):.6f} | L1 conf {float(row['l1_confidence']):.3f}"
    )
    cv2.putText(
        canvas,
        title,
        (26, 43),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.88,
        (20, 20, 20),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        detail,
        (26, 84),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.66,
        (45, 45, 45),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        score,
        (26, 122),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (65, 65, 65),
        1,
        cv2.LINE_AA,
    )
    footer_y = HEADER_HEIGHT + CHART_HEIGHT + 34
    cv2.putText(
        canvas,
        "Red box = preserved L1 detection | Cyan dashed = last visible bar | Orange dot = next-open entry | Green/red dot = trade exit",
        (26, footer_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (45, 45, 45),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Pale right = outcome-only future; gray after exit is not counted in P&L | dashed barriers = TP 5 ATR / SL 2 ATR | cost = 0.20%",
        (26, footer_y + 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (45, 45, 45),
        1,
        cv2.LINE_AA,
    )
    return canvas


def chart_filename(row: Mapping[str, Any], order: int) -> str:
    state = "WIN" if bool(row["net_profitable"]) else "LOSS"
    stamp = utc(row["available_at"])
    symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
    return (
        f"{order:02d}_{state}_{str(row['outcome']).upper()}_{symbol}_"
        f"{str(row['side']).upper()}_{stamp:%Y%m%dT%H%MZ}.png"
    )


def build_overview_pages(
    manifest: pd.DataFrame,
    *,
    building: Path,
    final_dir: Path,
) -> list[dict[str, Any]]:
    """Build eight readable 2x2 pages that cover all 31 source charts."""

    tile_w, tile_h = 960, 660
    header_h = 74
    pages: list[dict[str, Any]] = []
    chunks = [manifest.iloc[i : i + 4] for i in range(0, len(manifest), 4)]
    for page_number, chunk in enumerate(chunks, 1):
        canvas = np.full((header_h + tile_h * 2, tile_w * 2, 3), 238, dtype=np.uint8)
        title = (
            f"L2 q90 independent signals | 16 net wins / 15 net losses | "
            f"page {page_number}/{len(chunks)}"
        )
        cv2.putText(
            canvas,
            title,
            (22, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.86,
            (25, 25, 25),
            2,
            cv2.LINE_AA,
        )
        source_rows: list[dict[str, str]] = []
        for cell, row in enumerate(chunk.to_dict("records")):
            source = building / Path(str(row["chart_path"])).relative_to(
                repo_relative(final_dir)
            )
            image = cv2.imread(str(source), cv2.IMREAD_COLOR)
            require(image is not None, f"overview source unreadable: {source}")
            tile = cv2.resize(image, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
            y, x = header_h + (cell // 2) * tile_h, (cell % 2) * tile_w
            canvas[y : y + tile_h, x : x + tile_w] = tile
            cv2.rectangle(
                canvas, (x, y), (x + tile_w - 1, y + tile_h - 1), (80, 80, 80), 1
            )
            source_rows.append(
                {
                    "episode_id": str(row["episode_id"]),
                    "chart_sha256": str(row["chart_sha256"]),
                }
            )
        filename = f"overview_page_{page_number:02d}.png"
        path = building / filename
        require(
            cv2.imwrite(str(path), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]),
            f"write failed: {path}",
        )
        pages.append(
            {
                "path": repo_relative(final_dir / filename),
                "sha256": sha256_file(path),
                "pixel_sha256": pixel_sha256(canvas),
                "sources": source_rows,
            }
        )
    return pages


def build_gallery_html(
    manifest: pd.DataFrame, summary: Mapping[str, Any], output: Path
) -> None:
    """Write a folder-native HTML browser linking every original PNG."""

    cards: list[str] = []
    for row in manifest.to_dict("records"):
        relative = Path(
            os.path.relpath(ROOT / str(row["chart_path"]), output.parent)
        ).as_posix()
        state = "净盈利" if bool(row["net_profitable"]) else "净亏损"
        cards.append(
            "<article>"
            f"<a href='{html.escape(relative)}'><img loading='lazy' src='{html.escape(relative)}'></a>"
            f"<h2>#{int(row['display_order']):02d} · {state} · {html.escape(str(row['outcome']).upper())} · "
            f"{html.escape(str(row['symbol']))} · {html.escape(str(row['side']).upper())}</h2>"
            f"<p>{html.escape(str(row['available_at']))}<br>"
            f"毛收益 {float(row['realized_ret']):+.3%} · 扣成本 {float(row['net_ret']):+.3%}</p>"
            "</article>"
        )
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>L2 q90 31个独立信号</title>
<style>body{{margin:0;background:#111;color:#eee;font:15px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif}}
header{{position:sticky;top:0;z-index:2;background:#181818ee;padding:14px 20px;border-bottom:1px solid #444}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(620px,1fr));gap:14px;padding:14px}}
article{{background:#1c1c1c;border:1px solid #3c3c3c;border-radius:9px;overflow:hidden}}
img{{display:block;width:100%;height:auto;background:white}}h2{{font-size:16px;margin:10px 12px 3px}}
p{{margin:3px 12px 12px;color:#bbb}}</style></head><body>
<header><strong>L2 q90 独立信号：{int(summary["selected_independent_signals"])}个</strong> ·
净盈利 {int(summary["net_profitable"])} · 净亏损 {int(summary["net_unprofitable"])} ·
TP {int(summary["outcomes"]["tp"])} / SL {int(summary["outcomes"]["sl"])} / TIMEOUT {int(summary["outcomes"]["timeout"])} · 点击图片看高清原图</header>
<main>{"".join(cards)}</main></body></html>"""
    output.write_text(document, encoding="utf-8")


def render_gallery() -> dict[str, Any]:
    """Render all selected signals atomically into an ordinary folder."""

    require(
        not GALLERY_DIR.exists(), f"refusing to replace existing gallery: {GALLERY_DIR}"
    )
    rows, summary = load_selected_signals()
    global_prereg = read_json(GLOBAL_PREREG)
    frames = load_snapshot(global_prereg, out=GLOBAL_OUTPUT, results=GLOBAL_RESULTS)
    building = GALLERY_DIR.with_name(GALLERY_DIR.name + ".building")
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)
    manifest_rows: list[dict[str, Any]] = []
    try:
        for order, row in enumerate(rows.to_dict("records"), 1):
            image = render_signal_chart(row, frames[str(row["symbol"])], order)
            filename = chart_filename(row, order)
            path = building / "charts" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            require(
                cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]),
                f"write failed: {path}",
            )
            manifest_rows.append(
                {
                    "display_order": order,
                    "episode_id": str(row["episode_id"]),
                    "symbol": str(row["symbol"]),
                    "side": str(row["side"]),
                    "available_at": str(row["available_at"]),
                    "selected_arm": str(row["selected_arm"]),
                    "selected_score": float(row["selected_score"]),
                    "selected_percentile": float(row["selected_percentile"]),
                    "outcome": str(row["outcome"]),
                    "barrier_positive": bool(row["barrier_positive"]),
                    "net_profitable": bool(row["net_profitable"]),
                    "exit_offset": int(row["exit_offset"]),
                    "entry_price": float(row["entry_price"]),
                    "realized_ret": float(row["realized_ret"]),
                    "net_ret": float(row["net_ret"]),
                    "chart_path": repo_relative(GALLERY_DIR / "charts" / filename),
                    "chart_sha256": sha256_file(path),
                    "chart_pixel_sha256": pixel_sha256(image),
                    "future_bars_rendered": HORIZON_BARS,
                    "future_bars_used_as_features": 0,
                }
            )
        manifest = pd.DataFrame(manifest_rows)
        manifest_path = building / "signal_manifest.csv"
        manifest.to_csv(manifest_path, index=False)
        pages = build_overview_pages(manifest, building=building, final_dir=GALLERY_DIR)
        gallery_path = building / "gallery.html"
        build_gallery_html(manifest, summary, gallery_path)
        receipt = {
            "protocol": "15m_l2_feature_addition_selected_q90_outcome_gallery_v1",
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "feature_dataset_sha256": sha256_file(FEATURE_DATASET),
            "scored_final_sha256": sha256_file(SCORED_FINAL),
            "manifest_path": repo_relative(GALLERY_DIR / "signal_manifest.csv"),
            "manifest_sha256": sha256_file(manifest_path),
            "gallery_path": repo_relative(GALLERY_DIR / "gallery.html"),
            "gallery_sha256": sha256_file(gallery_path),
            "overview_pages": pages,
            "source_charts": len(manifest_rows),
            "exact_l1_input_pixel_checks": len(manifest_rows),
            "future_bars_used_as_features": 0,
            "review_future_physically_shaded": True,
            "network_reads": 0,
            "training_performed": False,
            "threshold_changed": False,
            "holdout_rows_read": 0,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "telegram_sent": False,
            "orders_placed": False,
        }
        write_json(building / "render_receipt.json", receipt)
        building.replace(GALLERY_DIR)
        return receipt
    except Exception:
        if building.exists():
            shutil.rmtree(building)
        raise


def verify_gallery() -> dict[str, Any]:
    """Re-render all 31 charts and validate every gallery/overview identity."""

    receipt_path = GALLERY_DIR / "render_receipt.json"
    require(receipt_path.is_file(), "render receipt missing")
    receipt = read_json(receipt_path)
    manifest_path = ROOT / str(receipt["manifest_path"])
    gallery_path = ROOT / str(receipt["gallery_path"])
    verify_file(manifest_path, str(receipt["manifest_sha256"]), "signal manifest")
    verify_file(gallery_path, str(receipt["gallery_sha256"]), "gallery HTML")
    for page in receipt["overview_pages"]:
        verify_file(ROOT / str(page["path"]), str(page["sha256"]), "overview page")
    selected, summary = load_selected_signals()
    by_id = {str(row["episode_id"]): row for row in selected.to_dict("records")}
    manifest = pd.read_csv(manifest_path)
    require(len(manifest) == EXPECTED_SELECTED, "manifest row count drifted")
    global_prereg = read_json(GLOBAL_PREREG)
    frames = load_snapshot(global_prereg, out=GLOBAL_OUTPUT, results=GLOBAL_RESULTS)
    failures: list[str] = []
    for record in manifest.to_dict("records"):
        episode_id = str(record["episode_id"])
        row = by_id.get(episode_id)
        if row is None:
            failures.append(f"missing source row: {episode_id}")
            continue
        path = ROOT / str(record["chart_path"])
        try:
            verify_file(path, str(record["chart_sha256"]), f"chart {episode_id}")
            expected = render_signal_chart(
                row, frames[str(row["symbol"])], int(record["display_order"])
            )
            require(
                pixel_sha256(expected) == str(record["chart_pixel_sha256"]),
                f"chart pixels drifted: {episode_id}",
            )
        except (
            SignalGalleryError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            cv2.error,
        ) as exc:  # report all failed identities together
            failures.append(str(exc))
    payload = {
        "protocol": str(receipt["protocol"]),
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "selected_rows_match_training_receipt": len(selected) == EXPECTED_SELECTED,
            "net_profitability_is_16_win_15_loss": summary["net_profitable"] == 16
            and summary["net_unprofitable"] == 15,
            "barrier_outcomes_are_13_tp_14_sl_4_timeout": summary["outcomes"]
            == {"sl": 14, "timeout": 4, "tp": 13},
            "three_timeouts_are_net_profitable": summary["positive_timeouts"] == 3,
            "all_chart_bytes_and_pixels_reproduced": not failures,
            "all_31_exact_l1_input_pixels_reproduced": not failures
            and len(manifest) == 31,
            "future_feature_rows": 0,
            "holdout_rows_read": 0,
        },
        "charts_checked": len(manifest),
        "failures": failures,
        "passed": not failures,
    }
    require(all(payload["checks"].values()), f"gallery verification failed: {payload}")
    write_json(GALLERY_DIR / "verify_receipt.json", payload)
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--render", action="store_true", help="render all 31 selected signals"
    )
    parser.add_argument(
        "--verify", action="store_true", help="re-render and verify the gallery"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require(args.render or args.verify, "choose --render and/or --verify")
    if args.render:
        receipt = render_gallery()
        print(
            json.dumps(receipt["summary"], ensure_ascii=False, indent=2, sort_keys=True)
        )
    if args.verify:
        result = verify_gallery()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Render a non-training 12-hour historical follow-up gallery for frozen MA events.

The caller supplies a committed plan and a frozen sample of representative positive
variants.  The renderer reads the recorded 15-minute OHLC source without changing
it, recomputes the six SMA/EMA lines from HL2 on the complete source prefix, and
shows the first 48 closed bars after the original five-bar confirmation.  This is
retrospective visual review only: the original first five post-core bars were used
when the source event was selected, so the gallery makes no predictive or trading
claim.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BAR_MS = 15 * 60 * 1000
MA_PERIODS = (20, 60, 120)
MA_COLUMNS = tuple(f"{kind}{period}" for period in MA_PERIODS for kind in ("sma", "ema"))
FONT_CANDIDATES = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
)


class Followup50Error(RuntimeError):
    """Raised when frozen review inputs cannot be reproduced safely."""


def sha256_file(path: Path) -> str:
    """Return the byte digest used to bind all rendered assets to their sources."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise Followup50Error(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise Followup50Error(f"JSON object required: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise Followup50Error(f"cannot read JSONL {path}: {error}") from error
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise Followup50Error(f"invalid JSONL {path}:{number}: {error}") from error
        if not isinstance(value, dict):
            raise Followup50Error(f"JSON object required: {path}:{number}")
        rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError as error:
        raise Followup50Error(f"path is outside repository: {path}") from error


def configure_chinese_font() -> None:
    """Use a local macOS Chinese font when it is available; never download one."""

    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            font_manager.fontManager.addfont(str(candidate))
            name = font_manager.FontProperties(fname=str(candidate)).get_name()
            plt.rcParams["font.family"] = [name, "sans-serif"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def ensure_committed(paths: Iterable[Path]) -> str:
    """Require the builder and frozen controls to be committed on the only branch."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise Followup50Error(f"review builder must run on main, found {branch!r}")
    rels = [relative(path) for path in paths]
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *rels], cwd=ROOT, text=True
    ).strip()
    if status:
        raise Followup50Error("commit builder and frozen plan inputs before generating:\n" + status)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if len(head) != 40:
        raise Followup50Error("cannot resolve current commit")
    return head


def parse_utc(value: object, *, field: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise Followup50Error(f"{field} must have an explicit timezone: {value!r}")
    return timestamp.tz_convert("UTC")


def with_hl2_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute the source dataset's causal HL2 SMA/EMA 20/60/120 contract."""

    output = frame.copy()
    hl2 = (output["high"] + output["low"]) / 2.0
    for period in MA_PERIODS:
        output[f"sma{period}"] = hl2.rolling(period).mean()
        output[f"ema{period}"] = hl2.ewm(span=period, adjust=False).mean()
    return output


def candle_body_polygons(
    xs: np.ndarray, openings: pd.Series, closes: pd.Series, minimum_height: float
) -> list[list[tuple[float, float]]]:
    """Build visible OHLC body polygons while preserving bearish open-to-close height."""

    bodies: list[list[tuple[float, float]]] = []
    for x, opening, close in zip(xs, openings, closes):
        bottom = min(float(opening), float(close))
        top = max(float(opening), float(close), bottom + minimum_height)
        bodies.append([(x - 0.34, bottom), (x + 0.34, bottom), (x + 0.34, top), (x - 0.34, top)])
    return bodies


def load_source(path: Path) -> pd.DataFrame:
    """Read one chronological source, rejecting malformed OHLC rather than repairing it."""

    required = ["ts", "open", "high", "low", "close", "volume", "open_time"]
    try:
        frame = pd.read_csv(path, usecols=required)
    except (OSError, ValueError) as error:
        raise Followup50Error(f"cannot read source {path}: {error}") from error
    if frame.empty:
        raise Followup50Error(f"empty source: {path}")
    for column in ("ts", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if not np.isfinite(frame[["ts", "open", "high", "low", "close", "volume"]].to_numpy()).all():
        raise Followup50Error(f"non-finite OHLC source: {path}")
    frame["ts"] = frame["ts"].astype("int64")
    try:
        open_times = pd.to_datetime(frame["open_time"], utc=True)
    except (TypeError, ValueError) as error:
        raise Followup50Error(f"invalid open_time values in {path}: {error}") from error
    if open_times.isna().any():
        raise Followup50Error(f"missing open_time values in {path}")
    expected_open = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    if not (open_times == expected_open).all():
        raise Followup50Error(f"ts/open_time mismatch in {path}")
    frame["open_time"] = open_times
    if frame["ts"].duplicated().any() or not frame["ts"].is_monotonic_increasing:
        raise Followup50Error(f"source is not unique chronological data: {path}")
    valid = (
        (frame["low"] > 0)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & (frame["volume"] >= 0)
    )
    if not valid.all():
        raise Followup50Error(f"invalid OHLC candle in {path}")
    return with_hl2_mas(frame)


def validate_plan(plan: Mapping[str, Any], plan_path: Path, selection_path: Path) -> None:
    """Check the fixed temporal and non-training contract before source access."""

    expected = {
        "selection_path": relative(selection_path),
        "sample_size": 50,
        "pre_confirmation_bars": 64,
        "future_bars": 48,
        "bar_minutes": 15,
        "image_width": 1920,
        "image_height": 1000,
        "moving_average_price_source": "hl2",
        "time_zone": "Asia/Shanghai",
        "non_training_review_only": True,
        "training_eligible": False,
        "production_eligible": False,
    }
    for field, required in expected.items():
        if plan.get(field) != required:
            raise Followup50Error(f"plan contract drift {field}: {plan.get(field)!r} != {required!r}")
    if plan.get("future_interval") != (
        "indexes core_end_i+6 through core_end_i+53,48 consecutive15m bars,ending exactly12h after t0"
    ):
        raise Followup50Error("plan future interval contract drift")
    if plan.get("t0_definition") != (
        "close of source_core_end_i+5; earliest completion of original selection confirmation, not recorded online detection"
    ):
        raise Followup50Error("plan T0 contract drift")
    if not plan_path.is_file() or not selection_path.is_file():
        raise Followup50Error("missing plan or selection")


def selected_source_rows(
    plan: Mapping[str, Any], selection: list[dict[str, Any]], source_manifest: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Bind each frozen selection record to exactly its original positive manifest row."""

    positives = [row for row in source_manifest if row.get("sample_kind") == "positive"]
    if len(positives) != 8000 or len({str(row.get("event_id")) for row in positives}) != 1043:
        raise Followup50Error("source positive population contract drift")
    by_sample = {str(row.get("dataset_sample_id")): row for row in positives}
    if len(by_sample) != len(positives):
        raise Followup50Error("source manifest positive dataset IDs are not unique")
    if len(selection) != int(plan["sample_size"]):
        raise Followup50Error("selection size differs from frozen plan")
    if len({str(row.get("event_id")) for row in selection}) != len(selection):
        raise Followup50Error("selection contains duplicate event IDs")
    required = (
        "dataset_sample_id", "event_id", "source_path", "source_core_start_i", "source_core_end_i",
        "core_start_time", "core_end_time", "direction", "split", "image_path", "image_sha256",
        "post_bars", "variant_id", "variant_index", "venue", "symbol",
    )
    result: list[dict[str, Any]] = []
    for position, selected in enumerate(selection, start=1):
        missing = [field for field in required if field not in selected]
        if missing:
            raise Followup50Error(f"selection row {position} missing {missing}")
        original = by_sample.get(str(selected["dataset_sample_id"]))
        if original is None:
            raise Followup50Error(f"selection row {position} is not an original positive")
        for field in required:
            if selected[field] != original.get(field):
                raise Followup50Error(f"selection row {position} drifts from manifest field {field}")
        if int(selected["post_bars"]) != 5:
            raise Followup50Error(f"selection row {position} is not the requested nearest post_bars=5 variant")
        if selected["direction"] not in {"LONG", "SHORT"}:
            raise Followup50Error(f"selection row {position} has invalid direction")
        result.append(dict(selected))
    return result


def window_for_event(frame: pd.DataFrame, row: Mapping[str, Any], *, pre_bars: int, future_bars: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return the exact confirmation-inclusive history and 48 subsequent closed candles."""

    core_start, core_end = int(row["source_core_start_i"]), int(row["source_core_end_i"])
    if core_start > core_end or core_start < 0 or core_end >= len(frame):
        raise Followup50Error(f"invalid core indexes for {row['event_id']}")
    if frame["open_time"].iloc[core_start] != parse_utc(row["core_start_time"], field="core_start_time"):
        raise Followup50Error(f"core start timestamp drift for {row['event_id']}")
    if frame["open_time"].iloc[core_end] != parse_utc(row["core_end_time"], field="core_end_time"):
        raise Followup50Error(f"core end timestamp drift for {row['event_id']}")
    confirmation_i = core_end + 5
    future_start_i, future_end_i = confirmation_i + 1, confirmation_i + future_bars
    history_start_i = confirmation_i - pre_bars + 1
    if history_start_i < 0 or future_end_i >= len(frame):
        raise Followup50Error(f"required history/future outside source for {row['event_id']}")
    visible = frame.iloc[history_start_i : future_end_i + 1].copy()
    expected = np.arange(history_start_i, future_end_i + 1)
    if len(visible) != pre_bars + future_bars or not np.array_equal(visible.index.to_numpy(), expected):
        raise Followup50Error(f"source index continuity drift for {row['event_id']}")
    if not np.all(np.diff(visible["ts"].to_numpy()) == BAR_MS):
        raise Followup50Error(f"15-minute source gap in visible interval for {row['event_id']}")
    if not np.isfinite(visible[["open", "high", "low", "close", *MA_COLUMNS]].to_numpy()).all():
        raise Followup50Error(f"non-finite visible OHLC/MA interval for {row['event_id']}")
    if len(frame.iloc[future_start_i : future_end_i + 1]) != future_bars:
        raise Followup50Error(f"future bar count drift for {row['event_id']}")
    visible.insert(0, "source_index", visible.index.astype(int))
    anchor = {
        "core_start_i": core_start,
        "core_end_i": core_end,
        "core_start_open_time_utc": frame["open_time"].iloc[core_start].isoformat(),
        "core_end_open_time_utc": frame["open_time"].iloc[core_end].isoformat(),
        "confirmation_i": confirmation_i,
        "confirmation_open_time_utc": frame["open_time"].iloc[confirmation_i].isoformat(),
        "t0_close_time_utc": (frame["open_time"].iloc[confirmation_i] + pd.Timedelta(minutes=15)).isoformat(),
        "future_start_i": future_start_i,
        "future_end_i": future_end_i,
        "future_end_close_time_utc": (frame["open_time"].iloc[future_end_i] + pd.Timedelta(minutes=15)).isoformat(),
        "visible_start_i": history_start_i,
        "visible_bars": len(visible),
        "pre_confirmation_bars_including_confirmation_candle": pre_bars,
        "future_bar_count": future_bars,
        "continuity_ms": BAR_MS,
        "confirmation_close": float(frame["close"].iloc[confirmation_i]),
        "future_end_close": float(frame["close"].iloc[future_end_i]),
    }
    if pd.Timestamp(anchor["future_end_close_time_utc"]) != pd.Timestamp(anchor["t0_close_time_utc"]) + pd.Timedelta(hours=12):
        raise Followup50Error(f"12-hour endpoint drift for {row['event_id']}")
    raw_percent = 100.0 * (anchor["future_end_close"] / anchor["confirmation_close"] - 1.0)
    anchor["raw_close_change_percent"] = raw_percent
    anchor["direction_aligned_close_change_percent"] = raw_percent if row["direction"] == "LONG" else -raw_percent
    return visible.reset_index(drop=True), anchor


def render_chart(
    visible: pd.DataFrame, row: Mapping[str, Any], anchor: Mapping[str, Any], path: Path
) -> None:
    """Render exactly 1920x1000 pixels with original core/confirmation semantics visible."""

    configure_chinese_font()
    fig, ax = plt.subplots(figsize=(19.2, 10), dpi=100)
    fig.subplots_adjust(left=0.045, right=0.92, bottom=0.12, top=0.84)
    xs = np.arange(len(visible))
    start_i = int(anchor["visible_start_i"])
    core_x0 = int(anchor["core_start_i"]) - start_i
    core_x1 = int(anchor["core_end_i"]) - start_i
    confirmation_x0 = core_x1 + 1
    confirmation_x1 = int(anchor["confirmation_i"]) - start_i
    boundary_x = confirmation_x1 + 0.5
    colors = np.where(visible["close"] >= visible["open"], "#2678e8", "#6b3cae")
    values = visible[["low", "high", *MA_COLUMNS]].to_numpy(dtype=float)
    low, high = float(np.min(values)), float(np.max(values))
    span = max(high - low, abs(high) * 0.001, 1e-12)
    ax.set_ylim(low - span * 0.08, high + span * 0.1)
    ax.set_xlim(-1, len(visible))
    ax.set_facecolor("#ffffff")
    ax.axvspan(core_x0 - 0.5, core_x1 + 0.5, color="#f04e45", alpha=0.11, zorder=0)
    ax.axvspan(confirmation_x0 - 0.5, confirmation_x1 + 0.5, color="#efb34c", alpha=0.16, zorder=0)
    wicks = [[(x, lo), (x, hi)] for x, lo, hi in zip(xs, visible["low"], visible["high"])]
    ax.add_collection(LineCollection(wicks, colors=colors, linewidths=1.0, zorder=3))
    bodies = candle_body_polygons(xs, visible["open"], visible["close"], span * 0.0008)
    ax.add_collection(PolyCollection(bodies, facecolors=colors, edgecolors=colors, linewidths=0.45, zorder=4))
    palette = {20: "#5b606b", 60: "#3576d2", 120: "#8b4cb8"}
    for period, color in palette.items():
        ax.plot(xs, visible[f"sma{period}"], color=color, lw=1.35, alpha=0.9, zorder=2)
        ax.plot(xs, visible[f"ema{period}"], color=color, lw=1.1, ls=(0, (4, 2)), alpha=0.72, zorder=2)
    ax.axvline(boundary_x, color="#1478d4", lw=1.6, ls=(0, (4, 3)), zorder=5)
    ax.text((core_x0 + core_x1) / 2, 0.02, "原始核心", transform=ax.get_xaxis_transform(),
            ha="center", va="bottom", fontsize=10, color="#9d302b")
    ax.text((confirmation_x0 + confirmation_x1) / 2, 0.075, "原筛选确认 5 根", transform=ax.get_xaxis_transform(),
            ha="center", va="bottom", fontsize=10, color="#9a6811")
    ax.text(boundary_x + 0.5, 0.98, "T0：最早确认收盘", transform=ax.get_xaxis_transform(),
            ha="left", va="top", fontsize=10, color="#1267b4")
    ax.text(len(visible) - 0.4, 0.98, "+12h 收盘", transform=ax.get_xaxis_transform(),
            ha="right", va="top", fontsize=10, color="#49586d")
    ax.grid(True, ls=(0, (1, 4)), color="#d6dae0", lw=0.85)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(axis="both", length=0, labelsize=10, labelcolor="#667080", pad=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ticks = np.unique(np.linspace(0, len(visible) - 1, 9).round().astype(int))
    local_times = pd.to_datetime(visible["open_time"], utc=True).dt.tz_convert("Asia/Shanghai")
    ax.set_xticks(ticks, [local_times.iloc[x].strftime("%m-%d\n%H:%M") for x in ticks])
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    direction_color = "#1967c9" if row["direction"] == "LONG" else "#763ca7"
    core_time = pd.Timestamp(anchor["core_start_open_time_utc"]).tz_convert("Asia/Shanghai")
    t0_local = pd.Timestamp(anchor["t0_close_time_utc"]).tz_convert("Asia/Shanghai")
    fig.text(0.045, 0.955, f"样本 #{int(row['sample_order']):02d}  ·  {row['event_id']}  ·  {row['symbol']}  ·  {row['direction']}",
             fontsize=18, weight="bold", color=direction_color)
    fig.text(0.045, 0.922,
             f"{row['venue']}  |  {row['split']}  |  核心起点 {core_time:%Y-%m-%d %H:%M} UTC+8  |  T0 {t0_local:%Y-%m-%d %H:%M} UTC+8",
             fontsize=10.5, color="#687487")
    fig.text(0.045, 0.885,
             "历史回看 · 原筛选用到核心后5根 · T0为最早确认时刻 · 非实时检测/交易回测",
             fontsize=11.5, color="#6a4f24")
    raw = float(anchor["raw_close_change_percent"])
    aligned = float(anchor["direction_aligned_close_change_percent"])
    fig.text(0.045, 0.055,
             f"T0→+12h 收盘原始变化 {raw:+.2f}%  |  按 {row['direction']} 方向的收盘变化 {aligned:+.2f}%  |  描述性价格移动，非收益/胜率",
             fontsize=10.5, color="#596579")
    legend = [
        Line2D([0], [0], color="#5b606b", lw=1.35, label="SMA20"), Line2D([0], [0], color="#5b606b", lw=1.1, ls=(0, (4, 2)), label="EMA20"),
        Line2D([0], [0], color="#3576d2", lw=1.35, label="SMA60"), Line2D([0], [0], color="#3576d2", lw=1.1, ls=(0, (4, 2)), label="EMA60"),
        Line2D([0], [0], color="#8b4cb8", lw=1.35, label="SMA120"), Line2D([0], [0], color="#8b4cb8", lw=1.1, ls=(0, (4, 2)), label="EMA120"),
    ]
    ax.legend(handles=legend, loc="upper left", fontsize=8.5, ncol=3, frameon=False)
    fig.savefig(path, dpi=100, facecolor="white")
    plt.close(fig)


def gallery_html(rows: list[dict[str, Any]]) -> str:
    """Return a self-contained local gallery; no CDN, labels, or training metadata."""

    cards = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>MA 启动事件 · 50 个历史后续图</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#172536;font:15px -apple-system,BlinkMacSystemFont,'STHeiti','Songti SC',sans-serif}header{padding:24px max(18px,4vw);background:#fff;border-bottom:1px solid #dde3eb}h1{font-size:24px;margin:0 0 8px}p{margin:6px 0;color:#5c6b7d;line-height:1.55}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:16px}button{font:inherit;padding:8px 13px;background:#fff;border:1px solid #cdd6e1;border-radius:7px;color:#263a51;cursor:pointer}button.active{background:#196be0;border-color:#196be0;color:#fff}main{max-width:1820px;margin:20px auto;padding:0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px}.card{background:#fff;border:1px solid #e0e5ec;border-radius:10px;overflow:hidden;box-shadow:0 2px 5px #162f4d0a}.chart{width:100%;display:block;cursor:zoom-in}.meta{padding:11px 12px 13px}.identity{font-weight:650}.tag{font-size:12px;border-radius:99px;padding:3px 7px;margin-left:6px;background:#e8eef8;color:#1e5dae}.SHORT{background:#f0e9f7;color:#6b3599}.details{font-size:12px;margin-top:7px;color:#637287}.original{display:inline-flex;gap:8px;align-items:center;margin-top:10px;color:#285d9b;text-decoration:none;font-size:13px}.original img{width:70px;height:45px;object-fit:cover;border:1px solid #d4dce7}.empty{padding:35px;color:#637287}.modal{position:fixed;inset:0;background:#0b1421d9;z-index:10;display:none;align-items:center;justify-content:center;padding:25px}.modal.open{display:flex}.modal img{max-width:96vw;max-height:92vh;box-shadow:0 8px 32px #000}.close{position:fixed;right:18px;top:14px;font-size:28px;color:#fff;background:none;border:0}.hint{font-size:13px;margin-top:15px}@media(max-width:520px){header{padding:18px}.grid{grid-template-columns:1fr}main{padding:0 11px}}</style>
<header><h1>均线启动事件：50 个真实历史后续图</h1><p>冻结的 1,043 个原始事件中按固定随机种子抽样。每张从原核心后第 5 根确认收盘（T0）起，展示后续连续 48 根 15 分钟 K 线至 +12 小时。</p><p>原事件筛选本身使用了核心后 5 根，故这里只作历史回看和视觉核对，不能据此推断预测能力或交易收益。</p><div class=\"toolbar\"><button class=\"active\" data-filter=\"ALL\">全部 <span id=\"count\"></span></button><button data-filter=\"LONG\">LONG</button><button data-filter=\"SHORT\">SHORT</button></div></header>
<main><div class=\"grid\" id=\"grid\"></div><p class=\"hint\">点击主图可放大；每张图附有原始干净训练图副本，仅用于比对，字节哈希在 manifest 中核验。</p></main><div class=\"modal\" id=\"modal\"><button class=\"close\" aria-label=\"关闭\">×</button><img id=\"large\"></div>
<script>const ROWS=__ROWS__,grid=document.getElementById('grid'),modal=document.getElementById('modal'),large=document.getElementById('large');let filter='ALL';const esc=s=>String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));function render(){let rows=ROWS.filter(r=>filter==='ALL'||r.direction===filter);document.getElementById('count').textContent=`(${rows.length})`;grid.innerHTML=rows.length?rows.map(r=>`<article class=\"card\"><img class=\"chart\" src=\"${esc(r.chart)}\" alt=\"${esc(r.event_id)} 后续图\" loading=\"lazy\" data-full=\"${esc(r.chart)}\"><div class=\"meta\"><div class=\"identity\">样本 #${String(r.sample_order).padStart(2,'0')} · ${esc(r.event_id)} <span class=\"tag ${esc(r.direction)}\">${esc(r.direction)}</span></div><div class=\"details\">${esc(r.symbol)} · ${esc(r.venue)} · ${esc(r.split)}<br>核心 ${esc(r.core_start_local)} UTC+8<br>T0→+12h 原始 ${Number(r.raw_close_change_percent).toFixed(2)}% · 方向 ${Number(r.direction_aligned_close_change_percent).toFixed(2)}%</div><a class=\"original\" href=\"${esc(r.original_image)}\" target=\"_blank\" rel=\"noopener\"><img src=\"${esc(r.original_image)}\" alt=\"${esc(r.event_id)} 原图缩略图\">展开原始干净图</a></div></article>`).join(''):'<div class=\"empty\">此方向没有图。</div>';document.querySelectorAll('.chart').forEach(i=>i.onclick=()=>{large.src=i.dataset.full;modal.classList.add('open')})}document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));render()});modal.onclick=e=>{if(e.target===modal||e.target.className==='close')modal.classList.remove('open')};document.addEventListener('keydown',e=>{if(e.key==='Escape')modal.classList.remove('open')});render();</script></html>""".replace("__ROWS__", cards)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="Committed frozen plan.json path relative to repository or absolute.")
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    plan = read_json(plan_path)
    selection_path = ROOT / str(plan.get("selection_path", ""))
    manifest_path = ROOT / str(plan.get("source_manifest", ""))
    validate_plan(plan, plan_path, selection_path)
    if sha256_file(selection_path) != str(plan.get("selection_sha256")):
        raise Followup50Error("frozen selection SHA mismatch")
    if sha256_file(manifest_path) != str(plan.get("source_manifest_sha256")):
        raise Followup50Error("source manifest SHA mismatch")
    output_dir = ROOT / str(plan.get("output_dir", ""))
    if output_dir.exists():
        raise Followup50Error(f"review output already exists; refusing to overwrite history: {output_dir}")
    source_rows = selected_source_rows(plan, read_jsonl(selection_path), read_jsonl(manifest_path))
    dataset_root = ROOT / str(plan.get("dataset_root", ""))
    required_commit_paths = (Path(__file__), plan_path, selection_path)
    builder_commit = ensure_committed(required_commit_paths)
    output_dir.mkdir(parents=True)
    (output_dir / "charts").mkdir()
    (output_dir / "originals").mkdir()
    (output_dir / "data").mkdir()
    manifest: list[dict[str, Any]] = []
    gallery_rows: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    source_frames: dict[str, pd.DataFrame] = {}
    try:
        for ordinal, row in enumerate(source_rows, start=1):
            source_rel = str(row["source_path"])
            source_path = ROOT / source_rel
            if not source_path.is_file():
                raise Followup50Error(f"source missing for {row['event_id']}: {source_rel}")
            if source_rel not in source_frames:
                source_hashes[source_rel] = sha256_file(source_path)
                source_frames[source_rel] = load_source(source_path)
            original_path = dataset_root / str(row["image_path"])
            if not original_path.is_file():
                raise Followup50Error(f"original image missing for {row['event_id']}: {original_path}")
            original_sha = sha256_file(original_path)
            if original_sha != str(row["image_sha256"]):
                raise Followup50Error(f"original image SHA mismatch for {row['event_id']}")
            visible, anchor = window_for_event(
                source_frames[source_rel], row, pre_bars=int(plan["pre_confirmation_bars"]), future_bars=int(plan["future_bars"])
            )
            stem = f"{ordinal:02d}_{row['event_id']}_{row['symbol']}_{row['direction']}"
            chart_rel = f"charts/{stem}.png"
            original_rel = f"originals/{stem}.png"
            data_rel = f"data/{stem}.csv"
            render_chart(visible, row, anchor, output_dir / chart_rel)
            shutil.copyfile(original_path, output_dir / original_rel)
            visible.to_csv(output_dir / data_rel, index=False)
            copied_sha = sha256_file(output_dir / original_rel)
            if copied_sha != original_sha:
                raise Followup50Error(f"copied original SHA mismatch for {row['event_id']}")
            entry = {
                "ordinal": ordinal, "sample_order": row["sample_order"],
                "event_id": row["event_id"], "dataset_sample_id": row["dataset_sample_id"], "variant_id": row["variant_id"],
                "variant_index": row["variant_index"], "post_bars": row["post_bars"], "direction": row["direction"],
                "symbol": row["symbol"], "venue": row["venue"], "split": row["split"], "source_path": source_rel,
                "source_sha256": source_hashes[source_rel], "source_manifest_image_path": row["image_path"],
                "original_image_sha256": original_sha, "original_image_copy": original_rel,
                "original_image_copy_sha256": copied_sha, "chart": chart_rel, "chart_sha256": sha256_file(output_dir / chart_rel),
                "visible_ohlc_ma_csv": data_rel, "visible_ohlc_ma_csv_sha256": sha256_file(output_dir / data_rel),
                "moving_average_price_source": "hl2", "moving_average_formula": "(high + low) / 2",
                "training_eligible": False, "production_eligible": False, **anchor,
            }
            manifest.append(entry)
            gallery_rows.append({
                "sample_order": entry["sample_order"], "event_id": entry["event_id"], "direction": entry["direction"], "symbol": entry["symbol"], "venue": entry["venue"],
                "split": entry["split"], "chart": chart_rel, "original_image": original_rel,
                "core_start_local": pd.Timestamp(entry["core_start_open_time_utc"]).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M"),
                "raw_close_change_percent": entry["raw_close_change_percent"],
                "direction_aligned_close_change_percent": entry["direction_aligned_close_change_percent"],
            })
            print(f"rendered {ordinal}/50 {row['event_id']}", flush=True)
        for source_rel, expected_sha in source_hashes.items():
            if sha256_file(ROOT / source_rel) != expected_sha:
                raise Followup50Error(f"source changed during render: {source_rel}")
        write_json(output_dir / "manifest.json", manifest)
        summary = {
            "experiment_id": plan["experiment_id"], "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "builder": relative(Path(__file__)), "builder_sha256": sha256_file(Path(__file__)), "builder_commit": builder_commit,
            "plan_path": relative(plan_path), "plan_sha256": sha256_file(plan_path), "selection_path": relative(selection_path),
            "selection_sha256": sha256_file(selection_path), "source_manifest": relative(manifest_path),
            "source_manifest_sha256": sha256_file(manifest_path), "events": len(manifest), "unique_events": len({x["event_id"] for x in manifest}),
            "directions": dict(sorted(Counter(x["direction"] for x in manifest).items())), "splits": dict(sorted(Counter(x["split"] for x in manifest).items())),
            "source_files": len(source_hashes), "pre_confirmation_bars_including_confirmation_candle": int(plan["pre_confirmation_bars"]),
            "future_bars": int(plan["future_bars"]), "bar_minutes": int(plan["bar_minutes"]), "endpoint": "T0 close + 12 hours",
            "moving_average_price_source": "hl2", "non_training_review_only": True, "training_eligible": False, "production_eligible": False,
            "manifest_sha256": sha256_file(output_dir / "manifest.json"),
        }
        write_json(output_dir / "summary.json", summary)
        (output_dir / "index.html").write_text(gallery_html(gallery_rows))
    except Exception:
        # A partial review directory is intentionally retained for forensic inspection but is never silently reused.
        raise
    print(output_dir / "index.html")


if __name__ == "__main__":
    main()

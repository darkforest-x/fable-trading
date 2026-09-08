"""Present saved follow-through labels and physically separate hindsight charts.

Only this experiment's hashed outcomes and contexts are read. No source OHLCV,
model inference, labels-to-features feedback or old image modification occurs.
All new figures are written beneath results/future_only. Each wide figure
shows the 100 bars preceding confirmation, the confirmation candle, and up to
72 later candles, bounded by its original cohort end. The later region is
explicitly hindsight, never a claimed model input. Six MAs, ATR and IMACD come
from the saved context, not a newly seeded rolling calculation.

Selection is frozen: four earliest complete-72 immediate confirmations by
timeframe/side, prioritizing the later cohort; two earliest later-cohort
complete-72 delayed confirmations, one per timeframe; and four earliest
immediate examples by timeframe and positive/nonpositive 24-bar net label.
The last category is explicitly outcome-selected, post-hoc illustration.
Candidates are deduplicated, with selection reasons preserved. Statistics use
all saved events, not just illustrated outcomes. This module places no orders.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-followthrough-20260908-v1"
DATA = ROOT / "data/imacd_yolo_followthrough_20260908_v1"
FUTURE = EXP / "results/future_only"
FOLD_ENDS = {"pre_holdout": pd.Timestamp("2026-05-04", tz="UTC"),
             "holdout_review": pd.Timestamp("2026-07-01", tz="UTC")}
FOLD_LABEL = {"pre_holdout": "1/1–5/4 · 已暴露", "holdout_review": "5/4–7/1 · 再次事后评估"}
MA_NAMES = ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")
MA_COLORS = ("#63a8a3", "#438d87", "#83a6cd", "#6888ba", "#9299a1", "#606a74")
MA_LABELS = ("20简单均线", "20指数均线", "60简单均线", "60指数均线", "120简单均线", "120指数均线")


def configure_chinese_font():
    """Use an installed CJK font; fail clearly rather than render missing glyphs."""
    candidates = ("/System/Library/Fonts/STHeiti Medium.ttc",
                  "/System/Library/Fonts/Hiragino Sans GB.ttc",
                  "/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    for candidate in candidates:
        if Path(candidate).is_file():
            font_manager.fontManager.addfont(candidate)
            name = font_manager.FontProperties(fname=candidate).get_name()
            plt.rcParams.update({"font.family": name, "axes.unicode_minus": False})
            return dict(path=candidate, family=name)
    raise RuntimeError("A local Chinese font is required for the follow-through charts")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            result.update(chunk)
    return result.hexdigest()


def number(value, digits=2):
    return f"{float(value):,.{digits}f}" if value is not None and pd.notna(value) else "N/A"


def pct_bp(value):
    return number(None if value is None else value/100) + "%"


def tf(minutes):
    return "1H" if int(minutes) == 60 else "4H"


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|"*len(headers),
                      *("| " + " | ".join(map(str, row)) + " |" for row in rows)])


def stats(frame):
    """Equal-event descriptive means; never compound overlapping observations."""
    complete = frame.loc[frame.complete.eq(True)]
    matched = complete.loc[complete.control_n.eq(3) & complete.matched_excess_bp.notna()]
    mean = lambda name, source=complete: float(source[name].mean()) if len(source) else None
    return dict(n=len(frame), complete=len(complete), censored=len(frame)-len(complete),
        gross_bp=mean("gross_bp"), net_bp=mean("net_bp"),
        positive_pct=100*complete.net_bp.gt(0).mean() if len(complete) else None,
        direction_pct=100*complete.gross_bp.gt(0).mean() if len(complete) else None,
        median_net_bp=float(complete.net_bp.median()) if len(complete) else None,
        matched_n=len(matched), matched_net_bp=mean("net_bp", matched),
        random_net_bp=mean("control_mean_net_bp", matched), excess_bp=mean("matched_excess_bp", matched),
        mfe_atr=mean("mfe_atr"), mae_atr=mean("mae_atr"),
        same_exit_imacd_gross_bp=mean("same_exit_imacd_gross_bp"))


def validate_outcomes(outcomes):
    required = {"event_id", "symbol", "timeframe_min", "fold", "side", "mode", "anchor_kind", "horizon",
        "anchor_i", "entry_i", "end_i", "entry_at", "exit_at", "complete", "observed_bars", "gross_bp", "net_bp",
        "mfe_bp", "mae_bp", "mfe_atr", "mae_atr", "control_n", "control_mean_net_bp", "matched_excess_bp",
        "same_exit_imacd_gross_bp", "signal_i", "confirmation_i", "signal_available_at", "confirmation_available_at"}
    if not required.issubset(outcomes.columns):
        raise ValueError(f"missing outcome columns: {sorted(required-set(outcomes.columns))}")
    if outcomes.duplicated(["event_id", "anchor_kind", "horizon"]).any():
        raise ValueError("duplicate event/anchor/horizon outcomes")
    if (not outcomes.timeframe_min.isin([60, 240]).all() or not outcomes.fold.isin(FOLD_ENDS).all()
            or not outcomes.anchor_kind.isin(["imacd", "confirmation"]).all()
            or not outcomes.horizon.isin([6, 24, 72]).all()
            or not outcomes["mode"].isin(["immediate", "delayed", "unconfirmed", "censored"]).all()
            or not outcomes.complete.isin([True, False]).all()):
        raise ValueError("unknown outcome group or completeness flag")
    incomplete = outcomes.loc[~outcomes.complete.eq(True)]
    if incomplete[["gross_bp", "net_bp", "mfe_bp", "mae_bp", "mfe_atr", "mae_atr"]].notna().any().any():
        raise ValueError("incomplete horizon has a populated future outcome")
    full = outcomes.loc[outcomes.complete.eq(True)]
    if (not np.isfinite(full[["gross_bp", "net_bp"]].to_numpy(float)).all()
            or not np.isclose(full.net_bp, full.gross_bp-20).all()):
        raise ValueError("static 20bp sensitivity does not match saved gross/net labels")
    if not full.entry_i.eq(full.anchor_i+1).all() or not full.end_i.eq(full.entry_i+full.horizon-1).all():
        raise ValueError("entry or holding horizon differs from declared next-open clock")
    for fold, end in FOLD_ENDS.items():
        dates = pd.to_datetime(full.loc[full.fold.eq(fold), "exit_at"], utc=True)
        if not dates.le(end).all():
            raise ValueError("completed outcome exits after its frozen cohort end")
    if not full.control_n.isin([0, 1, 2, 3]).all():
        raise ValueError("unexpected count in the frozen three-control contract")
    matched = full.loc[full.control_n.eq(3)]
    if not np.isclose(matched.net_bp-matched.control_mean_net_bp, matched.matched_excess_bp).all():
        raise ValueError("saved matched excess differs from same-event comparison")
    partial = full.loc[full.control_n.lt(3)]
    if partial[["control_mean_net_bp", "matched_excess_bp"]].notna().any().any():
        raise ValueError("fewer than three complete controls must not receive matched metrics")
    return dict(outcome_rows=len(outcomes), complete_rows=len(full), unique_event_anchor_horizon=True,
                incomplete_returns_absent=True, next_open_horizon_verified=True, static_20bp_verified=True,
                matched_excess_reconciled=True)


def select_cases(outcomes):
    """Select at most ten fixed roles; never replace a missing role with a winner."""
    long_window = outcomes.loc[outcomes.anchor_kind.eq("confirmation") & outcomes.horizon.eq(72)
                               & outcomes.complete.eq(True)].copy()
    long_window["signal_available_at"] = pd.to_datetime(long_window.signal_available_at, utc=True)
    long_window["fold_priority"] = long_window.fold.map({"holdout_review": 0, "pre_holdout": 1})
    immediate = long_window.loc[long_window["mode"].eq("immediate")]
    ordered = immediate.sort_values(["fold_priority", "signal_available_at", "event_id"])
    selected, roles, missing = {}, [], []

    def take(frame, role, posthoc=False):
        if frame.empty:
            missing.append(role)
            return
        row = frame.iloc[0]
        event_id = row.event_id
        if event_id not in selected:
            selected[event_id] = dict(event_id=event_id, reasons=[], selected_with_outcome=False)
        selected[event_id]["reasons"].append(role)
        selected[event_id]["selected_with_outcome"] |= posthoc
        roles.append(dict(role=role, event_id=event_id, posthoc=posthoc))

    for minutes in (60, 240):
        for side in (1, -1):
            take(ordered.loc[ordered.timeframe_min.eq(minutes) & ordered.side.eq(side)],
                 f"first_immediate_{minutes}_{'long' if side == 1 else 'short'}")
    delayed = long_window.loc[long_window["mode"].eq("delayed") & long_window.fold.eq("holdout_review")]
    delayed = delayed.sort_values(["signal_available_at", "event_id"])
    for minutes in (60, 240):
        take(delayed.loc[delayed.timeframe_min.eq(minutes)], f"first_later_delayed_{minutes}")
    labels = outcomes.loc[outcomes.anchor_kind.eq("confirmation") & outcomes.horizon.eq(24)
                          & outcomes.complete.eq(True), ["event_id", "net_bp"]]
    examples = immediate.merge(labels, on="event_id", suffixes=("_72", "_24"), validate="one_to_one")
    examples = examples.sort_values(["signal_available_at", "event_id"])
    for minutes in (60, 240):
        part = examples.loc[examples.timeframe_min.eq(minutes)]
        take(part.loc[part.net_bp_24.gt(0)], f"posthoc_24bar_positive_{minutes}", True)
        take(part.loc[part.net_bp_24.le(0)], f"posthoc_24bar_nonpositive_{minutes}", True)
    return list(selected.values()), roles, missing


def wide_chart(context, event, label24, path, selection):
    """Render saved MA/IMACD and observed core indices; change presentation only.

    The core time interval is not enlarged to the full setup. Its displayed
    price bounds are the saved candles' high/low, not a reconstructed YOLO
    pixel bounding box. The full window is e-100 through e+72, inclusive.
    """
    font = configure_chinese_font()
    minutes, p, e = int(event.timeframe_min), int(event.signal_i), int(event.confirmation_i)
    step, fold_end = pd.Timedelta(minutes=minutes), FOLD_ENDS[event.fold]
    if e != int(event.anchor_i) or int(event.end_i) != e+72:
        raise ValueError("selected 72-bar confirmation clock differs")
    start, end = e-100, e+72
    visible = context.loc[(context.index >= start) & (context.index <= end)].copy()
    if (len(visible) != end-start+1 or not visible.index.to_series().diff().iloc[1:].eq(1).all()
            or not visible.open_time.diff().iloc[1:].eq(step).all()
            or not (visible.open_time+step <= fold_end).all()):
        raise ValueError("wide chart must be continuous and bounded by its cohort")
    if (visible.loc[e, "open_time"]+step != pd.Timestamp(event.confirmation_available_at)
            or visible.loc[e+1, "open_time"] != pd.Timestamp(event.entry_at)
            or not np.isclose(visible.loc[e+1, "open"], event.entry_price)):
        raise ValueError("chart does not reproduce confirmation and next-open reference")
    core_start, core_end = int(event.core_start_i), int(event.core_end_i)
    if not start <= core_start <= core_end <= e:
        raise ValueError("saved detection core lies outside confirmation history")
    core = visible.loc[core_start:core_end]
    core_low, core_high = float(core.low.min()), float(core.high.max())
    xs = np.arange(len(visible))
    fig, (ax, osc) = plt.subplots(2, 1, figsize=(19, 8), sharex=True,
        gridspec_kw={"height_ratios": [3.3, 1]}, layout="constrained")
    fig.set_facecolor("#fafbfc")
    for axis in (ax, osc):
        axis.set_facecolor("#fafbfc")
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", color="#d6dee7", alpha=.5)
        axis.set_axisbelow(True)
        axis.axvspan(e-start+.5, end-start+.5, color="#dceafa", alpha=.8, zorder=0)
        axis.axvline(e-start+.5, color="#527aa4", linewidth=.8, linestyle=":")
    for i, candle in enumerate(visible.itertuples()):
        color = "#159b8d" if candle.close >= candle.open else "#d75768"
        ax.vlines(i, candle.low, candle.high, color=color, linewidth=.8)
        ax.add_patch(Rectangle((i-.33, min(candle.open, candle.close)), .66,
            max(abs(candle.close-candle.open), abs(candle.open)*.000005), color=color, linewidth=0))
    for name, color, label in zip(MA_NAMES, MA_COLORS, MA_LABELS):
        ax.plot(xs, visible[name], color=color, linewidth=.9, label=label)
    setup = max(start, int(event.setup_start_i))
    for axis in (ax, osc):
        axis.axvspan(setup-start-.5, p-start-.5, color="#d9a744", alpha=.11, zorder=0)
    ax.add_patch(Rectangle((core_start-start-.5, core_low), core_end-core_start+1,
        max(core_high-core_low, abs(core_low)*.000005), edgecolor="#965cb5", facecolor="#b991d1",
        alpha=.25, linewidth=1.5, zorder=3, label="YOLO检测核心区间"))
    ax.annotate("检测核心", xy=((core_start+core_end)/2-start, core_low), xytext=(0, -24),
        textcoords="offset points", ha="center", fontsize=9, color="#70408b",
        arrowprops={"arrowstyle": "-", "color": "#965cb5"},
        bbox={"boxstyle": "round,pad=.2", "fc": "white", "ec": "none", "alpha": .85})
    if p == e:
        ax.axvline(e-start, color="#008b80", linewidth=1.7)
        ax.text(e-start-1, .81, "IMACD箭头 + YOLO确认\n同根共振", transform=ax.get_xaxis_transform(),
            ha="right", va="top", fontsize=10, color="#006d65",
            bbox={"boxstyle": "round,pad=.35", "fc": "#e1f2ee", "ec": "none", "alpha": .95})
    else:
        ax.axvline(p-start, color="#c68b30", linewidth=1.5)
        ax.axvline(e-start, color="#008b80", linewidth=1.5, linestyle="--")
        ax.text(p-start-1, .81, "IMACD箭头", transform=ax.get_xaxis_transform(), ha="right", va="top",
            fontsize=10, color="#916018", bbox={"boxstyle": "round,pad=.3", "fc": "#fbefdb", "ec": "none"})
        ax.text(e-start+1, .71, "YOLO确认", transform=ax.get_xaxis_transform(), ha="left", va="top",
            fontsize=10, color="#006d65", bbox={"boxstyle": "round,pad=.3", "fc": "#e1f2ee", "ec": "none"})
    signal_price = float(visible.loc[p, "low" if int(event.side) == 1 else "high"])
    ax.scatter([p-start], [signal_price], marker="^" if int(event.side) == 1 else "v",
               s=65, color="#008b80" if int(event.side) == 1 else "#c94c67", zorder=5)
    entry_price = float(event.entry_price)
    ax.axhline(entry_price, color="#8793a3", linewidth=.8, linestyle=":")
    ax.scatter([e+1-start], [entry_price], s=22, color="#334f72", zorder=5)
    ax.annotate(f"确认次开盘参考价 {entry_price:,.7g}", xy=(e+1-start, entry_price), xytext=(14, 14),
        textcoords="offset points", fontsize=9, color="#334f72",
        bbox={"boxstyle": "round,pad=.25", "fc": "white", "ec": "none", "alpha": .9},
        arrowprops={"arrowstyle": "-", "color": "#8793a3"})
    for horizon, label, color in ((24, "24根观察终点", "#47648c"), (72, "72根展示终点", "#7588a3")):
        marker_x = e+horizon-start
        for axis in (ax, osc):
            axis.axvline(marker_x, color=color, linewidth=.95, linestyle="--", alpha=.9)
        ax.text(marker_x-1, .965, label, transform=ax.get_xaxis_transform(), ha="right", va="top",
            fontsize=9, color=color, bbox={"boxstyle": "round,pad=.2", "fc": "#fafbfc", "ec": "none", "alpha": .9})
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.7g}"))
    ax.legend(loc="upper left", ncol=4, fontsize=8, framealpha=.85)
    ax.text(.015, 1.015, "确认前历史 · 图像检测实际仅用最近18/19根",
            transform=ax.transAxes, fontsize=9, color="#526170")
    ax.text(.605, 1.015, "浅蓝区：事后走势，未进入本次检测",
            transform=ax.transAxes, fontsize=9, color="#24598c")
    osc.plot(xs, visible.md, color="#527ed0", linewidth=1.3, label="IMACD主线")
    osc.plot(xs, visible.sb, color="#d39a3d", linewidth=1.2, label="信号线")
    osc.axhline(0, color="#748291", linewidth=.85, label="零轴")
    osc.axvline(e-start, color="#008b80", linewidth=.9, linestyle="--")
    osc.legend(loc="upper left", fontsize=8)
    ticks = np.linspace(0, len(visible)-1, 9).astype(int)
    osc.set_xticks(ticks, [visible.open_time.iloc[i].strftime("%m-%d\n%H:%M") for i in ticks])
    osc.set_xlim(-1, len(visible)+1)
    osc.set_xlabel("UTC K线开盘时间 · 浅橙区为原蓄势时间段 · 原始模型输入未改动")
    side = "多头" if int(event.side) == 1 else "空头"
    mode_label = "即时共振" if event["mode"] == "immediate" else "延迟确认"
    badge = "按结果分类的事后示例" if selection["selected_with_outcome"] else "按时间首例，未挑收益"
    fig.suptitle(f"{event.symbol} / {tf(minutes)} / {side} / {mode_label} | {badge}\n"
        f"24根静态净位移 {pct_bp(label24.net_bp)}（毛位移减20bp，非实盘盈亏）"
        f" | 确认等待 {float(event.delay_bars)*minutes/60:g}小时", fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return dict(event_id=event.event_id, symbol=event.symbol, timeframe_min=minutes, side=int(event.side),
        fold=event.fold, mode=event["mode"], reasons=selection["reasons"],
        selected_with_outcome=selection["selected_with_outcome"],
        historical_bars_before_confirmation=e-start, future_bars=72,
        first_open_at=str(visible.open_time.iloc[0]), confirmation_open_at=str(visible.loc[e, "open_time"]),
        confirmation_available_at=str(event.confirmation_available_at), future_first_open_at=str(visible.loc[e+1, "open_time"]),
        future_last_close_at=str(visible.open_time.iloc[-1]+step), fold_end=str(fold_end),
        core_start_i=core_start, core_end_i=core_end, core_price_low=core_low, core_price_high=core_high,
        core_box_kind="saved_core_time_interval_with_candle_high_low_bounds",
        observation_24_close_at=str(visible.loc[e+24, "open_time"]+step),
        display_72_close_at=str(visible.loc[e+72, "open_time"]+step),
        reference_next_open_price=entry_price, chinese_font=font, presentation_only=True,
        path=str(path.relative_to(ROOT)), sha256=digest(path), training_eligible=False,
        is_original_model_input=False, future_visible_to_model=False)


def grouped_tables(outcomes, horizon):
    rows = []
    for fold in FOLD_ENDS:
        for minutes in (60, 240):
            base = outcomes.loc[outcomes.fold.eq(fold) & outcomes.timeframe_min.eq(minutes) & outcomes.horizon.eq(horizon)]
            groups = [("原箭头全体", "p+1", base.loc[base.anchor_kind.eq("imacd")])]
            for mode, label in (("immediate", "即时共振"), ("delayed", "最终延迟确认（事后分类）"),
                                ("unconfirmed", "最终未确认（事后分类）"), ("censored", "确认随访截断")):
                groups.append((label, "p+1", base.loc[base.anchor_kind.eq("imacd") & base["mode"].eq(mode)]))
            for mode, label in (("immediate", "即时共振实际锚点"), ("delayed", "延迟确认实际锚点")):
                groups.append((label, "e+1", base.loc[base.anchor_kind.eq("confirmation") & base["mode"].eq(mode)]))
            for label, anchor, frame in groups:
                s = stats(frame)
                rows.append([FOLD_LABEL[fold], tf(minutes), label, anchor, f"{s['complete']}/{s['n']}",
                    pct_bp(s["gross_bp"]), pct_bp(s["net_bp"]), pct_bp(s["median_net_bp"]),
                    number(s["positive_pct"])+"%", s["matched_n"], pct_bp(s["random_net_bp"]), pct_bp(s["excess_bp"])])
    return table(["时段", "周期", "分组", "起点", "完整/全部", "平均毛位移", "平均静态净位移",
                  "中位静态净位移", "净正比例", "匹配事件", "随机净位移", "匹配超额"], rows)


def run():
    """Read frozen labels/contexts, then create only new hindsight artifacts."""
    source = str(Path(__file__).relative_to(ROOT))
    subprocess.run(["git", "ls-files", "--error-unmatch", source], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    if subprocess.check_output(["git", "status", "--porcelain", "--", source], cwd=ROOT, text=True).strip():
        raise ValueError("commit report source before reading follow-through outcomes")
    summary_path = EXP / "results/summary.json"
    summary = json.loads(summary_path.read_text())
    for relative, expected in summary["files"].items():
        if digest(ROOT / relative) != expected:
            raise ValueError(f"saved follow-through artifact changed: {relative}")
    outcomes = pd.read_csv(DATA / "outcomes.csv")
    validation = validate_outcomes(outcomes)
    selected, roles, missing = select_cases(outcomes)
    FUTURE.mkdir(parents=True, exist_ok=True)
    cache, figures = {}, []
    full72 = outcomes.loc[outcomes.anchor_kind.eq("confirmation") & outcomes.horizon.eq(72)].set_index("event_id")
    labels24 = outcomes.loc[outcomes.anchor_kind.eq("confirmation") & outcomes.horizon.eq(24)].set_index("event_id")
    for selection in selected:
        event = full72.loc[selection["event_id"]]
        event = event.copy()
        event["event_id"] = selection["event_id"]
        key = f"{event.symbol}_{int(event.timeframe_min)}"
        if key not in cache:
            path = DATA / "contexts" / f"{key}.csv.gz"
            relative = str(path.relative_to(ROOT))
            if relative not in summary["files"] or digest(path) != summary["files"][relative]:
                raise ValueError("selected context is not covered by a frozen SHA")
            context = pd.read_csv(path)
            context["open_time"] = pd.to_datetime(context.open_time, utc=True)
            cache[key] = context.set_index("bar_i", verify_integrity=True).sort_index()
        target = FUTURE / f"{selection['event_id']}_hindsight_72.png"
        figures.append(wide_chart(cache[key], event, labels24.loc[selection["event_id"]], target, selection))

    headline, same_exit, anchor_controls = [], [], []
    for minutes in (60, 240):
        post = outcomes.loc[outcomes.fold.eq("holdout_review") & outcomes.timeframe_min.eq(minutes) & outcomes.horizon.eq(24)]
        for label, anchor, mode in (("原箭头全体", "imacd", None), ("即时共振", "confirmation", "immediate"),
                                    ("延迟确认", "confirmation", "delayed")):
            frame = post.loc[post.anchor_kind.eq(anchor)]
            if mode:
                frame = frame.loc[frame["mode"].eq(mode)]
            s = stats(frame)
            headline.append([tf(minutes), f"{24*minutes/60:g}h", label, "p+1" if anchor == "imacd" else "e+1",
                f"{s['complete']}/{s['n']}", pct_bp(s["net_bp"]), number(s["direction_pct"])+"%",
                s["matched_n"], pct_bp(s["random_net_bp"]), pct_bp(s["excess_bp"])])
        for anchor in ("imacd", "confirmation"):
            s = stats(post.loc[post.anchor_kind.eq(anchor) & post["mode"].eq("immediate")])
            anchor_controls.append([tf(minutes), anchor, s["complete"], s["matched_n"],
                pct_bp(s["matched_net_bp"]), pct_bp(s["random_net_bp"]), pct_bp(s["excess_bp"])])
        for fold in FOLD_ENDS:
            frame = outcomes.loc[outcomes.fold.eq(fold) & outcomes.timeframe_min.eq(minutes) & outcomes.horizon.eq(24)
                                 & outcomes.anchor_kind.eq("confirmation") & outcomes["mode"].eq("delayed")]
            s = stats(frame)
            same_exit.append([FOLD_LABEL[fold], tf(minutes), s["complete"], pct_bp(s["same_exit_imacd_gross_bp"]),
                              pct_bp(s["gross_bp"])])
    reviews_path = EXP / "results/independent_review.json"
    reviews = json.loads(reviews_path.read_text()) if reviews_path.exists() else {"status": "not yet available"}
    review_fields = {key: reviews[key] for key in ("n_checks", "all_passed", "failed_checks", "status") if key in reviews}
    primary = [row for row in summary["summary_rows"] if row["fold"] == "holdout_review"
               and row["anchor_kind"] == "confirmation" and row["horizon"] == 24]
    primary = sorted(primary, key=lambda row: (row["timeframe_min"], row["mode"] != "immediate"))
    primary_rows = [[tf(row["timeframe_min"]), row["mode"], row["matched"], pct_bp(row["excess_mean_bp"]),
                     row["blocks"], number(row["p"], 6), number(row["p_holm"], 6)] for row in primary]
    path_rows = [[tf(row["timeframe_min"]), row["mode"], row["complete"], pct_bp(row["mfe_median_bp"]),
                 pct_bp(row["mae_median_bp"]), number(row["mfe_median_atr"]), number(row["mae_median_atr"])] for row in primary]
    negative = len(primary) == 4 and all(row["excess_mean_bp"] is not None and row["excess_mean_bp"] < 0 for row in primary)
    primary_interpretation = ("四组预注册confirmation主比较的匹配超额均为负，当前结果没有支持追加YOLO改善这个固定持有期的表现。"
        "即使某组原始平均净位移为正，也不能据此称成功；必须连同相同匹配条件下的随机对照、正向比例和样本数解释。"
        if negative else "按四组预注册confirmation主比较解释，不根据哪个锚点或子表更有利来改变主结论。")
    coverage = pd.DataFrame(summary.get("coverage", []))
    coverage_digest = {"groups": len(coverage)}
    if len(coverage):
        coverage_digest.update(symbols=int(coverage.symbol.nunique()),
            events_by_period=coverage.groupby(["fold", "timeframe_min"], as_index=False).events.sum().to_dict("records"))
    lines = [f"# IMACD + YOLO 后续走势：24根统计与完整事后图\n\n"
        "这次把模型确认之后的走势展开，而不是把历史输入截在确认点。主比较固定24根：1H是24小时，4H是96小时；"
        "另列6根与72根敏感度。全部统计来自冻结事件全集，不根据展示图挑样本。\n\n"
        "**先看后段（5/4–7/1）的全样本：** 净位移只是方向性持有位移减去固定20bp的静态成本敏感度，"
        "不是含资金费、滑点、保证金和重叠仓位的完整实盘PnL。\n\n",
        table(["周期", "持有时长", "分组", "实际计时起点", "完整/全部", "平均静态净位移", "同向收盘比例",
               "匹配事件", "随机净位移", "匹配超额"], headline),
        "\n\n原箭头全体从p+1开盘计；即时共振p=e，确认组同样从真实e+1开盘计。延迟确认只能从真正e+1开始，"
        "不能把原p+1价冒充模型确认后的入场价。随机与匹配超额只在有匹配的同一事件子集上平均，"
        "不能把全样本净均值直接减去不同分母的随机均值。这里必须有3条完整随机对照才记匹配；不足3条不补抽、不填超额。\n\n"
        f"**{primary_interpretation}** 同向收盘比例指固定时长结束时方向性毛位移为正，不是人工启动准确率。\n\n"
        "主统计检验只有后段24根、confirmation锚点的1H/4H×即时/延迟四组。以下是主程序保存的ISO周整块符号检验与Holm4校正，"
        "报告不重跑或挑选p值；相邻周的持有窗口仍可能重叠，因此是探索性证据，不是随机试验或实盘获利认证。\n\n",
        table(["周期", "确认组", "匹配事件", "匹配超额", "ISO周块数", "单侧p", "Holm4 p"], primary_rows),
        "\n\n## 同钟即时事件：随机控制抽样的敏感性\n\n"
        "即时共振p=e，两种锚点的实际行情时钟相同，但冻结随机种子包含anchor_kind，imacd与confirmation各自独立抽3条控制。"
        "因此相同事件的对照均值与匹配覆盖可能不同，小池可以出现超额符号翻转。预注册主表是confirmation，"
        "不能挑imacd副表的有利数字，也不能把相同时间下的差异解释成等待效应。\n\n",
        table(["周期", "锚点标签", "完整事件", "匹配事件", "匹配事件净位移", "随机净位移", "匹配超额"], anchor_controls),
        "\n\n## 持有途中曾经走多远\n\n"
        "MFE/MAE均为完整24根窗口的事后价格路径标签；MAE为非负不利幅度，不是组合最大回撤，不能推断止损触发先后或实际可获得利润。\n\n",
        table(["周期", "确认组", "完整事件", "有利偏移中位", "不利偏移中位", "有利偏移中位ATR", "不利偏移中位ATR"], path_rows),
        "\n\n"
        "## 如何看下面的宽图\n\n"
        "每图显示确认前100根历史、确认根，以及确认后的72根。浅蓝区全部是事后观察，模型当时看不到；"
        "左侧100根是已发生的历史上下文，也不声称全部曾送入YOLO。历史与未来区使用同一真实价格坐标、"
        "六根close均线和IMACD双线/零轴。所有新图只写入future_only目录，原始模型输入保持不变，禁止作训练输入。\n\n"
        "紫色框只标保存的YOLO检测核心K线区间，不扩大成整段蓄势；框的上下界取这几根K线的高低，"
        "不是重新绘制原像素检测框。图内中文标出IMACD箭头、YOLO确认或同根共振、确认次开盘参考价，"
        "以及确认后24根观察终点与72根展示终点。这些是展示说明，没有改变指标、检测核心或确认规则。\n\n"
        "前四个选例角色是1H/4H×多/空的最早即时共振，优先后段且要求72根完整；其次每周期最早后段延迟确认。"
        "最后四个角色按24根静态净位移正/非正取最早即时例，属于明确的事后结果选图。相同事件去重，"
        "不会为凑图或让结果漂亮而换成后面的赢家。\n\n"]
    lookup = {item["event_id"]: item for item in figures}
    for sequence, selection in enumerate(selected, 1):
        item = lookup[selection["event_id"]]
        label = labels24.loc[selection["event_id"]]
        classification = "含按24根结果挑选的事后示例" if item["selected_with_outcome"] else "仅按时间选例，未按收益挑选"
        lines.append(f"### {sequence}. {item['symbol']} · {tf(item['timeframe_min'])} · {'多' if item['side']==1 else '空'}"
            f" · {'即时' if item['mode']=='immediate' else '延迟'}\n\n"
            f"**{classification}。** 选例角色：`{', '.join(item['reasons'])}`。"
            f"24根静态净位移{pct_bp(label.net_bp)}，该数字是事后结果。\n\n"
            f"![历史与事后未来严格区分的完整走势](../{item['path']})\n\n"
            f"[打开原尺寸宽图]({ROOT / item['path']})。模型确认UTC：{item['confirmation_available_at']}；"
            f"未来区开始UTC：{item['future_first_open_at']}；最后未来K线收盘UTC：{item['future_last_close_at']}。"
            f"实际前文{item['historical_bars_before_confirmation']}根，未来{item['future_bars']}根；"
            "未来区不曾进入原模型识别。\n\n")
    lines += [f"缺少的固定选例角色：`{missing}`。去重后实际{len(figures)}幅图；缺组不以其他类别替代。\n\n"
        "## 24根：原箭头全体及最终状态分层\n\n"
        "下表p+1的“最终延迟确认/最终未确认”分层在p时不可知，是事后解释用途，不能当成可执行策略；"
        "真正可用的确认结果仍看e+1锚点。即时组在p收盘已知，因为p=e。完整/全部明确保留各时长的截断分母；"
        "未满完整持有期不填收益数字。\n\n", grouped_tables(outcomes, 24),
        "\n\n## 延迟确认：同事件、同退出时点的事后对照\n\n"
        "以下并列真实e+1开盘锚点与同事件原p+1开盘至同一退出点的毛位移。原p+1对照在当时不知道以后会确认，"
        "只能用于解释等待期间已经发生的价格变化，不能称提前识别策略。\n\n",
        table(["时段", "周期", "完整延迟事件", "原p+1至同退出点毛位移", "真实e+1至退出点毛位移"], same_exit),
        "\n\n## 6根敏感度\n\n", grouped_tables(outcomes, 6),
        "\n\n## 72根敏感度\n\n", grouped_tables(outcomes, 72),
        "\n\n## 规则、覆盖与验证\n\n"
        "本报告重新核对保存产物SHA、事件/锚点/时长唯一性、下一开盘和退出时钟、截断不填收益、20bp静态差值与匹配超额。"
        "完整匹配规则与覆盖沿用冻结主程序，以下原样引用，不由报告按结果改变。\n\n"
        f"```json\n{json.dumps({'rules':summary.get('rules',{}),'coverage':coverage_digest,'report_checks':validation,'independent_review':review_fields}, ensure_ascii=False, indent=2)}\n```\n\n"
        f"[完整独立复核]({reviews_path})；来源commit `{summary['source_commit']}`。"
        "来源输入SHA由summary.source_inputs和files保留。原计算出现NumPy matmul运行警告，保存数值有限本身不能证明计算正确；"
        "周符号检验的独立标量复算结果以审核回执为准，没有通过审核前不把p值当已核准证据。"
        "报告不重复YOLO，也不修改原输入图或标签。\n\n"
        "## 风险与诚实声明\n\n"
        f"- 这批历史此前已暴露，前段与模型验证时段重合，不能称全新盲测；当前Owner明确授权后续走势核对。"
        f"本次1H第{summary.get('holdout_consumptions',{}).get('60','未记录')}次、4H第{summary.get('holdout_consumptions',{}).get('240','未记录')}次holdout消耗。\n"
        "- 事件均值、净正比例与匹配超额不等于组合复利收益；持有窗口重叠，不建立真实仓位或资金曲线。\n"
        "- 20bp是固定往返扣除的敏感度，不包含真实资金费、成交滑点、交易所保证金与订单执行。没有新TP/SL。\n"
        "- 净正只是该固定时长的事后方向位移标签，不等于人工认定的真正启动；模型conf不是收益概率。\n"
        "- val AUC：N/A，本轮没有人工真假启动金标或新增分类评估；模型得分top-decile毛/净收益：N/A，"
        "本轮未冻结得分排序分组，不能事后另选高分池。单一基线为原IMACD箭头全体，已与确认组同表列出。\n"
        "- 延迟和最终未确认分层有事后信息，不能在原箭头时回填状态；统计与图中都明确保留真实确认时钟。\n"
        "- future_only中的所有图片含模型当时不可见的未来，只能用于审核/解释，training_eligible=false、production_eligible=false。\n"
        "- TradingView、spike、TG/Bark与实盘配置保持原口径；本报告不改变通知门或执行配置。\n\n"
        "## 下一步选项\n\n"
        "本轮核对的是确认后的真实价格路径，人工定义的真假启动仍没有金标。Owner可以先看完整宽图，"
        "给出符合与不符合目标形态的反馈；若进一步比较YOLO这道门的价值，应先冻结共享或更稳定的随机控制、"
        "真实退出规则，再独立评估。本报告不直接改阈值、TP/SL、成本或上线。\n\n"
        "## 复现\n\n"
        "以下是从零生成的顺序。主程序拒绝覆盖已存在产物；如果已有outcomes/contexts与summary，"
        "从独立verify那一行开始，不重复运行主程序。报告读取保存产物，写完Markdown即转换HTML；"
        "最后一行是可独立重做的HTML转换命令。\n\n"
        "```bash\ncd /Users/zhangzc/fable-trading\n"
        ".venv/bin/python -m yoyo.evaluation.imacd_yolo_followthrough\n"
        ".venv/bin/python -m yoyo.evaluation.imacd_yolo_followthrough_verify\n"
        ".venv/bin/python -m yoyo.evaluation.imacd_yolo_followthrough_report\n"
        ".venv/bin/python scripts/md_to_html.py analysis/p1_imacd_yolo_followthrough_20260908.md --out-dir analysis/html\n```\n"]
    report = ROOT / "analysis/p1_imacd_yolo_followthrough_20260908.md"
    report.write_text("".join(lines))
    subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/md_to_html.py", str(report),
                    "--out-dir", "analysis/html"], cwd=ROOT, check=True)
    html = ROOT / "analysis/html" / report.with_suffix(".html").name
    receipt = dict(summary_sha256=digest(summary_path), source_sha256=digest(Path(__file__)),
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        report_sha256=digest(report), html_sha256=digest(html), validation=validation, selection_roles=roles, missing_roles=missing,
        figures=figures, future_only=True, model_inference_runs=0, training_eligible=False, production_eligible=False)
    (FUTURE / "report_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(ROOT / "analysis/html" / report.with_suffix(".html").name)


if __name__ == "__main__":
    run()

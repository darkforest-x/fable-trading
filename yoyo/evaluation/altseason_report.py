"""Render the frozen multi-venue altcoin study without rerunning its strategy.

The caller first commits this builder and the experiment plan, then supplies
the saved summary/event/portfolio/calendar/coverage ledgers. This module never
downloads candles, computes entry features, simulates trades, selects model
parameters, sends notifications or changes live state. Pickled feature frames
are read only for explicitly retrospective gallery images. Future highs label
the gallery and calendar events; they never enter the supplied trading ledger.

All figures retain the blue IMACD main line, orange signal line and zero axis,
with no histogram. Gallery selection is fixed: up to five distinct-asset best
naturally exited focus cases, three worst focus cases, the best and worst
dense/pullback examples, and three complete calendar doubling events with no
1H focus position intersecting that event's start-to-peak interval. This is an
outcome-selected explanation gallery, explicitly not an unbiased success rate.

Source contract: experiments/active/exp-altseason-multivenue-20260910-v1/
PROJECT_PLAN.md. Unknown optional evidence is labelled unavailable, never zero.
Core accounting and chart timestamp fields are checked before publication.
Markdown is immediately converted by the repository's existing HTML builder,
which embeds local PNGs for a standalone report. No network dependencies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BUILDER = Path(__file__).resolve()
EXPERIMENT = ROOT / "experiments/active/exp-altseason-multivenue-20260910-v1"
DEFAULT_RESULTS = EXPERIMENT / "results"
DEFAULT_REPORT = ROOT / "analysis/p1_altseason_multivenue_20260910.md"
ARMS = ("focus_sma60", "focus_md", "focus_sma20", "focus_3r", "dense_sma60",
        "pullback_sma60", "young_breakout_sma20")
MAIN_ARMS = ("focus_sma60", "dense_sma60", "pullback_sma60")
ARM_LABELS = {
    "focus_sma60": "A 蓄势释放 · SMA60保护", "focus_md": "A 蓄势释放 · 主线回零",
    "focus_sma20": "A 蓄势释放 · SMA20保护", "focus_3r": "A 蓄势释放 · 固定3R",
    "dense_sma60": "B 密集突破 · SMA60保护", "pullback_sma60": "C 回踩再启动 · SMA60保护",
    "young_breakout_sma20": "D 短历史突破 · SMA20保护",
}
PERIOD_LABELS = {"full": "全部61天", "first31": "前31天 · 07/10—08/10", "last30": "后30天 · 08/10—09/09"}
SCOPE_LABELS = {"combined": "跨所 · 同资产互斥", "okx": "OKX", "binance": "Binance", "gate": "Gate"}
FEATURE_LABELS = {
    "relative_volume": "量比", "tr_expansion": "振幅扩张", "close_location": "收盘在K线位置",
    "body_fraction": "实体比例", "momentum10": "动能1.0", "higher_permission": "高周期许可",
    "prior24h_return": "此前24H涨幅", "six_ma_width_atr": "六线宽度/ATR",
    "atr_pct": "初始波动/价格", "near_zero_bars": "蓄势根数",
    "regime_breadth": "全市场站上SMA60比例",
}
LABELS = {
    "scope": "市场", "period": "时段", "minutes": "周期", "arm": "规则", "events": "候选数",
    "return_pct": "组合净收益%", "max_drawdown_pct": "收盘最大回撤%", "trades": "成交笔数",
    "win_rate": "胜率%", "profit_factor": "PF", "mean_net_bp": "候选平均净bp",
    "mean_net_r": "平均净R", "mean_control_bp": "匹配随机净bp", "mean_excess_bp": "匹配超额bp",
    "permutation_p": "置换p", "holm_p": "Holm p", "top1_positive_profit_share": "最大赢家/正利润%",
    "asset_balanced_excess_bp": "资产均衡超额bp", "permutation_assets": "置换资产数",
    "top5_positive_profit_share": "前5赢家/正利润%", "return_minus_top1_contribution_pct": "扣最大赢家贡献后%",
    "return_minus_top5_contribution_pct": "扣前5赢家贡献后%", "boundary_marks": "段末盯市笔数",
    "peak_positions": "最多同时资产", "feature": "入场时特征", "bucket": "事先分桶", "n": "样本数",
    "mean_mfe_pct": "平均最高浮盈%", "venue": "交易所", "symbol": "合约", "asset": "底层资产",
    "instrument": "行情片段", "raw": "原始数", "eligible": "可评估数", "segments": "连续片段数",
    "warmup": "预热", "gaps": "缺口", "reason": "状态/原因", "net_pct": "净价格收益%",
    "mfe_pct": "持有期最高浮盈%", "risk_pct": "初始风险%", "net_r": "净R",
    "capture_pct": "毛收益/最高浮盈%", "giveback_pct": "最高价至退出回落%", "entry_time": "入场UTC",
    "exit_time": "退出UTC/上界", "exit_reason": "退出原因", "kind": "日/周", "window_start": "窗口开始UTC",
    "window_end": "窗口结束UTC", "close_pct": "收盘涨幅%", "peak_pct": "最高影线涨幅%",
    "coverage_hours": "完整小时", "contracts": "合约数", "assets": "资产数", "signals": "候选数",
    "new_assets": "OKX目录外资产数", "positive_assets": "有自然退出盈利的新增资产",
    "fifty_assets": "自然退出净涨≥50%的新增资产", "mean_net_40bp": "固定40bp净bp",
    "mean_net_60bp": "固定60bp净bp", "mean_net_turnover_bp": "按进出名义收费净bp",
    "random_return_pct_mean": "匹配随机组合均值%", "random_schedules": "随机排程数",
    "actual_matching_fraction": "有匹配候选比例%", "top_n": "前10%样本数", "descriptive_auc": "描述性AUC",
    "top_decile_gross_bp": "前10%毛bp", "top_decile_net_bp": "前10%净bp",
    "top_decile_win_rate": "前10%胜率%", "top_decile_excess_bp": "前10%匹配超额bp",
    "result": "路径状态", "opportunity_count": "日历标签数",
    "frozen_base_return_pct": "原路径基准贡献%", "frozen_40bp_return_pct": "固定40bp贡献%",
    "frozen_60bp_return_pct": "固定60bp贡献%", "frozen_actual_turnover_return_pct": "按进出名义收费贡献%",
    "funding_known_trades": "有已观测资金费的成交", "funding_known_fraction": "已观测成交占比%",
    "observed_funding_cost_pct": "已观测资金费成本%", "known_cohort_turnover_return_pct": "可观测子集费用后贡献%",
    "known_cohort_after_observed_funding_pct": "同子集再扣已观测资金费%", "funding_unknown_trades": "资金费未知成交",
    "proxy_settlements": "使用价格代理的结算", "ambiguous_settlements": "时序不明结算",
    "uncertain_funding_range_pct": "不确定资金费区间%",
}
PERCENT_FRACTIONS = {"win_rate", "top1_positive_profit_share", "top5_positive_profit_share",
                     "actual_matching_fraction", "top_decile_win_rate", "funding_known_fraction"}
INTEGER_COLUMNS = {"events", "trades", "boundary_marks", "peak_positions", "n", "contracts", "assets",
                   "signals", "new_assets", "positive_assets", "fifty_assets", "coverage_hours", "top_n",
                   "random_schedules", "opportunity_count", "funding_known_trades", "funding_unknown_trades",
                   "proxy_settlements", "ambiguous_settlements"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _committed() -> str:
    """Rendering is disallowed until the exact source and plan are committed."""
    for path in (BUILDER, EXPERIMENT / "PROJECT_PLAN.md"):
        relative = path.relative_to(ROOT).as_posix()
        saved = subprocess.check_output(["git", "show", "HEAD:"+relative], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError("Commit exact report source and plan before rendering: "+relative)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _bool(series: pd.Series, name: str) -> pd.Series:
    mapping = {True: True, False: False, "True": True, "False": False,
               "true": True, "false": False, 1: True, 0: False}
    values = series.map(mapping)
    if values.isna().any():
        raise ValueError("Ambiguous boolean values: "+name)
    return values.astype(bool)


def _number(value, decimals=2):
    if value is None or pd.isna(value):
        return "未提供/不可估"
    if isinstance(value, (bool, np.bool_)):
        return "是" if value else "否"
    if isinstance(value, (int, float, np.number)):
        if not np.isfinite(float(value)):
            return "+∞" if value > 0 else "−∞"
        return f"{float(value):,.{decimals}f}"
    return str(value).replace("|", "／").replace("\n", " ")


def _cell(value, column):
    if column == "arm":
        return ARM_LABELS.get(value, str(value))
    if column == "scope":
        return SCOPE_LABELS.get(value, str(value))
    if column == "period":
        return PERIOD_LABELS.get(value, str(value))
    if column == "feature":
        return FEATURE_LABELS.get(value, str(value))
    if column == "result":
        return {"no_prior_entry": "峰值前无持仓交集", "held_full_peak_bar": "持有覆盖整个峰值K线",
                "peak_bar_order_unknown": "峰值K线内先后未知", "exited_before_peak": "参与过但峰值前已退出"}.get(value, str(value))
    if column == "minutes" and pd.notna(value):
        return "1H" if int(value) == 60 else "4H" if int(value) == 240 else str(value)
    if column in PERCENT_FRACTIONS and pd.notna(value):
        return _number(float(value)*100)
    if column.endswith("_time") or column.startswith("window_"):
        return str(value) if pd.notna(value) else "未提供"
    return _number(value, 0 if column in INTEGER_COLUMNS else 4 if column.endswith("_p") else 2)


def _table(frame: pd.DataFrame, columns: list[str] | tuple[str, ...]) -> str:
    if frame.empty:
        return "没有对应记录；不把缺失结果记成零。"
    lines = ["|"+"|".join(LABELS.get(c, c) for c in columns)+"|",
             "|"+"|".join("---" for _ in columns)+"|"]
    for row in frame.to_dict("records"):
        lines.append("|"+"|".join(_cell(row.get(c), c) for c in columns)+"|")
    return "\n".join(lines)


def _subset(frame: pd.DataFrame, **filters) -> pd.DataFrame:
    result = frame
    for column, value in filters.items():
        if column not in result:
            return result.iloc[:0]
        result = result.loc[result[column].isin(value) if isinstance(value, (tuple, list, set)) else result[column].eq(value)]
    return result


def _csv(folder: Path, name: str, *, required=True) -> pd.DataFrame:
    path = folder/name
    if not path.exists():
        if required:
            raise FileNotFoundError("Missing frozen report input: "+str(path))
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _require(frame, columns, name):
    if not set(columns).issubset(frame):
        raise ValueError(name+" missing core fields: "+str(sorted(set(columns)-set(frame))))


def load_results(folder: Path) -> dict:
    """Read saved ledgers and reject inconsistent core accounting evidence."""
    result = {key: _csv(folder, filename) for key, filename in (
        ("summary", "summary.csv"), ("portfolio", "portfolio_summary.csv"),
        ("events", "events.csv.gz"), ("calendar", "calendar_events.csv.gz"),
        ("diagnostics", "feature_diagnostics.csv"), ("coverage", "coverage.csv"))}
    result["costs"] = _csv(folder, "cost_diagnostics.csv", required=False)
    result["regime"] = _csv(folder, "regime.csv", required=False)
    result["scores"] = _csv(folder, "score_diagnostics.csv", required=False)
    result["opportunities"] = _csv(folder, "opportunity_audit.csv.gz", required=False)
    # The runner computes feature buckets only inside its period=='full' branch
    # and does not repeat that constant in the CSV. This is schema metadata,
    # not a missing numerical value being imputed as a favorable result.
    if "period" not in result["diagnostics"]:
        result["diagnostics"]["period"] = "full"
    result["manifest"] = json.loads((folder/"manifest.json").read_text())
    _require(result["summary"], ("scope", "minutes", "arm", "events", "mean_net_bp"), "summary")
    _require(result["portfolio"], ("scope", "minutes", "arm", "return_pct", "max_drawdown_pct", "trades"), "portfolio")
    events = result["events"]
    _require(events, ("event_id", "instrument", "venue", "symbol", "asset", "arm", "minutes", "valid"), "events")
    if events.event_id.duplicated().any():
        raise ValueError("Duplicate independent event identities in report input")
    events["valid"] = _bool(events.valid, "valid")
    for name in ("natural_exit", "censored"):
        if name in events:
            events.loc[events.valid, name] = _bool(events.loc[events.valid, name], name)
    for name in ("decision_time", "entry_time", "exit_time", "exit_time_lower", "exit_time_upper"):
        if name in events:
            events[name] = pd.to_datetime(events[name], utc=True, errors="coerce")
    known = events.loc[events.valid]
    if not known.empty:
        _require(known, ("entry_time", "exit_time", "entry_price", "exit_price", "initial_risk_frac",
                         "net_return", "net_r", "mfe_return", "features_path"), "valid events")
        numeric = known[["entry_price", "exit_price", "initial_risk_frac", "net_return", "net_r", "mfe_return"]].to_numpy(float)
        if not np.isfinite(numeric).all() or (numeric[:, :3] <= 0).any():
            raise ValueError("Nonfinite or impossible core event accounting")
        expected = known.exit_price/known.entry_price-1-.002
        if (not np.allclose(known.net_return, expected, atol=1e-10, rtol=1e-9)
                or not np.allclose(known.net_r, expected/known.initial_risk_frac, atol=1e-9, rtol=1e-9)):
            raise ValueError("Saved event net returns/R disagree with the frozen price/cost ledger")
        if known.entry_time.isna().any() or known.exit_time.isna().any() or (known.exit_time <= known.entry_time).any():
            raise ValueError("Invalid entry/exit clocks in report ledger")
    for key in ("summary", "portfolio"):
        frame = result[key]
        identity = [c for c in ("scope", "period", "minutes", "arm") if c in frame]
        if frame.duplicated(identity).any():
            raise ValueError("Ambiguous repeated summary keys: "+key)
        for column in ("events", "trades", "max_drawdown_pct"):
            if column in frame and (pd.to_numeric(frame[column], errors="raise").dropna() < 0).any():
                raise ValueError("Negative count or drawdown: "+column)
    calendar = result["calendar"]
    for name in ("window_start", "window_end", "peak_time"):
        if name in calendar:
            calendar[name] = pd.to_datetime(calendar[name], utc=True, errors="coerce")
    return result


def _plot_setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False,
        "axes.spines.right": False, "axes.labelcolor": "#3d5261", "xtick.color": "#536876",
        "ytick.color": "#536876", "axes.edgecolor": "#d0dce4", "grid.color": "#dce5eb",
        "figure.facecolor": "#f6f9fc", "axes.facecolor": "#ffffff", "savefig.facecolor": "#f6f9fc"})
    return plt


def _features(path_value, folder: Path) -> pd.DataFrame:
    path = Path(str(path_value))
    if not path.is_absolute():
        path = ROOT/path if (ROOT/path).exists() else folder/path
    path = path.resolve()
    if ROOT not in path.parents or not path.is_file():
        raise ValueError("Missing local saved feature frame: "+str(path))
    f = pd.read_pickle(path)
    if not isinstance(f, pd.DataFrame) or not isinstance(f.index, pd.DatetimeIndex) or f.index.tz is None:
        raise ValueError("Gallery needs a saved UTC feature DataFrame")
    _require(f, ("open", "high", "low", "close"), "gallery OHLC")
    if f.index.has_duplicates or not f.index.is_monotonic_increasing:
        raise ValueError("Gallery candle clocks must be unique and chronological")
    return f


def _case_plot(f: pd.DataFrame, row: dict, destination: Path, *, calendar=False) -> dict:
    """Draw saved prices and evidence only; never rerun an exit rule."""
    plt = _plot_setup()
    from matplotlib.patches import Rectangle
    minutes = int(row.get("minutes", 60))
    step = pd.Timedelta(minutes=minutes)
    if np.any(np.diff(f.index.as_unit("ns").asi8) != step.value):
        raise ValueError("Gallery source must be one continuous matching timeframe")
    if calendar:
        anchor, end = pd.Timestamp(row["window_start"]), pd.Timestamp(row["window_end"])
    else:
        anchor, end = pd.Timestamp(row["decision_time"])-step, pd.Timestamp(row["exit_time"])
        entry_time = pd.Timestamp(row["entry_time"])
        if entry_time not in f.index or not np.isclose(f.loc[entry_time, "open"], row["entry_price"], rtol=1e-10, atol=0):
            raise ValueError("Gallery entry price/time disagrees with its saved source")
        timing = row.get("exit_timing", "open")
        source_clock = end if timing == "open" else end-step
        if source_clock not in f.index:
            raise ValueError("Gallery exit clock lies outside its saved source")
        observed = f.loc[source_clock]
        if timing == "open":
            if row.get("exit_reason") == "take_profit_gap":
                consistent = row["exit_price"] <= observed.open
            else:
                consistent = np.isclose(observed.open, row["exit_price"], rtol=1e-10, atol=0)
        elif timing == "close":
            consistent = np.isclose(observed.close, row["exit_price"], rtol=1e-10, atol=0)
        elif timing == "intrabar_unknown":
            consistent = observed.low <= row["exit_price"] <= observed.high
        else:
            raise ValueError("Unrecognized gallery exit clock semantics")
        if not consistent:
            raise ValueError("Gallery exit fill is inconsistent with its saved source candle")
        entry_i, exit_i = f.index.get_loc(entry_time), f.index.get_loc(source_clock)
        complete_held = f.iloc[entry_i:exit_i+(timing == "close")]
        known_prices = [row["entry_price"], row["exit_price"], float(observed.open)]
        expected_peak = max(known_prices + ([float(complete_held.high.max())] if len(complete_held) else []))
        expected_mfe = max(0, expected_peak/row["entry_price"]-1)
        if not np.isclose(expected_mfe, row["mfe_return"], rtol=1e-9, atol=1e-10):
            raise ValueError("Gallery MFE includes unavailable prices or disagrees with the saved held path")
        if pd.notna(row.get("mae_return", np.nan)):
            expected_low = min(known_prices + ([float(complete_held.low.min())] if len(complete_held) else []))
            expected_mae = max(0, 1-expected_low/row["entry_price"])
            if not np.isclose(expected_mae, row["mae_return"], rtol=1e-9, atol=1e-10):
                raise ValueError("Gallery MAE disagrees with the saved held path")
    left = max(0, f.index.searchsorted(anchor)-48)
    right = min(len(f), f.index.searchsorted(end, side="right")+12)
    view = f.iloc[left:right]
    if len(view) < 2:
        raise ValueError("Insufficient saved gallery context")
    fig, (ax, indicator) = plt.subplots(2, 1, figsize=(15, 8.8), sharex=True,
        gridspec_kw={"height_ratios": [3.5, 1]}, constrained_layout=True)
    x = np.arange(len(view))
    up = view.close.ge(view.open).to_numpy()
    colors = np.where(up, "#0d998c", "#d95870")
    floor = max(float(view.high.max()-view.low.min())*.0009, np.finfo(float).eps)
    for j, (o, h, l, c) in enumerate(view[["open", "high", "low", "close"]].to_numpy(float)):
        ax.vlines(j, l, h, color=colors[j], linewidth=.65, alpha=.95)
        ax.add_patch(Rectangle((j-.32, min(o, c)), .64, max(abs(c-o), floor),
                              facecolor=colors[j], edgecolor=colors[j], linewidth=.3))
    for name, color in (("sma20", "#72b6af"), ("sma60", "#708fbe"), ("sma120", "#9aa5b1")):
        if name in view:
            ax.plot(x, view[name], color=color, linewidth=1, alpha=.85, label=name.upper())
    log_price = float(view.high.max()/view.low.min()) > 3
    if log_price:
        ax.set_yscale("log")
    ax.set_ylabel("Price (log scale)" if log_price else "Price")
    def xpos(t):
        return float((pd.Timestamp(t)-view.index[0])/step)
    if calendar:
        ax.axvspan(xpos(row["window_start"]), xpos(row["window_end"]), color="#e8b966", alpha=.09)
        ax.axvline(xpos(row["window_start"]), color="#ac8a4c", linewidth=1, linestyle="--")
        peak_time = row.get("peak_time")
        if peak_time is not None and pd.notna(peak_time):
            ax.scatter([xpos(peak_time)], [row["high"]], color="#b98a32", marker="D", s=44, zorder=5)
        subtitle = "Calendar outcome label; no overlapping 1H focus position before its peak"
    else:
        decision_x, entry_x, exit_x = xpos(row["decision_time"]), xpos(row["entry_time"]), xpos(row["exit_time"])
        ax.axvspan(entry_x, exit_x, color="#6288ca", alpha=.055)
        ax.axvline(decision_x, color="#7e70b5", linewidth=.9, linestyle="--", label="Signal close")
        ax.scatter([entry_x], [row["entry_price"]], marker="^", color="#158f7d", s=60, zorder=7, label="Next-open entry")
        ax.scatter([exit_x], [row["exit_price"]], marker="X", color="#bd5264", s=65, zorder=7, label="Simulated exit / mark")
        initial_stop = row.get("initial_stop", row["entry_price"]*(1-row["initial_risk_frac"]))
        ax.hlines(initial_stop, entry_x, exit_x, color="#c76577", linewidth=.8, linestyle=":", label="Frozen initial stop")
        peak_price = row["entry_price"]*(1+row["mfe_return"])
        if peak_price > 0:
            ax.hlines(peak_price, entry_x, exit_x, color="#a88b4a", linewidth=.7, linestyle=":", label="Known held-path MFE")
        subtitle = (f"Net {row['net_return']*100:+.2f}% / {row['net_r']:+.2f}R   |   "
                    f"Initial risk {row['initial_risk_frac']*100:.2f}%   |   MFE {row['mfe_return']*100:.2f}%")
        if row.get("exit_timing") == "intrabar_unknown":
            subtitle += "   |   exit clock shown at enclosing-close upper bound"
    for name, color, label in (("md", "#4b7cca", "IMACD"), ("sb", "#dc962e", "Signal")):
        if name in view:
            indicator.plot(x, view[name], color=color, linewidth=1.55, label=label)
    indicator.axhline(0, color="#738595", linewidth=.85)
    if not calendar and str(row.get("arm", "")).startswith("focus_"):
        run = row.get("near_zero_bars", np.nan)
        if pd.notna(run) and float(run) > 0:
            right_x = xpos(row["decision_time"])-1
            indicator.hlines(0, max(0, right_x-float(run)), right_x,
                             color="#d9ad61", linewidth=3.5, alpha=.55)
    indicator.set_ylabel("IMACD")
    ax.grid(alpha=.32, linestyle=":")
    indicator.grid(alpha=.32, linestyle=":")
    ticks = np.unique(np.linspace(0, len(view)-1, min(8, len(view))).astype(int))
    indicator.set_xticks(ticks)
    indicator.set_xticklabels([view.index[j].strftime("%m-%d\n%H:%M UTC") for j in ticks], fontsize=8)
    title = f"{row.get('venue', '')}  {row.get('symbol', '')}  {minutes//60}H  |  {row.get('arm', 'calendar doubling')}"
    ax.set_title(title+"\n"+subtitle, loc="left", fontsize=11, pad=14)
    ax.legend(loc="upper left", fontsize=7, ncol=3, frameon=False)
    indicator.legend(loc="upper left", fontsize=8, ncol=2, frameon=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=145)
    plt.close(fig)
    return dict(path=str(destination), bars=len(view), context_start=str(view.index[0]),
                context_end=str(view.index[-1]+step), price_scale="log" if log_price else "linear")


def _distinct(frame: pd.DataFrame, n: int, ascending: bool) -> pd.DataFrame:
    if frame.empty:
        return frame
    ordered = frame.sort_values(["net_return", "entry_time", "event_id"], ascending=[ascending, True, True])
    return ordered.drop_duplicates("asset").head(n)


def select_gallery(data: dict) -> list[tuple[str, dict]]:
    """Predeclared outcome-selected examples, retaining losses and misses."""
    e = data["events"]
    natural = e.loc[e.valid]
    if "natural_exit" in natural:
        natural = natural.loc[natural.natural_exit.eq(True)]
    else:
        return []
    focus = natural.loc[natural.arm.eq("focus_sma60")]
    selected = []
    for label, chosen in (("focus_top", _distinct(focus, 5, False)),
                          ("focus_loss", _distinct(focus.loc[focus.net_return.lt(0)], 3, True))):
        selected.extend((label, row) for row in chosen.to_dict("records"))
    for arm in ("dense_sma60", "pullback_sma60"):
        group = natural.loc[natural.arm.eq(arm)]
        for suffix, ascending in (("best", False), ("worst", True)):
            selected.extend((arm+"_"+suffix, row) for row in _distinct(group, 1, ascending).to_dict("records"))
    # A known old focus position can already cover the calendar move, even if
    # no new arrow is printed within this particular calendar interval.
    c = data["calendar"]
    required = {"close_return", "coverage_hours", "kind", "window_start", "window_end", "peak_time", "instrument"}
    if required.issubset(c):
        complete = np.where(c.kind.eq("day"), 24, np.where(c.kind.eq("week"), 168, -1))
        doubled = c.loc[c.close_return.ge(1) & c.coverage_hours.eq(complete)].sort_values(
            ["close_return", "window_start", "instrument"], ascending=[False, True, True])
        seen_assets = set()
        for row in doubled.to_dict("records"):
            asset = row.get("asset", row["instrument"])
            if asset in seen_assets or pd.isna(row["peak_time"]):
                continue
            existing = e.loc[e.valid & e.instrument.eq(row["instrument"]) & e.arm.eq("focus_sma60")
                             & e.minutes.eq(60) & e.entry_time.le(row["peak_time"]) & e.exit_time_upper.gt(row["window_start"])]
            if not existing.empty:
                continue
            row.update(minutes=60, features_path=row.get("features_path_60"))
            selected.append(("calendar_missed", row))
            seen_assets.add(asset)
            if len(seen_assets) == 3:
                break
    return selected


def _gallery(data: dict, folder: Path) -> tuple[str, list[dict]]:
    lines, manifest, used_events = [], [], set()
    descriptions = {
        "focus_top": "蓄势释放：自然退出净收益靠前的案例", "focus_loss": "蓄势释放：亏损案例",
        "dense_sma60_best": "密集价格突破：高收益代表", "dense_sma60_worst": "密集价格突破：最差代表",
        "pullback_sma60_best": "趋势回踩：高收益代表", "pullback_sma60_worst": "趋势回踩：最差代表",
        "calendar_missed": "日历翻倍：峰值前没有1H蓄势释放持仓交集",
    }
    for sequence, (kind, row) in enumerate(select_gallery(data), 1):
        event_id = row.get("event_id")
        if event_id and event_id in used_events:
            continue
        if event_id:
            used_events.add(event_id)
        heading = f"### {descriptions[kind]} · {row.get('venue', '')} {row.get('symbol', '')}"
        path_value = row.get("features_path")
        if path_value is None or pd.isna(path_value):
            # Coverage may supply this for a market with no events at all.
            coverage = data["coverage"]
            if {"instrument", "features_path_60"}.issubset(coverage):
                matches = coverage.loc[coverage.instrument.eq(row.get("instrument")), "features_path_60"].dropna()
                path_value = matches.iloc[0] if len(matches) else None
        if path_value is None or pd.isna(path_value):
            lines.extend([heading, "冻结输入没有这段行情的特征文件路径，未借用其他交易所代画。"])
            manifest.append(dict(kind=kind, event_id=event_id, status="missing_features_path"))
            continue
        f = _features(path_value, folder)
        destination = folder/"gallery"/f"case_{sequence:02}_{kind}.png"
        evidence = _case_plot(f, row, destination, calendar=kind == "calendar_missed")
        lines.append(heading)
        if kind == "calendar_missed":
            lines.append(f"{row['window_start']} 至 {row['window_end']}：收盘涨幅 {_number(row['close_return']*100)}%，"
                         f"最高影线涨幅 {_number(row.get('peak_return', np.nan)*100)}%。这是未来结果标签，不是当时可知的入场条件。")
            if "ready" in f:
                i = max(0, f.index.searchsorted(row["window_start"], side="right")-1)
                lines.append("窗口起点指标已完成预热。" if bool(f.ready.iloc[i]) else "窗口起点尚未完成主指标预热；不能把这一例解释为参数漏抓。")
        else:
            info = dict(row, net_pct=row["net_return"]*100, mfe_pct=row["mfe_return"]*100,
                        risk_pct=row["initial_risk_frac"]*100,
                        giveback_pct=(1-row["exit_price"]/(row["entry_price"]*(1+row["mfe_return"])))*100)
            lines.append(_table(pd.DataFrame([info]), ("minutes", "entry_time", "exit_time", "net_pct", "net_r", "risk_pct", "mfe_pct", "giveback_pct", "exit_reason")))
            lines.append("这是独立候选的模拟路径，未自动等同于组合实际分配到的仓位。最高浮盈只是持有期间已知价格极值；退出价由固定规则决定。")
        lines.append(f"![{kind} {row.get('symbol', '')} 全景]({destination})")
        manifest.append(dict(kind=kind, event_id=event_id, instrument=row.get("instrument"),
                             status="rendered", source_features=str(path_value), **evidence))
    return "\n\n".join(lines) if lines else "没有足够的自然退出/完整日历标签可生成预定案例。", manifest


def _expansion(data: dict) -> pd.DataFrame:
    coverage, events = data["coverage"], data["events"]
    if not {"venue", "asset"}.issubset(coverage):
        return pd.DataFrame()
    okx_assets = set(coverage.loc[coverage.venue.eq("okx"), "asset"].dropna())
    rows = []
    for venue in ("okx", "binance", "gate"):
        c = coverage.loc[coverage.venue.eq(venue)]
        assets = set(c.asset.dropna())
        unique = assets-okx_assets
        focus = events.loc[events.valid & events.venue.eq(venue) & events.arm.eq("focus_sma60")]
        natural = focus.loc[focus.natural_exit.eq(True)] if "natural_exit" in focus else focus.iloc[:0]
        rows.append(dict(scope=venue, contracts=c.symbol.nunique() if "symbol" in c else np.nan,
            assets=len(assets), signals=len(focus), new_assets=len(unique) if venue != "okx" else np.nan,
            positive_assets=natural.loc[natural.asset.isin(unique) & natural.net_return.gt(0), "asset"].nunique() if venue != "okx" else np.nan,
            fifty_assets=natural.loc[natural.asset.isin(unique) & natural.net_return.ge(.5), "asset"].nunique() if venue != "okx" else np.nan))
    return pd.DataFrame(rows)


def _plot_curves(folder: Path, data: dict) -> list[Path]:
    """Read saved account paths, checking summary endpoints where available."""
    plt = _plot_setup()
    paths = []
    for minutes in (60, 240):
        fig, (ax, dd) = plt.subplots(2, 1, sharex=True, figsize=(13, 6.6),
            gridspec_kw={"height_ratios": [3, 1]}, constrained_layout=True)
        made = 0
        for arm, color in zip(MAIN_ARMS, ("#207baf", "#159c87", "#b2873d")):
            choices = [folder/"portfolios"/f"combined_{minutes}_{arm}.csv.gz",
                       folder/"portfolios"/f"combined_full_{minutes}_{arm}.csv.gz"]
            source = next((p for p in choices if p.exists()), None)
            if source is None:
                continue
            curve = pd.read_csv(source)
            _require(curve, ("time", "equity", "drawdown"), "portfolio curve")
            times = pd.to_datetime(curve.time, utc=True)
            if times.duplicated().any() or not times.is_monotonic_increasing or not np.isfinite(curve.equity).all():
                raise ValueError("Invalid saved portfolio curve")
            matched = _subset(data["portfolio"], scope="combined", period="full", minutes=minutes, arm=arm)
            if len(matched) and not np.isclose((curve.equity.iloc[-1]/100000-1)*100, matched.return_pct.iloc[0], atol=1e-7):
                raise ValueError("Portfolio curve endpoint disagrees with saved summary")
            ax.plot(times, (curve.equity/100000-1)*100, label=arm, color=color, linewidth=1.3)
            dd.plot(times, curve.drawdown*100, color=color, linewidth=.9)
            made += 1
        if made:
            ax.set_title(f"Asset-exclusive cross-venue accounts | {minutes//60}H | identical exits for A / B / C", loc="left", pad=12)
            ax.set_ylabel("Account return (%)")
            dd.set_ylabel("Close DD (%)")
            ax.axhline(0, color="#a8b5c0", linewidth=.7)
            ax.legend(frameon=False, fontsize=8)
            for panel in (ax, dd):
                panel.grid(alpha=.3, linestyle=":")
            destination = folder/"gallery"/f"accounts_{minutes}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(destination, dpi=145)
            paths.append(destination)
        plt.close(fig)
    regime = data["regime"]
    if {"time", "breadth", "md_positive", "n_assets"}.issubset(regime) and not regime.empty:
        t = pd.to_datetime(regime.time, utc=True)
        fig, ax = plt.subplots(figsize=(13, 3.4), constrained_layout=True)
        ax.plot(t, regime.breadth*100, color="#168f81", label="Assets above SMA60 (%)")
        ax.plot(t, regime.md_positive*100, color="#4b7cca", label="Assets with positive IMACD (%)")
        ax.set_title("Completed-day market breadth; exact assets de-duplicated across venues", loc="left")
        ax.legend(frameon=False, fontsize=8)
        ax.grid(alpha=.3, linestyle=":")
        destination = folder/"gallery"/"market_regime.png"
        fig.savefig(destination, dpi=145)
        plt.close(fig)
        paths.append(destination)
    return paths


def build_markdown(data: dict, folder: Path, figures: list[Path], gallery: str, commit: str) -> str:
    """Answer with supplied evidence; no unsupported future regime prediction."""
    portfolio, summary, e = data["portfolio"], data["summary"], data["events"]
    combined = _subset(portfolio, scope="combined", period="full")
    main = _subset(combined, arm=MAIN_ARMS)
    expansion = _expansion(data)
    lines = ["# spike · 跨交易所山寨趋势研究：从抓到启动，到留下利润",
        "本轮回答的是：扩大到 Binance、Gate 后，最近两个月有哪些趋势能够按事先固定规则参与，"
        "哪些启动方式更容易兑现，失败发生在哪里。未来几个月可能进入山寨活跃期，是待验证的使用情境；本报告不把它当作已经确定的市场事实。",
        "**研究区间：2026-07-10 00:00 至 2026-09-09 00:00 UTC。全部结果为回顾性研究，"
        "没有根据本轮收益调参，也没有修改线上指标、通知或交易账户。**",
        "## 先看能真正分配资金的结果",
        "下表是独立模拟账户；不是把所有相互重叠的信号收益相加。各账户起始100,000 USDT，最多10个不同资产，"
        "同资产跨交易所互斥，名义/初损风险/现金/成交额容量共同约束仓位。A、B、C使用同一SMA60退出，只比较入场方式。",
        _table(main, ("minutes", "arm", "return_pct", "max_drawdown_pct", "trades", "win_rate", "profit_factor", "random_return_pct_mean", "random_schedules", "boundary_marks")),
        "比较时同时看收益、回撤、成交数与尾部依赖。单次高收益或一个翻倍币，不足以证明下一阶段也有同样结果。",
        "## 扩大市场，实际多了哪些机会",
        _table(expansion, ("scope", "contracts", "assets", "signals", "new_assets", "positive_assets", "fifty_assets")),
        "“新增”严格指冻结覆盖目录里OKX没有的底层资产，按资产去重；候选数仍可能包括不同周期/重复段。"
        "自然退出净涨≥50%来自A+SMA60独立候选，不等于账户收益50%。只有在目录同时提供venue/asset时才计算；"
        "无法证实身份的乘数别名不能自行合并，当前目录也不保证包含所有历史退市品种。",
        _table(_subset(portfolio, period="full", scope=("okx", "binance", "gate"), arm=MAIN_ARMS),
               ("scope", "minutes", "arm", "return_pct", "max_drawdown_pct", "trades", "random_return_pct_mean")),
        "## 市场阶段：同一规则在前后两段如何变化",
        "前31天、后30天使用同一批固定规则。组合表按日期区间的真实收盘权益变化展示，继承期初已有仓位，"
        "不会在8月10日人为清仓再启动。候选统计若按入场批次分组，一笔交易可以跨界持有，不能把它误读为纯月内已兑现收益。",
        _table(_subset(portfolio, scope="combined", period=("first31", "last30"), arm=MAIN_ARMS),
               ("period", "minutes", "arm", "return_pct", "max_drawdown_pct", "trades", "random_return_pct_mean", "boundary_marks")),
    ]
    for figure in figures:
        lines.append(f"![已保存账户或市场背景曲线]({figure})")
    lines += ["## 为什么会抓到，也为什么会错过",
        "A只认经过近零蓄势后的释放；行情已经上涨、主线长期不归零时，它不会为每次加速补一个新箭头。"
        "B独立观察均线密集后价格站上六线并突破前高；C观察已建立趋势中的回踩恢复。三者解决不同入口，"
        "是否互补要看完整候选与账户结果，不能只拿各自最漂亮的截图。D是短历史60—339根的独立规则，不能冒称与IMACD完全兼容。",
        _table(_subset(summary, scope="combined", period="full"),
               ("minutes", "arm", "events", "mean_net_bp", "mean_net_r", "win_rate", "mean_control_bp", "mean_excess_bp", "asset_balanced_excess_bp", "permutation_assets", "permutation_p", "holm_p")),
        "匹配随机要求同币同所同周期、同自然周、因果波动桶；最多3个且不放宽缺样。不同退出共用同一决策的控制索引。"
        "置换先按资产×自然周聚合，再按资产均衡；p对应资产均衡超额，不能与事件等权的平均超额混为一谈。"
        "Holm校正多重比较，但资产间仍可能有共同市场冲击；低p不能证明因果关系或未来收益。",
        "### 大涨标签中的参与与提前退出",
        "以下针对固定日历窗口内最高影线较窗口开盘上涨≥50%的事件，描述1H独立候选路径是否参与。"
        "日与周分别列，不能相加为独立行情数；同资产跨所同一次上涨也不是独立证据。"
        "“无持仓交集”还需结合预热/上市/缺口判断，“参与”不等于账户资金已经分配。",
    ]
    opportunity = data.get("opportunities", pd.DataFrame())
    if {"venue", "kind", "arm", "result"}.issubset(opportunity):
        counts = opportunity.groupby(["venue", "kind", "arm", "result"], dropna=False).size().rename("opportunity_count").reset_index()
        lines.append(_table(counts, ("venue", "kind", "arm", "result", "opportunity_count")))
    else:
        lines.append("没有完整机会归因账本；仅用下文有限案例说明，不能据此声称全市场捕获率。")
    lines += [
        "## 入场时的量、价格位置和高周期背景",
        "以下分桶只使用当时收盘已知的特征。量比是相对于本币过去成交量；高周期只取已完成K线。"
        "“高周期许可”不是事后等4H金叉，再倒画到更早1H。缺失值保留未知，不当成中性或零。",
    ]
    diagnostics = _subset(data["diagnostics"], scope="combined", period="full", arm=MAIN_ARMS)
    if diagnostics.empty:
        lines.append("尚无与主表相同口径的特征分桶，不能据案例推断哪项条件具有区分力。")
    else:
        for minutes in (60, 240):
            lines.extend([f"### {minutes//60}H 背景分桶",
                _table(_subset(diagnostics, minutes=minutes), ("arm", "feature", "bucket", "n", "win_rate", "mean_net_bp", "mean_mfe_pct", "mean_excess_bp"))])
    lines += ["这些是解释性对照，没有自动把表现最好的桶变成新的过滤阈值。真正的增益还需要固定条件后继续前向观察；"
              "一个后验赢家同时满足多项条件，并不说明每一项都有贡献。",
        "### 预定义单特征排序对照",
        "下表直接读取事前定义的单特征排序结果；AUC的标签是最终净收益是否为正，只是描述性排序诊断，"
        "不是训练模型验证AUC。前10%也不是事后按盈利挑选的10%；需连同匹配随机超额和样本数看。",
        _table(_subset(data.get("scores", pd.DataFrame()), scope="combined", period="full", arm="focus_sma60"),
               ("minutes", "feature", "n", "top_n", "descriptive_auc", "top_decile_gross_bp", "top_decile_net_bp", "top_decile_win_rate", "top_decile_excess_bp")),
        "## 止盈与利润回吐：大R不等于账户翻倍",
        "A的四个退出臂共享同一批入场和冻结的2ATR初损。SMA保护只升不降，先比较上一有效保护，失守后下一开盘退出；"
        "只有初始硬止损在盘中触发。3R是固定止盈对照，最高浮盈MFE是途中到过的价格，不是已兑现收益。",
        _table(_subset(combined, arm=("focus_sma60", "focus_md", "focus_sma20", "focus_3r")),
               ("minutes", "arm", "return_pct", "max_drawdown_pct", "trades", "random_return_pct_mean", "top1_positive_profit_share", "top5_positive_profit_share", "return_minus_top1_contribution_pct")),
        "“扣最大赢家贡献”只是在已发生路径上移除那笔利润的静态归因，没有重新分配资金，也不是重跑后的收益。"
        "R的分母始终是入场时固定风险；移动保护不得把分母缩小再宣称赚到更高R。",
        "## 全景复盘：包括大赢家、亏损和漏抓",
        "下列案例按报告开头固定的选图规则事后挑选，只解释路径，不代表胜率。每张尽量保留信号前48根以及退出/窗口后12根；"
        "数据边界不足时以真实可用范围为准。副图保留蓝色主线、橙色信号线和零轴，没有柱状图。",
        gallery,
        "## 成本、数据与风险的诚实说明",
        "主结果沿用每侧按入场名义10bp的静态成本，合计0.2%。高涨幅持仓的真实出场费按更大的出场名义计算，"
        "固定20/40/60bp压力也不一定覆盖它。以下若有成本补充，仅修正已冻结的事件路径，不能称已重配资金的完整组合。",
    ]
    costs = data["costs"]
    if costs.empty:
        lines.append("没有提供完成的成本/资金费补充账本；主结果未计完整资金费、盘口滑点与市场冲击。未知资金费不按零处理。")
    elif "frozen_actual_turnover_return_pct" in costs:
        cost_main = _subset(costs, scope="combined")
        lines.append(_table(cost_main, ("minutes", "arm", "trades", "frozen_base_return_pct",
            "frozen_40bp_return_pct", "frozen_60bp_return_pct", "frozen_actual_turnover_return_pct")))
        lines.append(_table(_subset(cost_main, arm=MAIN_ARMS), ("minutes", "arm", "funding_known_trades",
            "funding_known_fraction", "funding_unknown_trades", "known_cohort_turnover_return_pct",
            "known_cohort_after_observed_funding_pct", "proxy_settlements", "ambiguous_settlements",
            "uncertain_funding_range_pct")))
        lines.append("上述保持同一组成交与数量，仅改变成本贡献；费用改变后的可用现金并未重新驱动后续配仓，"
            "所以不是重算后的复利账户。资金费只覆盖已取得的历史记录，缺失未知；原生时间观察到0–8秒偏移，保守将开平仓边界±1分钟及盘中退出先后不明按区间处理。"
            "OKX/Gate若用1H成交K线开盘价作结算价格代理，不等于真实结算标记价；不能把可观测子集称为完整资金费覆盖。")
        lines.append(f"[完整跨所成本账本]({folder/'cost_diagnostics.csv'})。")
    else:
        columns = [c for c in ("scope", "period", "minutes", "arm") if c in costs]
        columns += [c for c in costs if c not in columns][:16]
        shown_costs = costs if len(costs) <= 100 else costs.head(40)
        lines.append(_table(shown_costs, columns))
        if len(costs) > 100:
            lines.append(f"成本账本共{len(costs):,}行，上表仅展示原始顺序前40条用于审计，不把其平均值当成全样本结论。"
                         f"[下载完整成本账本]({folder/'cost_diagnostics.csv'})。")
        lines.append("资金费若使用原生trade-bar开盘价代理，仍不是实际结算标记价。历史费率缺口、结算时刻边界或发布延迟未知，"
                     "都必须保留源账本限制；看到一条历史费率不等于完整资金费覆盖。")
    lines += ["每笔名义上限还包括信号K线USDT成交额1%与前24H成交额0.1%；这是条件容量限制，不能保证那个价位实际能成交。"
              "本实验没有模拟杠杆爆仓、交易所风控、最低下单单位或订单簿深度。",
        "行情缺口拆段重新预热，不填价格、不补零量；短观测历史不等同于刚上市。当前可读目录与残存历史存在幸存者偏差，"
        "交易所品种更多不等于每个品种都有连续可交易的两个月记录。",
        "无训练分类模型，因此训练/验证AUC不适用。本轮的基线与匹配随机直接检验价格净收益；"
        "若未另提供事先定义分数的Top10%诊断，则Top10%收益/排序AUC不可估，不能从事后赢家排名伪造。",
        "### 数据覆盖明细",
    ]
    coverage = data["coverage"]
    coverage_cols = [c for c in ("venue", "symbol", "asset", "instrument", "raw", "eligible", "segments", "warmup", "gaps", "reason") if c in coverage]
    if len(coverage) <= 100:
        lines.append(_table(coverage, coverage_cols or list(coverage.columns[:10])))
    else:
        lines.append(f"覆盖账本共{len(coverage):,}行，全部留存于 [coverage.csv]({folder/'coverage.csv'})；未截取成功合约后称为全市场。")
        if "venue" in coverage:
            counts = coverage.groupby("venue", dropna=False).size().rename("coverage_rows").reset_index()
            lines.append(_table(counts, ("venue", "coverage_rows")))
    manifest = data["manifest"]
    lines += ["## 复现与下一步",
        "先复核本轮失败模式、费用和图中真实退出，再决定是否把B/C作为额外候选接入前端。"
        "任何上线信号或保护规则变更需要单独说明；本报告没有自动promote、修改通知或下单。"
        "下一阶段应将规则保持不变，逐日记录新鲜信号和落空情况，以验证未来行情是否仍适用。",
        f"报告构建commit：`{commit}`。结果manifest：[{folder/'manifest.json'}]({folder/'manifest.json'})。",
        "Owner已明确授权最近两个月及任意历史。本配置holdout消耗次数以冻结manifest记录为准："
        f"`{manifest.get('holdout_consumption', manifest.get('holdout_consumption_count', 'manifest未提供次数'))}`；"
        "图像重绘只读取已保存事件与对应图表，不新增策略评分。",
        "```bash\n"+shlex.quote(str(ROOT/".venv/bin/python"))+" -m yoyo.evaluation.altseason_report --results "
        +shlex.quote(str(folder))+" --report "+shlex.quote(str(DEFAULT_REPORT))+"\n```",
        (EXPERIMENT/"RUNBOOK.md").read_text(),
        "完整市场获取/研究运行命令与输入SHA应以同目录manifest及已提交PROJECT_PLAN为准；"
        "本报告生成器不隐式重新下载可修订数据。",
        f"[冻结研究计划]({EXPERIMENT/'PROJECT_PLAN.md'}) · [候选账本]({folder/'events.csv.gz'}) · "
        f"[日历行情标签]({folder/'calendar_events.csv.gz'}) · [入场特征分桶]({folder/'feature_diagnostics.csv'})",
    ]
    return "\n\n".join(lines)+"\n"


def render(folder: Path, report: Path) -> dict:
    """Validate, render local figures, write Markdown, immediately build HTML."""
    folder, report = folder.resolve(), report.resolve()
    commit = _committed()
    data = load_results(folder)
    (folder/"gallery").mkdir(exist_ok=True)
    figures = _plot_curves(folder, data)
    gallery, gallery_manifest = _gallery(data, folder)
    markdown = build_markdown(data, folder, figures, gallery, commit)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT/"scripts/md_to_html.py"), str(report),
                    "--out-dir", str(ROOT/"analysis/html")], cwd=ROOT, check=True)
    html_path = ROOT/"analysis/html"/(report.stem+".html")
    source_files = ("summary.csv", "portfolio_summary.csv", "events.csv.gz", "calendar_events.csv.gz",
                    "feature_diagnostics.csv", "coverage.csv", "manifest.json", "cost_diagnostics.csv", "regime.csv",
                    "score_diagnostics.csv", "opportunity_audit.csv.gz")
    result = dict(builder_commit=commit, report=str(report), html=str(html_path),
        input_sha256={name: _sha(folder/name) for name in source_files if (folder/name).exists()},
        report_sha256=_sha(report), html_sha256=_sha(html_path), figures=list(map(str, figures)),
        gallery=gallery_manifest, gallery_counts=dict(Counter(row["status"] for row in gallery_manifest)),
        scoring_performed=False)
    (folder/"gallery"/"report_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    print(json.dumps(render(args.results, args.report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

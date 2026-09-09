"""Render the frozen mainstream comparison from saved research results only.

The experiment contract is
``experiments/active/exp-mainstream-super-trend-20260910-v1/PROJECT_PLAN.md``.
This module reads the runner's summary, per-coin results, ranking diagnostics,
candidate ledger, close-stamped portfolio curves and identity manifest. It
does not construct features, simulate trades or redo inference. Four explicitly
retrospective cases may read the runner's saved bars/features.
Every frozen arm and fold is retained, including losses and zero-trade arms.
The local project markdown converter embeds the Matplotlib figure in HTML.

Curve validation uses initial equity 1, including the initial cash peak in
drawdown; the first observed close is never renormalized. Candidate, selected,
natural-exit and censored counts are checked against the saved ledger before
writing a report. Source bytes must be committed before the render command.
The builder itself has no network, notification, model or execution actions.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
from urllib.parse import quote

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BUILDER = Path(__file__).resolve()
PLAN = ROOT / "experiments/active/exp-mainstream-super-trend-20260910-v1/PROJECT_PLAN.md"
DEFAULT_INPUT = ROOT / "analysis/output/mainstream_super_trend_20260910"
DEFAULT_REPORT = ROOT / "analysis/p1_mainstream_super_trend_20260910.md"
SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX")
ARMS = (
    "baseline", "volume2", "formation_price", "momentum_now", "momentum_memory",
    "lower_active", "higher_now", "higher_wait", "strict_same_time", "sequence",
    "sequence_wait", "baseline_3r",
)
FOLDS = ("calibration", "validation", "audit_pre", "recent")
FOLD_LABELS = {
    "calibration": "2023—2024 校准期", "validation": "2025 验证期",
    "audit_pre": "2026 年 holdout 前", "recent": "2026 年近期历史",
}
FOLD_DATES = {
    "calibration": "2023-01-01 至 2025-01-01",
    "validation": "2025-01-01 至 2026-01-01",
    "audit_pre": "2026-01-01 至 2026-05-04",
    "recent": "2026-05-04 至 2026-09-08 16:00",
}
ARM_LABELS = {
    "baseline": "原始多头释放", "volume2": "相对量≥2",
    "formation_price": "形成与价格位置", "momentum_now": "当前强动能",
    "momentum_memory": "形成期动能记忆", "lower_active": "低周期多头段有效",
    "higher_now": "高周期当刻许可", "higher_wait": "等待高周期许可",
    "strict_same_time": "严格同时满足", "sequence": "顺序候选",
    "sequence_wait": "顺序候选再等待", "baseline_3r": "原始释放固定3R",
    "buy_hold": "同期买入持有",
}
ARM_RULES = (
    ("baseline", "原始多头 release", "md"),
    ("volume2", "baseline + 当前量 / 前20根量中位数≥2", "md"),
    ("formation_price", "baseline + dense_recent 且当前收盘在六线之上", "md"),
    ("momentum_now", "baseline + 当前动能≥90", "md"),
    ("momentum_memory", "baseline + 形成期至释放本根曾有动能≥90", "md"),
    ("lower_active", "baseline + 低周期多头段仍有效", "md"),
    ("higher_now", "baseline + 最新完整高周期 md>sb 且 md>0", "md"),
    ("higher_wait", "baseline + 在原段失效前等待高周期首次许可", "md"),
    ("strict_same_time", "形成/位置 + 量≥2 + 当前动能≥90 + 低周期活动释放在1根base内", "md"),
    ("sequence", "形成/位置 + 量≥2 + 形成期动能记忆 + 低周期段有效", "md"),
    ("sequence_wait", "原释放时满足 sequence，再等待高周期首次许可", "md"),
    ("baseline_3r", "与 baseline 相同初始2ATR风险", "固定3R"),
)
SUMMARY_COLUMNS = (
    "fold", "minutes", "arm", "candidates", "trades", "natural_trades", "censored",
    "net_pct", "mdd_pct", "win_pct", "pf", "mean_net_r", "win_3r", "win_5r",
    "top3_profit_share_pct", "excess_bp", "p", "ci_low", "ci_high", "months",
    "holm_p", "matched_n", "positive_coins", "baseline_5r_retained", "baseline_5r_count",
)
COUNT_COLUMNS = ("candidates", "trades", "natural_trades", "censored", "win_3r", "win_5r",
                 "months", "matched_n", "positive_coins")
INPUT_FILES = ("summary.csv", "per_coin.csv", "portfolio_curves.pkl.gz",
               "events.csv.gz", "ranking.csv", "manifest.json")
LABELS = {
    "arm": "实验臂", "symbol": "币种", "candidates": "候选数", "trades": "实际成交",
    "natural_trades": "自然退出", "censored": "折末标记", "net_pct": "静态净收益%",
    "mdd_pct": "MDD%", "win_pct": "自然胜率%", "pf": "自然PF",
    "mean_net_r": "平均净R", "win_3r": "≥3R赢家", "win_5r": "≥5R赢家",
    "top3_profit_share_pct": "前3盈利占比%", "excess_bp": "匹配超额bp",
    "p": "置换p", "ci_low": "CI下界bp", "ci_high": "CI上界bp",
    "months": "有效月份", "holm_p": "Holm p", "matched_n": "匹配案例数",
    "positive_coins": "正收益币数/8", "baseline_5r_retained": "基线≥5R保留数", "baseline_5r_count": "基线≥5R总数",
    "n": "诊断样本数", "auc": "AUC", "top_n": "Top10%样本数",
    "top_gross_bp": "Top10%毛bp", "top_net_bp": "Top10%净bp",
    "top_win_pct": "Top10%胜率%", "top_excess_bp": "Top10%匹配超额bp",
    "score": "事前排序分数", "median_wait_bars": "等待bar中位数",
    "median_wait_price_bp": "等待入场价变化中位bp", "p_resolution": "p分辨率",
    "top_matched_n": "Top10%匹配数", "top_matched_case_net_bp": "Top10%匹配案例净bp",
    "top_control_net_bp": "Top10%对照净bp", "top_p": "Top10%置换p",
    "top_months": "Top10%月份", "top_p_resolution": "Top10%p分辨率",
    "top_ci_low": "Top10%CI下界bp", "top_ci_high": "Top10%CI上界bp",
    "natural_only_excess_bp": "仅自然退出超额bp", "natural_only_p": "仅自然退出p",
    "natural_only_months": "仅自然退出月份", "natural_only_ci_low": "仅自然退出CI下界bp",
    "natural_only_ci_high": "仅自然退出CI上界bp", "natural_only_p_resolution": "仅自然退出p分辨率",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_committed() -> str:
    """Only rendering needs this guard; pure format helpers remain testable."""
    name = BUILDER.relative_to(ROOT).as_posix()
    committed = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)
    if committed != BUILDER.read_bytes():
        raise ValueError("Commit exact mainstream_report.py bytes before rendering")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _number(value: object, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "不适用/不可估"
    if isinstance(value, (bool, np.bool_)):
        return "是" if value else "否"
    if isinstance(value, (int, float, np.number)):
        if math.isinf(float(value)):
            return "+∞" if value > 0 else "−∞"
        return f"{float(value):,.{decimals}f}"
    return str(value).replace("|", "／").replace("\n", " ")


def _table(frame: pd.DataFrame, columns: tuple[str, ...] | list[str]) -> str:
    if frame.empty:
        return "没有可展示记录；不以零替代未知指标。"
    lines = ["|" + "|".join(LABELS.get(c, c) for c in columns) + "|",
             "|" + "|".join("---" for _ in columns) + "|"]
    for _, row in frame.iterrows():
        cells = []
        for column in columns:
            value = row[column]
            if column == "arm":
                cells.append(f"{value}（{ARM_LABELS.get(value, value)}）")
            elif (column in ("p", "holm_p", "auc") or column.endswith("_p")) and pd.notna(value):
                cells.append(f"{float(value):.5g}")
            else:
                cells.append(_number(value, 0 if column in COUNT_COLUMNS or column in ("n", "top_n") else 2))
        lines.append("|" + "|".join(cells) + "|")
    return "\n".join(lines)


def _keys(arms: tuple[str, ...] = ARMS) -> set[tuple[str, int, str]]:
    return {(fold, minutes, arm) for fold in FOLDS for minutes in (60, 240) for arm in arms}


def _validate_frame(frame: pd.DataFrame, name: str, required: tuple[str, ...], *, coins: bool = False) -> None:
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")
    keys = ["fold", "minutes", "arm"] + (["symbol"] if coins else [])
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise ValueError(f"{name} has missing or duplicate identity keys")
    if not frame.fold.isin(FOLDS).all() or not frame.minutes.isin((60, 240)).all():
        raise ValueError(f"{name} has unknown fold/timeframe")
    if not frame.arm.isin((*ARMS, "buy_hold")).all():
        raise ValueError(f"{name} contains an unfrozen arm")
    actual = set(frame[["fold", "minutes", "arm"]].itertuples(index=False, name=None))
    if _keys() - actual:
        raise ValueError(f"{name} omits frozen results: {sorted(_keys() - actual)}")
    if coins:
        for key, group in frame.groupby(["fold", "minutes", "arm"], sort=False):
            if set(group.symbol) != set(SYMBOLS):
                raise ValueError(f"{name} does not retain all eight sleeves: {key}")


def _bool(series: pd.Series, name: str) -> pd.Series:
    translated = series.map({True: True, False: False, "True": True, "False": False,
                             "true": True, "false": False, 1: True, 0: False})
    if translated.isna().any():
        raise ValueError(f"events has missing/invalid boolean {name}")
    return translated.astype(bool)


def _event_counts(path: Path) -> dict[str, Counter]:
    """Count saved candidates in bounded memory without rerunning accounting."""
    counts = {name: Counter() for name in ("candidates", "trades", "natural_trades", "censored")}
    fields = ["fold", "minutes", "arm", "portfolio_selected", "natural_exit", "censored"]
    for chunk in pd.read_csv(path, usecols=fields, chunksize=100_000):
        if not chunk.fold.isin(FOLDS).all() or not chunk.minutes.isin((60, 240)).all() or not chunk.arm.isin(ARMS).all():
            raise ValueError("events has an unexpected identity")
        selected = _bool(chunk.portfolio_selected, "portfolio_selected")
        natural, censored = _bool(chunk.natural_exit, "natural_exit"), _bool(chunk.censored, "censored")
        if (selected & (natural == censored)).any():
            raise ValueError("selected events must be exactly one of natural exit or censored")
        identities = list(chunk[["fold", "minutes", "arm"]].itertuples(index=False, name=None))
        for name, mask in (("candidates", np.ones(len(chunk), dtype=bool)), ("trades", selected),
                           ("natural_trades", selected & natural), ("censored", selected & censored)):
            counts[name].update(key for key, keep in zip(identities, mask) if keep)
    return counts


def _drawdown(curve: pd.Series) -> pd.Series:
    return (curve / curve.cummax().clip(lower=1.0) - 1.0) * 100.0


def load_results(input_dir: Path) -> dict:
    """Validate saved input identities, complete tables, counts and curve metrics."""
    paths = {name: input_dir / name for name in INPUT_FILES}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    summary = pd.read_csv(paths["summary.csv"], float_precision="round_trip")
    per_coin = pd.read_csv(paths["per_coin.csv"], float_precision="round_trip")
    ranking = pd.read_csv(paths["ranking.csv"], float_precision="round_trip")
    manifest = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an identity object")
    _validate_frame(summary, "summary", SUMMARY_COLUMNS)
    _validate_frame(per_coin, "per_coin", ("fold", "minutes", "arm", "symbol", "net_pct", "mdd_pct", "trades", "win_pct", "pf"), coins=True)
    _validate_frame(ranking, "ranking", ("fold", "minutes", "arm"))
    for column in SUMMARY_COLUMNS[3:]:
        summary[column] = pd.to_numeric(summary[column], errors="raise")
    for column in COUNT_COLUMNS:
        values = summary.loc[summary.arm.ne("buy_hold"), column]
        if values.isna().any() or (values < 0).any() or (values % 1 != 0).any():
            raise ValueError(f"summary has invalid counts: {column}")
    counts = _event_counts(paths["events.csv.gz"])
    for row in summary.loc[summary.arm.ne("buy_hold")].itertuples(index=False):
        for name, counter in counts.items():
            if getattr(row, name) != counter[(row.fold, row.minutes, row.arm)]:
                raise ValueError(f"summary/ledger {name} mismatch: {row.fold}/{row.minutes}/{row.arm}")
    # This is a local runner-generated pickle, not an externally supplied payload.
    curves = pd.read_pickle(paths["portfolio_curves.pkl.gz"])
    if not isinstance(curves, dict):
        raise ValueError("portfolio_curves must be a dictionary of Series")
    expected = set(summary[["fold", "minutes", "arm"]].itertuples(index=False, name=None))
    if set(curves) != {f"{f}|{m}|{a}" for f, m, a in expected}:
        raise ValueError("curve keys and summary identities differ")
    for row in summary.itertuples(index=False):
        key = f"{row.fold}|{row.minutes}|{row.arm}"
        curve = curves[key]
        if not isinstance(curve, pd.Series) or not isinstance(curve.index, pd.DatetimeIndex) or curve.empty:
            raise ValueError(f"curve must be a nonempty datetime Series: {key}")
        if curve.index.tz is None or any(t.utcoffset().total_seconds() != 0 for t in curve.index):
            raise ValueError(f"curve must carry explicit UTC closes: {key}")
        if curve.index.hasnans or curve.index.has_duplicates or not curve.index.is_monotonic_increasing:
            raise ValueError(f"curve has invalid close clock: {key}")
        values = curve.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"curve must have finite nonnegative equity: {key}")
        np.testing.assert_allclose([(values[-1] - 1) * 100, -_drawdown(curve).min()],
                                   [row.net_pct, row.mdd_pct], rtol=1e-8, atol=1e-6,
                                   err_msg=f"summary/curve metrics differ: {key}")
    verification = input_dir / "verification.json"
    if verification.is_file():
        paths["verification.json"] = verification
    return dict(summary=summary, per_coin=per_coin, ranking=ranking, curves=curves,
                manifest=manifest, source_hashes={name: _sha(path) for name, path in paths.items()},
                verification=json.loads(verification.read_text(encoding="utf-8")) if verification.is_file() else None,
                ledger_counts={name: sum(counter.values()) for name, counter in counts.items()})


def plot_global(curves: dict[str, pd.Series], path: Path) -> None:
    """Eight panels: equity/drawdown, two clocks, validation/recent; no smoothing."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    # Two chromatic roots plus neutrals; line styles retain grayscale identity.
    styles = {
        "baseline": ("#30363d", "-"), "sequence": ("#2457a7", "-"),
        "sequence_wait": ("#2457a7", "--"), "baseline_3r": ("#a76324", ":"),
        "buy_hold": ("#7c838d", "-."),
    }
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "savefig.facecolor": "white"}):
        fig, axes = plt.subplots(4, 2, figsize=(16, 17), sharex="col")
        legend_handles = {}
        for column, fold in enumerate(("validation", "recent")):
            for level, minutes in enumerate((60, 240)):
                for arm, (color, linestyle) in styles.items():
                    key = f"{fold}|{minutes}|{arm}"
                    if key not in curves:
                        continue
                    series = curves[key]
                    for offset, data in enumerate((series, _drawdown(series))):
                        ax = axes[level * 2 + offset, column]
                        line, = ax.plot(data.index, data.values, color=color, linestyle=linestyle,
                                        linewidth=1.65, label=arm)
                        legend_handles.setdefault(arm, line)
                for offset in (0, 1):
                    ax = axes[level * 2 + offset, column]
                    metric = "Equity (initial cash = 1)" if offset == 0 else "Drawdown (%)"
                    ax.set_title(f"{minutes // 60}H | {'2025 validation' if fold == 'validation' else 'Recent history'} | {metric}", loc="left", pad=11)
                    ax.axhline(1 if offset == 0 else 0, color="#b8bdc4", linewidth=.7)
                    ax.grid(axis="y", color="#dfe3e8", linewidth=.6)
                    ax.tick_params(axis="x", labelbottom=True)
                    locator = mdates.AutoDateLocator(minticks=3, maxticks=5)
                    ax.xaxis.set_major_locator(locator)
                    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
                    if offset == 1:
                        ax.set_ylim(top=1)
        fig.suptitle("Frozen eight-coin long-only portfolios\n20 bp round-trip static fees; full bar-close paths; each fold starts in cash", x=.06, y=.995, ha="left", fontsize=16)
        fig.legend(list(legend_handles.values()), list(legend_handles), loc="upper center",
                   bbox_to_anchor=(.5, .953), ncol=5, frameon=False)
        fig.tight_layout(rect=(0, .015, 1, .925), h_pad=3.0, w_pad=3.0)
        fig.savefig(path, dpi=155)
        plt.close(fig)


def plot_cases(input_dir: Path) -> list[dict]:
    """Illustrate retrospective best/worst recent filled baseline; no new signals."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fields = ["fold", "minutes", "arm", "symbol", "event_id", "portfolio_selected", "valid",
              "natural_exit", "net_return", "net_r", "anchor_i", "entry_i", "exit_i",
              "entry_price", "exit_price", "initial_stop"]
    events = pd.read_csv(input_dir / "events.csv.gz", usecols=fields, float_precision="round_trip")
    mask = events.fold.eq("recent") & events.arm.eq("baseline")
    for name in ("portfolio_selected", "valid", "natural_exit"):
        mask &= _bool(events[name], name)
    recent = events.loc[mask]
    cases = []
    for minutes in (60, 240):
        group = recent.loc[recent.minutes.eq(minutes)].sort_values(["net_return", "event_id"], kind="stable")
        if group.empty:
            cases.append({"note": f"{minutes // 60}H recent baseline没有实际成交自然退出，不能生成事后最大/最小案例。"})
            continue
        for role, event in (("最大净收益", group.iloc[-1]), ("最小净收益", group.iloc[0])):
            symbol = event.symbol
            if symbol not in SYMBOLS:
                raise ValueError("Unknown case symbol")
            folder = input_dir / symbol
            bars = pd.read_pickle(folder / f"bars_{minutes}.pkl.gz")
            features = pd.read_pickle(folder / f"features_{minutes}.pkl.gz")
            if not bars.index.equals(features.index) or not {"md", "sb"}.issubset(features.columns):
                raise ValueError("Case bars/features identity or IMACD columns differ")
            anchor, entry, exit_i = (int(event[c]) for c in ("anchor_i", "entry_i", "exit_i"))
            if not 0 <= anchor < entry <= exit_i < len(bars):
                raise ValueError("Invalid case event indexes")
            first, last = max(0, anchor - 40), min(len(bars), exit_i + 21)
            b, f = bars.iloc[first:last], features.iloc[first:last]
            x = np.arange(len(b))
            with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                                 "axes.spines.top": False, "axes.spines.right": False}):
                fig, axes = plt.subplots(3, 1, figsize=(16, 11), sharex=True,
                                         gridspec_kw={"height_ratios": [3.5, 1, 1.6]})
                for i, candle in enumerate(b.itertuples()):
                    color = "#2457a7" if candle.close >= candle.open else "#a76324"
                    axes[0].vlines(i, candle.low, candle.high, color=color, linewidth=.8)
                    height = max(abs(candle.close - candle.open), abs(candle.close) * 1e-7)
                    axes[0].add_patch(Rectangle((i - .34, min(candle.open, candle.close)), .68, height,
                                               facecolor=color, edgecolor=color, linewidth=.5))
                for name, linestyle in (("sma20", "-"), ("ema20", "--"), ("sma60", "-"),
                                        ("ema60", "--"), ("sma120", "-"), ("ema120", "--")):
                    if name in f:
                        axes[0].plot(x, f[name], color="#7c838d", linestyle=linestyle, linewidth=.8, alpha=.65)
                axes[0].hlines(event.initial_stop, entry - first, exit_i - first,
                               color="#a76324", linestyle="--", linewidth=1.3, label="Initial 2 ATR stop")
                axes[0].scatter([entry - first], [event.entry_price], marker="^", s=100,
                                facecolor="white", edgecolor="#2457a7", linewidth=1.8, zorder=5, label="Next-open entry")
                axes[0].scatter([exit_i - first], [event.exit_price], marker="v", s=100,
                                facecolor="white", edgecolor="#30363d", linewidth=1.8, zorder=5, label="Accounted exit")
                axes[0].legend(loc="upper left", ncol=3, frameon=False)
                axes[0].set_ylabel("Price (USDT)")
                colors = np.where(b.close.ge(b.open), "#2457a7", "#a76324")
                axes[1].bar(x, b.volume, color=colors, width=.7)
                axes[1].set_ylabel("Volume")
                axes[2].plot(x, f.md, color="#2457a7", linewidth=1.45, label="IMACD md")
                axes[2].plot(x, f.sb, color="#a76324", linewidth=1.2, label="Signal sb")
                axes[2].axhline(0, color="#7c838d", linewidth=.7)
                axes[2].legend(loc="upper left", frameon=False, ncol=2)
                for ax in axes:
                    ax.axvline(anchor - first, color="#7c838d", linestyle=":", linewidth=1)
                    ax.axvspan(entry - first, exit_i - first, color="#2457a7", alpha=.04)
                    ax.grid(axis="y", color="#dfe3e8", linewidth=.6)
                    ax.set_xlim(-1, len(b))
                ticks = np.unique(np.linspace(0, len(b) - 1, min(7, len(b))).astype(int))
                axes[2].set_xticks(ticks, [b.index[i].strftime("%Y-%m-%d\n%H:%M") for i in ticks])
                axes[2].set_xlabel("Bar open time (UTC); dotted line = original release; shaded = held interval")
                role_id = "best" if role == "最大净收益" else "worst"
                fig.suptitle(f"{symbol} {minutes // 60}H | Recent baseline | Retrospective {role_id}\n"
                             f"Net {event.net_return * 100:+.2f}% | Net R {event.net_r:+.2f} | "
                             "Blue candles rise; orange candles fall; selected after outcomes", ha="left", x=.075, fontsize=15)
                fig.tight_layout(rect=(0, 0, 1, .93), h_pad=.8)
                path = input_dir / f"case_{minutes}_{role_id}.png"
                fig.savefig(path, dpi=145, facecolor="white")
                plt.close(fig)
            cases.append(dict(path=path, minutes=minutes, symbol=symbol, role=role,
                              event_id=event.event_id, net_return=event.net_return, net_r=event.net_r))
    return cases


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["_arm_order"] = out.arm.map({arm: i for i, arm in enumerate((*ARMS, "buy_hold"))})
    return out.sort_values("_arm_order", kind="stable")


def _headline(summary: pd.DataFrame) -> str:
    lines = []
    for fold in ("validation", "recent"):
        for minutes in (60, 240):
            group = summary.loc[summary.fold.eq(fold) & summary.minutes.eq(minutes)].set_index("arm")
            base = group.loc["baseline"]
            for arm in ("sequence", "sequence_wait"):
                row = group.loc[arm]
                delta = float(row.net_pct - base.net_pct)
                statistical = (pd.notna(row.holm_p) and row.holm_p < .01
                               and pd.notna(row.excess_bp) and row.excess_bp > 0)
                verdict = "匹配超额通过 Holm p<0.01" if statistical else "匹配超额未达到 Holm p<0.01 的正超额标准"
                lines.append(f"- {FOLD_LABELS[fold]} {minutes // 60}H 的 `{arm}`：静态净收益 {_number(row.net_pct)}%，"
                             f"相对 baseline {delta:+.2f} 个百分点，组合 MDD {_number(row.mdd_pct)}%，"
                             f"实际成交 {_number(row.trades, 0)}、自然退出 {_number(row.natural_trades, 0)}，"
                             f"正收益币 {_number(row.positive_coins, 0)}/8；{verdict}。")
    return "\n".join(lines)


def _link(label: str, path: Path) -> str:
    return f"[{label}]({quote(str(path.resolve()), safe='/')})"


def _reproduction(manifest: dict, input_dir: Path, report_path: Path, html_dir: Path) -> str:
    commands = manifest.get("reproduction_commands", manifest.get("run_command"))
    lines = []
    if isinstance(commands, str) and commands.strip():
        lines += ["账本生成命令来自输入 manifest：", "```bash", commands, "```"]
    elif isinstance(commands, list) and commands and all(isinstance(c, str) for c in commands):
        lines += ["账本生成命令来自输入 manifest：", "```bash", "\n".join(commands), "```"]
    else:
        runner = [str(ROOT / ".venv/bin/python"), "-m", "yoyo.evaluation.mainstream_research", "--out", str(input_dir)]
        lines += ["账本使用本仓冻结runner生成；复跑仅在源和builder指纹完全一致时恢复，否则使用新的明确输出目录：",
                  "```bash", "cd " + shlex.quote(str(ROOT)), shlex.join(runner), "```"]
    command = [sys.executable, "-m", "yoyo.evaluation.mainstream_report", "--input-dir", str(input_dir),
               "--report", str(report_path), "--html-dir", str(html_dir)]
    lines += ["先确认精确 builder 已提交，再运行：", "```bash", "cd " + shlex.quote(str(ROOT)),
              shlex.join(command), "```", "该命令生成 global.png 和 MD，随后立即调用 scripts/md_to_html.py；不重算收益。"]
    return "\n\n".join(lines)


def render_markdown(results: dict, input_dir: Path, report_path: Path, figure_path: Path,
                    html_dir: Path, commit: str, cases: list[dict] | None = None) -> str:
    """Generate claims from actual rows; never choose a winner or omit a fold."""
    summary, per_coin, ranking = (results[name] for name in ("summary", "per_coin", "ranking"))
    lines = ["# 主流币超级趋势：固定规则迁移回测", "",
             "本报告检验从 USELESS 复盘提出的固定启动候选能否迁移到八个主流币。"
             "下面的收益均为静态交易成本后净收益，不能等同包含实际资金费、点差与冲击的可执行实盘收益。",
             "## 先看实际结果", _headline(summary),
             "这些比较来自事先冻结的规则，没有按回测结果修改参数或选出新组合。"
             "正收益、低回撤、AUC 或单个显著 p 值均不能单独证明生产可用；零成交也不代表有效捕获趋势。"
             "大R可能部分来自很小的初始风险分母，不能等同账户收益；等待确认的追价成本见等待价格变化列。",
             "## 范围与指标口径",
             "固定 BTC、ETH、SOL、XRP、DOGE、ADA、LINK、AVAX，全部为多头。1H 和 4H 独立资金账本；"
             "初始资金100,000单位，每币固定12,500袖套，闲置保留现金。每折独立从现金开始，持仓不跨折。",
             "|折|UTC 左闭右开边界|\n|---|---|\n" + "\n".join(f"|{FOLD_LABELS[f]}|{FOLD_DATES[f]}|" for f in FOLDS),
             "组合净收益取八袖套逐bar收盘净值的平均曲线；组合 MDD 从该曲线及初始现金峰值计算，不平均单币MDD。"
             "候选数与单仓实际成交分别报告。自然退出胜率/PF/平均净R及≥3R、≥5R净R赢家不混入折末标记仓；"
             "折末标记仓仍计入组合净值。PF与前3盈利占比按实际入场/退出袖套权益差portfolio_net_pnl计算；八币起点相同，不将逐笔收益率相加冒充资金盈亏。"
             "不适用或不可估值保留为空值说明，不补成零；PF为∞表示样本内没有可用亏损分母，不代表无风险。",
             "经结构审计，测试区间为8×129,280=1,034,240根15m，含热身的完整来源共1,312,534根；八币无缺口、重复、无效OHLC或非有限值。"
             "2023年前完整4H为2,173—2,174根，满足550根预热；闭合时间和源SHA验证不等于历史confirm或当年first-seen字节可追溯。",
             "## 2025 与近期历史的净值及回撤",
             "下图固定展示 baseline、sequence、sequence_wait、baseline_3r 和存在的同期买入持有曲线。"
             "各面板保持真实收盘路径与起始现金1，不重标首个观测值、不平滑，不用低回撤替代成交与趋势捕获证据。",
             f"![八币组合净值与回撤]({os.path.relpath(figure_path, report_path.parent)})",
             "所有12臂的完整结果与其余时间折见后续表；图例精选不改变实验结果全集。"]
    for fold in FOLDS:
        lines += [f"## {FOLD_LABELS[fold]}：全部实验臂"]
        for minutes in (60, 240):
            group = _ordered(summary.loc[summary.fold.eq(fold) & summary.minutes.eq(minutes)])
            negative = group.loc[group.arm.isin(ARMS) & group.net_pct.lt(0), "arm"].tolist()
            inactive = group.loc[group.arm.isin(ARMS) & group.trades.eq(0), "arm"].tolist()
            lines += [f"### {minutes // 60}H", "固定顺序展示，未按收益排序。"
                      + (" 静态净收益为负：" + "、".join(negative) + "。" if negative else " 本折没有静态净收益为负的实验臂；需同时看成交数与匹配对照。")
                      + (" 零成交：" + "、".join(inactive) + "。" if inactive else ""),
                      _table(group, ["arm", "candidates", "trades", "natural_trades", "censored", "net_pct", "mdd_pct", "win_pct", "pf", "mean_net_r"]),
                      _table(group, ["arm", "win_3r", "win_5r", "top3_profit_share_pct", "positive_coins", "baseline_5r_retained", "baseline_5r_count"]),
                      _table(group, ["arm", "matched_n", "months", "excess_bp", "ci_low", "ci_high", "p", "holm_p"])]
            extra = [c for c in summary.columns if c not in SUMMARY_COLUMNS]
            if extra:
                lines += ["补充诊断：自然退出条件子集和等待代价另列；主匹配表含同边界折末仓。", _table(group, ["arm", *extra])]
    lines += ["## 逐币拆解：收益是否依赖少数赢家",
              "下面固定展开2025与近期历史的 baseline、sequence、sequence_wait。"
              "逐币净收益及MDD属于独立袖套；组合结论仍以组合曲线为准。其他臂与时间折均保留在完整 per_coin.csv。"]
    for fold in ("validation", "recent"):
        for minutes in (60, 240):
            for arm in ("baseline", "sequence", "sequence_wait"):
                group = per_coin.loc[per_coin.fold.eq(fold) & per_coin.minutes.eq(minutes) & per_coin.arm.eq(arm)].set_index("symbol").loc[list(SYMBOLS)].reset_index()
                positive = int(group.net_pct.gt(0).sum())
                lines += [f"### {FOLD_LABELS[fold]} {minutes // 60}H · {arm}",
                          f"八个固定袖套中 {positive}/8 静态净收益为正；以下按预定币序显示，亏损与无成交袖套均保留。",
                          _table(group, ["symbol", "net_pct", "mdd_pct", "trades", "win_pct", "pf"])]
    lines += ["## 事后案例：实际赢家与实际输家",
              "每周期从recent baseline实际成交、valid且自然退出事件，固定选net_return最高与最低各一例。"
              "这是事后描述性选择，用于核对形态与会计，不是独立证据抽样或新规则选择。图中价格/IMACD来自已存bars/features。"]
    for case in cases or []:
        if "path" not in case:
            lines += [case["note"]]
            continue
        lines += [f"### {case['minutes'] // 60}H · {case['symbol']} · {case['role']}",
                  f"event_id：`{case['event_id']}`；静态净收益 {_number(case['net_return'] * 100)}%，净R {_number(case['net_r'])}。"
                  "绘图不重新模拟触损顺序，精确会计以原账本及执行审计为准。",
                  f"![实际成交案例]({os.path.relpath(case['path'], report_path.parent)})"]
    lines += ["## 单特征排序与零假设对照",
              "固定 relative_volume 为连续事前质量分数，按所有valid且自然退出候选评价 AUC 和 top-decile 毛/净收益、胜率及匹配超额。这里不限portfolio_selected，是候选排序诊断，不是实际成交Top10%组合。"
              "AUC只诊断排序，不能代替扣0.2%成本后的正净收益与置换检验。候选过滤的增量必须同时对照原始 baseline 与匹配随机入场。"
              "主匹配诊断纳入全部实际选中valid事件（含同折末censored），自然退出条件子集以natural_only列另存。原始 p 与 Holm p 来自 runner，报告不重抽样、不改检验家族；CI与月份数一起显示，月份少或匹配不足时结论受限。"]
    for fold in FOLDS:
        for minutes in (60, 240):
            group = _ordered(ranking.loc[ranking.fold.eq(fold) & ranking.minutes.eq(minutes)])
            columns = ["arm"] + [c for c in ranking.columns if c not in ("arm", "fold", "minutes")]
            lines += [f"### {FOLD_LABELS[fold]} {minutes // 60}H · 固定单特征诊断", _table(group, columns)]
    lines += ["## 冻结规则与统一执行模型",
              "|实验臂|原释放时的资格或等待规则|退出|\n|---|---|---|\n" + "\n".join(f"|{arm}|{rule}|{exit_rule}|" for arm, rule, exit_rule in ARM_RULES),
              "原型参数34/9、focus12、冻结0.10ATR与六线SMA/EMA20/60/120沿用既定版本；基础周期至少550根热身。"
              "1H采用15m先行与4H背景，4H采用1H先行与UTC日线背景。高周期 source_close 必须≤基础收盘；双方热身不足不默认为通过。",
              "动能为(close−SMA50)/最近50根绝对乖离最大值×100，强动能阈值90。量比的前20根中位数不含本根。"
              "形成期记忆只读近零段已闭合bar至释放本根。低周期多头段在反向释放、md≤0或原箱底失守时失效。",
              "原始/确认收盘后下一根真实open入场，以实际entry−2×实际决策bar ATR定初损。"
              "md≤0后下一open退出，初损持续有效；同bar先止损，跳空使用更差open。等待臂保留原释放资格，"
              "在段失效前等待首次高周期许可，实际入场时钟与ATR移到真实更晚决策。",
              "md臂没有固定TP，max_hold_days=36500；实际历史窗口由折末截断。折末仓按最后收盘标记、扣退出费并记censored。"
              "baseline_3r作为固定3R退出对照。未实现Pine额外的原冻结价格箱失守退出，不能宣称逐笔复刻Pine或手动持仓。",
              "每币单仓，入场名义额等于当时袖套权益，随后数量固定；并非持仓期间实时名义额/权益始终≤1x。"
              "同实际确认时刻多个anchor最多形成一次实际入场；其他候选不重复计算成交。",
              "每笔固定往返0.2%：每侧按entry-notional扣10bp。匹配随机对照同币、同周期、实际decision_close所属月、"
              "事前ATR百分比分位五桶，每案例至多3个、不复用，固定seed20260910；对照md>0，排除所有臂实际decision并集及原release。"
              "过滤臂共用baseline时点匹配映射，等待臂按实际晚到时点匹配。对照与案例用相同障碍、退出、费用和折末规则。"
              "按自然月分块置换/重采样；每折两周期的所有非baseline臂属于共同Holm家族。",
              "完整预注册口径：" + _link("PROJECT_PLAN.md", PLAN),
              "## 风险与诚实声明",
              "- 这是观察USELESS之后提出候选的历史迁移评价。相关市场历史已被旧研究读取，不能称为完全未见样本外或新的盲测。",
              f"- Owner已明确批准本轮历史读取；这是该配置第{results['manifest'].get('holdout_consumption', '未知')}次消耗holdout（2026-05-04起）。首跑保留，固定规则会计核验属于再次评价，不是调参或新的盲测。",
              "- 固定往返0.2%是研究假设，不是某账户实际佣金。长期actual funding、点差与冲击未完整覆盖；不得将缺失资金费填零后称完整净收益。资金费与交易费分开，参见[OKX资金费说明](https://www.okx.com/en-us/help/funding-fees-for-perpetual-contracts-faq)。",
              "- 当前存续的八个主流币构成固定研究池，存在存续与对象选择偏差；纯多头共同市场beta必须由同期买入持有及匹配随机对照共同观察。",
              "- 零交易、少月份、少匹配案例、少数大赢家或只有单币盈利均削弱迁移证据；前3盈利依赖与≥5R赢家保留用于暴露此问题。",
              "- md退出相对旧30天研究边界改为36500天，且未包含Pine原箱退出；本轮各臂可公平横比，不等于原回测逐笔复现。",
              "- YOLO不在此主表。八币多年原生3m/5m覆盖不完整，不能用15m插值伪造小周期，也不能称本结果为YOLO联合回测。",
              "- 历史15m源缺少保留的confirm字段，闭合时间检查不等于历史逐根confirm=1已证；现时修订佐证也不等于历史first-seen证明。",
              "- 本builder只校验已存账本计数及净值/MDD一致性，不重放特征、成交、对照选择、置换或数据获取，不取代runner与因果测试。",
              "## 下一步与需Owner决定的事项",
              "先按上表辨别跨2025和近期两折是否同向、是否仍有足够自然成交、≥5R赢家及匹配正超额；"
              "若不足，应记录当前候选未获支持，保留全部不利结果，不靠改阈值或延长窗口补正。"
              "若结果支持候选，也仅形成进一步独立检验的理由，应继续保留既有研究授权和固定实验纪律。"
              "部署、promote、线上信号、真实下单另需明确授权。",
              "## 账本、身份与复现",
              "已保存输入的SHA固定如下；所有候选、未成交记录、边界仓和不利结果均可回查。",
              "|输入|SHA256|\n|---|---|\n" + "\n".join(f"|{_link(name, input_dir / name)}|`{digest}`|" for name, digest in results["source_hashes"].items()),
              f"渲染时HEAD：`{commit}`；report builder SHA256：`{_sha(BUILDER)}`；预注册计划SHA256：`{_sha(PLAN)}`。",
              "事件账本总计：" + "，".join(f"{LABELS[name]} {value:,}" for name, value in results["ledger_counts"].items()) + "。各臂候选可能共享底层市场事件，不能把跨臂总数当独立样本数。",
              _reproduction(results["manifest"], input_dir, report_path, html_dir),
              "输入manifest完整身份（原样保留，不推断缺失授权或来源）：",
              "```json", json.dumps(results["manifest"], ensure_ascii=False, indent=2), "```", ""]
    if results.get("verification"):
        check = results["verification"]
        lines += ["## 独立会计核验记录",
                  f"保存的核验状态：`{check.get('status', '未记录')}`；独立重放候选 "
                  f"{_number(check.get('independently_replayed_candidates'), 0)}，非空袖套对账 "
                  f"{_number(check.get('reconciled_nonempty_sleeves'), 0)}。状态来自已存verification.json，不是报告builder再次重放所得。",
                  "```json", json.dumps(check, ensure_ascii=False, indent=2), "```"]
    return "\n\n".join(lines)


def build_report(input_dir: Path = DEFAULT_INPUT, report_path: Path = DEFAULT_REPORT,
                 html_dir: Path = ROOT / "analysis/html") -> dict[str, object]:
    """Write one PNG, MD and immediately converted HTML after input validation."""
    commit = _require_committed()
    input_dir, report_path, html_dir = input_dir.resolve(), report_path.resolve(), html_dir.resolve()
    results = load_results(input_dir)
    figure_path = input_dir / "global.png"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    plot_global(results["curves"], figure_path)
    cases = plot_cases(input_dir)
    text = render_markdown(results, input_dir, report_path, figure_path, html_dir, commit, cases)
    report_path.write_text(text, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts/md_to_html.py"), str(report_path),
                    "--out-dir", str(html_dir)], cwd=ROOT, check=True)
    html_path = html_dir / (report_path.stem + ".html")
    if not html_path.is_file():
        raise RuntimeError("Markdown converter did not create the requested HTML")
    return {"markdown": str(report_path), "html": str(html_path), "figure": str(figure_path),
            "case_figures": [str(case["path"]) for case in cases if "path" in case]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", "--out", dest="input_dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--html-dir", type=Path, default=ROOT / "analysis/html")
    args = parser.parse_args(argv)
    print(json.dumps(build_report(args.input_dir, args.report, args.html_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

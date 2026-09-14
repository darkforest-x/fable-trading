#!/usr/bin/env python3
"""Render the frozen ETH multi-timeframe replay receipts into the owner report.

This renderer deliberately consumes only JSON/CSV artifacts under this
experiment's ``results`` directory.  It never imports a replay engine, reads
market data, or selects an arm: ``selection.json`` is the sole configuration
authority.  The fail-closed inputs make a partially completed validation
impossible to present as a final report.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import math
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-release-eth-multitf-20260914-v1"
RESULTS = EXPERIMENT / "results"
DEVELOPMENT = RESULTS / "development"
VALIDATION = RESULTS / "validation"
SELECTION_PATH = RESULTS / "selection.json"
REPORT = PROJECT / "analysis/p1_release_eth_multitf_20260914.md"
HTML_DIR = PROJECT / "analysis/html"
FIGURES = RESULTS / "report"
NARRATIVE = EXPERIMENT / "REPORT_NARRATIVE.md"
TIMEFRAMES = (15, 60, 240)


def _load_json(path: Path) -> Any:
    """Load a required frozen receipt with a useful error when it is absent."""

    if not path.is_file():
        raise RuntimeError(f"required frozen receipt is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON receipt: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_summaries(payload: Any, path: Path) -> list[dict[str, Any]]:
    """Accept only the runner's list receipt (or its explicit list wrapper)."""

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping) and isinstance(payload.get("summaries"), list):
        rows = payload["summaries"]
    else:
        raise RuntimeError(f"{path} must contain a list of summary objects")
    if not rows or not all(isinstance(row, Mapping) for row in rows):
        raise RuntimeError(f"{path} has no usable summary objects")
    summaries = [dict(row) for row in rows]
    required = {"key", "minutes", "period", "arm", "stats", "inference", "breakdowns"}
    for row in summaries:
        missing = sorted(required - set(row))
        if missing:
            raise RuntimeError(f"summary {row.get('key', '<unknown>')} missing {missing}")
    return summaries


def _get(value: Mapping[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _number(value: Any, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "不可用"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(numeric):
        return "不可用"
    return f"{numeric:+.{digits}f}" if signed else f"{numeric:.{digits}f}"


def _pct(value: Any, digits: int = 2, signed: bool = True) -> str:
    suffix = _number(value, digits, signed)
    return suffix if suffix == "不可用" else f"{suffix}%"


def _bp(value: Any) -> str:
    suffix = _number(value, 2, True)
    return suffix if suffix == "不可用" else f"{suffix} bp"


def _count(value: Any) -> str:
    if value is None:
        return "不可用"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _cell(value: Any) -> str:
    return str(value).replace("|", "／").replace("\n", " ")


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    output = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    output.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def _summary_index(summaries: Iterable[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    indexed: dict[tuple, dict[str, Any]] = {}
    for summary in summaries:
        key = (int(summary["minutes"]), str(summary["period"]), str(summary["arm"]), summary.get("precision", "parent_ohlc"))
        if key in indexed:
            raise RuntimeError(f"duplicate summary receipt for {key}")
        indexed[key] = summary
    return indexed


def _find_summary(
    summaries: Iterable[dict[str, Any]], minutes: int, period: str, arm: str
) -> dict[str, Any] | None:
    for summary in summaries:
        if (int(summary["minutes"]) == minutes and str(summary["period"]) == period
                and str(summary["arm"]) == arm and summary.get("precision", "parent_ohlc") == "parent_ohlc"):
            return summary
    return None


def _require_summary(
    summaries: Iterable[dict[str, Any]], minutes: int, period: str, arm: str
) -> dict[str, Any]:
    summary = _find_summary(summaries, minutes, period, arm)
    if summary is None:
        raise RuntimeError(f"missing required summary: {minutes}m {period} {arm}")
    return summary


def _receipt_rows(receipt: Any) -> list[tuple[str, Any]]:
    """Flatten a receipt without guessing values that the runner did not save."""

    if not isinstance(receipt, Mapping):
        return [("source receipt", "不可用")]
    fields = (
        ("source_path", "来源路径"),
        ("rows", "消费行数"),
        ("first_open", "首根"),
        ("end_exclusive", "截止（exclusive）"),
        ("consumed_prefix_sha256", "消费前缀 SHA256"),
        ("holdout_consumed", "holdout consumed"),
        ("gaps", "缺口"),
        ("duplicates", "重复"),
    )
    return [(label, receipt.get(key, "不可用")) for key, label in fields]


def _summary_row(summary: dict[str, Any], label: str | None = None) -> list[str]:
    stats = summary["stats"]
    return [
        label or str(summary["arm"]),
        _pct(stats.get("return_pct")),
        _pct(stats.get("marked_return_pct")),
        _pct(stats.get("path_dd_pct"), signed=False),
        _count(stats.get("trades")),
        _bp(stats.get("gross_bp")),
        _bp(stats.get("net_bp")),
        _bp(stats.get("matched_case_bp")),
        _bp(stats.get("control_bp")),
        _bp(stats.get("excess_bp")),
        _count(stats.get("matched_n")),
    ]


def _direction_rows(summary: dict[str, Any], breakdown: str) -> list[list[str]]:
    values = _get(summary, "breakdowns", breakdown, default={})
    if not isinstance(values, Mapping) or not values:
        return [["不可用", "不可用", "不可用", "不可用", "不可用", "不可用", "不可用", "不可用", "不可用", "不可用", "不可用", "不可用"]]
    rows: list[list[str]] = []
    for name, stats in sorted(values.items()):
        if not isinstance(stats, Mapping):
            continue
        rows.append([
            str(name).upper(), _count(stats.get("trades")), _pct(stats.get("win_rate_pct")),
            _bp(stats.get("net_bp")), _bp(stats.get("matched_case_bp")),
            _bp(stats.get("control_bp")), _bp(stats.get("excess_bp")),
            _count(stats.get("matched_n")), _pct(stats.get("matched_coverage_pct")),
            _number(stats.get("pf_currency"), 3), _number(stats.get("pf_unit"), 3),
            _number(stats.get("holding_hours_mean"), 2),
        ])
    return rows or [["不可用"] * 12]


def _breakdown_detail_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    breakdowns = _get(summary, "breakdowns", default={})
    for group in ("side", "year"):
        values = breakdowns.get(group, {}) if isinstance(breakdowns, Mapping) else {}
        if not isinstance(values, Mapping):
            continue
        for name, stats in sorted(values.items()):
            if not isinstance(stats, Mapping):
                continue
            rows.append([
                group, str(name).upper(), _count(stats.get("trades")), _bp(stats.get("net_bp")),
                _bp(stats.get("matched_case_bp")), _bp(stats.get("control_bp")),
                _bp(stats.get("excess_bp")), _count(stats.get("matched_n")),
                _pct(stats.get("top1_positive_pnl_share_pct"), signed=False),
                _pct(stats.get("top5_positive_pnl_share_pct"), signed=False),
                _bp(stats.get("net_bp_excluding_top1")),
                _count(stats.get("mfe_1r_count")), _count(stats.get("realized_1r_count")),
                _count(stats.get("mfe_3r_count")), _count(stats.get("realized_3r_count")),
                _count(stats.get("mfe_10r_count")), _count(stats.get("realized_10r_count")),
                _number(stats.get("holding_hours_mean"), 2), _number(stats.get("holding_hours_median"), 2),
            ])
    return rows or [["不可用"] * 19]


def _equity_path(summary: dict[str, Any], phase_dir: Path) -> Path:
    path = phase_dir / f"{summary['key']}_equity.csv.gz"
    if not path.is_file():
        raise RuntimeError(f"missing required continuous equity CSV: {path}")
    return path


def _plot_equities(
    continuous: list[dict[str, Any]], selection: Mapping[str, Any]
) -> list[Path]:
    """Draw descriptive continuous-account curves from saved equity CSVs only."""

    FIGURES.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    choices = selection["selection"]
    arms = (("O0 original zero cost", "O0"), ("O1 cost", "O1"),
            ("E1 unit notional", "E1"), ("Selected", None), ("SMA", "SMA"))
    colors = ("#68758a", "#bc6c25", "#2a9d8f", "#7b2cbf", "#457b9d")
    for minutes in TIMEFRAMES:
        selected = str(_get(choices, str(minutes), "selected", default=""))
        if not selected:
            raise RuntimeError(f"selection receipt omits {minutes}m selected arm")
        fig, axis = plt.subplots(figsize=(10, 4.8), constrained_layout=True)
        all_positive = True
        series: list[tuple[str, pd.DataFrame, str]] = []
        if len(arms) != len(colors):
            raise RuntimeError("plot labels/colors differ")
        for (label, arm), color in zip(arms, colors):
            active_arm = selected if arm is None else arm
            summary = _require_summary(continuous, minutes, "continuous", active_arm)
            frame = pd.read_csv(_equity_path(summary, VALIDATION), parse_dates=["time"])
            if not {"time", "equity"}.issubset(frame.columns) or frame.empty:
                raise RuntimeError(f"invalid equity CSV for {summary['key']}")
            equity = pd.to_numeric(frame["equity"], errors="coerce")
            if equity.isna().any() or float(equity.iloc[0]) <= 0.0:
                raise RuntimeError(f"invalid equity values for {summary['key']}")
            all_positive = all_positive and bool(equity.gt(0.0).all())
            frame = frame.assign(normalized=equity / float(equity.iloc[0]))
            series.append((f"{label} ({active_arm})", frame, color))
        for label, frame, color in series:
            axis.plot(frame["time"], frame["normalized"], label=label, linewidth=1.3, color=color)
        if all_positive:
            axis.set_yscale("log")
        axis.set_title(f"{minutes}m continuous account equity (normalized)")
        axis.set_xlabel("UTC")
        axis.set_ylabel("Equity / initial equity")
        axis.grid(alpha=0.25, which="both")
        axis.legend(fontsize=8, ncol=2)
        output = FIGURES / f"continuous_equity_{minutes}m.png"
        fig.savefig(output, dpi=160)
        plt.close(fig)
        outputs.append(output)
    return outputs


def _plot_validation_heatmap(
    validation: list[dict[str, Any]], selection: Mapping[str, Any]
) -> Path:
    """Draw selected 2025 month returns from runner-produced monthly CSVs."""

    rows: list[np.ndarray] = []
    choices = selection["selection"]
    for minutes in TIMEFRAMES:
        selected = str(_get(choices, str(minutes), "selected", default=""))
        summary = _require_summary(validation, minutes, "validation2025", selected)
        path = VALIDATION / f"{summary['key']}_months.csv"
        if not path.is_file():
            raise RuntimeError(f"missing required validation months CSV: {path}")
        months = pd.read_csv(path)
        if not {"month", "return_pct"}.issubset(months.columns):
            raise RuntimeError(f"invalid monthly CSV: {path}")
        parsed = pd.to_datetime(months["month"], errors="coerce")
        if parsed.isna().any() or not parsed.dt.year.eq(2025).all():
            raise RuntimeError(f"validation months must be exactly 2025: {path}")
        ordered = months.assign(month_number=parsed.dt.month).sort_values("month_number")
        if ordered["month_number"].tolist() != list(range(1, 13)):
            raise RuntimeError(f"validation monthly receipt is incomplete: {path}")
        rows.append(pd.to_numeric(ordered["return_pct"], errors="raise").to_numpy(dtype=float))
    array = np.vstack(rows)
    bound = max(1.0, float(np.nanmax(np.abs(array))))
    fig, axis = plt.subplots(figsize=(10.5, 3.5), constrained_layout=True)
    image = axis.imshow(array, cmap="RdYlGn", vmin=-bound, vmax=bound, aspect="auto")
    axis.set_title("Frozen candidates:2025 monthly return (fees included)")
    axis.set_xlabel("Month")
    axis.set_ylabel("Timeframe")
    axis.set_xticks(np.arange(12), [str(month) for month in range(1, 13)])
    axis.set_yticks(np.arange(3), ["15m", "1h", "4h"])
    for row in range(array.shape[0]):
        for column in range(array.shape[1]):
            axis.text(column, row, f"{array[row, column]:+.1f}%", ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=axis, label="Account return %")
    output = FIGURES / "validation_2025_monthly_heatmap.png"
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output


def _selection_rows(selection: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    choices = selection["selection"]
    for minutes in TIMEFRAMES:
        record = _get(choices, str(minutes), default={})
        checks = record.get("checks", {}) if isinstance(record, Mapping) else {}
        for arm, conditions in sorted(checks.items()):
            if not isinstance(conditions, Mapping):
                rows.append([f"{minutes}m", str(arm), "不可用", "不可用",
                             str(record.get("selected", "不可用")),
                             "是" if bool(record.get("challenger_admitted")) else "否"])
                continue
            for name, passed in sorted(conditions.items()):
                rows.append([
                    f"{minutes}m", str(arm), str(name), "通过" if bool(passed) else "未通过",
                    str(record.get("selected", "不可用")),
                    "是" if bool(record.get("challenger_admitted")) else "否",
                ])
    return rows or [["不可用"] * 6]


def _inference_rows(
    validation: list[dict[str, Any]], selection: Mapping[str, Any], family: list[dict[str, Any]]
) -> list[list[str]]:
    family_by_key = {(int(row["minutes"]), str(row["arm"])): row for row in family}
    rows: list[list[str]] = []
    for minutes in TIMEFRAMES:
        selected = str(_get(selection, "selection", str(minutes), "selected", default=""))
        summary = _require_summary(validation, minutes, "validation2025", selected)
        inference = summary["inference"]
        family_row = family_by_key.get((minutes, selected), {})
        rows.append([
            f"{minutes}m", selected, _number(inference.get("auc"), 4),
            _count(inference.get("top_decile_n")), _bp(inference.get("top_decile_gross_bp")),
            _bp(inference.get("top_decile_net_bp")), _bp(inference.get("top_decile_matched_excess_bp")),
            _bp(inference.get("top_decile_matched_case_bp")), _bp(inference.get("top_decile_control_bp")),
            _count(inference.get("top_decile_matched_n")),
            _number(inference.get("ranking_p"), 6), _number(inference.get("matched_p"), 6),
            f"[{_bp(inference.get('excess_ci95_lower_bp'))}, {_bp(inference.get('excess_ci95_upper_bp'))}]",
            _number(family_row.get("holm_p"), 6), str(family_row.get("threshold", "不可用")),
        ])
    return rows


def _require_selection(selection: Any) -> Mapping[str, Any]:
    if not isinstance(selection, Mapping):
        raise RuntimeError("selection receipt must be an object")
    choices = selection.get("selection")
    if not isinstance(choices, Mapping):
        raise RuntimeError("selection receipt omits selection mapping")
    for minutes in TIMEFRAMES:
        item = choices.get(str(minutes))
        if not isinstance(item, Mapping) or not item.get("selected") or not isinstance(item.get("checks"), Mapping):
            raise RuntimeError(f"selection receipt is incomplete for {minutes}m")
    return selection


def _require_family(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise RuntimeError("validation_family.json must contain a list")
    rows = [dict(row) for row in payload if isinstance(row, Mapping)]
    required = {"minutes", "arm", "matched_p", "holm_p", "threshold", "positive_net_and_excess"}
    if len(rows) != len(payload) or any(required - set(row) for row in rows):
        raise RuntimeError("validation_family.json has incomplete rows")
    return rows


def _narrative() -> str:
    """Include the owner-authored conclusion without making it a frozen input."""

    if not NARRATIVE.is_file():
        return "> 主叙事占位：冻结结果完成后由主负责人写入 `REPORT_NARRATIVE.md`，再运行本脚本。"
    text = NARRATIVE.read_text(encoding="utf-8").strip()
    return text or "> 主叙事占位：`REPORT_NARRATIVE.md` 当前为空。"


def main() -> int:
    """Create Markdown, figures and self-contained HTML after both phases freeze."""

    global RESULTS, DEVELOPMENT, VALIDATION, SELECTION_PATH, FIGURES, REPORT, HTML_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    RESULTS, REPORT = args.results.resolve(), args.report.resolve()
    DEVELOPMENT, VALIDATION = RESULTS / "development", RESULTS / "validation"
    SELECTION_PATH, FIGURES = RESULTS / "selection.json", RESULTS / "report"
    HTML_DIR = REPORT.parent / "html"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    development_path = DEVELOPMENT / "all_summaries.json"
    validation_path = VALIDATION / "all_summaries.json"
    family_path = VALIDATION / "validation_family.json"
    development_receipt_path = DEVELOPMENT / "source_receipt.json"
    validation_receipt_path = VALIDATION / "source_receipt.json"
    development = _as_summaries(_load_json(development_path), development_path)
    validation = _as_summaries(_load_json(validation_path), validation_path)
    selection = _require_selection(_load_json(SELECTION_PATH))
    family = _require_family(_load_json(family_path))
    development_receipt = _load_json(development_receipt_path)
    validation_receipt = _load_json(validation_receipt_path)
    _summary_index(development)
    _summary_index(validation)

    # Require the reporting contract before writing any partial owner artifact.
    for minutes in TIMEFRAMES:
        selected = str(_get(selection, "selection", str(minutes), "selected"))
        for arm in ("O0", "O1", "E1", "E2", "E3", "E4", "E5", "SMA"):
            _require_summary(development, minutes, "development", arm)
        _require_summary(development, minutes, "development", "O1_immediate")
        for period in ("dev2023", "dev2024"):
            for arm in ("C0", "C1", "C2", "C3", "C4", "C5", "SMA"):
                _require_summary(development, minutes, period, arm)
        for period in ("validation2025", "later2026"):
            for arm in ("O1", selected, "SMA"):
                _require_summary(validation, minutes, period, arm)
        for arm in dict.fromkeys(("O0", "O1", "E1", "O1_immediate", "C0", selected, "SMA")):
            _require_summary(validation, minutes, "continuous", arm)
        if not any(int(row["minutes"]) == minutes and str(row["arm"]) == selected for row in family):
            raise RuntimeError(f"validation family lacks selected {minutes}m {selected}")

    figures = _plot_equities(validation, selection)
    heatmap = _plot_validation_heatmap(validation, selection)
    frozen_source_receipt = selection.get("source_receipt", {})
    if not isinstance(frozen_source_receipt, Mapping) or not isinstance(development_receipt, Mapping):
        raise RuntimeError("source receipts must be JSON objects")
    for field in ("consumed_prefix_sha256", "rows", "first_open", "last_close", "end_exclusive"):
        if development_receipt.get(field) != frozen_source_receipt.get(field):
            raise RuntimeError(f"development source receipt differs from frozen selection: {field}")
    if not isinstance(validation_receipt, Mapping) or validation_receipt.get("holdout_consumed") is not False:
        raise RuntimeError("validation source receipt is absent or consumed holdout")
    builder_sha = selection.get("builder_sha256", "不可用")
    builder_commit = selection.get("builder_commit", "不可用")
    narrative = _narrative()
    precision_rows = []
    for r in validation:
        if r.get("precision") == "15m":
            base = _require_summary(validation, r["minutes"], r["period"], r["arm"])
            precision_rows.append([str(r["minutes"])+"m", r["arm"], _pct(base["stats"]["return_pct"]),
                                   *_summary_row(r)[1:]])
    precision_table = _table(["父周期", "臂", "父OHLC账户收益", "15m精度账户收益", "标记收益", "路径DD", "交易", "毛bp", "净bp", "匹配case bp", "匹配control bp", "超额bp", "匹配n"], precision_rows)
    operating_rows = []
    for m in TIMEFRAMES:
        selected = str(_get(selection, "selection", str(m), "selected"))
        for arm in ("C0", selected):
            s = _require_summary(validation, m, "continuous", arm)["stats"]
            operating_rows.append([str(m)+"m", arm, _count(s["trades"]), _pct(s["gross_win_rate_pct"]),
                _pct(s["win_rate_pct"]), _number(s["pf_currency"],3), _number(s["pf_unit"],3), _number(s["fees"]),
                _pct(s["exposure_pct"]), _count(s["gross_winners_turned_net_losers"]), _number(s["holding_hours_mean"]),
                _count(s["boundary_exits"]), _bp(s["matched_case_bp"]), _bp(s["control_bp"]), _bp(s["excess_bp"]), _count(s["matched_n"])])
    operating_table = _table(["周期", "臂", "交易", "毛胜率", "扣费胜率", "货币PF", "单位PF", "手续费USDT", "在场时间比例", "毛赢转净亏笔数", "平均持有h", "行政退出数", "匹配case bp", "匹配control bp", "超额bp", "匹配n"], operating_rows)

    continuous_rows: list[list[str]] = []
    for minutes in TIMEFRAMES:
        selected = str(_get(selection, "selection", str(minutes), "selected"))
        for label, arm in (("O0 original / zero cost", "O0"), ("O1 cost", "O1"),
                           ("E1 unit notional", "E1"), ("O1 immediate-stop diagnostic", "O1_immediate"),
                           ("C0 baseline", "C0"), ("Selected", selected), ("SMA", "SMA")):
            continuous_rows.append([f"{minutes}m {label} ({arm})", *_summary_row(
                _require_summary(validation, minutes, "continuous", arm), arm
            )[1:]])

    engineering_rows: list[list[str]] = []
    for minutes in TIMEFRAMES:
        for arm in ("O0", "O1", "O1_immediate", "E1", "E2", "E3", "E4", "E5", "SMA"):
            engineering_rows.append([f"{minutes}m", *_summary_row(
                _require_summary(development, minutes, "development", arm), arm
            )])

    candidate_rows: list[list[str]] = []
    for minutes in TIMEFRAMES:
        for period in ("dev2023", "dev2024"):
            for arm in ("C0", "C1", "C2", "C3", "C4", "C5", "SMA"):
                candidate_rows.append([f"{minutes}m", period, *_summary_row(
                    _require_summary(development, minutes, period, arm), arm
                )])

    validation_rows: list[list[str]] = []
    for minutes in TIMEFRAMES:
        selected = str(_get(selection, "selection", str(minutes), "selected"))
        for period in ("validation2025", "later2026"):
            for label, arm in (("O1 cost", "O1"), ("C0 execution baseline", "C0"), ("Selected", selected), ("SMA", "SMA")):
                validation_rows.append([f"{minutes}m", period, label, *_summary_row(
                    _require_summary(validation, minutes, period, arm), arm
                )[1:]])

    selected_breakdowns: list[str] = []
    selected_details: list[list[str]] = []
    for minutes in TIMEFRAMES:
        arm = str(_get(selection, "selection", str(minutes), "selected"))
        summary = _require_summary(validation, minutes, "continuous", arm)
        selected_breakdowns.extend([
            f"### {minutes}m selected {arm} — {summary['period']}",
            "",
            _table(
                ["分组", "交易", "净胜率", "净单位bp（全样本）", "匹配case bp", "匹配control bp", "超额bp", "匹配n", "匹配覆盖", "货币PF", "单位PF", "平均持有小时"],
                _direction_rows(summary, "side"),
            ),
            "",
        ])
        selected_details.extend([[f"{minutes}m", *row] for row in _breakdown_detail_rows(summary)])

    receipt_table = _table(
        ["阶段", "字段", "冻结值"],
        [["development", field, value] for field, value in _receipt_rows(development_receipt)]
        + [["validation", field, value] for field, value in _receipt_rows(validation_receipt)],
    )
    receipt_hashes = _table(
        ["冻结输入", "SHA256"],
        [
            ("development/all_summaries.json", _sha256(development_path)),
            ("validation/all_summaries.json", _sha256(validation_path)),
            ("selection.json", _sha256(SELECTION_PATH)),
            ("validation/validation_family.json", _sha256(family_path)),
        ],
    )
    figure_links = "\n".join(
        f"![{path.stem}]({path})"
        for path in (*figures, heatmap)
    )
    report = f"""# P1 ETH multi-timeframe release replay — 2026-09-14

## 数据、范围与不可外推边界

本轮账本初始账户为 **500 USDT**；每次成交费率为 **0.1% notional**，开/平各一次；期末行政结算若发生，按退出费处理。`账户收益%` 是连续复利账户权益变化，`单位bp` 是每笔等权单笔收益均值，二者不能互换或相加。开发期为2023–2024，验证期为2025，后段描述期为2026-01至2026-04；2022仅作指标 warmup。完整历史此前已被研究使用，不能称为 pristine / blind OOS。

中央 holdout（>=2026-05-04）消费标记：**{validation_receipt.get('holdout_consumed', '不可用')}**（False = 未使用）。没有 TradingView 原生编译/逐笔 ledger parity；没有 funding、维持保证金、合约最小量或 tick rounding 模型。本报告不把这些缺口默认为零。

## 主叙事

{narrative}

### 来源前缀回执

{receipt_table}

冻结 builder commit：`{builder_commit}`；builder SHA：`{builder_sha}`；selection frozen at：`{selection.get('frozen_at', '不可用')}`；selection 仅使用：`{selection.get('selected_using', '不可用')}`。

### 报告输入哈希

{receipt_hashes}

## 连续账户主对照（2023-01至2026-04；仅描述、不参与选择）

| 周期／臂 | 账户收益% | 标记收益% | 路径DD% | 交易 | 毛单位bp | 净单位bp | 匹配case bp | 匹配control bp | 匹配超额bp | 匹配n |
|---|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join("| " + " | ".join(row) + " |" for row in continuous_rows) + f"""

这里的 `净单位bp` 是全样本均值；当匹配支持不足时，匹配 case/control/超额和匹配 n 保持分别呈现，绝不把全样本均值替作匹配估计。

## 单变量工程链（全 27 行）

| 周期 | 臂 | 账户收益% | 标记收益% | 路径DD% | 交易 | 毛单位bp | 净单位bp | 匹配case bp | 匹配control bp | 匹配超额bp | 匹配n |
|---|---|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join("| " + " | ".join(row) + " |" for row in engineering_rows) + f"""

## 开发期候选链：2023 / 2024 独立折

| 周期 | 折 | 臂 | 账户收益% | 标记收益% | 路径DD% | 交易 | 毛单位bp | 净单位bp | 匹配case bp | 匹配control bp | 匹配超额bp | 匹配n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join("| " + " | ".join(row) + " |" for row in candidate_rows) + f"""

## 冻结选择检查

{_table(["周期", "挑战臂", "冻结条件", "结果", "冻结选择", "是否有准入挑战者"], _selection_rows(selection))}

选择表只复述开发期冻结判断；没有根据验证或后段结果改变任何选择。

## 验证 2025 与后段 2026-01至04

| 周期 | 时段 | 对照 | 账户收益% | 标记收益% | 路径DD% | 交易 | 毛单位bp | 净单位bp | 匹配case bp | 匹配control bp | 匹配超额bp | 匹配n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join("| " + " | ".join(row) + " |" for row in validation_rows) + f"""

## 2025 冻结选择：分数与月簇推断

{_table(["周期", "选择", "AUC", "top-decile n", "top gross", "top net", "top 匹配超额", "top匹配case", "top匹配control", "top匹配n", "排名置换p", "月簇匹配p", "月簇95% CI", "Holm family p", "门槛"], _inference_rows(validation, selection, family))}

`ranking p` 只检验排序诊断；月簇 p 和 CI 针对匹配超额。Holm 仅校正这次预先定义的验证家族，不能消除完整历史已被观察带来的选择偏差。

## 2025 成交精度诊断（信号与BE仍在父周期收盘更新）

{precision_table}

这四组使用同源完整15m子K线，只改变止损成交路径精度。收益相同不代表有逐笔 tick 或 TradingView 原生 parity。

## 连续账户运营指标（原信号执行修正版与冻结候选）

{operating_table}

## 连续选择臂：多空、年度、集中度与兑现

{chr(10).join(selected_breakdowns)}

| 周期 | 分组类型 | 分组 | 交易 | 净单位bp（全样本） | 匹配case bp | 匹配control bp | 超额bp | 匹配n | Top1正盈利占比 | Top5正盈利占比 | 去掉Top1净bp | MFE≥1R | 实现≥1R | MFE≥3R | 实现≥3R | MFE≥10R | 实现≥10R | 平均持有h | 中位持有h |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
""" + "\n".join("| " + " | ".join(row) + " |" for row in selected_details) + f"""

MFE 是曾达到对应 R 的笔数，`实现≥R` 是实际结算达到对应 R 的笔数；它们描述兑现/回吐，不表示加入新的止盈参数。

## 图

{figure_links}

## 可复现命令

```bash
.venv/bin/python scripts/reproduce_release_eth_multitf.py --out /tmp/release-eth-reproduction-20260914
.venv/bin/python scripts/report_release_eth_multitf.py --results /tmp/release-eth-reproduction-20260914 --report /tmp/release-eth-reproduction-20260914/report.md
# Reconcile delivered ledgers and rebuild the canonical report:
.venv/bin/python scripts/audit_release_eth_evidence.py
.venv/bin/python scripts/report_release_eth_multitf.py
```

## 风险与诚实声明

本报告没有读取或评分 holdout，也没有自动 promote、修改阈值、改变成本假设或执行任何实盘动作。不可用字段显示为“不可用”，没有从其他期间、其他臂或原始行情推断填补。后段2026是既见历史上的描述检查，不能升级为盲测或实盘证据。

## 下一步选项（待项目所有者决策）

- 本轮已记录为探索性研究：15m C2 保留观察，1h C3 /4h C1 优化失败；无实盘准入。
- 后续值得研究的是保本净成本与趋势利润兑现；TP/SL/BE 数值改动、holdout、promote 和实盘动作依项目规则另需明确授权。本轮未执行。
"""
    REPORT.write_text(report, encoding="utf-8")
    subprocess.run(
        [sys.executable, str(PROJECT / "scripts/md_to_html.py"), str(REPORT), "--out-dir", str(HTML_DIR)],
        cwd=PROJECT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

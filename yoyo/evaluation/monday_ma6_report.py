"""Assemble reviewed report exports for the Monday MA6 retest Data App.

This module is an artifact loader only: it does not discover opportunities,
calculate returns, replay orders, or infer a common evidence cutoff. It reads
the parent's explicit artifacts/monday-ma6-retest-20261024/report-data exports
and embeds reviewed rows into the existing report snapshot while preserving
the app's stable identity.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT_DIR = REPO_ROOT / "experiments/active/exp-monday-ma6-retest-20261024-v1"
DEFAULT_PROJECT_DIR = REPO_ROOT / "reports/monday-ma6-retest-20261024"
DEFAULT_REPORT_DATA_DIR = REPO_ROOT / "artifacts/monday-ma6-retest-20261024/report-data"
DEFAULT_SUMMARY_PATH = REPO_ROOT / "artifacts/monday-ma6-retest-20261024/summary.json"
DEFAULT_SAMPLES_PATH = REPO_ROOT / "artifacts/monday-ma6-retest-20261024/samples.json"

REPORT_TABLES = ("summary", "metrics", "funnel", "opportunities", "orders", "trades", "fills")
CSV_QUERIES = tuple(query_id for query_id in REPORT_TABLES if query_id not in {"summary", "metrics"})

# Stable display schema. A column is shown only when at least one reviewed row
# actually contains it; raw source rows remain available in the source panel
# and CSV exports. These are aliases supplied by the upstream summary builder.
METRIC_COLUMNS = (
    "exit_mode", "asset_group", "period", "basis", "opportunity_period_basis",
    "grain", "cut_dimension", "cut_value",
    "opportunity_n", "invalidated_n", "reclaim_n", "confirmed_n", "submitted_n",
    "canceled_n", "unfilled_n", "filled_n", "closed_n", "censored_n",
    "wins", "losses", "flat", "win_rate", "mean_win_r", "mean_loss_r",
    "mean_gross_r", "mean_fee_r", "mean_net_r", "total_net_r", "mean_net_bp",
    "mean_r", "total_r", "median_r", "mean_bp", "payoff", "profit_factor",
    "planned_rr_median", "median_holding_hours", "p90_holding_hours",
    "mfe_r_mean", "mae_r_mean", "profitable_excursion_then_sl_n",
    "fee_positive_excursion_then_sl_n", "tp_legs_then_sl_n", "longest_loss_streak",
    "longest_stop_streak", "loss_streak_span", "longest_single_symbol_loss_streak",
    "single_symbol_streak_span",
    "eligible_signal_n", "fully_matched_signal_n", "complete_control_group_n",
    "controls_per_signal", "matched_signal_mean_r", "matched_control_mean_r",
    "paired_excess_r", "paired_signal_planned_rr", "paired_control_planned_rr",
    "matched_signal_mean_net_r", "matched_control_mean_net_r", "paired_excess_net_r",
    "matched_signal_mean_gross_r", "matched_control_mean_gross_r",
    "paired_ci_low", "paired_ci_high", "paired_p", "paired_p_holm", "independent_market_weeks",
    "complete_opportunity_pairs", "attempt_signal_mean_r", "attempt_control_mean_r",
    "attempt_paired_excess_r", "signal_fill_rate", "random_fill_rate",
)

OPPORTUNITY_COLUMNS = (
    "opportunity_id", "exit_mode", "asset_group", "symbol", "direction", "period",
    "trigger_week", "touch_time_utc", "monitor_start_utc", "invalidated_at_utc",
    "reclaim_time_utc", "confirmed_at_utc", "submitted_at_utc", "first_touch_at",
    "trigger_time", "confirmation_at", "invalidation_at", "week", "side", "level",
    "reason", "status", "invalidation_reason", "decision_reason",
)
ORDER_COLUMNS = (
    "order_id", "opportunity_id", "exit_mode", "asset_group", "symbol", "direction",
    "submitted_at_utc", "limit_price", "stop_price", "target_price", "canceled_at_utc",
    "submitted_at", "entry_price", "entry", "stop", "cancel_reason", "population", "status",
)
TRADE_COLUMNS = (
    "trade_id", "opportunity_id", "exit_mode", "asset_group", "symbol", "direction",
    "entry_at_utc", "entry", "stop", "target_price", "exit_at_utc", "exit_price",
    "outcome", "closed", "censored", "basis", "gross_r", "fee_r", "net_r", "mean_r",
    "mfe_r", "mae_r", "profitable_excursion_then_sl", "holding_hours",
    "entry_interval_start", "entry_time",
    "exit_time", "entry_price", "stop_price", "return_r", "return_pct", "side", "net_bp",
)
FILL_COLUMNS = (
    "fill_id", "trade_id", "order_id", "opportunity_id", "exit_mode", "asset_group",
    "symbol", "direction", "side", "timestamp_utc", "price", "quantity", "notional",
    "fee_rate", "fee_type", "fee", "fee_r", "time", "commission", "fee_class",
)
SAMPLE_COLUMNS = (
    "sample_id", "exit_mode", "asset_group", "symbol", "direction", "sample_kind",
    "outcome", "seed", "sampling_pool", "trade_id", "opportunity_id",
    "image_data_url", "image_file", "data_file",
)

QUERY_LABELS = {
    "summary": "全局范围与品种来源覆盖",
    "metrics": "退出策略 × 品种组 × 时段及分解结果",
    "funnel": "机会与成交漏斗",
    "opportunities": "全部策略机会",
    "orders": "挂单、撤销与未成交明细",
    "trades": "成交、闭合与截尾交易明细",
    "fills": "实际成交与逐笔费用明细",
    "samples": "固定种子K线审查样本",
}

NULL_WORDS = {"", "null", "none", "nan", "na"}
NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _parse_cell(value: str | None, key: str = "") -> Any:
    if value is None:
        return None
    text = value.strip()
    if text.lower() in NULL_WORDS:
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    # IDs and timestamps remain exact strings; all other scalar numeric CSV
    # fields become numbers for sorting and right-aligned DataTable rendering.
    normalized_key = key.lower()
    if normalized_key == "seed":
        pass
    elif normalized_key == "id" or normalized_key.endswith("_id") or "timestamp" in normalized_key or "_time" in normalized_key or normalized_key.endswith("_at"):
        return text
    if len(text) > 1 and text.startswith("0") and text[1].isdigit():
        return text
    if NUMERIC_RE.fullmatch(text):
        try:
            if "." not in text and "e" not in text.lower():
                return int(text)
            return float(text)
        except ValueError:
            return text
    return text


def _read_csv(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return [
            {key: _parse_cell(value, key) for key, value in row.items() if key is not None}
            for row in reader
        ]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _csv_candidates(folder: Path, name: str) -> list[Path]:
    return [path for path in (folder / f"{name}.csv", folder / f"{name}.csv.gz") if path.is_file()]


def _read_export(folder: Path, name: str) -> tuple[list[dict[str, Any]], list[Path]]:
    candidates = _csv_candidates(folder, name)
    if not candidates:
        return [], []
    first = _read_csv(candidates[0])
    for duplicate in candidates[1:]:
        other = _read_csv(duplicate)
        if other != first:
            raise ValueError(
                f"conflicting duplicate report exports for {name}: "
                f"{candidates[0]} and {duplicate}"
            )
    return first, candidates


def _safe_source_file(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = base_dir / path
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"sample asset must resolve inside the repository: {path_text}") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"sample asset does not exist: {path}")
    return resolved


def _as_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
    if mime is None:
        raise ValueError(f"unsupported sample image extension: {path.suffix}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _load_sample_rows(samples_path: Path, asset_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    if not samples_path.is_file():
        return []
    payload = json.loads(samples_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("rows", payload.get("samples", []))
    else:
        raise ValueError(f"unsupported samples.json root type: {type(payload).__name__}")
    if not isinstance(rows, list):
        raise ValueError("samples.json rows/samples must be a list")

    resolved_rows: list[dict[str, Any]] = []
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise ValueError("every sample row must be a JSON object")
        row = dict(raw_row)
        image_file = row.get("image_file")
        if image_file and not row.get("image_data_url"):
            image_path = _safe_source_file(str(image_file), samples_path.parent)
            if asset_paths is not None:
                asset_paths.append(image_path)
            row["image_data_url"] = _as_data_url(image_path)
        data_file = row.get("data_file")
        if data_file and "plot_data" not in row:
            data_path = _safe_source_file(str(data_file), samples_path.parent)
            if asset_paths is not None:
                asset_paths.append(data_path)
            if data_path.suffix.lower() == ".json":
                row["plot_data"] = json.loads(data_path.read_text(encoding="utf-8"))
            elif data_path.suffix.lower() == ".csv":
                row["plot_data"] = _read_csv(data_path)
            elif data_path.name.lower().endswith(".csv.gz"):
                row["plot_data"] = _read_csv(data_path)
            else:
                raise ValueError(f"unsupported sample data file: {data_path}")
        resolved_rows.append(row)
    return resolved_rows


def _rule_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Render the current confirmed config as source-backed rule rows."""
    stop_buffer = float(config.get("stop_buffer", 0.005)) * 100
    new_extremes = "首根完整触价15mK线建立运行不利极值；之后每根完整K线严格刷新时累计一次；监控或未成交挂单在收盘累计第三次时失效。"
    target_modes = config.get("target_policies", [])
    target_labels = {
        "trigger_week_monday_high": "周一高点",
        "trigger_week_monday_low": "周一低点",
        "trigger_week_upper_1618": "上方另一侧1.618",
        "trigger_week_lower_1618": "下方另一侧1.618",
    }
    long_targets = "；".join(target_labels.get(value, value) for value in config.get("target_levels_long", []))
    short_targets = "；".join(target_labels.get(value, value) for value in config.get("target_levels_short", []))
    return [
        {
            "section": "触发与监控", "item": "周一 1.618 首次触价",
            "long": "周一下方1.618首次触价后观察做多",
            "short": "周一上方1.618首次触价后观察做空",
            "detail": "按纽约时间每周完成周一高低区间后，记录周内首个方向性触价；加密品种要求触发周的前缀连续。",
        },
        {
            "section": "方向与失效", "item": "不利新极值累计失效",
            "long": "首次触价K线建立运行低点；之后每根完整15m严格创新低累计一次",
            "short": "首次触价K线建立运行高点；之后每根完整15m严格创新高累计一次",
            "detail": new_extremes,
        },
        {
            "section": "确认", "item": "六均线站回 + 后续3根",
            "long": "完整15m收盘严格高于六条MA，随后三根完整收盘仍严格高于六条MA",
            "short": "完整15m收盘严格低于六条MA，随后三根完整收盘仍严格低于六条MA",
            "detail": "首个完整15m收盘严格站过全部六MA后，再要求后续三根完整收盘都保持同侧；期间失败重置连续根数；每周每方向只取首个序列。",
        },
        {
            "section": "均线", "item": "20 / 60 / 120 SMA 与 EMA",
            "long": "SMA20、SMA60、SMA120",
            "short": "EMA20、EMA60、EMA120",
            "detail": "由 yoyo.datasets.ma_rope_filter.add_six_mas 计算，只使用完成K线收盘；SMA为完整窗口均值。",
        },
        {
            "section": "入场", "item": "确认后的均线回踩限价",
            "long": "后续回踩时，六条均线最高线挂买单",
            "short": "后续回踩时，六条均线最低线挂卖单",
            "detail": "仅在后续完整15m收盘后更新未成交限价；若新价已可成交或几何无效则撤销，不追认历史触价成交。",
        },
        {
            "section": "止损", "item": f"六均线包络外 {stop_buffer:g}%",
            "long": f"最低MA × (1 − {stop_buffer:g}%)",
            "short": f"最高MA × (1 + {stop_buffer:g}%)",
            "detail": "最近一次未成交挂单改单时重算止损；成交后冻结。不设保本、追踪或成交后的新极值撤单。",
        },
        {
            "section": "止盈", "item": "三种目标分配",
            "long": f"周一高点全平；上方1.618全平；两目标各半（{long_targets}）",
            "short": f"周一低点全平；下方1.618全平；两目标各半（{short_targets}）",
            "detail": "配置：" + "、".join(target_modes) + "。两目标各半按两条原始数量各半的native目标腿线性合计逐笔收益和费用，不设保本交互。",
        },
        {
            "section": "时间边界", "item": "监控、撤单与持仓期限",
            "long": "当前纽约触发周监控；未成交挂单下周一到期；成交仓位保留原周锚点",
            "short": "与多头方向镜像",
            "detail": "仅当前触发纽约周继续监控；未成交挂单到下周一纽约00:00到期。成交仓位沿用原周锚点，没有持仓时间或周末退出。行情源结束或意外缺口边界未决，不计入闭合盈亏。",
        },
        {
            "section": "成本与样本", "item": "研究成本与匹配对照",
            "long": "加密maker 0.02% / taker 0.05% 单边；黄金仅报毛收益",
            "short": "随机对照每信号3个候选；仅全部成交闭合且同片段者严格配对",
            "detail": "费用按成交逐笔名义金额计入；maker 0.02%、taker 0.05%单边。对照同币、同纽约周、同方向及滞后14根波动桶；只对三个对照全成交平仓且同连续片段的信号严格配对。",
        },
    ]


def _flatten_metadata(value: Any, prefix: str = "") -> dict[str, Any]:
    """Keep summary.json metadata readable in the app's one-row table."""
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_metadata(child, name))
        return flattened
    if isinstance(value, list):
        return {prefix: json.dumps(value, ensure_ascii=False, separators=(",", ":"))}
    if prefix:
        return {prefix: value}
    return {}


def _set_grain(rows: list[dict[str, Any]], grain: str) -> list[dict[str, Any]]:
    tagged = []
    for row in rows:
        item = dict(row)
        item["grain"] = grain
        tagged.append(item)
    return tagged


def _source_last(summary_metadata: dict[str, Any]) -> str | None:
    """Use the data artifact's coverage endpoint, never the report build time."""
    value = summary_metadata.get("source_last")
    if value in (None, ""):
        return None
    if not isinstance(value, (str, int, float)):
        raise ValueError("summary.json source_last must be a scalar coverage endpoint")
    return str(value)


def _summary_progress(metadata: dict[str, Any]) -> tuple[bool | None, int | None, int | None]:
    """Read explicit replay progress without inferring completion from row counts."""
    complete = metadata.get("complete")
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("artifact summary.json complete must be a JSON boolean")

    def count(name: str) -> int | None:
        value = metadata.get(name)
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            raise ValueError(f"artifact summary.json {name} must be a non-negative integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"artifact summary.json {name} must be a non-negative integer") from exc
        if parsed < 0 or str(value).strip() not in {str(parsed), f"{parsed}.0"}:
            raise ValueError(f"artifact summary.json {name} must be a non-negative integer")
        return parsed

    return complete, count("completed_symbols"), count("expected_symbols")


def _summary_query_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Split global metadata from the source's per-symbol coverage ledger."""
    coverage = payload.get("coverage", [])
    if coverage is None:
        coverage = []
    if not isinstance(coverage, list) or any(not isinstance(row, dict) for row in coverage):
        raise ValueError("artifact summary.json coverage must be a list of JSON objects")

    metadata_payload = {key: value for key, value in payload.items() if key != "coverage"}
    metadata = _flatten_metadata(metadata_payload)
    metadata["record_type"] = "global"
    metadata["coverage_asset_count"] = len(coverage)
    source_last = payload.get("source_last")
    if coverage and source_last not in (None, "") and all(row.get("last") not in (None, "") for row in coverage):
        metadata["coverage_assets_ending_before_source_last"] = sum(
            str(row["last"]) != str(source_last) for row in coverage
        )
    if "source_first" in payload:
        metadata.setdefault("first_time_utc", payload["source_first"])
    if "source_last" in payload:
        metadata.setdefault("last_time_utc", payload["source_last"])

    rows = [metadata]
    rows.extend({"record_type": "asset", **_flatten_metadata(row)} for row in coverage)
    return rows, metadata


def _query_source(
    query_id: str,
    paths: list[Path],
    *,
    pending: bool,
    caveats: list[str] | None = None,
    replay_progress: str | None = None,
) -> dict[str, Any]:
    definitions = {
        "summary": [
            {"label": "覆盖时间", "definition": "source_last 是所有品种来源尾中的最新时点，不表示每个品种都覆盖到该时间；逐品种 first/last 保留在同一查询的 coverage 明细行。"},
            {"label": "样本覆盖", "definition": "summary.json 中每个 coverage 对象对应一个源品种；全局覆盖条数直接由该列表读取。"},
            {"label": "回放状态", "definition": "只有 summary.json.complete 为 true 才标记历史回放完成；进行中只展示 completed_symbols/expected_symbols，不根据已见品种或行数推断全量完成。"},
        ],
        "metrics": [
            {"label": "R / bp", "definition": "mean_r、total_r、mean_bp 按该行 basis 解释；毛与净不可混合。XAUUSD 为 gross basis，mean_fee_r、mean_net_r、total_net_r 与 mean_net_bp 为空，表示费率未知，不是零。"},
            {"label": "交易统计", "definition": "win_rate 的源单位为 0–1，界面显示为百分比；mean_win_r、mean_loss_r、payoff、计划RR中位数及持仓P90均来自分析导出 的闭合交易切片。"},
            {"label": "连亏与止损序列", "definition": "longest_loss_streak、longest_stop_streak 按分析导出 的时间排序结果展示；loss_streak_span 是跨品种合并序列的最长连亏区间，single_symbol_streak_span 是单品种最长连亏区间，不额外重算。"},
            {"label": "闭合交易分期", "definition": "earlier/later 按实际成交柱开始 entry_interval_start 分期；跨切点持仓从前后期排除。全期不做此排除。逐笔 entry_time 是保守入场柱结束时点，不应替代该分期锚。"},
            {"label": "提交队列机会期望", "definition": "attempt_*_r 按 submitted_at 分期，以合格提交信号机会为分母；未成交仅按零暴露纳入机会期望，不计为盈亏交易或逐笔胜率。提交早于切点而退出晚于切点的样本从前后期机会期望排除。"},
            {"label": "严格成交配对", "definition": "matched_*_r 与 paired_excess_r 只使用信号对应的三个随机对照全部成交、闭合且处于同一连续片段的完整3/3组；complete_opportunity_pairs 与机会期望口径分母另行展示。"},
            {"label": "持仓诊断", "definition": "MFE/MAE仅计实际成交持仓期间；浮盈后止损、最长连亏与持仓时间按原生实验导出值展示。"},
        ],
        "funnel": [
            {"label": "状态漏斗", "definition": "首次触价、失效、四根收盘确认、提交、撤销、未成交与成交分别计数；仅首次站回但未完成确认的次数没有单独采集。闭合与截尾见交易统计；未成交与失效不作为零R交易。"},
        ],
        "trades": [
            {"label": "闭合与截尾", "definition": "闭合收益与来源结束/数据连续性边界造成的截尾记录分别保留；截尾不作为已实现止盈/止损结果。"},
            {"label": "两目标各半", "definition": "目标各半由原生实验把两条原始数量各半的native目标腿线性合成，没有保本交互，未重复回放。"},
        ],
        "fills": [
            {"label": "逐次费用", "definition": "fee、fee_type、fee_rate及fee_r从原生实验逐笔成交输出读取；本报告不重新计费。"},
        ],
    }
    source = {
        "label": QUERY_LABELS[query_id],
        "files": [_relative(path) for path in paths],
        "evidenceFlow": [
            {"title": "冻结规则与历史行情", "detail": "experiments/active/exp-monday-ma6-retest-20261024-v1/config.json 保存 Notion 原文、Owner 确认及实施口径；各 symbols/*/identity.json 记录实际输入路径、大小、时间与代码身份。"},
            {"title": "原生成交", "detail": "monday_ma6_retest 调用 NautilusTrader 1.231.0 run_native，逐品种输出机会、挂单、两条独立目标腿及成交事件；各半仓由无交互的两条腿线性合成。"},
            {"title": "统计与原始导出", "detail": "monday_ma6_analysis 读取已完成原生输出，区分策略/随机、闭合/截尾、共同闭合入场及实际入场分期，生成 artifacts/monday-ma6-retest-20261024/report-data 的 CSV.gz；未成交不进入逐笔胜率。"},
            {"title": "本报告装载", "detail": "monday_ma6_report 仅装载上述统一分析导出及 monday_ma6_charts 的真实行情图，不重放成交、不重算统计。此组件对应文件见 Sources。"},
        ],
        "metricDefinitions": definitions.get(query_id, []),
        "caveats": list(caveats or []),
    }
    if replay_progress:
        source["caveats"].append(replay_progress)
    if pending:
        source["caveats"].append("结果导出尚未提供；空行不代表零机会、零成交或零收益。")
    return source


def build_snapshot(
    project_dir: Path = DEFAULT_PROJECT_DIR,
    experiment_dir: Path = DEFAULT_EXPERIMENT_DIR,
    samples_path: Path = DEFAULT_SAMPLES_PATH,
    *,
    report_data_dir: Path = DEFAULT_REPORT_DATA_DIR,
    summary_path: Path = DEFAULT_SUMMARY_PATH,
    complete: bool = False,
) -> dict[str, Any]:
    data_path = project_dir / "src/data.json"
    if not data_path.is_file():
        raise FileNotFoundError(f"report app snapshot does not exist: {data_path}")
    snapshot = json.loads(data_path.read_text(encoding="utf-8"))
    if snapshot.get("surface") != "report" or not snapshot.get("id"):
        raise ValueError("report app must retain its prepared report surface and stable id")

    original_rules = snapshot.get("queries", {}).get("rules", {})
    config_path = experiment_dir / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config_meta = _relative(config_path)
    else:
        config = {}
        config_meta = "experiments/active/exp-monday-ma6-retest-20261024-v1/config.json"

    loaded: dict[str, list[dict[str, Any]]] = {query_id: [] for query_id in REPORT_TABLES}
    loaded_paths: dict[str, list[Path]] = {}
    for query_id in CSV_QUERIES:
        loaded[query_id], loaded_paths[query_id] = _read_export(report_data_dir, query_id)

    # The grouped rows in summary.csv.gz and the temporal/side/symbol slices in
    # metrics.csv.gz are one UI table with an explicit grain tag. The separate
    # summary query is the single coverage metadata record from summary.json.
    grouped_rows, grouped_paths = _read_export(report_data_dir, "summary")
    cut_rows, cut_paths = _read_export(report_data_dir, "metrics")
    loaded["metrics"] = _set_grain(grouped_rows, "summary") + _set_grain(cut_rows, "cut")
    loaded_paths["metrics"] = grouped_paths + cut_paths

    summary_metadata: dict[str, Any] = {}
    summary_paths: list[Path] = []
    if summary_path.is_file():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("artifact summary.json must contain a JSON object")
        loaded["summary"], summary_metadata = _summary_query_rows(payload)
        summary_paths = [summary_path]
    loaded_paths["summary"] = summary_paths

    replay_complete, completed_symbols, expected_symbols = _summary_progress(summary_metadata)
    report_exports_present = any(_csv_candidates(report_data_dir, name) for name in REPORT_TABLES)
    if summary_paths and (replay_complete is True or (completed_symbols or 0) > 0) and not report_exports_present:
        raise FileNotFoundError(
            "artifact summary.json reports replay progress, but the explicit report-data directory "
            f"contains no CSV exports: {report_data_dir}"
        )
    if replay_complete is False and completed_symbols is not None and expected_symbols is not None:
        replay_progress_note = (
            f"历史回放进行中 {completed_symbols}/{expected_symbols}；当前统计仅含已完成品种，不作方案排名。"
        )
    elif replay_complete is False:
        replay_progress_note = "历史回放尚未标记完成；当前表不得作为全量方案排名。"
    else:
        replay_progress_note = None

    sample_paths = [samples_path] if samples_path.is_file() else []
    samples = _load_sample_rows(samples_path, sample_paths)
    required_csvs = ("funnel", "opportunities", "orders", "trades", "fills")
    all_required_paths_exist = (
        bool(loaded_paths.get("summary"))
        and bool(grouped_paths)
        and bool(cut_paths)
        and all(loaded_paths.get(query_id) for query_id in required_csvs)
        and bool(sample_paths)
    )
    result_rows_exist = bool(loaded["metrics"] or loaded["funnel"])
    if complete and replay_complete is not True:
        raise ValueError("cannot mark report complete unless artifact summary.json complete is true")
    if complete:
        missing = [query_id for query_id in required_csvs if not loaded_paths.get(query_id)]
        if not grouped_paths:
            missing.insert(0, "summary.csv(.gz)")
        if not cut_paths:
            missing.insert(1 if not grouped_paths else 0, "metrics.csv(.gz)")
        if not loaded_paths.get("summary"):
            missing.insert(0, "artifact summary.json")
        if not sample_paths:
            missing.append("samples.json")
        if missing:
            raise ValueError("cannot mark report complete; missing exports: " + ", ".join(missing))
        if len(samples) < 3:
            raise ValueError("cannot mark report complete; expected at least three reviewed K-line samples")
        if not _source_last(summary_metadata):
            raise ValueError("cannot mark report complete without artifact summary.json source_last")

    queries = snapshot.setdefault("queries", {})
    for query_id in REPORT_TABLES:
        queries[query_id] = {
            "rows": loaded[query_id],
            "source": _query_source(
                query_id,
                loaded_paths[query_id],
                pending=not bool(loaded_paths[query_id]),
                replay_progress=replay_progress_note,
            ),
        }
    queries["samples"] = {
        "rows": samples,
        "source": _query_source(
            "samples",
            sample_paths,
            pending=not bool(sample_paths),
            caveats=["图像和 plot_data 由 samples.json 引用或内嵌；本模块不绘图、不挑选样本。"],
            replay_progress=replay_progress_note,
        ),
    }
    # Preserve the reviewed Notion/config rule rows and bindings from the
    # initial snapshot; updating result exports must not revise strategy rules.
    if config:
        rules_query = dict(original_rules)
        rules_query["rows"] = _rule_rows(config)
        rule_source = dict(rules_query.get("source", {}))
        rule_source["files"] = [config_meta]
        rule_source["caveats"] = [
            "策略方向依据当前config中的周一上下1.618目标定义：触下方1.618后观察多头；上方1.618为其空头镜像。",
            "实现口径按本轮owner clarification与implementation_interpretations；本报告不修改配置。",
        ]
        rules_query["source"] = rule_source
        queries["rules"] = rules_query
    else:
        queries["rules"] = original_rules

    report = snapshot.setdefault("report", {})
    cutoff = _source_last(summary_metadata)
    if replay_complete is False:
        progress_status = (
            f"历史回放进行中 {completed_symbols}/{expected_symbols}，当前统计仅为已完成品种，不作方案排名。"
            if completed_symbols is not None and expected_symbols is not None
            else "历史回放进行中；当前统计仅供检查，不作全量方案排名。"
        )
    else:
        progress_status = None
    report["scopeStatus"] = (
        "规则、数据结果与样本已载入报告，构建器未执行任何成交回放或统计。"
        if complete
        else progress_status
        if progress_status
        else "历史回放已标记完成；报告数据已载入，仍需复核。"
        if replay_complete is True and all_required_paths_exist
        else "历史回放已标记完成；报告导出尚待完整装载。"
        if replay_complete is True
        else "规则来源已审；结果导出已载入待复核。"
        if all_required_paths_exist
        else "部分结果表已载入，其他导出仍待提供。"
        if result_rows_exist
        else "规则与来源已审；历史回放结果尚未生成。"
    )
    if cutoff:
        report["asOf"] = cutoff
        report["evidenceCutoff"] = cutoff
    else:
        report.pop("asOf", None)
        report["evidenceCutoff"] = None
    report["resultRowsExist"] = result_rows_exist
    report["sampleCount"] = len(samples)
    report["resultExportsComplete"] = all_required_paths_exist
    report["replayComplete"] = replay_complete
    report["completedSymbols"] = completed_symbols
    report["expectedSymbols"] = expected_symbols
    interpretation = summary_metadata.get("interpretation_markdown")
    if interpretation not in (None, "") and not isinstance(interpretation, str):
        raise ValueError("artifact summary.json interpretation_markdown must be a string")
    report["interpretationMarkdown"] = interpretation or None
    report["sources"] = report.get("sources", [])
    source_paths = [config_meta]
    for paths in loaded_paths.values():
        source_paths.extend(_relative(path) for path in paths)
    if sample_paths:
        source_paths.extend(_relative(path) for path in sample_paths)
    recorded = {item.get("path") for item in report["sources"] if isinstance(item, dict)}
    for relative in dict.fromkeys(source_paths):
        if relative not in recorded:
            report["sources"].append({"path": relative, "type": "reviewed-file"})

    snapshot["generatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    has_result_exports = all_required_paths_exist or result_rows_exist
    snapshot["status"] = "reviewed" if complete else "results-loaded-review-required" if has_result_exports else "rules-reviewed-results-pending"
    snapshot["buildStatus"] = "complete" if complete else "updating" if has_result_exports else "creating"
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT_DIR)
    parser.add_argument("--report-data-dir", type=Path, default=DEFAULT_REPORT_DATA_DIR)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--samples-path", type=Path, default=DEFAULT_SAMPLES_PATH)
    parser.add_argument("--complete", action="store_true", help="mark the app complete after required evidence is present and reviewed")
    args = parser.parse_args()

    snapshot = build_snapshot(
        args.project_dir.resolve(), args.experiment_dir.resolve(), args.samples_path.resolve(),
        report_data_dir=args.report_data_dir.resolve(),
        summary_path=args.summary_json.resolve(), complete=args.complete
    )
    output = args.project_dir / "src/data.json"
    output.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "app_id": snapshot["id"],
        "status": snapshot["status"],
        "buildStatus": snapshot["buildStatus"],
        "result_rows": {query_id: len(snapshot["queries"][query_id]["rows"]) for query_id in REPORT_TABLES},
        "sample_rows": len(snapshot["queries"]["samples"]["rows"]),
        "evidence_as_of": snapshot.get("report", {}).get("asOf"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

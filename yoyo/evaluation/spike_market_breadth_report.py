"""Render the bounded SPIKE market-breadth matched-control conclusion.

This reporter deliberately treats stage-one candidates as opaque bytes.  It
only hashes ``candidate_context.csv.gz`` to verify the matched-control input
pin; it never parses that ledger, the market panel, or any normalized OHLCV.
The only tabular inputs are the four small summary CSVs named below.  This
keeps the report reproducible without opening the development candidate detail
or any post-development/holdout data.

The report is a research conclusion only.  It displays cross-cohort evidence
without inventing an acceptance gate, and does not modify any production
filter, notification, registry, or model setting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE_ONE = ROOT / "experiments/active/exp-spike-market-breadth-20260913-v2/results"
DEFAULT_MATCHED = ROOT / "experiments/active/exp-spike-market-breadth-matched-controls-20260913-v2/results"
STAGE_ONE_CODE = ROOT / "yoyo/evaluation/spike_market_breadth_study.py"
MATCHED_CONTROLS_CODE = ROOT / "yoyo/evaluation/spike_market_breadth_matched_controls.py"
MATCHED_OUTPUTS = ("matched_control_pairs.csv.gz", "matched_control_summary.csv", "control_receipts.csv")
FROZEN_RULE = "joint_delta_60m > 0"

_OUTCOME_REQUIRED = {
    "variant", "timeframe_min", "slice", "metric", "candidates", "closed", "censored",
    "mean_net_r", "median_net_r", "win_rate", "realized_ge_10r_count", "realized_ge_10r",
}
_RULE_REQUIRED = {
    "variant", "timeframe_min", "rule", "baseline_candidates", "candidate_retention",
    "baseline_realized_ge_10r_count", "kept_realized_ge_10r_count", "exact_entry_10r_retention",
    "candidates", "closed", "mean_net_r", "median_net_r", "win_rate", "realized_ge_10r",
}
_SLICES_REQUIRED = {"variant", "timeframe_min", "slice", "metric", "candidates", "mean_net_r", "win_rate"}
_STAGE_SLICE_DESCRIPTION_COLUMNS = (
    "variant", "timeframe_min", "slice", "metric", "candidates", "closed", "censored", "mean_net_r",
    "median_net_r", "win_rate", "realized_ge_10r_count", "realized_ge_10r",
)
_MATCHED_REQUIRED = {
    "variant", "timeframe_min", "metric", "slice", "targets", "matched", "match_rate",
    "target_mean_net_r", "control_mean_net_r", "paired_delta_mean_net_r", "paired_sign_flip_p",
    "sign_flip_unit", "unmatched_reasons",
}


def sha256(path: Path) -> str:
    """Return a byte identity without decoding the artifact's rows."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing required manifest: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be an object: {path}")
    return value


def _require_table(path: Path, columns: set[str], *, label: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"missing required {label}: {path}")
    table = pd.read_csv(path)
    missing = columns.difference(table.columns)
    if missing:
        raise ValueError(f"{label} missing columns: " + ", ".join(sorted(missing)))
    return table


def _stage_artifact(stage_one: Path, name: str) -> Path:
    path = stage_one / name
    if not path.is_file():
        raise FileNotFoundError(f"stage-one artifact is missing: {path}")
    return path


def validate_input_pins(stage_one: Path, matched: Path) -> tuple[dict, dict]:
    """Fail closed unless both reports point at the exact same stage-one bytes.

    Candidate detail and source catalog are intentionally not parsed here.  The
    matched-control manifest is compared to their complete byte SHA-256 values,
    and stage one's own manifest must also pin those bytes.  The check is the
    evidence boundary that prevents joining an attractive control result to a
    different candidate population.
    """
    stage_manifest = _load_json(_stage_artifact(stage_one, "manifest.json"))
    matched_manifest = _load_json(_stage_artifact(matched, "manifest.json"))
    candidate = _stage_artifact(stage_one, "candidate_context.csv.gz")
    source = _stage_artifact(stage_one, "source_manifest.csv")
    actual = {
        "candidate_context.csv.gz": sha256(candidate),
        "source_manifest.csv": sha256(source),
    }
    stage_outputs = stage_manifest.get("outputs")
    if not isinstance(stage_outputs, dict):
        raise ValueError("stage-one manifest missing outputs object")
    matched_pins = {
        "candidate_context.csv.gz": matched_manifest.get("input_candidate_context_sha256"),
        "source_manifest.csv": matched_manifest.get("input_source_manifest_sha256"),
    }
    for name, actual_sha in actual.items():
        if stage_outputs.get(name) != actual_sha:
            raise ValueError(f"stage-one manifest hash mismatch for {name}")
        if matched_pins[name] != actual_sha:
            raise ValueError(f"matched manifest does not reference stage-one {name}")
    for key in ("development_start", "development_end_exclusive"):
        if matched_manifest.get(key) != stage_manifest.get(key):
            raise ValueError(f"matched manifest {key} differs from stage one")
    return stage_manifest, matched_manifest


def _number(value: object, *, integer: bool = False) -> float | int:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return math.nan
    return int(number) if integer else float(number)


def _format(value: object, *, percent: bool = False, integer: bool = False, pvalue: bool = False,
            decimal: bool = False) -> str:
    number = _number(value, integer=integer)
    if isinstance(number, float) and math.isnan(number):
        return "—"
    if integer:
        return f"{number:,}"
    if percent:
        return f"{float(number):.1%}"
    if pvalue:
        return f"{float(number):.4g}"
    if decimal:
        return f"{float(number):.3f}"
    return f"{float(number):.3f}"


def _markdown_table(frame: pd.DataFrame, columns: Iterable[tuple[str, str, dict]]) -> str:
    """Render a compact deterministic Markdown table without optional extras."""
    columns = list(columns)
    header = "| " + " | ".join(title for title, _, _ in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for row in frame.itertuples(index=False):
        cells = []
        for _, field, options in columns:
            value = getattr(row, field)
            if options:
                value = _format(value, **options)
            elif pd.isna(value):
                value = "—"
            else:
                value = str(value)
            cells.append(str(value).replace("|", "\\|").replace("\n", "<br>"))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, divider, *rows])


def _one_row(table: pd.DataFrame, *, variant: str, timeframe: int, metric: str, slice_name: str) -> pd.Series:
    selected = table.loc[
        table.variant.astype(str).eq(variant)
        & pd.to_numeric(table.timeframe_min, errors="coerce").eq(timeframe)
        & table.metric.astype(str).eq(metric)
        & table.slice.astype(str).eq(slice_name)
    ]
    if len(selected) != 1:
        raise ValueError(
            f"expected exactly one matched row for {variant}/{timeframe}/{metric}/{slice_name}; got {len(selected)}"
        )
    return selected.iloc[0]


def _assert_stage_hashes(stage_one: Path, manifest: dict, names: Iterable[str]) -> None:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("stage-one manifest missing outputs object")
    for name in names:
        path = _stage_artifact(stage_one, name)
        if outputs.get(name) != sha256(path):
            raise ValueError(f"stage-one manifest hash mismatch for {name}")


def _assert_study_code_pin(manifest: dict, *, code: Path, label: str) -> None:
    """Require a manifest to name the exact current study-generator bytes."""
    code_sha = manifest.get("study_code_sha256")
    if not isinstance(code_sha, str) or len(code_sha) != 64 or any(char not in "0123456789abcdef" for char in code_sha.lower()):
        raise ValueError(f"{label} manifest has invalid study_code_sha256")
    if code_sha != sha256(code):
        raise ValueError(f"{label} manifest study_code_sha256 differs from current generator")


def _assert_matched_artifact_pins(matched: Path, manifest: dict) -> Path:
    """Verify all matched artifacts and the exact generator bytes before parsing.

    Pairs and receipts remain opaque: this reporter reads their bytes only to
    check manifest identity.  The summary is returned for the one subsequent
    CSV parse that produces the report tables.
    """
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("matched manifest missing outputs object")
    paths = {}
    for name in MATCHED_OUTPUTS:
        path = _stage_artifact(matched, name)
        expected = outputs.get(name)
        if not isinstance(expected, str) or expected != sha256(path):
            raise ValueError(f"matched manifest hash mismatch for {name}")
        paths[name] = path
    _assert_study_code_pin(manifest, code=MATCHED_CONTROLS_CODE, label="matched")
    return paths["matched_control_summary.csv"]


def build_spike_market_breadth_report(stage_one: Path, matched: Path, report: Path) -> dict:
    """Verify pinned development summaries and write their Chinese conclusion.

    ``stage_one`` and ``matched`` are result directories, enabling unit tests
    and one-off report delivery to use temporary bundles.  Existing input bytes
    are never modified.  ``report`` is the sole output.
    """
    stage_one, matched, report = Path(stage_one), Path(matched), Path(report)
    stage_manifest, matched_manifest = validate_input_pins(stage_one, matched)
    _assert_study_code_pin(stage_manifest, code=STAGE_ONE_CODE, label="stage-one")
    matched_summary = _assert_matched_artifact_pins(matched, matched_manifest)
    _assert_stage_hashes(stage_one, stage_manifest, (
        "outcome_summary.csv", "frozen_candidate_rule.csv", "single_variable_slices.csv",
    ))
    outcome = _require_table(stage_one / "outcome_summary.csv", _OUTCOME_REQUIRED, label="outcome summary")
    frozen = _require_table(stage_one / "frozen_candidate_rule.csv", _RULE_REQUIRED, label="frozen candidate rule")
    slices = _require_table(stage_one / "single_variable_slices.csv", _SLICES_REQUIRED, label="single-variable slices")
    controls = _require_table(matched_summary, _MATCHED_REQUIRED, label="matched-control summary")
    if not frozen.rule.astype(str).eq(FROZEN_RULE).all() or stage_manifest.get("frozen_rule") != FROZEN_RULE:
        raise ValueError("stage-one frozen rule is not the expected joint_delta_60m > 0")
    if controls.duplicated(["variant", "timeframe_min", "metric", "slice"]).any():
        raise ValueError("matched-control summary has duplicate cohort rows")
    if not controls.sign_flip_unit.astype(str).eq("calendar_month").all():
        raise ValueError("matched-control summary must use sign_flip_unit=calendar_month")

    baseline = outcome.loc[(outcome.slice.astype(str) == "all") & (outcome.metric.astype(str) == "all")].copy()
    if baseline.empty:
        raise ValueError("outcome summary lacks baseline rows")
    baseline["timeframe_min"] = pd.to_numeric(baseline.timeframe_min, errors="raise").astype(int)
    keys = [(str(row.variant), int(row.timeframe_min)) for row in baseline.sort_values(["variant", "timeframe_min"]).itertuples(index=False)]
    if len(set(keys)) != len(keys):
        raise ValueError("outcome summary has duplicate baseline cohorts")
    frozen["timeframe_min"] = pd.to_numeric(frozen.timeframe_min, errors="raise").astype(int)
    frozen_keys = [(str(row.variant), int(row.timeframe_min)) for row in frozen.itertuples(index=False)]
    if len(set(frozen_keys)) != len(frozen_keys):
        raise ValueError("frozen candidate rule has duplicate baseline cohorts")
    if set(frozen_keys) != set(keys):
        raise ValueError("frozen candidate rule does not match baseline variant/timeframe cohorts")
    for variant, timeframe in keys:
        _one_row(controls, variant=variant, timeframe=timeframe, metric="baseline", slice_name="all")
        _one_row(controls, variant=variant, timeframe=timeframe, metric="joint_delta_60m", slice_name="positive_rule")
    frozen = frozen.sort_values(["variant", "timeframe_min"]).reset_index(drop=True)
    controls_view = controls.loc[
        ((controls.metric.astype(str) == "baseline") & (controls.slice.astype(str) == "all"))
        | ((controls.metric.astype(str) == "joint_delta_60m") & (controls.slice.astype(str) == "positive_rule"))
    ].copy()
    controls_view["timeframe_min"] = pd.to_numeric(controls_view.timeframe_min, errors="raise").astype(int)
    controls_view["cohort"] = controls_view.apply(
        lambda row: "基线" if row.metric == "baseline" else "冻结规则：delta_60m > 0", axis=1)
    controls_view = controls_view.sort_values(["variant", "timeframe_min", "metric"]).reset_index(drop=True)
    baseline_controls = controls.loc[
        (controls.metric.astype(str) == "baseline") & (controls.slice.astype(str) == "all")
    ].copy()
    baseline_targets = pd.to_numeric(baseline_controls.targets, errors="raise")
    baseline_matched = pd.to_numeric(baseline_controls.matched, errors="raise")
    if (baseline_targets.lt(0) | baseline_matched.lt(0) | baseline_matched.gt(baseline_targets)).any():
        raise ValueError("matched-control baseline has invalid target or matched totals")
    total_baseline_targets = int(baseline_targets.sum())
    total_baseline_matched = int(baseline_matched.sum())
    if total_baseline_targets <= 0:
        raise ValueError("matched-control baseline has no targets")
    total_baseline_match_rate = total_baseline_matched / total_baseline_targets
    other_mask = (slices.slice.astype(str).isin(("bottom_quartile", "top_quartile"))
                  & slices.metric.astype(str).ne("joint_delta_60m"))
    other = slices.loc[other_mask, [name for name in _STAGE_SLICE_DESCRIPTION_COLUMNS if name in slices.columns]].copy()
    if other.empty:
        raise ValueError("single-variable slices lacks non-frozen top/bottom cohorts")
    other["timeframe_min"] = pd.to_numeric(other.timeframe_min, errors="raise").astype(int)
    matched_other = controls.loc[
        controls.slice.astype(str).isin(("bottom_quartile", "top_quartile"))
        & controls.metric.astype(str).ne("joint_delta_60m")
    ].copy()
    matched_other["timeframe_min"] = pd.to_numeric(matched_other.timeframe_min, errors="raise").astype(int)
    joined_other = other.merge(
        matched_other[["variant", "timeframe_min", "metric", "slice", "matched", "match_rate",
                       "target_mean_net_r", "control_mean_net_r", "paired_delta_mean_net_r", "paired_sign_flip_p"]],
        on=["variant", "timeframe_min", "metric", "slice"], how="left", validate="one_to_one",
    )
    if joined_other.matched.isna().any():
        raise ValueError("matched-control summary lacks a top/bottom single-variable cohort")
    joined_other = joined_other.sort_values(["variant", "timeframe_min", "metric", "slice"]).reset_index(drop=True)
    descriptive_p = pd.to_numeric(matched_other.paired_sign_flip_p, errors="coerce")
    if descriptive_p.isna().any() or (~descriptive_p.between(0, 1)).any():
        raise ValueError("descriptive matched-control cohorts have invalid paired p values")
    descriptive_comparisons = len(descriptive_p)
    if descriptive_comparisons == 0:
        raise ValueError("matched-control summary lacks descriptive paired p values")
    descriptive_min_p = float(descriptive_p.min())
    descriptive_bonferroni_upper = min(1.0, descriptive_min_p * descriptive_comparisons)
    frozen_evidence = controls.loc[
        (controls.metric.astype(str) == "joint_delta_60m")
        & (controls.slice.astype(str) == "positive_rule")
    ].copy()
    frozen_evidence["timeframe_min"] = pd.to_numeric(frozen_evidence.timeframe_min, errors="raise").astype(int)
    frozen_evidence = frozen_evidence.merge(
        frozen[["variant", "timeframe_min", "baseline_realized_ge_10r_count", "kept_realized_ge_10r_count",
                "exact_entry_10r_retention"]],
        on=["variant", "timeframe_min"], how="inner", validate="one_to_one",
    ).sort_values(["variant", "timeframe_min"]).reset_index(drop=True)
    if len(frozen_evidence) != len(keys):
        raise ValueError("matched-control summary lacks a frozen-rule cohort")
    deltas = pd.to_numeric(frozen_evidence.paired_delta_mean_net_r, errors="coerce")
    frozen_p = pd.to_numeric(frozen_evidence.paired_sign_flip_p, errors="coerce")
    if deltas.isna().any() or frozen_p.isna().any() or (~frozen_p.between(0, 1)).any():
        raise ValueError("frozen-rule cohorts lack valid matched effects or paired p values")
    direction_inconsistent = bool((deltas.gt(0).any()) and (deltas.lt(0).any()))
    all_frozen_p_not_significant = bool(frozen_p.ge(0.05).all())
    ten_r_loss = bool(pd.to_numeric(frozen_evidence.kept_realized_ge_10r_count, errors="coerce").lt(
        pd.to_numeric(frozen_evidence.baseline_realized_ge_10r_count, errors="coerce")).any())
    if not (direction_inconsistent and all_frozen_p_not_significant and ten_r_loss):
        raise ValueError("frozen-rule evidence does not support the required uniform-gate rejection conclusion")

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"""# SPIKE 市场广度：冻结候选的匹配随机对照整合报告

## 结论

冻结的 `joint_delta_60m > 0` **应拒绝作为统一硬过滤**：{len(frozen_evidence)} 个 cohort 的 matched 配对差值净R跨组合方向翻转，全部 {len(frozen_p)} 个 paired p 均不显著（最小 p={_format(frozen_p.min(), pvalue=True)}），且至少一个 cohort 丢失了原有 ≥10R 候选。此结论只拒绝这条冻结硬过滤；不构成任何上线、通知、训练或仓位调整建议。

## 范围与证据边界

- 阶段一开发窗口：`{stage_manifest.get('development_start')}` 至 `{stage_manifest.get('development_end_exclusive')}`（右端排除）。本整合器没有读取 holdout、市场面板、候选明细或原始逐笔大表；`candidate_context.csv.gz` 仅以字节 SHA-256 核验，未解析行内容。
- matched manifest 的候选与来源 SHA 分别与阶段一的 `candidate_context.csv.gz`、`source_manifest.csv` 一致；阶段一 manifest 也再次固定了两者。报告器还逐字节核验 `matched_control_pairs.csv.gz`、`matched_control_summary.csv`、`control_receipts.csv` 和当前 matched-controls 生成器代码身份；pairs 与 receipts 不解析。随机化种子为 `{matched_manifest.get('seed')}`，所有展示行的配对显著性单位均已核验为日历月。
- 阶段一 manifest 记录 `holdout_consumed={stage_manifest.get('holdout_consumed')}`；本报告不把这轮开发期读作新的 holdout 消耗，也不据此声称独立样本外验证。

## 各周期基线（共同执行的已实现结果）

{_markdown_table(baseline.sort_values(['variant', 'timeframe_min']), [
    ('变体', 'variant', {}), ('周期(分)', 'timeframe_min', {'integer': True}), ('候选', 'candidates', {'integer': True}),
    ('已平仓', 'closed', {'integer': True}), ('均值净R', 'mean_net_r', {'decimal': True}), ('中位净R', 'median_net_r', {'decimal': True}),
    ('胜率', 'win_rate', {'percent': True}), ('≥10R', 'realized_ge_10r', {'percent': True}),
])}

## 冻结单变量候选：`joint_delta_60m > 0`

{_markdown_table(frozen, [
    ('变体', 'variant', {}), ('周期(分)', 'timeframe_min', {'integer': True}), ('基线候选', 'baseline_candidates', {'integer': True}),
    ('保留候选', 'candidates', {'integer': True}), ('保留率', 'candidate_retention', {'percent': True}),
    ('均值净R', 'mean_net_r', {'decimal': True}), ('胜率', 'win_rate', {'percent': True}), ('≥10R保留率', 'exact_entry_10r_retention', {'percent': True}),
])}

阶段一只冻结这一条单变量候选；没有将广度水平、密度、量价扩张、BTC/ETH 背景或其他切片叠加成新规则。

## 匹配随机对照：基线与冻结规则

基线 summary 合计目标 `{_format(total_baseline_targets, integer=True)}`、匹配 `{_format(total_baseline_matched, integer=True)}`，动态匹配率 `{_format(total_baseline_match_rate, percent=True)}`。

{_markdown_table(controls_view, [
    ('变体', 'variant', {}), ('周期(分)', 'timeframe_min', {'integer': True}), ('队列', 'cohort', {}),
    ('目标', 'targets', {'integer': True}), ('匹配', 'matched', {'integer': True}), ('匹配率', 'match_rate', {'percent': True}),
    ('目标均值净R', 'target_mean_net_r', {'decimal': True}), ('对照均值净R', 'control_mean_net_r', {'decimal': True}),
    ('配对差值净R', 'paired_delta_mean_net_r', {'decimal': True}), ('月块 sign-flip p', 'paired_sign_flip_p', {'pvalue': True}),
])}

未匹配原因仍保留在 `matched_control_summary.csv`；低于 100% 的匹配率不得被解释成对照组支持。

## 其他单变量：matched top/bottom 对比（描述性）

{_markdown_table(joined_other, [
    ('变体', 'variant', {}), ('周期(分)', 'timeframe_min', {'integer': True}), ('变量', 'metric', {}), ('分位', 'slice', {}),
    ('候选', 'candidates', {'integer': True}), ('匹配', 'matched', {'integer': True}), ('匹配率', 'match_rate', {'percent': True}),
    ('目标均值净R', 'target_mean_net_r', {'decimal': True}), ('对照均值净R', 'control_mean_net_r', {'decimal': True}),
    ('配对差值净R', 'paired_delta_mean_net_r', {'decimal': True}), ('月块 sign-flip p', 'paired_sign_flip_p', {'pvalue': True}),
])}

该表排除已经冻结的 `joint_delta_60m`。`matched_control_summary.csv` 动态给出 `{descriptive_comparisons}` 个其余变量×top/bottom×cohort 描述性 paired p：最小未校正 p=`{_format(descriptive_min_p, pvalue=True)}`，Bonferroni 上界=`min(1, {descriptive_comparisons} × {_format(descriptive_min_p, pvalue=True)}) = {_format(descriptive_bonferroni_upper, pvalue=True)}`。不得事后挑选其中任何一行来重选变量、阈值或组合规则。

## 跨组合证据与研究建议

本研究**没有预注册**降噪阈值、≥10R 保留阈值或统一验收门；下表只并列拒绝这条冻结规则的证据，且不会改动信号、通知、仓位或注册表。

{_markdown_table(frozen_evidence, [
    ('变体', 'variant', {}), ('周期(分)', 'timeframe_min', {'integer': True}), ('匹配率', 'match_rate', {'percent': True}),
    ('配对差值净R', 'paired_delta_mean_net_r', {'decimal': True}), ('月块 p', 'paired_sign_flip_p', {'pvalue': True}),
    ('基线≥10R', 'baseline_realized_ge_10r_count', {'integer': True}), ('保留≥10R', 'kept_realized_ge_10r_count', {'integer': True}),
    ('≥10R保留率', 'exact_entry_10r_retention', {'percent': True}),
])}

**研究结论：跨 cohort 方向翻转、全部 paired p 不显著及 ≥10R 丢失共同拒绝 `joint_delta_60m > 0` 作为统一硬过滤。它不是其他规则的上线验收，也不可上线。**

## 如何优化（下一轮研究，而非上线）

- 先把市场广度作为市场状态标签或排序维度，保留现有规则的单变量身份，不把它直接变成交易过滤。
- 下一轮只测试一个变量：按信号周期归一化广度窗口，避免把 30m 面板的固定 60m 变化直接当作所有信号周期的同义特征。
- 重算 `launch_density` 时排除目标币，避免候选自身的启动进入它自己的市场环境指标。
- 这些项目需要新的预注册与 owner 决策；在完成独立验证前，不得上线、训练、通知或改仓位。

## 风险与诚实声明

- 这是已消费开发期上的单变量研究及其匹配随机对照，不是新的样本外、前向或实盘证据。
- 匹配对照衡量的是同一候选池与同币/同月/同波动桶随机入场的差异；它不能证明交易成本、流动性、滑点、资金费或实际执行会与历史回放一致。
- 单变量 top/bottom 表包含多个描述性比较，不能把其中看似较好的行当作发现后确认，亦不能绕开冻结规则追加条件。

## 复现命令

```bash
python3 -m yoyo.evaluation.spike_market_breadth_report \\
  --stage-one {stage_one} \\
  --matched {matched} \\
  --report {report}
```
""", encoding="utf-8")
    return {
        "report": str(report), "stage_one_manifest_sha256": sha256(stage_one / "manifest.json"),
        "matched_manifest_sha256": sha256(matched / "manifest.json"), "cohorts": len(frozen_evidence),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-one", type=Path, default=DEFAULT_STAGE_ONE)
    parser.add_argument("--matched", type=Path, default=DEFAULT_MATCHED)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    build_spike_market_breadth_report(args.stage_one, args.matched, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

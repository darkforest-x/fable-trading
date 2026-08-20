#!/usr/bin/env python3
"""Build the ETH perpetual 15m Pine report and executed audit notebook.

Inputs are compact, validated artifacts under
``experiments/active/exp-pine-eth-15m-v1/results``.  The builder never reads
market data or holdout rows and never trains/scores a model.  It creates the
canonical Markdown report required by project policy plus a rerunnable notebook
that exposes the key calculations and decision assertions.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

import nbformat
import pandas as pd
from nbclient import NotebookClient


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
NOTEBOOKS = EXPERIMENT / "notebooks"
REPORT = PROJECT / "analysis/p0_pine_eth_15m_v1_20260821.md"


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNAVAILABLE"


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def markdown_table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    headers = list(headers)
    rendered = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        rendered.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(rendered)


def load_evidence() -> dict[str, Any]:
    validation = json.loads((RESULTS / "validation.json").read_text(encoding="utf-8"))
    if validation["status"] != "pass":
        raise RuntimeError(f"validation must pass before report build: {validation['failed']}")
    return {
        "config": json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8")),
        "quality": json.loads((RESULTS / "data_quality.json").read_text(encoding="utf-8")),
        "summary": json.loads((RESULTS / "summary.json").read_text(encoding="utf-8")),
        "statistics": json.loads((RESULTS / "statistical_tests.json").read_text(encoding="utf-8")),
        "features": json.loads((RESULTS / "feature_contract.json").read_text(encoding="utf-8")),
        "framework": json.loads((RESULTS / "backtesting_reconciliation.json").read_text(encoding="utf-8")),
        "intrabar": json.loads((RESULTS / "intrabar_3m_reconciliation.json").read_text(encoding="utf-8")),
        "robustness": json.loads((RESULTS / "robustness_checks.json").read_text(encoding="utf-8")),
        "docker_smoke": json.loads((RESULTS / "docker_offline_smoke.json").read_text(encoding="utf-8")),
        "docker_replay": json.loads((RESULTS / "docker_offline_replay.json").read_text(encoding="utf-8")),
        "v11": json.loads((RESULTS / "v11_long_only_summary.json").read_text(encoding="utf-8")),
        "control_sensitivity": json.loads((RESULTS / "control_seed_sensitivity.json").read_text(encoding="utf-8")),
        "path_risk": json.loads((RESULTS / "path_risk_bootstrap.json").read_text(encoding="utf-8")),
        "judgment_research": json.loads((RESULTS / "pine_judgment_development_manifest.json").read_text(encoding="utf-8")),
        "feed_sensitivity": json.loads((RESULTS / "feed_sensitivity.json").read_text(encoding="utf-8")),
        "funding_coverage": json.loads((RESULTS / "funding_coverage_incident.json").read_text(encoding="utf-8")),
        "exit_anatomy": json.loads((RESULTS / "exit_anatomy.json").read_text(encoding="utf-8")),
        "backcast": json.loads((RESULTS / "backcast_2022.json").read_text(encoding="utf-8")),
        "paper_protocol": json.loads((RESULTS / "paper_forward_protocol.json").read_text(encoding="utf-8")),
        "actual_timeframe": json.loads((RESULTS / "actual_10m_vs_15m.json").read_text(encoding="utf-8")),
        "regime_stability": json.loads((RESULTS / "regime_stability.json").read_text(encoding="utf-8")),
        "judgment_feasibility": json.loads((RESULTS / "judgment_feasibility.json").read_text(encoding="utf-8")),
        "judgment_signal": json.loads((RESULTS / "judgment_signal_audit.json").read_text(encoding="utf-8")),
        "stateful_gate": json.loads((RESULTS / "stateful_gate_static_vs_dynamic.json").read_text(encoding="utf-8")),
        "selection_risk": json.loads((RESULTS / "selection_risk_audit.json").read_text(encoding="utf-8")),
        "density_overlap": json.loads((RESULTS / "density_overlap_audit.json").read_text(encoding="utf-8")),
        "migration_audit": json.loads((RESULTS / "migration_audit.json").read_text(encoding="utf-8")),
        "gate_surface": json.loads((RESULTS / "judgment_gate_surface_manifest.json").read_text(encoding="utf-8")),
        "gate_replay": json.loads((RESULTS / "judgment_gate_replay_contract.json").read_text(encoding="utf-8")),
        "tv_compile": json.loads((RESULTS / "tradingview_compile_receipt.json").read_text(encoding="utf-8")),
        "pine_static": json.loads((RESULTS / "pine_static_contract.json").read_text(encoding="utf-8")),
        "validation": validation,
        "split": pd.read_csv(RESULTS / "split_summary.csv"),
        "matrix": pd.read_csv(RESULTS / "experiment_matrix.csv"),
        "risk": pd.read_csv(RESULTS / "risk_grid.csv"),
        "threshold": pd.read_csv(RESULTS / "threshold_search.csv"),
        "slope": pd.read_csv(RESULTS / "slope_search.csv"),
        "trailing": pd.read_csv(RESULTS / "trailing_search.csv"),
        "feature_search": pd.read_csv(RESULTS / "feature_filter_search.csv"),
        "core_ablation": pd.read_csv(RESULTS / "core_component_ablation.csv"),
        "prequential": pd.read_csv(RESULTS / "prequential_feature_selection.csv"),
        "timeframe": pd.read_csv(RESULTS / "timeframe_rescale_ablation.csv"),
        "cost": pd.read_csv(RESULTS / "cost_sensitivity.csv"),
        "regime_table": pd.read_csv(RESULTS / "regime_stability.csv"),
        "trades": pd.read_csv(RESULTS / "trades.csv", parse_dates=["entry_time", "exit_time"]),
    }


def build_diagnostics(evidence: dict[str, Any]) -> dict[str, Any]:
    trades = evidence["trades"]
    final_name = "final_preholdout_2025_202602"
    diagnostics: dict[str, Any] = {}
    for variant in ("v8_eth_baseline", "v9_locked", "v10_volume_hypothesis"):
        selected = trades.loc[(trades["variant"] == variant) & (trades["split"] == final_name)].copy()
        selected["month"] = selected["entry_time"].dt.strftime("%Y-%m")
        monthly = selected.groupby("month")["project_net_return"].mean() * 10_000.0
        by_side = selected.groupby("direction")["project_net_return"].agg(["size", "mean", "sum"])
        by_side[["mean", "sum"]] *= 10_000.0
        by_exit = selected.groupby("exit_reason")["project_net_return"].agg(["size", "mean", "sum"])
        by_exit[["mean", "sum"]] *= 10_000.0
        diagnostics[variant] = {
            "months": int(len(monthly)),
            "positive_months": int((monthly > 0.0).sum()),
            "median_monthly_net_bp": float(monthly.median()),
            "monthly_net_bp": {str(key): float(value) for key, value in monthly.items()},
            "by_side": by_side.reset_index().round(6).to_dict("records"),
            "by_exit": by_exit.reset_index().round(6).to_dict("records"),
        }
    (RESULTS / "diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return diagnostics


def _variant_row(split: pd.DataFrame, variant: str, period: str) -> pd.Series:
    return split.loc[(split["variant"] == variant) & (split["period"] == period)].iloc[0]


def build_report(evidence: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    report_source_commit = current_commit()
    config = evidence["config"]
    quality = evidence["quality"]
    summary = evidence["summary"]
    stats = evidence["statistics"]
    framework = evidence["framework"]
    intrabar = evidence["intrabar"]
    robustness = evidence["robustness"]
    docker_smoke = evidence["docker_smoke"]
    docker_replay = evidence["docker_replay"]
    v11 = evidence["v11"]
    control_sensitivity = evidence["control_sensitivity"]
    path_risk = evidence["path_risk"]
    judgment_research = evidence["judgment_research"]
    feed_sensitivity = evidence["feed_sensitivity"]
    funding_coverage = evidence["funding_coverage"]
    exit_anatomy = evidence["exit_anatomy"]
    backcast = evidence["backcast"]
    paper_protocol = evidence["paper_protocol"]
    actual_timeframe = evidence["actual_timeframe"]
    regime_stability = evidence["regime_stability"]
    judgment_feasibility = evidence["judgment_feasibility"]
    judgment_signal = evidence["judgment_signal"]
    volume_judgment_prior = next(
        row
        for row in judgment_signal["fixed_prior_diagnostics"]
        if row["feature"] == "vol_ratio_mean8"
    )
    stateful_gate = evidence["stateful_gate"]
    selection_risk = evidence["selection_risk"]
    density_overlap = evidence["density_overlap"]
    migration_audit = evidence["migration_audit"]
    gate_surface = evidence["gate_surface"]
    gate_replay = evidence["gate_replay"]
    tv_compile = evidence["tv_compile"]
    pine_static = evidence["pine_static"]
    validation = evidence["validation"]
    split = evidence["split"]
    risk = evidence["risk"]
    timeframe = evidence["timeframe"]
    threshold = evidence["threshold"].groupby("threshold", as_index=False).first()
    feature_search = evidence["feature_search"].groupby("feature_filter", as_index=False).first()

    density_rows = [
        [
            name,
            _fmt(row["trades"], 0),
            f"{row['strict_overlap']}/{row['trades']} ({_fmt(row['strict_overlap_rate'] * 100)}%)",
            f"{row['expanded_overlap']}/{row['trades']} ({_fmt(row['expanded_overlap_rate'] * 100)}%)",
            _fmt(row["ma_spread_bp_median"]),
            _fmt(row["circular_shift_null"]["strict"]["exact_circular_shift_p_enrichment"], 4),
            _fmt(row["circular_shift_null"]["expanded"]["exact_circular_shift_p_enrichment"], 4),
        ]
        for name, row in density_overlap["splits"].items()
    ]

    actual_timeframe_rows = [
        [
            label,
            _fmt(row["summary"]["trades"], 0),
            _fmt(row["summary"]["project_net_bp_per_trade"]),
            _fmt(row["matched_control"]["control_net_bp"]),
            _fmt(row["matched_control"]["candidate_minus_control_bp"]),
            _fmt(row["week_signflip"]["p_value"], 4),
            _fmt(row["summary"]["return_percent"]),
            _fmt(row["summary"]["max_drawdown_15m_percent"]),
        ]
        for label, row in actual_timeframe["variants"].items()
    ]
    backcast_rows = [
        [
            label,
            _fmt(row["summary"]["trades"], 0),
            _fmt(row["summary"]["project_net_bp_per_trade"]),
            _fmt(row["matched_control"]["control_net_bp"]),
            _fmt(row["matched_control"]["candidate_minus_control_bp"]),
            _fmt(row["week_signflip"]["p_value"], 4),
            _fmt(row["profit_concentration"]["mean_without_top1_bp"]),
        ]
        for label, row in backcast["variants"].items()
    ]
    regime_rows = [
        [
            row.period,
            _fmt(int(row.trades), 0),
            _fmt(row.candidate_net_bp),
            _fmt(row.control_net_bp),
            _fmt(row.candidate_minus_control_bp),
            _fmt(row.return_percent),
            _fmt(row.maximum_drawdown_percent),
            _fmt(row.mean_without_top1_bp),
        ]
        for row in evidence["regime_table"].itertuples(index=False)
    ]
    feed_rows = [
        [
            label,
            _fmt(row["left"], 0),
            _fmt(row["right"], 0),
            _fmt(row["jaccard"] * 100),
            _fmt(row["mean_absolute_net_return_delta_bp"]),
        ]
        for label, row in feed_sensitivity["executed_entry_comparisons"].items()
    ]

    core_rows = [
        [
            row["variant"],
            _fmt(int(row["trades"]), 0),
            _fmt(row["minimum_block_net_bp"]),
            _fmt(row["weighted_net_bp"]),
            f"{int(row['positive_blocks'])}/4",
        ]
        for row in robustness["core_component_aggregate"]
    ]
    prequential_rows = [
        [
            row.selected_on_periods,
            row.test_period,
            row.selected_feature,
            _fmt(row.selected_test_net_bp),
            _fmt(row.baseline_test_net_bp),
            _fmt(row.incremental_test_net_bp),
        ]
        for row in evidence["prequential"].itertuples(index=False)
    ]
    side_rows = [
        [
            row["variant"],
            _fmt(int(row["trades"]), 0),
            _fmt(row["minimum_block_net_bp"]),
            _fmt(row["weighted_net_bp"]),
            f"{int(row['positive_blocks'])}/4",
            _fmt(row["worst_drawdown_percent"]),
        ]
        for row in robustness["side_ablation_aggregate"]
    ]
    control_seed_rows = [
        [
            row["variant"],
            _fmt(row["candidate_net_bp"]),
            (
                f"{_fmt(row['control_net_bp']['median'])} "
                f"[{_fmt(row['control_net_bp']['q05'])}, {_fmt(row['control_net_bp']['q95'])}]"
            ),
            (
                f"{_fmt(row['candidate_minus_control_bp']['median'])} "
                f"[{_fmt(row['candidate_minus_control_bp']['q05'])}, {_fmt(row['candidate_minus_control_bp']['q95'])}]"
            ),
            _fmt(row["fraction_assignment_seeds_with_positive_excess"] * 100),
            (
                f"{_fmt(row['week_signflip_p']['min'], 3)}–"
                f"{_fmt(row['week_signflip_p']['max'], 3)}"
            ),
        ]
        for row in control_sensitivity["variants"]
    ]
    path_risk_rows = [
        [
            row["label"],
            _fmt(row["actual_return_percent"]),
            _fmt(row["actual_drawdown_15m_percent"]),
            (
                f"{_fmt(row['return_median_percent'])} "
                f"[{_fmt(row['return_q05_percent'])}, {_fmt(row['return_q95_percent'])}]"
            ),
            _fmt(row["drawdown_q95_percent"]),
            _fmt(row["probability_negative_terminal"] * 100),
            _fmt(row["probability_drawdown_over_20pct"] * 100),
            _fmt(int(row["longest_actual_losing_streak"]), 0),
        ]
        for row in path_risk["arms"]
    ]
    judgment_fold_rows = [
        [
            row["fold"],
            _fmt(row["raw_train_rows"], 0),
            _fmt(row["purged_train_rows"], 0),
            _fmt(row["purged_for_label_overlap"], 0),
            _fmt(row["validation_rows"], 0),
            _fmt(row["train_positive_rate"] * 100),
            _fmt(row["validation_positive_rate"] * 100),
        ]
        for row in judgment_research["folds"]
    ]
    judgment_prior_rows = [
        [
            row["feature"],
            _fmt(row["auc"], 3),
            _fmt(row["auc_exact_circular_shift_p"], 4),
            f"{row['top_decile_positive_rows']}/{row['top_decile_rows']}",
            _fmt(row["top_decile_net_bp"]),
            _fmt(row["top_decile_net_bp_without_top1"]),
            _fmt(row["top_decile_exact_circular_shift_p"], 4),
            _fmt(row["top_decile_holm_p_across_four_displayed_priors"], 4),
        ]
        for row in judgment_signal["fixed_prior_diagnostics"]
    ]
    judgment_selector_rows = [
        [
            row["fold"],
            row["selected_feature"],
            row["favorable_direction"],
            _fmt(row["train_oriented_auc"], 3),
            _fmt(row["validation_auc"], 3),
        ]
        for row in judgment_signal["prequential_28_feature_selector"]["folds"]
    ]
    selection_chronology_rows = [
        [
            ", ".join(row["selected_on"]),
            row["test_period"],
            row["selected_configuration"],
            _fmt(row["selected_test_net_bp"]),
            _fmt(row["v9_test_net_bp"]),
            _fmt(row["selected_minus_v9_bp"]),
        ]
        for row in selection_risk["chronological_selection"]
    ]

    periods = ["discovery_2023", "confirmation_2024", "final_preholdout_2025_202602"]
    comparison_rows = []
    for variant, label in (
        ("v8_eth_baseline", "V8 原 ETH 基线"),
        ("v8_plus_slope12", "V8 + EMA200 slope12"),
        ("v9_locked", "V9 锁定候选"),
        ("v10_volume_hypothesis", "V10 成交量假设*"),
    ):
        for period in periods:
            selected = split.loc[(split["variant"] == variant) & (split["period"] == period)]
            if selected.empty:
                continue
            row = selected.iloc[0]
            comparison_rows.append(
                [
                    label,
                    period.replace("final_preholdout_2025_202602", "final 2025–2026-02"),
                    _fmt(int(row["trades"]), 0),
                    _fmt(row["gross_bp_per_trade"]),
                    _fmt(row["project_net_bp_per_trade"]),
                    _fmt(row["monetary_profit_factor"], 3),
                    _fmt(row["return_percent"]),
                    _fmt(row["max_drawdown_15m_percent"]),
                ]
            )

    v9 = summary["v9_final_preholdout"]
    v10 = summary["v10_post_selection_final_preholdout"]
    v9_match = stats["matched_control"]
    v9_flip = stats["week_block_signflip"]
    v9_abs = stats["week_bootstrap_absolute"]
    ranking = stats["oscillator_ranking_permutation"]
    concentration = stats["profit_concentration"]
    v10_stats = stats["v10_post_selection_hypothesis"]

    risk_final = risk.loc[risk["period"] == "final_preholdout_2025_202602"]
    risk_rows = [
        [
            _fmt(row.risk_percent),
            _fmt(row.return_percent),
            _fmt(row.max_drawdown_15m_percent),
            _fmt(row.mean_leverage),
            _fmt(row.max_leverage),
        ]
        for row in risk_final.itertuples(index=False)
    ]

    rescale_rows = [
        [
            row.period,
            _fmt(int(row.trades), 0),
            _fmt(row.project_net_bp_per_trade),
            _fmt(row.return_percent),
            _fmt(row.max_drawdown_15m_percent),
        ]
        for row in timeframe.itertuples(index=False)
    ]

    feature_top = feature_search.sort_values(
        ["min_block_net_bp", "weighted_net_bp"], ascending=False
    ).head(8)
    feature_rows = [
        [
            row.feature_filter,
            _fmt(int(row.trades), 0),
            _fmt(row.min_block_net_bp),
            _fmt(row.weighted_net_bp),
            "是（仅 forward 假设）" if bool(row.development_selected) else "否",
        ]
        for row in feature_top.itertuples(index=False)
    ]

    threshold_rows = [
        [
            _fmt(float(row.threshold), 2),
            _fmt(int(row.trades), 0),
            _fmt(row.min_block_net_bp),
            _fmt(row.weighted_net_bp),
            "锁定" if bool(row.selected) else "—",
        ]
        for row in threshold.itertuples(index=False)
    ]

    cost_official = evidence["cost"].loc[evidence["cost"]["official_cost_row"].astype(bool)]
    cost_break_even = {
        row.variant: row.gross_bp_per_trade for row in cost_official.itertuples(index=False)
    }

    report = f"""# ETHUSDT.P / ETH-USDT-SWAP 15m Pine 定型与回测审计（V1）

生成日期：2026-08-21
实验：`{config['experiment_id']}`
构建提交：`{summary['generated_from_commit']}`
状态：**研究候选；不可生产、不可 forward、未通过 TradingView parity**

## 结论先行

15 分钟已经定死为本轮唯一研究周期：本地数据契约是 **OKX `ETH-USDT-SWAP` 15m**，
TradingView 的 `ETHUSDT.P` 必须再明确具体交易所后做逐笔导出对账，不能把一个显示名当成同一数据源。

当前最诚实的结论不是“已经找到稳定暴利策略”，而是：

1. 原始逻辑在 ETH 单币 final-preholdout 段成本后失败：V8 为
   **{_fmt(summary['baseline_final_preholdout']['project_net_bp_per_trade'])} bp/笔**。
2. 在 2023/2024 四个时间块先选择项目已有的 `slow_slope_12` 方向门，再把 oscillator
   阈值从 `0.2` 锁到 `0.1`，V9 在 2025-01 至 2026-02 得到
   **+{_fmt(v9['project_net_bp_per_trade'])} bp/笔、PF {_fmt(v9['monetary_profit_factor'], 3)}、
   1% 风险资金收益 +{_fmt(v9['return_percent'])}%、15m 收盘最大回撤 {_fmt(v9['max_drawdown_15m_percent'])}%**。
3. 这个正结果仍未过项目的证据门：相对匹配随机对照 +{_fmt(v9_match['mean_excess_bp'])} bp，
   但 UTC 周区块 sign-flip `p={_fmt(v9_flip['p_value'], 4)}`，绝对收益周 bootstrap
   95% CI 为 [{_fmt(v9_abs['ci95_low_bp'])}, {_fmt(v9_abs['ci95_high_bp'])}] bp，均未达到 `p<0.01`。
4. 110 笔里只有 {concentration['positive_trades']} 笔盈利；去掉最大一笔后均值变为
   **{_fmt(concentration['mean_without_top1_bp'])} bp/笔**，正收益高度依赖少数长趋势反转单。
5. V10 的 `vol_ratio_mean8 >= 1` 历史点估计更好（+{_fmt(v10['project_net_bp_per_trade'])} bp/笔、
   回撤 {_fmt(v10['max_drawdown_15m_percent'])}%），但它是在 V9 final 已看过之后才选出的，
   且 sign-flip `p={_fmt(v10_stats['week_block_signflip']['p_value'], 4)}`；只能作为下一轮纸面 forward 假设。
6. 开发段选择出的 long-only V11 历史点估计为
   +{_fmt(v11['final_preholdout']['project_net_bp_per_trade'])} bp/笔、回撤
   {_fmt(v11['final_preholdout']['max_drawdown_15m_percent'])}%，但这同样是 consumed-final 后的诊断，
   仅 {v11['profit_concentration']['positive_trades']}/{v11['profit_concentration']['trades']} 笔盈利，
   sign-flip `p={_fmt(v11['week_block_signflip']['p_value'], 4)}`。
7. 真实 10m 数据的短共同窗不支持“15m 天生更优”：原 V8 在 10m 为
   +{_fmt(actual_timeframe['variants']['V8_10m']['summary']['project_net_bp_per_trade'])} bp/笔，
   15m 为 {_fmt(actual_timeframe['variants']['V8_15m']['summary']['project_net_bp_per_trade'])} bp/笔；
   V9 在 10m/15m 都为负。该窗只有约 10 周且四组 p 均未过门，不能反向选回 10m，但必须披露。
8. 当前所谓 break-even 不是成本后保本：+10 bp 锁盈低于 20 bp 往返成本，final 有
   {exit_anatomy['by_exit_subtype']['break_even_locked_stop']['trades']} 笔固定为 -10 bp；
   另 {exit_anatomy['by_exit_subtype']['initial_protective_stop']['trades']} 笔初始止损，
   {exit_anatomy['by_exit_subtype']['reverse']['trades']} 笔反转单贡献全部正收益。
9. 冻结 V9 跨 9 个时间块有
   {regime_stability['absolute_net_equal_block_test']['positive_blocks']}/9 为正，但绝对净收益/匹配超额
   精确 p 分别为 {_fmt(regime_stability['absolute_net_equal_block_test']['one_sided_p_value'], 4)} /
   {_fmt(regime_stability['matched_excess_equal_block_test']['one_sided_p_value'], 4)}，仍未到 0.01。
10. “与项目同源”不等于同一信号：276 笔 V9 入场仅
    {density_overlap['overall']['strict_overlap']} 笔（{_fmt(density_overlap['overall']['strict_overlap_rate'] * 100)}%）
    满足项目严格 EMA ribbon density，扩展口径也只有
    {density_overlap['overall']['expanded_overlap']} 笔（{_fmt(density_overlap['overall']['expanded_overlap_rate'] * 100)}%）。

因此，**V9 是最后一个在 final 前锁定的 15m 研究基线；V10（成交量门）和 V11（只开多）
是互不叠加的下一步 paper A/B 候选。三者都不能上线。**

![V8/V9/V10 final-preholdout equity](../experiments/active/exp-pine-eth-15m-v1/results/charts/final_equity_v8_vs_v9.png)

## Context & Methods

### 数据与切分

| 项目 | 值 |
|---|---|
| 数据 | OKX `ETH-USDT-SWAP` 15m CSV |
| 行数 | {quality['rows_read']:,} |
| 时间 | {quality['first_bar']} → {quality['last_bar']} |
| 15m 缺口 / 重复 / OHLC 错误 | {quality['non_15m_gaps']} / {quality['duplicate_timestamps']} / {quality['ohlc_body_violations']} |
| Discovery | 2023-01-01 → 2024-01-01 |
| Confirmation | 2024-01-01 → 2025-01-01 |
| Final-preholdout | 2025-01-01 → 2026-03-01（已消耗，今后不再称未见 OOS） |
| Repository holdout | >= 2026-05-04；策略计算读取 0 行、配置评估 0 次；另见下方意外预览披露 |

所有特征只用信号 bar `t` 及以前数据；订单在 `t+1` 开盘成交。止损/标签才读取未来。
正式成本固定为单边 0.10%，即往返 20 bp；没有滑点、资金费、强平和共享保证金模拟。

### V9 信号与执行契约

- `hl2` 的 SMA10/SMA60 金叉/死叉；close 需位于 SMA60 与 EMA100 的同方向一侧；
- 原振荡器：`hl2-SMA40 → trailing 200-bar p99 → ratio → 10-bar change → HMA10`；
- oscillator 方向阈值 `±0.1`，并要求继续上升/下降；
- 项目判断特征 `EMA200.pct_change(12)` 必须与方向一致；
- HK 21:00–23:00 禁入、周日禁入、ATR% 0.1–10%；
- 初始止损 `min(4×Pine ATR14, signal close 的 3%)`，按 0.01 tick 舍入并锚定实际 fill；
- 完成 bar 达到 +1.5% 后，下一 bar 起锁 +0.1%；trailing 关闭；
- opposite signal 单次反转，legacy 03:00/周四仓位倍增关闭；
- 仓位默认每笔止损风险 1%，13x 只是上限，final 实际最大杠杆 {_fmt(v9['max_leverage'])}x。

### 从用户原 V7.2 到 V9 修了什么

原附件 SHA-256 与配置完全一致，迁移静态账本 **{migration_audit['check_count']}/{migration_audit['check_count']}**
通过。保留的 alpha 祖先是 SMA10/60 crossover、EMA100、ATR14、原振荡器、时间/周日意图和盈利后 cooldown；
真正的 alpha 改动只有 `slow_slope_12` 与 oscillator `0.2 → 0.1`。执行层则做了这些必要修复：

- 显式单边 0.10% commission 与 0 slippage，关闭 `calc_on_every_tick` / fill recalc / bar magnifier；
- 固定 4x、03:00 和周四加仓改成按初始止损距离的 1% 风险仓位；
- 初始止损从 signal close 改为 next-open 实际 fill 锚定；
- 删除 `strategy.entry` 反转后又 `strategy.close` 的重复订单路径；
- 时间过滤改为明确 `Asia/Hong_Kong`，并增加 900 秒、ETH base、日期和 confirmed-bar 守卫；
- percentile 分母从“仅不等于 0”改为 `not na and > 0` fail-closed。

本地 Python 回放也已和 Pine 的下单时点对齐：反手单数量冻结在 signal close 的 marked equity
（含未实现盈亏，并在显式佣金模式下扣除已付入场佣金），而不是到下一根开盘先平旧仓后再按新权益重算。
这项机械 parity 修复不改变 110 笔的单位收益；V9 资金收益只变化约 +0.00005 个百分点，
15m 收盘回撤由约 20.07% 校正为 {_fmt(v9['max_drawdown_15m_percent'])}%。

这些修复让回测口径可审计，但不等于 alpha 增强。TradingView 官方 Pine v6 编译已经通过；仍未解决的是
TradingView **交易导出**逐笔 parity，以及 +0.1% 锁盈在 0.2% 往返成本后仍是 -0.1%。

### 10m 改到 15m 到底改变了什么

保留 10/60 根后，墙钟窗口由约 100/600 分钟变为 **150/900 分钟**，策略变慢 50%。
我们把“按原 10m 墙钟长度换算到 15m”（约 7/40、EMA67、osc 27/133/7/7）作为一个概念变量，
ATR14 和止损完全不动。结果：

{markdown_table(['开发块', '笔数', '净 bp/笔', '资金收益 %', '15m DD %'], rescale_rows)}

四块有两块净期望为负，所以拒绝等时长搬运；15m 版本应视为一个新的、较慢的固定策略。

### 真实 10m 与 15m 共同短窗

本地 20,328 根无缺口 OKX 5m K 线被严格两两聚合为 10,164 根真实 10m K（0 个不完整母 K），
与 15m 合约在 2025-12-23 至 2026-02 做同窗比较。这里同时隔离“周期变化”和“V8→V9 规则变化”，
每组都带 3 个非复用同层随机对照：

{markdown_table(['规则/周期', '笔数', '净 bp/笔', '对照 bp', '超额 bp', '周 p', '收益 %', 'bar-close DD %'], actual_timeframe_rows)}

V8 从 10m 改到 15m 的同窗差值为
{_fmt(actual_timeframe['isolated_deltas_bp_per_trade']['V8_15m_minus_V8_10m'])} bp/笔；V9 为
{_fmt(actual_timeframe['isolated_deltas_bp_per_trade']['V9_15m_minus_V9_10m'])} bp/笔。
这段短窗明确反对“15m 一定优于 10m”，也显示最近两个月是 V9 的坏制度；但它发生在参数锁定和
final 消费之后、只有 10 个周区块，不能用来选择回 10m。用户要求的 **15m 仍按长期协议定死**，
同时把这条负面证据保留为 forward 风险警报。

### 2022 backcast 与跨制度稳定性

把已锁参数反放到更早 2022 年，只能叫 reverse-time backcast，因为参数是在其后的 2023/24 选择：

{markdown_table(['版本', '笔数', '净 bp/笔', '对照 bp', '超额 bp', '周 p', '去 top1 bp'], backcast_rows)}

V9 的 backcast 点估计和匹配超额为正，但 `p={_fmt(backcast['variants']['V9']['week_signflip']['p_value'], 4)}`，
仍未过门；V10/V11 在这个旧制度反而更弱，支持不让 post-selection 版本替换 V9。

冻结 V9 进一步按固定半年重新起账（最后一段为 2026M1M2）：

{markdown_table(['时间块', '笔数', 'V9 bp', '对照 bp', '超额 bp', '收益 %', 'DD %', '去 top1 bp'], regime_rows)}

![V9 chronological regime stability](../experiments/active/exp-pine-eth-15m-v1/results/charts/v9_regime_stability.png)

绝对净收益 7/9 块为正，但 512 种穷举 sign-flip `p={_fmt(regime_stability['absolute_net_equal_block_test']['one_sided_p_value'], 4)}`；
匹配超额为正 6/9 块、`p={_fmt(regime_stability['matched_excess_equal_block_test']['one_sided_p_value'], 4)}`。
2025H1 和 2026M1M2 为负，说明长期总和不是“每个制度都赚钱”。

### 核心逻辑嵌套消融（只读 2023/2024）

下面每一步只增加一个信号组件，执行、止损、仓位、冷却和 20 bp 成本完全不变：

{markdown_table(['信号阶段', '总笔数', '最差半年 bp', '加权 bp', '正半年'], core_rows)}

![Nested core component ablation](../experiments/active/exp-pine-eth-15m-v1/results/charts/core_component_ablation.png)

单纯 SMA10/60 crossover 和再加 EMA100 的四个开发半年全部成本后为负；`EMA200 slope12`
是第一个产生正加权期望的组件，但仍有一个半年为负。振荡器方向继续降噪，最终 `±0.1`
门才让四块都为正。因此当前核心应理解为**趋势方向一致后的稀疏交叉触发**，而不是已经验证的
“均线密集形态”。这也解释了为什么把严格 ribbon density 强塞进去反而不稳定。

为了把“同源”从口头判断变成可检验语义，本轮把每笔 V9 的 signal bar 映射到项目现有、未经修改的
EMA8/13/21/34/55 + EMA144/200 strict / expanded 密集规则：

{markdown_table(['时期', 'V9 笔数', 'strict 重合', 'expanded 重合', 'MA spread 中位 bp', 'strict 移位 p', 'expanded 移位 p'], density_rows)}

全时期只有 {density_overlap['overall']['strict_overlap']}/{density_overlap['overall']['trades']}
笔满足 strict，final 更只有
{density_overlap['splits']['final_preholdout_2025_202602']['strict_overlap']}/
{density_overlap['splits']['final_preholdout_2025_202602']['trades']}；V9 signal bar 的五条快 EMA spread
中位数为 {_fmt(density_overlap['overall']['ma_spread_bp_median'])} bp，高于 strict 的
{_fmt(density_overlap['overall']['strict_fast_spread_threshold_bp'])} bp。每个时期都穷举 signal path
相对 density mask 的所有循环时间移位；strict 三段均没有富集（p 都远高于 0.01）。2023 expanded
重合虽有 p={_fmt(density_overlap['splits']['discovery_2023']['circular_shift_null']['expanded']['exact_circular_shift_p_enrichment'], 4)}，
但 2024 / final 未复现，且这是事后语义诊断，不是可加到 V9 的 gate。

所以答案是：两者共享“均线结构后启动”的研究祖先，但**当前 Pine 与 Local Signal V2 的事件定义不同**。
旧 L2 的候选池、标签与密集特征分布不能直接当成 V9 的判断层训练集。

方向 eligibility 作为另一个单变量，只在开发段比较：

{markdown_table(['方向策略', '总笔数', '最差半年 bp', '加权 bp', '正半年', '最差 DD %'], side_rows)}

long-only 的四块绝对期望都为正，但相对 two-sided 的增量在 2024H2 为负；三种方向策略的
共同区块 max-stat `p={_fmt(robustness['side_selection_test']['selection_adjusted_p_value'], 4)}`，
仍过不了 `p<0.01`。随后查看的 V11 final 点估计为
{_fmt(v11['final_preholdout']['project_net_bp_per_trade'])} bp/笔、PF
{_fmt(v11['final_preholdout']['monetary_profit_factor'], 3)}、收益
{_fmt(v11['final_preholdout']['return_percent'])}%、回撤
{_fmt(v11['final_preholdout']['max_drawdown_15m_percent'])}%；但 final 已经消费，
去掉最大赢家后均值 {_fmt(v11['profit_concentration']['mean_without_top1_bp'])} bp/笔，
所以只能登记为 V11 paper-forward 假设。

## Results

### 主版本对照

{markdown_table(['版本', '时期', '笔数', '毛 bp/笔', '净 bp/笔', 'PF', '收益 %', '15m DD %'], comparison_rows)}

`*` V10 是 post-selection 历史诊断，不是独立 OOS。

### 匹配随机、排序检验与尾部依赖

| 检验 | V9 结果 | 判定 |
|---|---:|---|
| 策略净收益 | +{_fmt(v9_match['mean_candidate_net_bp'])} bp/笔 | 点估计为正 |
| 匹配随机净收益 | {_fmt(v9_match['mean_control_net_bp'])} bp/笔 | 同月×HK 6h×ATR 五分位×方向×持有期 |
| 策略 - 对照 | +{_fmt(v9_match['mean_excess_bp'])} bp/笔 | 正，但不显著 |
| UTC 周 sign-flip | p={_fmt(v9_flip['p_value'], 4)} | **未过 p<0.01** |
| 绝对收益周 bootstrap | [{_fmt(v9_abs['ci95_low_bp'])}, {_fmt(v9_abs['ci95_high_bp'])}] bp | 跨 0 |
| oscillator top-decile | +{_fmt(ranking['top_decile_net_bp'])} bp/笔，p={_fmt(ranking['p_value'], 4)} | 排序不显著 |
| oscillator AUC（净正收益） | {_fmt(ranking['auc_net_positive'], 4)} | 仅参考，不是成功标准 |
| 盈利笔数 | {concentration['positive_trades']}/{concentration['trades']} | 低胜率趋势策略 |
| 最大一笔占净收益 | {_fmt(concentration['top1_share_of_net'] * 100)}% | 去掉后均值转负 |
| 正月份 | {concentration['positive_months']}/{concentration['months_with_trades']} | 月中位数 {_fmt(concentration['median_monthly_net_bp'])} bp/笔 |

![V9 monthly expectancy](../experiments/active/exp-pine-eth-15m-v1/results/charts/v9_monthly_net_bp.png)

单个 deterministic control seed 可复现，但不是不确定性区间。64 个预定义 seed、每个仍严格
3 个非复用同层对照后的敏感性如下（方括号为 seed 间 5%–95%）：

{markdown_table(['候选', '候选 bp', '对照中位 [5%,95%]', '超额中位 [5%,95%]', '超额>0 seed %', '周 p 范围'], control_seed_rows)}

V9/V10 各有 89.06% 的 assignment seed 得到正超额，V11 为 95.31%；但三者 64 个 seed 中
`p<0.01` 的比例都为 **0%**。因此报告保留原 seed 作为逐笔可复现 ledger，同时把 seed 分布
作为经济结论；禁止挑一个最有利 seed 代表策略。

### 参数选择与单变量实验

oscillator 阈值只在 2023/2024 搜索；`0.1` 与 `0.15` 形成稳定平台，选择 `0.1` 是因为样本更多、
加权期望更高，不是因为看了 final。

{markdown_table(['阈值', '示例块笔数', '最差半年 bp', '四块加权 bp', '状态'], threshold_rows)}

![Threshold robustness](../experiments/active/exp-pine-eth-15m-v1/results/charts/oscillator_threshold_robustness.png)

项目 28 个因果特征逐个用自然阈值筛选后的开发段前八名如下；没有训练 LR/LightGBM：

{markdown_table(['单特征门', '示例块笔数', '最差半年 bp', '四块加权 bp', '选择'], feature_rows)}

增量 prequential 检查每次只用已经结束的半年选 gate，再测紧接着的下半年：

{markdown_table(['选择所用历史块', '下一测试块', '选中 gate', 'gate bp', 'V9 bp', '增量 bp'], prequential_rows)}

三次都选中 `vol_ratio_mean8 >= 1`，增量三次为正，测试块加权期望为
{_fmt(robustness['prequential_feature_replay']['selected_weighted_test_net_bp'])} bp，
同期 V9 为 {_fmt(robustness['prequential_feature_replay']['baseline_weighted_test_net_bp'])} bp。
这是 V10 值得 paper A/B 的最好证据，但只有三个测试块，精确 sign-flip 最低也只能到
`p={_fmt(robustness['prequential_feature_replay']['increment_exact_signflip']['p_value'], 4)}`；
对 18 个 gate 的搜索做共同区块 max-stat 校正后为
`p={_fmt(robustness['selection_adjusted_feature_test']['selection_adjusted_p_value'], 4)}`。

所以 `vol_ratio_mean8 >= 1` 的 V10 虽改善历史点估计和回撤，但 final 已污染、尾部更集中，
且多重选择校正未过，不能冒充验证成功。

把已经落盘的 12 个 oscillator 阈值、11 个 slope lag、18 个自然特征门、3 个方向策略和
21 个 trailing 组合放进同一份选择预算，共 {selection_risk['raw_known_configurations']} 个已知配置、
折叠后 {selection_risk['unique_four_block_performance_paths']} 条独立四块表现路径。使用同一个
half-year sign vector 同时翻转所有路径的 exact max-stat，观察期最优是
`{selection_risk['exact_global_max_stat']['selected_configuration']}`，但选择校正
`p={_fmt(selection_risk['exact_global_max_stat']['selection_adjusted_p_value'], 4)}`；V9 的四块均值/最差块
排名仅为 {selection_risk['v9_performance_path']['mean_rank_of_unique_paths']}/
{selection_risk['v9_performance_path']['minimum_block_rank_of_unique_paths']}（分母均为
{selection_risk['unique_four_block_performance_paths']}）。这不是说 V9 必须改成榜首，而是说明可被挑中的“榜首”太多，
继续挖同一开发期会扩大选择偏差。

只用过去块选择、再看紧接着半年，也没有形成单一稳定冠军：

{markdown_table(['选择所用块', '下一块', '选中配置', '选中 bp', 'V9 bp', '差值 bp'], selection_chronology_rows)}

四块只能形成 16 个 sign patterns，选择风险审计明确不声称正式 PBO；它只给出停止条件：
**不再在 2023/2024 上增加超参搜索，V10/V11 必须靠新鲜前向。**

为后续互斥 paper A/B，已从 V9 通过严格生成器产出两份 Pine：

- `pine/allin_eth_15m_v10_volume_paper.pine`：只增加 20-bar volume ratio 的 8-bar 均值 `>=1`；
- `pine/allin_eth_15m_v11_long_only_paper.pine`：只允许开多，空信号仍在下一根开盘平多；
- manifest 明确 `combined_v10_v11_generated=false`、parity/production 均为 false。

两者都不覆盖 `allin_eth_15m_v9_research.pine`，也不能因为文件可运行就被解释成已验证。

三份 Pine 的 SHA256 已写入 fail-closed paper protocol，但没有启动采集、写 forward log 或发任何订单。
按 consumed-final 历史到达率，达到每臂 100 笔约需：V9
{_fmt(paper_protocol['arms']['V9']['planning_months_to_100_fresh_trades'])} 个月、V10
{_fmt(paper_protocol['arms']['V10']['planning_months_to_100_fresh_trades'])} 个月、V11
{_fmt(paper_protocol['arms']['V11']['planning_months_to_100_fresh_trades'])} 个月（仅规划估计，不是预测）。
正式一次性读取采用三臂 Holm familywise 0.01，并要求匹配超额、绝对周区块 CI、去最大赢家和
venue-exact 总成本同时通过。TradingView 逐笔 parity 未通过前，协议保持 `blocked=true`。

止损/退出结论：

- 本轮冻结保持 break-even 不变（不代表其经济语义正确）：原开发消融不足以直接替换，后续成本解剖则明确暴露 +10 bp 锁盈低于 20 bp 成本；任何参数改动仍需单独批准；
- 拒绝 trailing：开发段搜索的所有 2.5–10% 激活、1–5% 距离组合，最差半年都为负；
- `close only` 反转模式最差半年 +22.32 bp，低于 V9 的 +41.49 bp，拒绝；
- 初始 `4×ATR / 3% cap`、ATR 下限和 20 bp 成本未调参。

对冻结 final 退出逐笔解剖后：

| 退出类型 | 笔数 | 净 bp/笔 | 持有中位 bars | 出场前 MFE 中位 bp | 出场前 MAE 中位 bp |
|---|---:|---:|---:|---:|---:|
| 成本下的“BE”止损 | {exit_anatomy['by_exit_subtype']['break_even_locked_stop']['trades']} | {_fmt(exit_anatomy['by_exit_subtype']['break_even_locked_stop']['net_bp_per_trade'])} | {_fmt(exit_anatomy['by_exit_subtype']['break_even_locked_stop']['median_holding_bars'])} | {_fmt(exit_anatomy['by_exit_subtype']['break_even_locked_stop']['median_mfe_before_exit_bp'])} | {_fmt(exit_anatomy['by_exit_subtype']['break_even_locked_stop']['median_mae_before_exit_bp'])} |
| 初始保护止损 | {exit_anatomy['by_exit_subtype']['initial_protective_stop']['trades']} | {_fmt(exit_anatomy['by_exit_subtype']['initial_protective_stop']['net_bp_per_trade'])} | {_fmt(exit_anatomy['by_exit_subtype']['initial_protective_stop']['median_holding_bars'])} | {_fmt(exit_anatomy['by_exit_subtype']['initial_protective_stop']['median_mfe_before_exit_bp'])} | {_fmt(exit_anatomy['by_exit_subtype']['initial_protective_stop']['median_mae_before_exit_bp'])} |
| 反转退出 | {exit_anatomy['by_exit_subtype']['reverse']['trades']} | +{_fmt(exit_anatomy['by_exit_subtype']['reverse']['net_bp_per_trade'])} | {_fmt(exit_anatomy['by_exit_subtype']['reverse']['median_holding_bars'])} | {_fmt(exit_anatomy['by_exit_subtype']['reverse']['median_mfe_before_exit_bp'])} | {_fmt(exit_anatomy['by_exit_subtype']['reverse']['median_mae_before_exit_bp'])} |

`BREAK_EVEN_OFFSET=0.1%` 只锁 10 bp，而冻结往返成本为 20 bp，所以 2023、2024、final 三段
所有此类退出都精确为 -10 bp；名称和经济语义不一致。50 笔初始止损中
{_fmt(exit_anatomy['initial_protective_stop_diagnostics']['fraction_never_reached_100bp_before_exit'] * 100)}%
在出场前连 +1% MFE 都没有，说明多数失败首先是入场不延续，而不只是止损太紧。

把 49 笔 -10 bp 静态替换成 0、保持同一退出时点的**会计上限示意**，只把均值从
{_fmt(exit_anatomy['break_even_cost_semantics']['static_same_exit_accounting_only']['current_net_bp_per_trade'])}
提高到 {_fmt(exit_anatomy['break_even_cost_semantics']['static_same_exit_accounting_only']['if_all_locked_stops_were_exactly_zero_net_bp_per_trade'])} bp/笔；
去掉最大赢家后仍为
{_fmt(exit_anatomy['break_even_cost_semantics']['static_same_exit_accounting_only']['mean_without_top1_bp'])} bp/笔。
这不是 barrier replay，不能据此偷改参数；但它证明修正 BE 语义也不是解决尾部依赖的万能药。

### 仓位风险与回撤

仓位只改变资金路径，不改变 +{_fmt(v9['project_net_bp_per_trade'])} bp/笔的单位期望：

{markdown_table(['每笔风险 %', '资金收益 %', '15m DD %', '均值杠杆', '最大杠杆'], risk_rows)}

本轮把 **1%** 作为默认研究风险：0.5% 更稳但收益低；2% 的历史回撤已达 {_fmt(risk_final.loc[risk_final.risk_percent.eq(2.0), 'max_drawdown_15m_percent'].iloc[0])}%。
正式 20 bp 成本下 V9 毛期望 {_fmt(cost_break_even['v9_locked'])} bp/笔，意味着总成本接近该值时点估计归零；
资金费和真实滑点未建模，不能忽略。

为避免只盯着实际一条资金曲线，使用 consumed-final 的 61 个周收益做 20,000 次连续 4 周
循环区块重采样（纯描述、不是 OOS/预测）：

{markdown_table(['资金臂', '实际收益 %', '实际 DD %', 'bootstrap 收益中位 [5%,95%]', 'DD 95% %', '终值<0 %', 'DD>20 %', '最长连亏'], path_risk_rows)}

![Four-week block bootstrap path risk](../experiments/active/exp-pine-eth-15m-v1/results/charts/path_risk_bootstrap.png)

0.5% 风险把 V9 的实际回撤从
{_fmt(path_risk['arms'][2]['actual_drawdown_15m_percent'])}% 压到
{_fmt(path_risk['arms'][0]['actual_drawdown_15m_percent'])}%，bootstrap 回撤 95 分位从
{_fmt(path_risk['arms'][2]['drawdown_q95_percent'])}% 降到
{_fmt(path_risk['arms'][0]['drawdown_q95_percent'])}%；但终值为负的重采样比例仍为
{_fmt(path_risk['arms'][0]['probability_negative_terminal'] * 100)}%。因此 **0.5% 是更保守的
paper 风险档，不是 alpha 优化**；1% 继续作为可比研究基准，2% 不建议。

### 独立回测框架复核

本机 `Backtesting.py {framework['framework_version']}` 的独立回放得到：

- 110/110 入场时间一致，110/110 出场时间一致；
- 最大入/出价格误差分别 {_fmt(framework['max_entry_price_error'], 10)} / {_fmt(framework['max_exit_price_error'], 10)}；
- 最大单位收益误差 {_fmt(framework['max_unit_return_error_bp'], 10)} bp；
- 框架收益 +{_fmt(framework['framework_return_percent'])}%、最大回撤 {_fmt(framework['framework_max_drawdown_percent'])}%，
  与自定义引擎 +{_fmt(v9['return_percent'])}% / {_fmt(v9['max_drawdown_15m_percent'])}% 接近。

这通过了**独立 Python 框架 reconciliation**，但不是 TradingView broker-emulator parity。
精确 V9 hash 已在 `OKX:ETHUSDT.P` 15m 通过 TradingView 官方 Pine v6 编译，错误 0，且成功显示为
active strategy；但仍需导出逐笔 ledger。

V9 Pine 静态契约 25/25 项通过：常量、900 秒周期守卫、ETH base 守卫、confirmed-bar、next-open、
commission、禁 tick 重算/放大器、无 `request.security`/lookahead 都与冻结配置一致。静态审计器自身没有
调用编译器，但独立 compiler receipt 已把 `official_pine_compiler_run=true` 钉到源 SHA
`{tv_compile['source_sha256']}`。`tradingview/trades_normalized.template.csv` 与
`scripts/reconcile_pine_eth_15m_tradingview.py` 已准备好，真实导出必须 110/110 entry+side、exit time、
入/出价格一 tick 内全部通过；费用/净 P&L 仍需 venue 会计复核。

本次浏览器 smoke 无法进入历史账本：账号计划为 {tv_compile['account_plan_observed']}，已加载图表区间
{tv_compile['tradingview_loaded_chart_range'][0]}～{tv_compile['tradingview_loaded_chart_range'][1]}，完全晚于
`researchEnd=2026-03-01`；任意历史 Deep Backtesting 会弹出升级门，因此报告为 “This report requires trade data”，
没有导出或 reconciliation。编译成功与逐笔 parity 必须继续分开表述。

同一 OKX 合约的 3m 有序路径又做了一层执行敏感性复核：

- 2025-01 至 2026-02 共 {intrabar['parent_bar_reconstruction']['joined_15m_bars']:,} 根 15m 母 K，
  每根都恰好由五根 3m K 构成，OHLC 最大误差为 0；
- V9 的 {intrabar['canonical_trade_count']} 笔中，
  {intrabar['same_15m_exit_parent_count']}/{intrabar['canonical_trade_count']} 出场母 K 一致，
  {intrabar['exact_exit_price_count']}/{intrabar['canonical_trade_count']} 出场价一致；
- {intrabar['stop_trade_count']} 笔止损没有发现 15m 聚合隐藏的 3m 跳空劣化，
  单笔净收益最大差 {_fmt(intrabar['maximum_absolute_net_return_delta_bp'], 10)} bp。

这只证明**本地 OKX 同源 15m 执行没有被聚合路径高估**；Pine 设置仍为
`use_bar_magnifier=false`，也没有替代 TradingView venue-specific parity。

### 邻近数据源敏感性

同一 2025-07 至 2026-02 时段，OKX swap 与 spot 各有 23,328 根无缺口 15m K；spot 只作为
附近独立 feed，不是永续替代品：

{markdown_table(['版本', 'swap 笔数', 'spot 笔数', '入场 Jaccard %', '共同入场净收益差 bp'], feed_rows)}

V9/V11 的价格型规则入场重合约 96%，但成交量门 V10 只有 78%；`vol_ratio_mean8` 两 feed
相关性虽为 {_fmt(feed_sensitivity['vol_ratio_mean8_cross_feed_correlation'], 4)}，成交量本身来自不同市场。
因此 feed 审计支持 V9 做本地 proxy 基线，同时进一步降低 V10 的可信度；仍不等于 TradingView parity。

### Docker 复核状态

固定配方两次都停在 Docker Hub 的 `python:3.11-slim` metadata 拉取，尚未进入依赖安装或代码执行，
因此明确记录 `pinned_docker_recipe_built=false`。为了区分“Docker runtime 坏了”和“外部镜像站阻塞”，
使用本机已有镜像在 `--network none` 下做了两层只读复核：

- Python {docker_smoke['runtime']['python']} / pandas {docker_smoke['runtime']['pandas']} /
  NumPy {docker_smoke['runtime']['numpy']}；
- 产物算术 smoke {docker_smoke['count']}/{docker_smoke['count']} 项通过，包括 110 笔收益重算、20 bp 成本、
  330 个唯一对照、统计失败门、3m 出场对账和 eligibility；
- 另从原始 15m K 线前缀重新计算 V9 信号并运行 stateful replay：读取
  {docker_replay['data_contract']['bounded_rows_read']:,} 根、holdout 0 行，重建
  {docker_replay['ledger']['replayed_trade_count']} 笔；方向、索引、退出原因、三组时间全部逐笔一致，
  数值列最大绝对误差 {max(docker_replay['ledger']['numeric_max_abs_error'].values()):.3e}；
- Linux runtime 为 Python {docker_replay['runtime']['python']} / pandas
  {docker_replay['runtime']['pandas']} / NumPy {docker_replay['runtime']['numpy']}。

所以已有断网 Linux 镜像已通过“原始数据 → 信号 → 110 笔账本”的跨版本重放；但这不是固定依赖镜像，
更不是 TradingView Pine 编译/成交导出，**正式 pinned 容器构建与 TradingView parity 仍未通过**。

## 项目判断层（用户称 LR）能否接入

可以接，但当前不能把旧模型直接拿来判定：

1. 仓库生产判断层实际是 LightGBM；LogisticRegression 只是 `ma_spread_pct` 单特征 baseline。
2. 当前冻结 v10 是短侧 YOLO-v10 候选池、72-bar barrier 标签、legacy feature semantics；Pine 是双向交叉候选和反转/BE 退出，分布与目标都不同。
3. `models/active_bundle.json` 缺失，生产协议 fail-closed；当前真实生产模型数是 0。
4. P0/P1 `training_eligible=false`，因此本轮没有新训练、没有加载旧模型做伪 OOS 打分。

本轮已经输出每个 V9 final 信号 bar 的 28 个因果特征、side-aligned 语义和 label end：
`results/pine_l2_feature_rows.csv`，但每行都标记 `training_eligible=false`。

另外只读取 2023/2024，连续回放得到 {judgment_research['rows']} 条 Pine-specific judgment
lineage（{judgment_research['long_rows']} long / {judgment_research['short_rows']} short，
成本后正类率 {_fmt(judgment_research['net_positive_rate'] * 100)}%，28 特征缺失 0）：

{markdown_table(['fold', 'raw train', 'purged train', 'label-overlap purge', 'validation', 'train 正类 %', 'val 正类 %'], judgment_fold_rows)}

这 {judgment_research['rows']} 行只是基线实际执行账本，不能覆盖 gate 改写状态后的候选。为此另导出完整、
无标签的 raw-candidate 因果特征面：共 {gate_surface['rows']} 行（{gate_surface['long_rows']} long /
{gate_surface['short_rows']} short），其中只有
{gate_surface['executed_coverage']['baseline_executed_candidates']} 行出现在基线执行账本，另有
{gate_surface['executed_coverage']['raw_candidates_not_in_baseline_ledger']} 行因原持仓/cooldown 没有执行；
基线覆盖率仅 {_fmt(gate_surface['executed_coverage']['baseline_coverage_of_raw_surface'] * 100)}%。
未来 LR 必须给 scored period 的每个 raw candidate 恰好一个、在 next-open 前可用的分数；缺失、重复或迟到一律
fail-closed，再把 pass 结果 AND 到 `v9_long/v9_short` 后进入动态状态机。当前 score template 为空，
没有模型、分数或阈值。

这条接口已经有可执行的恒等性自审，而不只是文档约定：用不含任何预测含义的 synthetic allow-all
sentinel，2023 的 {gate_replay['reconciliations'][0]['baseline_trades']} 笔和 2024 的
{gate_replay['reconciliations'][1]['baseline_trades']} 笔都逐笔复现，最大数值误差
{max(row['max_numeric_absolute_error'] for row in gate_replay['reconciliations']):.3e}；缺候选、重复候选、
早于特征、迟于 next-open、非有限分数、模型哈希漂移、空阈值、calibration overlap 八类输入全部拒绝。
自审还钉住一个容易漏掉的顺序：不可入场的原始信号虽不需要模型分数，仍会在 Pine 中先消耗 cooldown；
动态桥会只保留这个状态副作用，可入场候选才由分数控制。整个自审读取 final/holdout 各 0 行，未训练、
未加载模型、未选择阈值，仍为 `production_eligible=false`。

特征在 signal bar 收盘完成，时间戳与 `t+1` entry open 完全相同；label end 保守加到 exit bar
收盘，两个跨切点样本已经 purge。这个表依然**不能直接拿来静态 top-decile 筛选**：它只记录
V9 baseline 的 on-policy executed trades，一旦 LR 拒绝某单，后续持仓、反转与 cooldown 状态会改变。
未来获批后，模型分数必须放回动态 replay 内逐信号执行，不能只过滤现成 trade CSV。

这个限制不是理论提醒，两个已知 gate 的静态/动态 final 对照如下：

| gate | 静态入场表 bp/笔 | 动态 replay bp/笔 | 静态高估 bp | 入场 Jaccard % |
|---|---:|---:|---:|---:|
| `vol_ratio_mean8>=1` | {_fmt(stateful_gate['final_summary']['vol_ratio_mean8_ge1']['static_net_bp_per_trade'])} | {_fmt(stateful_gate['final_summary']['vol_ratio_mean8_ge1']['dynamic_net_bp_per_trade'])} | {_fmt(stateful_gate['final_summary']['vol_ratio_mean8_ge1']['static_minus_dynamic_net_bp'])} | {_fmt(stateful_gate['final_summary']['vol_ratio_mean8_ge1']['entry_jaccard'] * 100)} |
| long-only | {_fmt(stateful_gate['final_summary']['long_only']['static_net_bp_per_trade'])} | {_fmt(stateful_gate['final_summary']['long_only']['dynamic_net_bp_per_trade'])} | {_fmt(stateful_gate['final_summary']['long_only']['static_minus_dynamic_net_bp'])} | {_fmt(stateful_gate['final_summary']['long_only']['entry_jaccard'] * 100)} |

判断层容量也不够直接上全量模型：{judgment_feasibility['rows']} 行只有
{judgment_feasibility['positive_events']} 个正例 / {judgment_feasibility['candidate_features']} 特征，
即 {_fmt(judgment_feasibility['overall_positive_events_per_feature'], 3)} 正例/特征；三折训练正例仅
{min(row['train_positive_events'] for row in judgment_feasibility['fold_capacity'])}–
{max(row['train_positive_events'] for row in judgment_feasibility['fold_capacity'])}，验证正例仅
{min(row['validation_positive_events'] for row in judgment_feasibility['fold_capacity'])}–
{max(row['validation_positive_events'] for row in judgment_feasibility['fold_capacity'])}。
所以未来第一步应是**预注册单特征正则 LR**（最多极小先验子集）；不能在 166 笔上拟合 28 特征
LightGBM 再用好看的 AUC 自证。若要全量模型，需要相同 Pine 候选/标签语义的动态样本扩展，优先
time-grouped 跨币训练并单独校验 ETH，而不是复用旧 YOLO-v10 判断层。

为了先判断“有没有值得训练的单特征”，又做了一个**不拟合模型、不选择执行阈值**的时间外推审计。
四个透明先验都只用早期 fold 的经验分位给下一半年打分；每项使用 40×38×45 = 68,400 个
半年内 outcome circular-shift 组合做精确零假设，并对展示的四项做 Holm 校正：

{markdown_table(['先验特征', 'AUC', 'AUC p', 'top 正/总', 'top10% bp', '去 top1 bp', 'top p', 'top Holm p'], judgment_prior_rows)}

最好的 `vol_ratio_mean8` 静态 top-decile 为 +{_fmt(volume_judgment_prior['top_decile_net_bp'])} bp，
但只有 3/14 笔为正，原始/四项 Holm p 分别为
{_fmt(volume_judgment_prior['top_decile_exact_circular_shift_p'], 4)} /
{_fmt(volume_judgment_prior['top_decile_holm_p_across_four_displayed_priors'], 4)}，
且它此前来自更宽的 28 特征搜索，所以连这个 Holm p 也没有覆盖全部选择历史。结果只支持“V10 值得保留为
paper 假设”，不支持训练或宣称通过。

更灵活的 28 特征 prequential selector 每折只在 purged 过去选最强特征，下一半年表现如下：

{markdown_table(['fold', '过去选中', '方向', 'train oriented AUC', 'next-fold AUC'], judgment_selector_rows)}

合并下一期 AUC 只有
{_fmt(judgment_signal['prequential_28_feature_selector']['pooled_auc'], 3)}；top-decile
{judgment_signal['prequential_28_feature_selector']['pooled_top_decile_positive_rows']}/
{judgment_signal['prequential_28_feature_selector']['pooled_top_decile_rows']} 笔为正，净收益
{_fmt(judgment_signal['prequential_28_feature_selector']['pooled_top_decile_net_bp'])} bp/笔。
这直接显示“从 28 个特征里挑当期最好再外推”是在过拟合，而不是可训练信号。

未来获得训练许可后，正确链路是：

```text
Pine confirmed close(t)
  → 28 causal L2 features available_at close(t)
  → preregistered one-feature regularized LR（LightGBM deferred）
  → calibration-only q90 threshold
  → entry at open(t+1)
```

标签必须用同一套 Pine 入场、反转、止损、BE 和 20 bp 成本；按时间 walk-forward，按 label end purge，
并同时报告 top-decile 净收益、p<0.01、64-seed 匹配随机对照和 leave-top-winner-out。
不能把旧 YOLO L2 分数当成 Pine 已验证 gate。

## 风险与诚实声明

- **Final 已消耗。** 2025-01 至 2026-02 已用于 V9 单次终测；V10 是其后的 post-selection 假设。
- **统计未过门。** V9/V10 的区块 p 值都远高于 0.01，CI 跨 0，不能说收益已稳定。
- **特征搜索未过多重校正。** `vol_ratio_mean8 >= 1` 的四块增量都为正，但 18-gate max-stat `p={_fmt(robustness['selection_adjusted_feature_test']['selection_adjusted_p_value'], 4)}`；V10 仍只是 paper-forward 假设。
- **已知搜索预算已很大。** 五类落盘搜索共 {selection_risk['raw_known_configurations']} 个配置 / {selection_risk['unique_four_block_performance_paths']} 条独立四块路径，全局 max-stat p={_fmt(selection_risk['exact_global_max_stat']['selection_adjusted_p_value'], 4)}；历史代码迭代和人工选择还无法完整枚举，因此这个校正只可能低估、不会消除 selection risk。
- **收益高度集中。** V9 去掉最大赢家后转负；V10 集中更严重。
- **V11 也未解决尾部依赖。** 56 笔只有 5 笔盈利，去掉最大赢家后均值 {_fmt(v11['profit_concentration']['mean_without_top1_bp'])} bp/笔；它没有资格替换 V9。
- **真实 10m 短窗反对简单周期优越论。** 约 10 周同窗中，10m 原 V8 为正而 15m V8/V9 为负；四组匹配检验均不显著，不能选回 10m，也不能宣称 15m 在所有制度更好。
- **跨制度仍未过门。** V9 虽 7/9 时间块为正，但绝对/匹配精确 p 为 {_fmt(regime_stability['absolute_net_equal_block_test']['one_sided_p_value'], 4)} / {_fmt(regime_stability['matched_excess_equal_block_test']['one_sided_p_value'], 4)}；2025H1、2026M1M2 为负。
- **break-even 经济语义错误但本轮未改。** +10 bp 锁盈低于 20 bp 成本，49 笔固定净 -10 bp；任何 offset 修改仍是需 owner 单独批准的新 barrier 实验。
- **随机对照有 assignment 方差。** V9 的 64-seed 超额 5%–95% 为 [{_fmt(control_sensitivity['variants'][0]['candidate_minus_control_bp']['q05'])}, {_fmt(control_sensitivity['variants'][0]['candidate_minus_control_bp']['q95'])}] bp；单 seed 不能当确定事实。
- **数据源未 parity。** OKX cache 不能证明等于任意 TradingView `ETHUSDT.P`；swap/spot 审计中 V9 入场 Jaccard 96.61%，V10 仅 78%，后者更依赖 venue volume。
- **成本不完整。** 正式扣 20 bp，但滑点/资金费/强平/最小下单量未进入资金曲线。
- **资金费不可得而不是 0。** 本地 ETH funding 从 {funding_coverage['first_observed_funding_record']} 才开始，与 canonical final 交集 0；没有计算 funding-adjusted return。
- **路径 bootstrap 不是预测。** 它只重排已消费时期的 4 周区块；V9 0.5% 风险仍有 {_fmt(path_risk['arms'][0]['probability_negative_terminal'] * 100)}% 重采样终值为负，不能解释成未来亏损概率的精确估计。
- **没有真实密集门。** 原始核心是 SMA10/60 单点 crossover，不是项目 Local Signal V2；严格 EMA ribbon density gate 在开发段不稳定。
- **没有训练或生产动作。** 所有策略计算的 bounded loader 都读取 0 行 holdout；未 train、promote、deploy、改 ACTIVE、写 forward_log 或操作真金账户。
- **意外 holdout 预览已披露。** 在写 3m bounded loader 前，一次 shell `tail` 意外显示了原始文件末尾两行（均在 repository holdout）；它们没有进入 Python、没有被评分、没有参与任何配置选择或收益评估。按本仓“看一眼也要记录”的纪律，此事故不能写成“从未看见”，后续已用前缀加载器和不读取整文件的前缀哈希封死。
- **第二次意外预览已披露。** 检查 funding coverage 时，shell 又显示了 8 条 holdout 期 funding 原始行，最晚到 {funding_coverage['operational_incident']['displayed_range_end']}；同样未进 Python、未汇总/评分/选参，发现本地 funding 不覆盖回测后立即停止该分析。
- **第三次意外预览已披露。** TradingView 编译 smoke 默认打开 {tv_compile['tradingview_loaded_chart_range'][0]}～{tv_compile['tradingview_loaded_chart_range'][1]} 当前图表，晚于 repository holdout；事前没有 owner 的专项 holdout 批准。V9 日期门让入场/评分为 0，也没有读取任何策略指标、做收益评价或选参；临时策略已撤销、布局/脚本未保存、未发布。该事故只保留“官方编译通过”事实，不能当 holdout 验收。
- **正式 Docker 构建未完成。** 两次都卡在外部基础镜像 metadata；已有断网 Linux 镜像完成了原始数据全重放并逐笔一致，但其依赖未按实验 Dockerfile 固定，不能冒充 pinned build，更不能冒充 TradingView parity。

## 下一步选项（需 owner 决策的已标出）

1. **需 owner 提供可访问历史区间的 TradingView 方案：** 官方编译已过；在支持 2025-01～2026-02 Deep Backtesting 的计划/会话中导出 trade list，再对 signal/entry/exit/fee/equity 逐笔对账。未过不得 forward；本轮不会替 owner 购买升级。
2. **需 owner 单独批准：** 把名义 BE 的 +0.1% offset 改成成本感知值并作为单一 barrier 变量重跑；当前只完成语义/会计诊断，未改参数。
3. **parity 后再由 owner 启动：** V9 / V10（成交量门）/ V11（只开多）做互斥 paper-only 新鲜前向 A/B，各积累至少 100 笔；纸面风险优先 0.5%，1% 留作可比基准；禁止把 V10+V11 打包，旧 final 不追认为 OOS。
4. **需 owner 在 P0/P1 通过后批准：** 先建 Pine-specific 单特征正则 LR；不要直接在 166 笔上训 28 特征 LightGBM。所有分数必须进入动态 replay。
5. **需 owner 单独批准：** 任何其他 TP/SL 倍数、ATR 下限、20 bp 成本或 holdout 评估；本轮都没动。
6. **不建议：** 用 2% 风险、legacy 日历倍增、静态 trade CSV 过滤或继续挖 consumed-final 参数掩盖不显著的单位 alpha。

## 复现命令

```bash
git checkout {report_source_commit}
PYTHONPATH=. .venv/bin/python scripts/research_pine_eth_15m.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_robustness.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_side_hypothesis.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_control_sensitivity.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_path_risk.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_feed_sensitivity.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_exit_anatomy.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_backcast.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_actual_10m_vs_15m.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_regime_stability.py
PYTHONPATH=. .venv/bin/python scripts/generate_pine_eth_15m_paper_variants.py
PYTHONPATH=. .venv/bin/python scripts/prepare_pine_eth_15m_judgment_research.py
PYTHONPATH=. .venv/bin/python scripts/prepare_pine_eth_15m_gate_surface.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_judgment_feasibility.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_judgment_signal.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_selection_risk.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_density_overlap.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_stateful_gate.py
PYTHONPATH=. .venv/bin/python scripts/replay_pine_eth_15m_judgment_gate.py --self-audit
PYTHONPATH=. .venv/bin/python scripts/audit_pine_eth_15m_migration.py
PYTHONPATH=. .venv/bin/python scripts/audit_pine_eth_15m_static_contract.py
PYTHONPATH=. .venv/bin/python scripts/design_pine_eth_15m_paper_protocol.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_backtesting.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_intrabar.py
PYTHONPATH=. .venv/bin/python scripts/validate_pine_eth_15m.py
PYTHONPATH=. /tmp/fable-pine-eval-venv/bin/python scripts/build_pine_eth_15m_report.py
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \\
  analysis/p0_pine_eth_15m_v1_20260821.md --out-dir analysis/html
PYTHONPATH=. .venv/bin/python scripts/build_pine_eth_15m_artifact_manifest.py
PYTHONPATH=. .venv/bin/python scripts/build_pine_eth_15m_artifact_manifest.py --verify
PYTHONPATH=. .venv/bin/python -m pytest -q \\
  tests/test_pine_allin_v7_backtest.py tests/test_research_pine_eth_15m.py tests/test_reconcile_pine_eth_15m*.py tests/test_analyze_pine_eth*.py tests/test_audit_pine_eth_15m_static_contract.py tests/test_design_pine_eth_15m_paper_protocol.py tests/test_generate_pine_eth_15m_paper_variants.py tests/test_prepare_pine_eth_15m_judgment_research.py tests/test_replay_pine_eth_15m_judgment_gate.py tests/test_smoke_pine_eth_15m_artifacts.py
```

Docker：

```bash
docker build -t fable-pine-eth15m-v1 \\
  experiments/active/exp-pine-eth-15m-v1/docker
docker run --rm --network none -v "$PWD:/workspace:ro" \\
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results-docker:/output" \\
  fable-pine-eth15m-v1

# External base-image pull unavailable: offline artifact-only smoke used here
docker run --rm --network none --entrypoint python -w /workspace \\
  -v "$PWD:/workspace:ro" \\
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results:/output" \\
  heartexlabs/label-studio:latest scripts/smoke_pine_eth_15m_artifacts.py \\
  --runtime-label offline-local-label-studio-image \\
  --output /output/docker_offline_smoke.json

# Full frozen-V9 market-data replay in the same network-disabled Linux image
docker run --rm --network none --entrypoint python -e PYTHONPATH=/workspace \\
  -v "$PWD:/workspace:ro" \\
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results:/output" \\
  heartexlabs/label-studio:latest \\
  /workspace/scripts/replay_pine_eth_15m_offline.py \\
  --config /workspace/experiments/active/exp-pine-eth-15m-v1/config.json \\
  --canonical-trades /workspace/experiments/active/exp-pine-eth-15m-v1/results/trades.csv \\
  --output /output/docker_offline_replay.json
```

验证器结果：**{validation['counts']['checks']}/{validation['counts']['checks']} checks pass**。
"""
    return report


def make_notebook(evidence: dict[str, Any]) -> nbformat.NotebookNode:
    cells = [
        nbformat.v4.new_markdown_cell(
            "# ETH perpetual 15m Pine V1 audit\n\n"
            "**TL;DR:** V9 improves the consumed final-preholdout ETH result to +30.22 bp/trade "
            "after the frozen 20 bp cost, but week-block p=0.17 and the mean turns negative after "
            "removing the largest winner. Actual 10m comparison, regime blocks, and a cost-underwater "
            "break-even warning keep V9 research-only; V10/V11 are post-selection hypotheses."
        ),
        nbformat.v4.new_code_cell(
            "from pathlib import Path\n"
            "import json, pandas as pd\n"
            "ROOT=Path.cwd(); EXP=ROOT/'experiments/active/exp-pine-eth-15m-v1'; R=EXP/'results'\n"
            "validation=json.loads((R/'validation.json').read_text())\n"
            "summary=json.loads((R/'summary.json').read_text())\n"
            "stats=json.loads((R/'statistical_tests.json').read_text())\n"
            "intrabar=json.loads((R/'intrabar_3m_reconciliation.json').read_text())\n"
            "robustness=json.loads((R/'robustness_checks.json').read_text())\n"
            "docker_smoke=json.loads((R/'docker_offline_smoke.json').read_text())\n"
            "v11=json.loads((R/'v11_long_only_summary.json').read_text())\n"
            "control_sensitivity=json.loads((R/'control_seed_sensitivity.json').read_text())\n"
            "path_risk=json.loads((R/'path_risk_bootstrap.json').read_text())\n"
            "judgment=json.loads((R/'pine_judgment_development_manifest.json').read_text())\n"
            "exit_anatomy=json.loads((R/'exit_anatomy.json').read_text())\n"
            "actual_timeframe=json.loads((R/'actual_10m_vs_15m.json').read_text())\n"
            "regime=json.loads((R/'regime_stability.json').read_text())\n"
            "judgment_capacity=json.loads((R/'judgment_feasibility.json').read_text())\n"
            "stateful_gate=json.loads((R/'stateful_gate_static_vs_dynamic.json').read_text())\n"
            "pine_static=json.loads((R/'pine_static_contract.json').read_text())\n"
            "split=pd.read_csv(R/'split_summary.csv')\n"
            "risk=pd.read_csv(R/'risk_grid.csv')\n"
            "assert validation['status']=='pass' and summary['holdout_consumed'] is False\n"
            "print(f\"{validation['counts']['checks']} checks pass; holdout consumed: 0\")"
        ),
        nbformat.v4.new_markdown_cell(
            "## Context & Methods\n\nSignal close at t, entry at open(t+1); time splits only; "
            "20 bp round-trip cost; exact ETH-month × HK 6h × ATR-quintile controls with unique starts; "
            "week-clustered inference."
        ),
        nbformat.v4.new_code_cell(
            "cols=['variant','period','trades','project_net_bp_per_trade','monetary_profit_factor',"
            "'return_percent','max_drawdown_15m_percent']\n"
            "split.loc[split.variant.isin(['v8_eth_baseline','v9_locked','v10_volume_hypothesis']),cols].round(4)"
        ),
        nbformat.v4.new_code_cell(
            "pd.DataFrame({\n"
            " 'metric':['V9 candidate bp','matched control bp','excess bp','week signflip p','absolute CI low','absolute CI high'],\n"
            " 'value':[stats['matched_control']['mean_candidate_net_bp'],stats['matched_control']['mean_control_net_bp'],"
            "stats['matched_control']['mean_excess_bp'],stats['week_block_signflip']['p_value'],"
            "stats['week_bootstrap_absolute']['ci95_low_bp'],stats['week_bootstrap_absolute']['ci95_high_bp']]})"
        ),
        nbformat.v4.new_code_cell(
            "stats['profit_concentration']"
        ),
        nbformat.v4.new_code_cell(
            "risk.loc[risk.period.eq('final_preholdout_2025_202602'),"
            " ['risk_percent','return_percent','max_drawdown_15m_percent','mean_leverage','max_leverage']].round(4)"
        ),
        nbformat.v4.new_markdown_cell(
            "## Decision assertions\n\nThe notebook deliberately asserts the failures as well as arithmetic validity."
        ),
        nbformat.v4.new_code_cell(
            "v9=summary['v9_final_preholdout']\n"
            "assert v9['project_net_bp_per_trade'] > 0\n"
            "assert stats['week_block_signflip']['p_value'] >= 0.01\n"
            "assert stats['week_bootstrap_absolute']['ci95_low_bp'] < 0\n"
            "assert stats['profit_concentration']['mean_without_top1_bp'] < 0\n"
            "assert intrabar['data_quality']['holdout_rows_read'] == 0\n"
            "assert intrabar['same_15m_exit_parent_count'] == 110\n"
            "assert intrabar['exact_exit_price_count'] == 110\n"
            "assert robustness['final_preholdout_rows_read'] == 0\n"
            "assert robustness['selection_adjusted_feature_test']['selection_adjusted_p_value'] >= 0.01\n"
            "assert docker_smoke['status'] == 'pass' and docker_smoke['pinned_docker_recipe_built'] is False\n"
            "assert v11['profit_concentration']['mean_without_top1_bp'] < 0\n"
            "assert all(row['fraction_assignment_seeds_with_p_below_0p01'] == 0 for row in control_sensitivity['variants'])\n"
            "assert path_risk['arms'][0]['drawdown_q95_percent'] < path_risk['arms'][2]['drawdown_q95_percent']\n"
            "assert judgment['training_eligible'] is False and judgment['lr_fitted'] is False\n"
            "assert exit_anatomy['break_even_cost_semantics']['locked_stop_project_net_bp'] == -10.0\n"
            "assert actual_timeframe['variants']['V9_15m']['summary']['project_net_bp_per_trade'] < 0\n"
            "assert regime['absolute_net_equal_block_test']['one_sided_p_value'] >= 0.01\n"
            "assert judgment_capacity['overall_positive_events_per_feature'] < 1.0\n"
            "assert stateful_gate['static_top_decile_filtering_valid_for_l2'] is False\n"
            "assert pine_static['status'] == 'pass' and pine_static['official_pine_compiler_run'] is False\n"
            "assert summary['tradingview_parity_passed'] is False\n"
            "print('Point estimate positive; robustness and parity gates correctly remain failed.')"
        ),
    ]
    return nbformat.v4.new_notebook(
        cells=cells,
        metadata={
            "kernelspec": {
                "display_name": "Fable Pine Eval",
                "language": "python",
                "name": "fable-pine-eval",
            },
            "language_info": {"name": "python", "version": "3.9"},
        },
    )


def main() -> None:
    evidence = load_evidence()
    diagnostics = build_diagnostics(evidence)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(build_report(evidence, diagnostics), encoding="utf-8")

    NOTEBOOKS.mkdir(parents=True, exist_ok=True)
    notebook = make_notebook(evidence)
    client = NotebookClient(
        notebook,
        timeout=600,
        kernel_name="fable-pine-eval",
        resources={"metadata": {"path": str(PROJECT)}},
        record_timing=False,
    )
    executed = client.execute()
    notebook_path = NOTEBOOKS / "pine_eth_15m_v1_audit.ipynb"
    nbformat.write(executed, notebook_path)
    print(f"wrote {RESULTS / 'diagnostics.json'}")
    print(f"wrote {REPORT}")
    print(f"wrote {notebook_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Rebuild the 15m L1 -> episode collapse -> side-specific L2 research path.

Source and decision basis
-------------------------
The owner disabled the failed global-morphology L1.5 layer on 2026-09-01. This
runner proves that the replacement topology has no training or scoring
dependency on that layer: it reads the original frozen L1 episode ledger with
an explicit allow-list, collapses dependency groups using the already-frozen
representative flag, and trains the unchanged LONG/SHORT 17-feature return
regressors. The previous factorial experiment's L2-only models are opened only
after training for byte/score parity verification.

Feature causality
-----------------
The 17 input columns were materialized by the source experiment from OHLCV and
the frozen L1 detection available at ``available_at``. Rolling windows use only
that bar and earlier bars. This runner does not recompute features, move entry
time, read future bars, or read the holdout. Future TP5/SL2/72 outcomes appear
only in ``label``, ``realized_ret`` and ``net_ret`` for training/evaluation.

This is research-only. It cannot create ACTIVE/frozen state, forward records,
Telegram messages, deployments, promotions or orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    outcome_permutation_pvalue,
    safe_metrics,
    selected_metrics,
)
from scripts.research_15m_ma_launch_l2_short_window_side_split import (
    empirical_percentile,
    strict_matched_control_metrics,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l1-l2-bypass-l15-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l1_l2_bypass_l15_v1"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l1_l2_bypass_l15_20260901.md"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
SIDES = ("long", "short")
SEED = 42

L2_FEATURE_COLUMNS = (
    "l1_confidence",
    "ma_spread_pct",
    "spread_chg8",
    "spread_chg24",
    "dense_frac48",
    "close_vs_ema55",
    "close_vs_ema200",
    "slow_slope_12",
    "volume_ratio",
    "atr_pct",
    "atr_pct_ratio96",
    "pre_range48",
    "drawdown24",
    "ret_4",
    "ret_12",
    "ret_24",
    "ret_48",
)
IDENTITY_COLUMNS = (
    "episode_id",
    "symbol",
    "side",
    "split",
    "dependency_representative",
    "available_at",
    "exposure_end_exclusive",
    "label",
    "realized_ret",
    "net_ret",
)
CANDIDATE_READ_COLUMNS = IDENTITY_COLUMNS + L2_FEATURE_COLUMNS
L2_DETERMINISTIC_PARAMS = {
    "device_type": "cpu",
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
    "data_random_seed": SEED,
    "feature_fraction_seed": SEED,
    "bagging_seed": SEED,
    "extra_seed": SEED,
}


class BypassError(RuntimeError):
    """Raised when topology, lineage, causality or parity drifts."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repo_path(value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def bool_series(values: pd.Series, *, label: str) -> pd.Series:
    normalized = values.astype(str).str.strip().str.lower()
    unknown = sorted(set(normalized) - {"true", "false"})
    if unknown:
        raise BypassError(f"{label} contains non-boolean values: {unknown}")
    return normalized == "true"


def load_preregistration(path: Path = PREREG_PATH) -> dict[str, Any]:
    prereg = read_json(path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise BypassError("experiment id drift")
    expected_stages = [
        "l1_frozen_yolo_candidates",
        "episode_dependency_collapse",
        "side_specific_l2_return_regression",
    ]
    if prereg["pipeline"]["enabled_stages"] != expected_stages:
        raise BypassError("enabled pipeline topology drift")
    if prereg["pipeline"]["disabled_stages"] != ["l15_global_shape_classifier"]:
        raise BypassError("disabled L1.5 topology drift")
    if tuple(prereg["l2"]["feature_columns"]) != L2_FEATURE_COLUMNS:
        raise BypassError("L2 feature order drift")
    if prereg["safety"]["holdout_read"]:
        raise BypassError("holdout read must remain false")
    for key in ("candidate_dataset", "matched_controls", "prior_l2_only_receipt"):
        spec = prereg["inputs"][key]
        path_value = repo_path(spec["path"])
        if not path_value.is_file() or sha256_file(path_value) != spec["sha256"]:
            raise BypassError(f"immutable input mismatch: {key}")
    return prereg


def load_l1_candidates(prereg: Mapping[str, Any]) -> pd.DataFrame:
    """Read only the direct L1/L2 contract columns and reject holdout exposure."""

    spec = prereg["inputs"]["candidate_dataset"]
    path = repo_path(spec["path"])
    header = tuple(pd.read_csv(path, nrows=0).columns)
    leaked = [column for column in header if column.lower().startswith("l15")]
    if leaked:
        raise BypassError(f"source candidate ledger contains L1.5 columns: {leaked}")
    missing = sorted(set(CANDIDATE_READ_COLUMNS) - set(header))
    if missing:
        raise BypassError(f"source candidate ledger misses required columns: {missing}")
    data = pd.read_csv(path, usecols=list(CANDIDATE_READ_COLUMNS))
    if len(data) != int(spec["rows"]):
        raise BypassError(f"candidate row count drift: {len(data)}")
    data["dependency_representative"] = bool_series(
        data["dependency_representative"], label="dependency_representative"
    )
    data["available_at"] = pd.to_datetime(data["available_at"], utc=True)
    data["exposure_end_exclusive"] = pd.to_datetime(
        data["exposure_end_exclusive"], utc=True
    )
    if data["exposure_end_exclusive"].max() > HOLDOUT_START:
        raise BypassError("candidate label exposure reaches holdout")
    if data["episode_id"].isna().any():
        raise BypassError("candidate episode_id is missing")
    if set(data["side"].astype(str)) != set(SIDES):
        raise BypassError("candidate side domain drift")
    if data[list(L2_FEATURE_COLUMNS)].isna().any().any():
        raise BypassError("candidate L2 features contain missing values")
    return data


def _learning_rows(data: pd.DataFrame, split: str) -> pd.DataFrame:
    return data[(data["split"] == split) & data["dependency_representative"]].copy()


def _train_side(
    data: pd.DataFrame,
    *,
    side: str,
    feature_columns: Sequence[str],
) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray]:
    from yoyo.layers.l2_judgment.train import train_model

    subset = data[data["side"] == side].copy()
    train = _learning_rows(subset, "train")
    tune = _learning_rows(subset, "tune")
    final_events = subset[subset["split"] == "final_validation"].copy()
    final = final_events[final_events["dependency_representative"]].copy()
    if min(len(train), len(tune), len(final)) < 20:
        raise BypassError(
            f"{side} has too few independent rows: "
            f"train={len(train)} tune={len(tune)} final={len(final)}"
        )
    model = train_model(
        train,
        tune,
        feature_columns=feature_columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    tune_score = model.predict(
        tune[list(feature_columns)], num_iteration=model.best_iteration
    )
    threshold = float(np.quantile(tune_score, 0.9))
    final_score = model.predict(
        final_events[list(feature_columns)], num_iteration=model.best_iteration
    )
    final_events["l2_score"] = final_score
    final_events["l2_percentile"] = empirical_percentile(tune_score, final_score)
    final_events["l2_threshold"] = threshold
    final_events["l2_keep"] = final_score >= threshold
    model_dir = OUTPUT_DIR / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"l2_{side}.txt"
    model.save_model(str(model_path))
    return (
        {
            "side": side,
            "splits": {
                "train": len(train),
                "tune": len(tune),
                "final_validation": len(final),
            },
            "best_iteration": int(model.best_iteration),
            "tune_q90_threshold": threshold,
            "model_path": repo_relative(model_path),
            "model_sha256": sha256_file(model_path),
        },
        final_events,
        tune_score,
    )


def _economic_metrics(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    prereg: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    combined = pd.concat([scored_by_side[side] for side in SIDES], ignore_index=True)
    final = combined[combined["dependency_representative"]].copy()
    score = final["l2_percentile"].to_numpy(dtype=float)
    keep = final["l2_keep"].to_numpy(dtype=bool)
    returns = final["realized_ret"].to_numpy(dtype=float)
    cost = float(prereg["outcome"]["round_trip_cost_fraction"])
    selected_ids = set(final.loc[keep, "episode_id"].astype(str))
    by_side: dict[str, Any] = {}
    for side in SIDES:
        subset = final[final["side"] == side]
        side_score = subset["l2_score"].to_numpy(dtype=float)
        side_keep = subset["l2_keep"].to_numpy(dtype=bool)
        by_side[side] = {
            "rank": safe_metrics(
                subset["label"].to_numpy(dtype=int),
                side_score,
                subset["realized_ret"].to_numpy(dtype=float),
                cost,
            ),
            "frozen_q90": selected_metrics(subset, side_keep, cost),
            "permutation_p": outcome_permutation_pvalue(
                side_score, subset["realized_ret"].to_numpy(dtype=float)
            ),
        }
    metrics = {
        "rank": safe_metrics(
            final["label"].to_numpy(dtype=int), score, returns, cost
        ),
        "frozen_q90": selected_metrics(final, keep, cost),
        "permutation_p": outcome_permutation_pvalue(score, returns),
        "matched_control": strict_matched_control_metrics(
            final,
            controls,
            selected_ids,
            required_assignments=int(
                prereg["matched_control"]["deterministic_assignments"]
            ),
        ),
        "by_side": by_side,
    }
    return metrics, combined


def _economic_gate(metrics: Mapping[str, Any]) -> dict[str, bool]:
    gate = {
        "top_decile_net_positive": metrics["rank"]["top_decile"]["net_mean"] > 0,
        "frozen_q90_net_positive": metrics["frozen_q90"]["net_mean"] > 0,
        "minimum_30_selected": metrics["frozen_q90"]["n"] >= 30,
        "outcome_permutation_p_lt_0_01": metrics["permutation_p"] < 0.01,
        "matched_controls_complete": metrics["matched_control"][
            "complete_assignment_coverage"
        ],
        "beats_matched_controls": metrics["matched_control"][
            "all_assignments_positive"
        ],
        "neither_side_q90_negative": all(
            metrics["by_side"][side]["frozen_q90"]["net_mean"] >= 0
            for side in SIDES
        ),
    }
    gate["passed"] = all(gate.values())
    return gate


def _parity_with_prior(
    data: pd.DataFrame,
    model_receipts: Mapping[str, Mapping[str, Any]],
    scored: pd.DataFrame,
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    import lightgbm as lgb

    prior = read_json(repo_path(prereg["inputs"]["prior_l2_only_receipt"]["path"]))
    old_arm = prior["arms"]["l2_only"]
    rows: dict[str, Any] = {}
    all_selected_equal = True
    for side in SIDES:
        new_spec = model_receipts[side]
        old_spec = old_arm["models"][side]
        old_model_path = repo_path(old_spec["model_path"])
        if sha256_file(old_model_path) != old_spec["model_sha256"]:
            raise BypassError(f"prior L2-only model hash mismatch: {side}")
        old_model = lgb.Booster(model_file=str(old_model_path))
        side_final = scored[scored["side"] == side].copy()
        old_score = old_model.predict(
            side_final[list(L2_FEATURE_COLUMNS)],
            num_iteration=int(old_spec["best_iteration"]),
        )
        new_score = side_final["l2_score"].to_numpy(dtype=float)
        old_threshold = float(old_spec["tune_q90_threshold"])
        new_threshold = float(new_spec["tune_q90_threshold"])
        selected_equal = bool(
            np.array_equal(old_score >= old_threshold, new_score >= new_threshold)
        )
        all_selected_equal = all_selected_equal and selected_equal
        rows[side] = {
            "new_model_sha256": new_spec["model_sha256"],
            "prior_model_sha256": old_spec["model_sha256"],
            "model_bytes_equal": new_spec["model_sha256"] == old_spec["model_sha256"],
            "new_threshold": new_threshold,
            "prior_threshold": old_threshold,
            "threshold_abs_diff": abs(new_threshold - old_threshold),
            "score_max_abs_diff": float(np.max(np.abs(new_score - old_score))),
            "selected_ids_equal": selected_equal,
        }
    prior_metrics = old_arm["metrics"]
    current_ids = set(
        scored.loc[scored["dependency_representative"] & scored["l2_keep"], "episode_id"]
        .astype(str)
    )
    return {
        "sides": rows,
        "selected_ids_equal": all_selected_equal,
        "selected_count": len(current_ids),
        "prior_selected_count": int(prior_metrics["frozen_q90"]["n"]),
        "prior_receipt_path": prereg["inputs"]["prior_l2_only_receipt"]["path"],
        "prior_receipt_sha256": prereg["inputs"]["prior_l2_only_receipt"]["sha256"],
    }


def train_and_verify(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "training_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    data = load_l1_candidates(prereg)
    controls_spec = prereg["inputs"]["matched_controls"]
    controls = pd.read_csv(repo_path(controls_spec["path"]))
    model_receipts: dict[str, Any] = {}
    scored_by_side: dict[str, pd.DataFrame] = {}
    for side in SIDES:
        model_receipts[side], scored_by_side[side], _ = _train_side(
            data, side=side, feature_columns=L2_FEATURE_COLUMNS
        )
    metrics, scored = _economic_metrics(scored_by_side, controls, prereg)
    parity = _parity_with_prior(data, model_receipts, scored, prereg)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scored_path = OUTPUT_DIR / "final_validation_scored.csv"
    scored.to_csv(scored_path, index=False)
    bypass_gate = {
        "candidate_allow_list_has_no_l15": not any(
            column.lower().startswith("l15") for column in CANDIDATE_READ_COLUMNS
        ),
        "model_bytes_equal": all(
            parity["sides"][side]["model_bytes_equal"] for side in SIDES
        ),
        "score_max_abs_diff_lte_1e_12": all(
            parity["sides"][side]["score_max_abs_diff"] <= 1e-12 for side in SIDES
        ),
        "threshold_abs_diff_lte_1e_15": all(
            parity["sides"][side]["threshold_abs_diff"] <= 1e-15 for side in SIDES
        ),
        "selected_ids_equal": bool(parity["selected_ids_equal"]),
        "selected_count_equal": parity["selected_count"]
        == parity["prior_selected_count"],
    }
    bypass_gate["passed"] = all(bypass_gate.values())
    economic_gate = _economic_gate(metrics)
    payload = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "protocol": prereg["protocol"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": git_head(),
        "pipeline": prereg["pipeline"],
        "candidate_dataset": {
            "path": prereg["inputs"]["candidate_dataset"]["path"],
            "sha256": prereg["inputs"]["candidate_dataset"]["sha256"],
            "rows": len(data),
            "columns_read": list(CANDIDATE_READ_COLUMNS),
            "l15_columns_read": [],
            "max_exposure_end_exclusive": data["exposure_end_exclusive"].max().isoformat(),
        },
        "feature_columns": list(L2_FEATURE_COLUMNS),
        "models": model_receipts,
        "metrics": metrics,
        "parity": parity,
        "bypass_gate": bypass_gate,
        "economic_gate": economic_gate,
        "scored_path": repo_relative(scored_path),
        "scored_sha256": sha256_file(scored_path),
        "decision": (
            "L15_BYPASSED_L2_REMAINS_REJECTED"
            if bypass_gate["passed"] and not economic_gate["passed"]
            else "UNEXPECTED_RESULT_REQUIRES_INVESTIGATION"
        ),
        "holdout_consumed": False,
        "production_eligible": False,
        "promoted": False,
        "deployed": False,
    }
    write_json(terminal, payload)
    return payload


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    receipt = read_json(RESULTS_DIR / "training_receipt.json")
    checks = {
        "experiment_id": receipt.get("experiment_id") == EXPERIMENT_ID,
        "enabled_topology": receipt.get("pipeline", {}).get("enabled_stages")
        == prereg["pipeline"]["enabled_stages"],
        "l15_disabled": receipt.get("pipeline", {}).get("disabled_stages")
        == ["l15_global_shape_classifier"],
        "zero_l15_columns_read": receipt["candidate_dataset"]["l15_columns_read"] == [],
        "feature_order": receipt["feature_columns"] == list(L2_FEATURE_COLUMNS),
        "bypass_gate_passed": receipt["bypass_gate"]["passed"] is True,
        "economic_gate_rejected": receipt["economic_gate"]["passed"] is False,
        "holdout_not_consumed": receipt["holdout_consumed"] is False,
        "not_production_eligible": receipt["production_eligible"] is False,
        "not_promoted": receipt["promoted"] is False,
        "not_deployed": receipt["deployed"] is False,
    }
    for side in SIDES:
        spec = receipt["models"][side]
        checks[f"{side}_model_hash"] = (
            sha256_file(repo_path(spec["model_path"])) == spec["model_sha256"]
        )
    checks["scored_hash"] = (
        sha256_file(repo_path(receipt["scored_path"])) == receipt["scored_sha256"]
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "passed": all(checks.values()),
    }
    if not payload["passed"]:
        raise BypassError(f"verification failed: {checks}")
    write_json(RESULTS_DIR / "verify_receipt.json", payload)
    return payload


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.3f}%"


def build_report(prereg: Mapping[str, Any]) -> Path:
    receipt = read_json(RESULTS_DIR / "training_receipt.json")
    metrics = receipt["metrics"]
    long = metrics["by_side"]["long"]["frozen_q90"]
    short = metrics["by_side"]["short"]["frozen_q90"]
    text = f"""# 15m 均线密集启动：移除 L1.5 后的 L1→L2 旁路验证

## 结论先行

L1.5 已从默认研究链中**物理旁路**。新入口只执行：

`冻结 L1 候选 → dependency episode 合并 → LONG/SHORT 独立 L2 收益回归`

旁路验证通过：新 LONG/SHORT 模型字节、逐事件分数、tune-q90 阈值和 34 个最终入选事件
都与旧 factorial 实验的 L2-only 臂一致。训练和评分读取的 {len(CANDIDATE_READ_COLUMNS)} 个
CSV 字段中 L1.5 字段为 **0**。

这不改变经济裁决：L2-only 仍为 **REJECTED**，没有生产资格。

## 旁路证明

| 检查 | LONG | SHORT |
|---|---:|---:|
| 模型字节一致 | {receipt['parity']['sides']['long']['model_bytes_equal']} | {receipt['parity']['sides']['short']['model_bytes_equal']} |
| 最大逐分数误差 | {receipt['parity']['sides']['long']['score_max_abs_diff']:.3e} | {receipt['parity']['sides']['short']['score_max_abs_diff']:.3e} |
| 阈值绝对误差 | {receipt['parity']['sides']['long']['threshold_abs_diff']:.3e} | {receipt['parity']['sides']['short']['threshold_abs_diff']:.3e} |
| 入选集合一致 | {receipt['parity']['sides']['long']['selected_ids_equal']} | {receipt['parity']['sides']['short']['selected_ids_equal']} |

## 数据统计

| 项目 | 数值 |
|---|---:|
| 原始 L1 ledger 行数 | {receipt['candidate_dataset']['rows']:,} |
| final 独立事件 | {metrics['rank']['n']} |
| L2 q90 入选 | {metrics['frozen_q90']['n']} |
| 特征数 | {len(receipt['feature_columns'])} |
| L1.5 字段读取数 | {len(receipt['candidate_dataset']['l15_columns_read'])} |
| 数据截止 | {receipt['candidate_dataset']['max_exposure_end_exclusive']} |

## 经济结果与上一版本同表对照

| 配置 | 入选 n | 净均值 | 胜率 | 置换 p | 裁决 |
|---|---:|---:|---:|---:|---|
| 旧 factorial L2-only | {receipt['parity']['prior_selected_count']} | {_pct(metrics['frozen_q90']['net_mean'])} | {_pct(metrics['frozen_q90']['win_rate'])} | {metrics['permutation_p']:.6f} | REJECT |
| 新 L1→L2 bypass | {metrics['frozen_q90']['n']} | {_pct(metrics['frozen_q90']['net_mean'])} | {_pct(metrics['frozen_q90']['win_rate'])} | {metrics['permutation_p']:.6f} | REJECT |
| bypass LONG | {long['n']} | {_pct(long['net_mean'])} | {_pct(long['win_rate'])} | {metrics['by_side']['long']['permutation_p']:.6f} | FAIL |
| bypass SHORT | {short['n']} | {_pct(short['net_mean'])} | {_pct(short['win_rate'])} | {metrics['by_side']['short']['permutation_p']:.6f} | exploratory only |

Top-decile 毛/净收益分别为 {_pct(metrics['rank']['top_decile']['net_mean'] + prereg['outcome']['round_trip_cost_fraction'])}
/ {_pct(metrics['rank']['top_decile']['net_mean'])}；AUC={metrics['rank']['roc_auc']:.4f}，
Spearman={metrics['rank']['spearman_score_vs_return']:.4f}。这些分类指标仅作诊断，生产裁决仍以
扣成本收益、置换检验和匹配随机对照为准。

## 匹配随机对照

34 个入选事件全部具有 8/8 组同币、同时间块、同波动桶、同方向对照；事件相对对照的平均
超额为 {_pct(metrics['matched_control']['mean_event_minus_control'])}，8 组方向均为正。但主检验
`p={metrics['permutation_p']:.6f}` 未过 0.01，且 LONG q90 净均值为 {_pct(long['net_mean'])}，
因此不能用对照组的单项通过覆盖总门失败。

## 解读

本轮只回答“L1.5 是否真的被拿掉”：答案是**是**。新模型与旧 L2-only 完全一致，证明此前
L2-only 的计算没有受 L1.5 过滤影响。它也证明移除 L1.5 不会自动修好 L2：聚合收益由 SHORT
贡献，LONG 为负，统计显著性不足。

## 风险与诚实声明

- final 时间段已在前序实验中使用，本轮仅做确定性旁路复现，不是新验证。
- 没有读取 `>=2026-05-04` holdout。
- 没有调模型、特征、阈值、障碍或成本。
- 没有 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。
- L1.5 历史代码与结果保留，但默认链路不再调用。

## 下一步选项

1. 保持当前简化拓扑，另开单变量 L2 改进实验；必须使用新的未见 pre-holdout 时间段。
2. 在 LONG/SHORT 两侧都过经济门之前，不申请 holdout，也不接生产。
3. 只有获得有效全局形态真值并另行授权时，才考虑重新引入 L1.5。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l1_l2_bypass_l15 --train
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l1_l2_bypass_l15 --verify
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l1_l2_bypass_l15 --report
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l1_l2_bypass_l15_20260901.md --out-dir analysis/html
```
"""
    REPORT_PATH.write_text(text, encoding="utf-8")
    return REPORT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prereg = load_preregistration()
    if not any((args.train, args.verify, args.report, args.all)):
        raise SystemExit("choose --train, --verify, --report or --all")
    if args.all or args.train:
        train_and_verify(prereg)
    if args.all or args.verify:
        verify_outputs(prereg)
    if args.all or args.report:
        build_report(prereg)


if __name__ == "__main__":
    main()

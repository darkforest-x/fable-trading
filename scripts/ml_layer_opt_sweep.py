"""Single-axis ML-layer optimization sweep on the current YOLO judgment pool.

Discipline (AGENTS.md):
- train/val only; never opens holdout for metrics or tuning
- does not write frozen models / forward_log / features.py FEATURE_COLUMNS
- one change per variant vs shared baseline; economic primary metric is
  top-decile mean net after 0.2% round-trip (not AUC)

Usage:
  python3 scripts/ml_layer_opt_sweep.py
  python3 scripts/ml_layer_opt_sweep.py --data data/judgment_yolo_swap.csv --tag ml_opt_yolo
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import (
    LGB_PARAMS,
    ROUND_TRIP_COST,
    evaluate,
    load_splits,
    permutation_pvalue,
    train_baseline,
    baseline_prob,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"
DEFAULT_DATA = PROJECT_DIR / "data" / "judgment_yolo_swap.csv"


def _train_binary(
    train: pd.DataFrame,
    val: pd.DataFrame,
    *,
    params: dict[str, Any],
    feature_columns: list[str],
    sample_weight: np.ndarray | None = None,
    num_boost_round: int = 600,
    early_stopping: int = 50,
) -> lgb.Booster:
    dtrain = lgb.Dataset(
        train[feature_columns],
        label=train["label"],
        weight=sample_weight,
        free_raw_data=False,
    )
    dval = lgb.Dataset(val[feature_columns], label=val["label"], reference=dtrain, free_raw_data=False)
    return lgb.train(
        params,
        dtrain,
        num_boost_round=num_boost_round,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(early_stopping, verbose=False)],
    )


def _train_regression(
    train: pd.DataFrame,
    val: pd.DataFrame,
    *,
    params: dict[str, Any],
    feature_columns: list[str],
    target: str = "realized_ret",
    num_boost_round: int = 600,
    early_stopping: int = 50,
) -> lgb.Booster:
    p = dict(params)
    p["objective"] = "regression"
    p.pop("scale_pos_weight", None)
    dtrain = lgb.Dataset(train[feature_columns], label=train[target], free_raw_data=False)
    dval = lgb.Dataset(val[feature_columns], label=val[target], reference=dtrain, free_raw_data=False)
    return lgb.train(
        p,
        dtrain,
        num_boost_round=num_boost_round,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(early_stopping, verbose=False)],
    )


def _pack_metrics(
    name: str,
    note: str,
    y_true: np.ndarray,
    y_score: np.ndarray,
    returns: np.ndarray,
    *,
    best_iteration: int | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    val = evaluate(y_true, y_score, returns)
    # Production-like gate: val q90 of scores (same as frozen threshold policy)
    thr = float(np.quantile(y_score, 0.9))
    gate = y_score >= thr
    gate_n = int(gate.sum())
    if gate_n > 0:
        gate_net = float(returns[gate].mean() - ROUND_TRIP_COST)
        gate_win = float(y_true[gate].mean())
        gate_gross = float(returns[gate].mean())
    else:
        gate_net = gate_win = gate_gross = float("nan")
    out: dict[str, Any] = {
        "variant": name,
        "note": note,
        "best_iteration": best_iteration,
        "val": val,
        "val_permutation_p": permutation_pvalue(y_true, y_score),
        "val_q90_gate": {
            "threshold": round(thr, 6),
            "n": gate_n,
            "mean_gross": None if gate_n == 0 else round(gate_gross, 5),
            "mean_net_0p2": None if gate_n == 0 else round(gate_net, 5),
            "win_rate": None if gate_n == 0 else round(gate_win, 4),
        },
    }
    if extra:
        out["extra"] = extra
    return out


def _recency_weights(train: pd.DataFrame, half_life_days: float = 60.0) -> np.ndarray:
    t = pd.to_datetime(train["signal_time"], utc=True)
    age_days = (t.max() - t).dt.total_seconds() / 86400.0
    w = np.exp(-np.log(2) * age_days.to_numpy(dtype=float) / half_life_days)
    return w / w.mean()


def _abs_ret_weights(train: pd.DataFrame, floor: float = 0.25) -> np.ndarray:
    """Up-weight large-move outcomes so ranking cares more about magnitude."""
    mag = np.abs(train["realized_ret"].to_numpy(dtype=float))
    w = floor + mag / (np.median(mag) + 1e-12)
    return w / w.mean()


def run_sweep(data: Path, tag: str) -> dict[str, Any]:
    train, val, holdout = load_splits(data)
    # holdout is loaded only to report sizes; never used for metrics
    y_val = val["label"].to_numpy()
    ret_val = val["realized_ret"].to_numpy()
    feats = list(FEATURE_COLUMNS)

    variants: list[tuple[str, str, Callable[[], tuple[np.ndarray, int | None, dict]]]] = []

    def add(name: str, note: str, fn: Callable[[], tuple[np.ndarray, int | None, dict]]) -> None:
        variants.append((name, note, fn))

    # --- 0 baseline ---
    def v_baseline() -> tuple[np.ndarray, int | None, dict]:
        m = _train_binary(train, val, params=dict(LGB_PARAMS), feature_columns=feats)
        s = m.predict(val[feats], num_iteration=m.best_iteration)
        return s, m.best_iteration, {"params": dict(LGB_PARAMS)}

    add("baseline", "Current LGB_PARAMS binary classifier", v_baseline)

    # --- 1 stronger regularization ---
    def v_strong_reg() -> tuple[np.ndarray, int | None, dict]:
        p = dict(LGB_PARAMS)
        p.update({"num_leaves": 7, "min_child_samples": 50, "lambda_l2": 5.0, "feature_fraction": 0.7})
        m = _train_binary(train, val, params=p, feature_columns=feats)
        return m.predict(val[feats], num_iteration=m.best_iteration), m.best_iteration, {"params": p}

    add("strong_reg", "num_leaves=7, min_child=50, l2=5, feat_frac=0.7", v_strong_reg)

    # --- 2 deeper trees ---
    def v_deeper() -> tuple[np.ndarray, int | None, dict]:
        p = dict(LGB_PARAMS)
        p.update({"num_leaves": 31, "min_child_samples": 20, "learning_rate": 0.03})
        m = _train_binary(train, val, params=p, feature_columns=feats)
        return m.predict(val[feats], num_iteration=m.best_iteration), m.best_iteration, {"params": p}

    add("deeper", "num_leaves=31, min_child=20, lr=0.03", v_deeper)

    # --- 3 scale_pos_weight ---
    def v_spw() -> tuple[np.ndarray, int | None, dict]:
        p = dict(LGB_PARAMS)
        pos = float(train["label"].sum())
        neg = float(len(train) - pos)
        spw = neg / max(pos, 1.0)
        p["scale_pos_weight"] = spw
        m = _train_binary(train, val, params=p, feature_columns=feats)
        return m.predict(val[feats], num_iteration=m.best_iteration), m.best_iteration, {"scale_pos_weight": spw}

    add("scale_pos_weight", "balance class frequency via scale_pos_weight", v_spw)

    # --- 4 recency sample weights ---
    def v_recency() -> tuple[np.ndarray, int | None, dict]:
        w = _recency_weights(train, half_life_days=60.0)
        m = _train_binary(train, val, params=dict(LGB_PARAMS), feature_columns=feats, sample_weight=w)
        return m.predict(val[feats], num_iteration=m.best_iteration), m.best_iteration, {
            "half_life_days": 60,
            "w_min": float(w.min()),
            "w_max": float(w.max()),
        }

    add("recency_weight_60d", "exp decay sample weight half-life 60d", v_recency)

    # --- 5 magnitude sample weights ---
    def v_mag() -> tuple[np.ndarray, int | None, dict]:
        w = _abs_ret_weights(train)
        m = _train_binary(train, val, params=dict(LGB_PARAMS), feature_columns=feats, sample_weight=w)
        return m.predict(val[feats], num_iteration=m.best_iteration), m.best_iteration, {
            "w_min": float(w.min()),
            "w_max": float(w.max()),
        }

    add("abs_ret_weight", "sample weight ∝ floor+|realized_ret|", v_mag)

    # --- 6 regression on realized_ret ---
    def v_reg() -> tuple[np.ndarray, int | None, dict]:
        p = dict(LGB_PARAMS)
        m = _train_regression(train, val, params=p, feature_columns=feats, target="realized_ret")
        s = m.predict(val[feats], num_iteration=m.best_iteration)
        return s, m.best_iteration, {"objective": "regression", "target": "realized_ret"}

    add("reg_realized_ret", "predict realized_ret; rank by predicted return", v_reg)

    # --- 7 multi-seed bagging ensemble ---
    def v_seed_ens() -> tuple[np.ndarray, int | None, dict]:
        seeds = [42, 7, 2026]
        scores = []
        iters = []
        for s in seeds:
            p = dict(LGB_PARAMS)
            p["seed"] = s
            p["bagging_seed"] = s
            p["feature_fraction_seed"] = s
            m = _train_binary(train, val, params=p, feature_columns=feats)
            scores.append(m.predict(val[feats], num_iteration=m.best_iteration))
            iters.append(m.best_iteration)
        avg = np.mean(np.vstack(scores), axis=0)
        return avg, int(np.mean(iters)), {"seeds": seeds, "iters": iters}

    add("seed_ensemble_3", "average 3 seeds {42,7,2026}", v_seed_ens)

    # --- 8 top features only (from a fresh baseline importance) ---
    def v_top_feats() -> tuple[np.ndarray, int | None, dict]:
        m0 = _train_binary(train, val, params=dict(LGB_PARAMS), feature_columns=feats)
        gain = m0.feature_importance(importance_type="gain")
        order = np.argsort(-gain)
        keep = [feats[i] for i in order[:15]]
        m = _train_binary(train, val, params=dict(LGB_PARAMS), feature_columns=keep)
        return m.predict(val[keep], num_iteration=m.best_iteration), m.best_iteration, {
            "n_features": 15,
            "features": keep,
        }

    add("top15_features", "retrain on top-15 gain features from baseline", v_top_feats)

    # --- 9 lower learning rate longer ---
    def v_slow() -> tuple[np.ndarray, int | None, dict]:
        p = dict(LGB_PARAMS)
        p["learning_rate"] = 0.02
        m = _train_binary(train, val, params=p, feature_columns=feats, num_boost_round=1200, early_stopping=80)
        return m.predict(val[feats], num_iteration=m.best_iteration), m.best_iteration, {"params": p}

    add("slow_lr_0p02", "lr=0.02, rounds=1200, es=80", v_slow)

    # --- 10 logreg baseline (single feature) for reference ---
    def v_logreg() -> tuple[np.ndarray, int | None, dict]:
        sc, lr = train_baseline(train)
        return baseline_prob(sc, lr, val), None, {"model": "logreg_ma_spread_pct"}

    add("baseline_logreg_ma_spread", "single-feature logistic baseline", v_logreg)

    rows: list[dict[str, Any]] = []
    for name, note, fn in variants:
        print(f"[ml_opt] running {name} ...", flush=True)
        score, it, extra = fn()
        # regression scores can be outside [0,1]; evaluate still uses ranking for top-decile
        # For AUC, regression scores are fine as ranking scores
        packed = _pack_metrics(name, note, y_val, score, ret_val, best_iteration=it, extra=extra)
        rows.append(packed)
        td = packed["val"]["top_decile"]
        print(
            f"  AUC={packed['val']['roc_auc']:.4f} p={packed['val_permutation_p']:.3f} "
            f"top_net={td['mean_net_ret']:+.5f} top_n={td['n']} q90_net={packed['val_q90_gate']['mean_net_0p2']}",
            flush=True,
        )

    # rank by top-decile net (primary), then q90 gate net, then AUC
    def rank_key(r: dict[str, Any]) -> tuple:
        td = r["val"]["top_decile"]["mean_net_ret"]
        qn = r["val_q90_gate"]["mean_net_0p2"]
        qn = -1e9 if qn is None or (isinstance(qn, float) and np.isnan(qn)) else qn
        return (td, qn, r["val"]["roc_auc"])

    ranked = sorted(rows, key=rank_key, reverse=True)
    baseline_row = next(r for r in rows if r["variant"] == "baseline")
    best = ranked[0]

    summary = {
        "dataset": str(data),
        "tag": tag,
        "cost_round_trip": ROUND_TRIP_COST,
        "holdout_policy": "holdout loaded for size only; NEVER evaluated or used for tuning",
        "splits": {
            "train_n": int(len(train)),
            "val_n": int(len(val)),
            "holdout_n": int(len(holdout)),
            "train_range": [str(train["signal_time"].min()), str(train["signal_time"].max())],
            "val_range": [str(val["signal_time"].min()), str(val["signal_time"].max())],
            "pos_rate_train": round(float(train["label"].mean()), 4),
            "pos_rate_val": round(float(val["label"].mean()), 4),
        },
        "primary_metric": "val top-decile mean_net_ret after 0.2% RT cost",
        "baseline_top_net": baseline_row["val"]["top_decile"]["mean_net_ret"],
        "best_variant": best["variant"],
        "best_top_net": best["val"]["top_decile"]["mean_net_ret"],
        "delta_vs_baseline": round(
            best["val"]["top_decile"]["mean_net_ret"] - baseline_row["val"]["top_decile"]["mean_net_ret"], 5
        ),
        "variants": rows,
        "ranking": [r["variant"] for r in ranked],
    }
    return summary


def write_report(summary: dict[str, Any], report_path: Path) -> None:
    lines: list[str] = []
    lines.append("# ML 层优化扫描（YOLO 判断池，val-only）\n")
    lines.append(f"**日期**：{pd.Timestamp.utcnow().strftime('%Y-%m-%d')}  \n")
    lines.append("**纪律**：未加 `--eval-holdout`；未改 `features.py` / 冻结模型 / `forward_log`。  \n")
    lines.append("**主指标**：val top-decile 扣 0.2% 往返后净收益（AUC 仅参考）。\n")
    lines.append("\n## 复现\n\n```bash\n")
    lines.append(f"python3 scripts/ml_layer_opt_sweep.py --data {summary['dataset']} --tag {summary['tag']}\n")
    lines.append("```\n")
    sp = summary["splits"]
    lines.append("\n## 数据\n\n")
    lines.append(f"| 项 | 值 |\n|---|---|\n")
    lines.append(f"| 数据集 | `{summary['dataset']}` |\n")
    lines.append(f"| train / val / holdout(n only) | {sp['train_n']} / {sp['val_n']} / {sp['holdout_n']} |\n")
    lines.append(f"| val 正类率 | {sp['pos_rate_val']} |\n")
    lines.append(f"| train 时间 | {sp['train_range'][0]} → {sp['train_range'][1]} |\n")
    lines.append(f"| val 时间 | {sp['val_range'][0]} → {sp['val_range'][1]} |\n")

    lines.append("\n## 结果表（按 top-decile 净收益排序）\n\n")
    lines.append(
        "| 排名 | 变体 | AUC | perm p | top-n | top 毛 | top 净@0.2% | top 胜率 | q90-n | q90 净@0.2% | best_iter |\n"
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    by_name = {r["variant"]: r for r in summary["variants"]}
    for i, name in enumerate(summary["ranking"], 1):
        r = by_name[name]
        td = r["val"]["top_decile"]
        g = r["val_q90_gate"]
        qn = g["mean_net_0p2"]
        qn_s = "—" if qn is None else f"{qn:+.5f}"
        lines.append(
            f"| {i} | `{name}` | {r['val']['roc_auc']:.4f} | {r['val_permutation_p']:.3f} | "
            f"{td['n']} | {td['mean_realized_ret']:+.5f} | **{td['mean_net_ret']:+.5f}** | "
            f"{td['win_rate']:.3f} | {g['n']} | {qn_s} | {r['best_iteration']} |\n"
        )

    base = by_name["baseline"]
    best = by_name[summary["best_variant"]]
    lines.append("\n## 对照基线\n\n")
    lines.append(
        f"- baseline top 净：`{base['val']['top_decile']['mean_net_ret']:+.5f}`  \n"
        f"- 最优变体：`{summary['best_variant']}` → `{best['val']['top_decile']['mean_net_ret']:+.5f}`  \n"
        f"- Δ：`{summary['delta_vs_baseline']:+.5f}`  \n"
    )

    lines.append("\n## 各变体说明\n\n")
    for r in summary["variants"]:
        lines.append(f"- **{r['variant']}**：{r['note']}\n")

    lines.append("\n## 解读\n\n")
    delta = summary["delta_vs_baseline"]
    if delta > 0.002:
        lines.append(
            f"最优变体相对 baseline 的 top 净提升 **{delta:+.5f}**（>20bp）。"
            "若 p 仍 <0.01 且 q90 gate 不塌，可作为挑战者做影子/冻结候选；"
            "**不得**仅凭 val 切换生产冻结。\n"
        )
    elif delta > 0:
        lines.append(
            f"最优变体仅小幅优于 baseline（Δ={delta:+.5f}），落在小样本噪声带内"
            f"（val top-n≈{best['val']['top_decile']['n']}）。**不建议**改主线超参。\n"
        )
    else:
        lines.append(
            f"没有任何变体在 top 净上显著打赢 baseline（最优 Δ={delta:+.5f}）。"
            "当前 LGB 默认配置对 YOLO 池已足够；ML 层继续堆复杂度 ROI 低，"
            "优先应打在候选质量（检测）与成本/出场，而非换模型族。\n"
        )

    # flag suspiciously high AUC
    if best["val"]["roc_auc"] >= 0.75:
        lines.append(
            "\n> **异常警示**：val AUC ≥ 0.75（YOLO 池已知现象）。优先怀疑选择偏置/小样本，"
            "而非「模型真的很强」。经济指标方差大，前向 100 笔才是硬闸门。\n"
        )

    lines.append("\n## 风险与诚实声明\n\n")
    lines.append(
        "- 本扫描 **未评估 holdout**，未消耗 holdout 配额。\n"
        "- val 已被多次选型使用，数字只用于排序，不得宣称样本外绩效。\n"
        "- 未写入 `models/frozen*`，未改前向配置。\n"
        "- 多变体并行扫描，存在多重比较；胜出者需要独立前向验证。\n"
    )
    lines.append("\n## 下一步选项（需 owner 决策的已标注）\n\n")
    lines.append(
        "1. 若最优 Δ 大且稳定：用该配置 **另打 tag 冻结影子**，不替换 ACTIVE（owner 决策）。\n"
        "2. 若全军覆没或噪声级：停止 ML 超参军备竞赛；资源转向 YOLO 重标/重训与前向密度。\n"
        "3. 可选：在 **规则 expanded 池** 上重复本扫描（更大 n，AUC 更低，更考验排序）。\n"
        "4. **不要** 因本结果消耗 holdout。\n"
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--tag", default="ml_opt_yolo")
    args = parser.parse_args()

    summary = run_sweep(args.data, args.tag)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUTPUT_DIR / f"{args.tag}_sweep.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = PROJECT_DIR / "analysis" / f"p2b_{args.tag}_report.md"
    write_report(summary, report)
    print(json.dumps({
        "best_variant": summary["best_variant"],
        "best_top_net": summary["best_top_net"],
        "delta_vs_baseline": summary["delta_vs_baseline"],
        "ranking": summary["ranking"],
        "json": str(out_json),
        "report": str(report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

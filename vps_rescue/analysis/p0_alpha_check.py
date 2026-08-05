#!/usr/bin/env python3
"""P0 alpha 检验：人工标注的"启动初期"正样本，其未来收益分布是否显著优于负样本。

数据来源（只读）：旧项目 yolo-yolo-okx-20-k 各版本数据集的 metadata.csv。
关键字段（由旧项目 tools/build_strict_dense_review_pack.py 生成）：
  - future_favorable_pct = 未来 72 根 K 线最高价 / 信号收盘价 - 1   （MFE，做多有利幅度）
  - future_adverse_pct   = 未来 72 根 K 线最低价 / 信号收盘价 - 1   （MAE，做多不利幅度，通常为负）
  - user_label 为人工复核标签（positive/negative），是权威标签。

运行：python3 analysis/p0_alpha_check.py
产出：analysis/output/ 下的统计表 CSV 与分布对比图 PNG，并在终端打印摘要。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

OLD_ROOT = Path(
    "/Users/zhangzc/Documents/Codex/2026-06-17/yolo-yolo-okx-20-k/outputs/yolo_ma_cluster_trader"
)
OUT_DIR = Path(__file__).resolve().parent / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 含人工标签 + future 字段的所有版本（v177/v178/v181 是历史版本的合并超集，
# 逐版本单独检验，合并检验时按 (inst_id, bar, signal_time, direction) 去重）。
DATASETS = {
    "v169_seed": "runs/human_strict_v169_seed_20260704",
    "v170_mixed": "runs/human_strict_v170_mixed_real_20260704",
    "v171_hard_mining": "runs/human_strict_v171_hard_mining_20260705",
    "v172_base": "runs/human_strict_v172_current_right_base_20260705",
    "v172_balanced": "runs/human_strict_v172_current_right_balanced_20260705",
    "v172_active_feedback": "runs/human_strict_v172_current_right_active_feedback_20260705",
    "v176_human_seed": "datasets/dual_ma20_60_120_v176_yolo175_human_seed_20260705_232011",
    "v177_combined": "datasets/dual_ma20_60_120_v177_combined_human_20260706_001043",
    "v178_user_feedback": "datasets/dual_ma20_60_120_v178_user_feedback_20260706",
    "v179_clean_feedback": "datasets/dual_ma20_60_120_v179_clean_user_feedback_20260706",
    "v180_hard_negative": "datasets/dual_ma20_60_120_v180_hard_negative_20260706",
    "v181_long_first": "datasets/dual_ma20_60_120_v181_long_first_20260706",
}

ROUNDTRIP_COST = 0.002  # 单边 taker 0.05% + 滑点 0.05%，往返约 0.2%

NUM_COLS = ["future_favorable_pct", "future_adverse_pct", "volume_ratio", "volume_z", "ma_spread_pct"]


def load_version(name: str, rel: str) -> pd.DataFrame:
    df = pd.read_csv(OLD_ROOT / rel / "metadata.csv", low_memory=False)
    keep = [
        c
        for c in [
            "user_label", "positive", "group", "direction", "candidate_mode",
            "bar", "inst_id", "signal_time", *NUM_COLS,
        ]
        if c in df.columns
    ]
    df = df[keep].copy()
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    if "bar" not in df.columns:
        df["bar"] = "unknown"
    if "candidate_mode" not in df.columns:
        df["candidate_mode"] = np.nan
    df["label"] = df["user_label"].where(df["user_label"].isin(["positive", "negative"]))
    df = df[df["label"].notna() & df["future_favorable_pct"].notna() & df["future_adverse_pct"].notna()]
    df["version"] = name
    return df


def rank_biserial(pos: np.ndarray, neg: np.ndarray, u_stat: float) -> float:
    """rank-biserial r = 2*U/(n1*n2) - 1，等价于 2*AUC-1；>0 表示正样本整体更大。"""
    return 2.0 * u_stat / (len(pos) * len(neg)) - 1.0


def test_block(df: pd.DataFrame, scope: str) -> dict:
    pos = df[df["label"] == "positive"]
    neg = df[df["label"] == "negative"]
    row = {"scope": scope, "n_pos": len(pos), "n_neg": len(neg)}
    for col, tag in [("future_favorable_pct", "fav"), ("future_adverse_pct", "adv")]:
        p_vals, n_vals = pos[col].to_numpy(), neg[col].to_numpy()
        for grp, vals in [("pos", p_vals), ("neg", n_vals)]:
            row[f"{tag}_{grp}_mean"] = np.mean(vals) if len(vals) else np.nan
            row[f"{tag}_{grp}_median"] = np.median(vals) if len(vals) else np.nan
            row[f"{tag}_{grp}_q25"] = np.percentile(vals, 25) if len(vals) else np.nan
            row[f"{tag}_{grp}_q75"] = np.percentile(vals, 75) if len(vals) else np.nan
        if len(p_vals) >= 3 and len(n_vals) >= 3:
            u, p = stats.mannwhitneyu(p_vals, n_vals, alternative="two-sided")
            row[f"{tag}_U_p"] = p
            row[f"{tag}_rank_biserial"] = rank_biserial(p_vals, n_vals, u)
        else:
            row[f"{tag}_U_p"] = np.nan
            row[f"{tag}_rank_biserial"] = np.nan
    # 经济意义：正样本中位 MFE - |中位 MAE| 与往返成本比较
    row["pos_median_net"] = row["fav_pos_median"] - abs(row["adv_pos_median"])
    row["neg_median_net"] = row["fav_neg_median"] - abs(row["adv_neg_median"])
    row["pos_net_minus_cost"] = row["pos_median_net"] - ROUNDTRIP_COST
    row["covers_cost"] = bool(row["pos_net_minus_cost"] > 0)
    return row


def cdf_xy(vals: np.ndarray):
    x = np.sort(vals)
    return x, np.arange(1, len(x) + 1) / len(x)


def plot_distributions(df: pd.DataFrame, title: str, path: Path) -> None:
    pos, neg = df[df["label"] == "positive"], df[df["label"] == "negative"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for j, (col, cname) in enumerate([
        ("future_favorable_pct", "future_favorable_pct (MFE)"),
        ("future_adverse_pct", "future_adverse_pct (MAE)"),
    ]):
        p_vals, n_vals = pos[col].to_numpy() * 100, neg[col].to_numpy() * 100
        lo, hi = np.percentile(np.concatenate([p_vals, n_vals]), [0.5, 99.5])
        bins = np.linspace(lo, hi, 50)
        ax = axes[0, j]
        ax.hist(n_vals, bins=bins, alpha=0.55, density=True, label=f"negative (n={len(n_vals)})", color="#888888")
        ax.hist(p_vals, bins=bins, alpha=0.55, density=True, label=f"positive (n={len(p_vals)})", color="#d62728")
        ax.axvline(np.median(n_vals), color="#444444", ls="--", lw=1)
        ax.axvline(np.median(p_vals), color="#d62728", ls="--", lw=1)
        ax.set_title(f"{cname} histogram")
        ax.set_xlabel("%")
        ax.legend(fontsize=8)
        ax = axes[1, j]
        for vals, lab, c in [(n_vals, "negative", "#888888"), (p_vals, "positive", "#d62728")]:
            x, y = cdf_xy(vals)
            ax.plot(x, y, label=lab, color=c)
        ax.set_xlim(lo, hi)
        ax.set_title(f"{cname} CDF")
        ax.set_xlabel("%")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    frames = [load_version(k, v) for k, v in DATASETS.items()]
    all_rows = pd.concat(frames, ignore_index=True)

    # 主分析限定 direction=long（v181 即 long-first；short 样本的"有利方向"定义相反，单独报告）
    long_rows = all_rows[all_rows["direction"] == "long"].copy()

    # 合并去重：同一 (inst_id, bar, signal_time) 的样本在多版本重复出现，
    # 保留最新版本（后加载的版本覆盖，标签以最近一次人工复核为准）
    merged = (
        long_rows.sort_values("version")
        .drop_duplicates(subset=["inst_id", "bar", "signal_time"], keep="last")
        .copy()
    )

    results = []
    for name in DATASETS:
        sub = long_rows[long_rows["version"] == name]
        if len(sub):
            results.append(test_block(sub, f"version:{name} (long)"))
    results.append(test_block(merged, "merged_dedup (long)"))

    # 敏感性：v169 的候选筛选可能带前视（strict 模式以未来收益筛候选），剔除后复验
    merged_no169 = merged[merged["version"] != "v169_seed"]
    results.append(test_block(merged_no169, "merged_dedup_excl_v169 (long)"))

    # short 样本单独参考（favorable/adverse 均按做多方向计算，short 语义相反）
    short_rows = all_rows[all_rows["direction"] == "short"]
    short_merged = (
        short_rows.sort_values("version")
        .drop_duplicates(subset=["inst_id", "bar", "signal_time"], keep="last")
    )
    if len(short_merged):
        results.append(test_block(short_merged, "merged_dedup (short, 参考)"))

    stats_version = pd.DataFrame(results)
    stats_version.to_csv(OUT_DIR / "stats_by_version.csv", index=False)

    # 分组：来源 group / 周期 bar / 币种 inst_id（均基于合并去重 long 集）
    by_group = [test_block(g, f"group:{k}") for k, g in merged.groupby("group") if (g["label"] == "positive").sum() >= 5]
    pd.DataFrame(by_group).to_csv(OUT_DIR / "stats_by_group.csv", index=False)

    by_bar = [test_block(g, f"bar:{k}") for k, g in merged.groupby("bar") if (g["label"] == "positive").sum() >= 5]
    pd.DataFrame(by_bar).to_csv(OUT_DIR / "stats_by_bar.csv", index=False)

    by_symbol = [
        test_block(g, f"symbol:{k}")
        for k, g in merged.groupby("inst_id")
        if (g["label"] == "positive").sum() >= 10
    ]
    pd.DataFrame(by_symbol).to_csv(OUT_DIR / "stats_by_symbol.csv", index=False)

    # 数值特征 vs 未来收益 Spearman 相关（合并去重 long 集，全体 + 仅正样本）
    feat_rows = []
    for scope, sub in [("all_long", merged), ("pos_only", merged[merged["label"] == "positive"])]:
        for feat in ["volume_ratio", "volume_z", "ma_spread_pct"]:
            for target in ["future_favorable_pct", "future_adverse_pct"]:
                s = sub[[feat, target]].dropna()
                if len(s) >= 30:
                    rho, p = stats.spearmanr(s[feat], s[target])
                    feat_rows.append({"scope": scope, "feature": feat, "target": target, "n": len(s), "spearman_rho": rho, "p": p})
    pd.DataFrame(feat_rows).to_csv(OUT_DIR / "spearman_features.csv", index=False)

    # 分布对比图
    plot_distributions(merged, "Merged dedup (long) — positive vs negative", OUT_DIR / "p0_dist_merged_long.png")
    for name in ["v181_long_first", "v172_base", "v172_balanced"]:
        sub = long_rows[long_rows["version"] == name]
        if len(sub):
            plot_distributions(sub, f"{name} (long) — positive vs negative", OUT_DIR / f"p0_dist_{name}.png")

    # 终端摘要
    key = stats_version[stats_version["scope"].str.startswith("merged_dedup (long")].iloc[0]
    summary = {
        "merged_long_n_pos": int(key["n_pos"]),
        "merged_long_n_neg": int(key["n_neg"]),
        "fav_pos_median": float(key["fav_pos_median"]),
        "fav_neg_median": float(key["fav_neg_median"]),
        "fav_U_p": float(key["fav_U_p"]),
        "fav_rank_biserial": float(key["fav_rank_biserial"]),
        "adv_pos_median": float(key["adv_pos_median"]),
        "adv_neg_median": float(key["adv_neg_median"]),
        "adv_U_p": float(key["adv_U_p"]),
        "adv_rank_biserial": float(key["adv_rank_biserial"]),
        "pos_median_net": float(key["pos_median_net"]),
        "roundtrip_cost": ROUNDTRIP_COST,
        "covers_cost": bool(key["covers_cost"]),
    }
    (OUT_DIR / "p0_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n全部统计表与图已写入", OUT_DIR)


if __name__ == "__main__":
    main()

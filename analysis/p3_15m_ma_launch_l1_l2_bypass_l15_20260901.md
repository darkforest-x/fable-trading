# 15m 均线密集启动：移除 L1.5 后的 L1→L2 旁路验证

## 结论先行

L1.5 已从默认研究链中**物理旁路**。新入口只执行：

`冻结 L1 候选 → dependency episode 合并 → LONG/SHORT 独立 L2 收益回归`

旁路验证通过：新 LONG/SHORT 模型字节、逐事件分数、tune-q90 阈值和 34 个最终入选事件
都与旧 factorial 实验的 L2-only 臂一致。训练和评分读取的 27 个
CSV 字段中 L1.5 字段为 **0**。

这不改变经济裁决：L2-only 仍为 **REJECTED**，没有生产资格。

## 旁路证明

| 检查 | LONG | SHORT |
|---|---:|---:|
| 模型字节一致 | True | True |
| 最大逐分数误差 | 0.000e+00 | 0.000e+00 |
| 阈值绝对误差 | 0.000e+00 | 0.000e+00 |
| 入选集合一致 | True | True |

## 数据统计

| 项目 | 数值 |
|---|---:|
| 原始 L1 ledger 行数 | 3,779 |
| final 独立事件 | 242 |
| L2 q90 入选 | 34 |
| 特征数 | 17 |
| L1.5 字段读取数 | 0 |
| 数据截止 | 2026-05-03T23:45:00+00:00 |

## 经济结果与上一版本同表对照

| 配置 | 入选 n | 净均值 | 胜率 | 置换 p | 裁决 |
|---|---:|---:|---:|---:|---|
| 旧 factorial L2-only | 34 | 0.389% | 35.294% | 0.192081 | REJECT |
| 新 L1→L2 bypass | 34 | 0.389% | 35.294% | 0.192081 | REJECT |
| bypass LONG | 17 | -0.509% | 23.529% | 0.865413 | FAIL |
| bypass SHORT | 17 | 1.287% | 47.059% | 0.050395 | exploratory only |

Top-decile 毛/净收益分别为 0.597%
/ 0.397%；AUC=0.4634，
Spearman=0.0327。这些分类指标仅作诊断，生产裁决仍以
扣成本收益、置换检验和匹配随机对照为准。

## 匹配随机对照

34 个入选事件全部具有 8/8 组同币、同时间块、同波动桶、同方向对照；事件相对对照的平均
超额为 0.768%，8 组方向均为正。但主检验
`p=0.192081` 未过 0.01，且 LONG q90 净均值为 -0.509%，
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

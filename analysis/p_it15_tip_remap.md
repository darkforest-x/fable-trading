# IT-15 · tip remap（框右缘 → 局部密度谷）— 诊断有用，不可当部署边

> 日期：2026-07-24 · **未碰 holdout** · 单变量：信号 bar 定义  
> 脚本：`scripts/it15_tip_remap.py` · 产物：`analysis/output/it15_tip_remap.json`  
> 上游：`analysis/p_tip_mapping_owner_intent.md`

## 0. 裁决

**诊断通过、部署否决。**

- 相对同一 Owner L/S 样本，把信号从框右缘 **前移到局部 `fast_spread` 谷（中位 −10 bar）**，
  raw PF 全面抬升（空边三期 raw **2.16 / 2.46 / 1.74**；多边 **1.79 / 1.54 / 2.10**）。  
- **但这是在「事后已被 Owner 选中的事件」上回到谷底**——样本选择带后视，**不是**可部署的
  盘口 tip 扫描边。  
- LGBM top-decile PF 出现 10–34 量级 → **不可信**（小样本 + 选择集内拟合）。  
- **不**申请 holdout、**不** promote、**不**改 live 切点。

## 1. 假设与改动

| | |
|--|--|
| 假设 | Owner 框右缘偏晚；真 tip ≈ 邻域密度谷 / 最后密集 bar |
| A | `signal_i = cut_global`（框右缘） |
| B | `signal_i = argmin fast_spread` on `[cut-24, cut]` |
| C | `[cut-24,cut]` 内最后满足 FAST∧FULL 的 bar，否则同 B |
| 成本 | TP5/SL2 − maker；`net_dir`（IT-09） |
| 切分 | 时间序 walk-forward 三期；特征在 **signal_i**（因果） |

## 2. 结果（raw PF；top 仅披露）

offset 中位：A=0 · B=**10** · C=**8** bar（相对 cut）。

| 定义 | 边 | 期1 raw | 期2 raw | 期3 raw | 三期≥1.3? |
|------|----|---------|---------|---------|-----------|
| A_cut | long | 1.254 | 1.025 | 1.448 | 否 |
| A_cut | short | 1.472 | 1.670 | 1.292 | 否（期3） |
| B_trough | long | 1.793 | 1.542 | 2.103 | **是*** |
| B_trough | short | 2.156 | 2.464 | 1.744 | **是*** |
| C_last_dense | long | 1.748 | 1.552 | 2.248 | **是*** |
| C_last_dense | short | 2.246 | 2.729 | 1.728 | **是*** |

\*星号 = **仅在 Owner 选中样本上**；部署等价扫描未做。

## 3. 机制

1. tip 映射审计：cut 处已扩张；谷底早 ~10 bar → remap 吃到更早的压缩段。  
2. Owner 只会标「后来看起来像启动」的框 → 在这些框上任意前移，都容易抬 PF
   （与 oracle 选点 PF 1.18 同类：**事后样本上的时间机器**）。  
3. 可部署对照仍是机械 emergence tip（历史 PF@maker ~**0.87**）——全市场扫描无 Owner 挑选。

## 4. 与判断层主线的关系

- **不**重复 IT-10~13 的选边/续势/fade。  
- 贡献：支持「判断层/入场的 tip 时刻定义可能歪了」——但 **解锁实盘必须先做机械扫描基线**，
  不能拿 Owner 子集 remap 报喜。  
- 下一步候选（需 Owner 批单变量）：全市场 `trough`/`last_dense` 因果扫描 base rate
  （对照 emergence）；过线再谈判断层过滤。

## 5. 风险与诚实声明

- top_PF 夸张 = 警告灯，不是 alpha。  
- 未碰 holdout / ACTIVE / 双检测器。  
- IT-14 红灯仍禁止「像素双模」捷径。

# tip 映射审计：`box_right_frac≈0.5` 是否冤枉 Owner「框=tip」

> 日期：2026-07-24 凌晨 · **未碰 holdout** · **未 promote** · **未改 ACTIVE**  
> 脚本：`scripts/tip_mapping_owner_intent_audit.py`  
> 产物：`analysis/output/tip_mapping_owner_intent_audit.json`  
> 样本：`owner_side_review/review_sheet.csv`（L/S=2513）

## 0. 裁决（一句话）

**两件事要分开：**

1. **`box_right_frac` 中位≈0.51 不能用来否定 Owner 的 tip 意图**——它只描述框右缘在
   *存档训练图* 窗内的位置；同一 cut 若 tip 对齐重裁，右缘分数会≈1.0。  
2. **在我们的机械 tip 定义（FAST≤0.0028 ∧ FULL≤0.0055）下，Owner 框右缘 cut 多数已不是 tip**——
   dense_at_cut 仅 **1.55%**，`spread_chg8>0` **97.6%**，相对局部谷底偏晚约 **10 bar**。

→ 正确姿势：**尊重「框=tip」主张，改映射/阈值/语义对齐，而不是说 Owner 标错。**  
旧报告用「右缘在窗 50%」暗示「不是 tip」——**冤枉了指标对象**。

## 1. 复现

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=. .venv/bin/python -u scripts/tip_mapping_owner_intent_audit.py
```

Holdout（≥2026-05-04）行跳过；本跑 skips.holdout=0（sheet 已是 pre-holdout 为主）。

## 2. A · 图像几何（不涉及行情）

| 量 | 值 |
|----|-----|
| n | 2513 |
| `box_right_frac` 中位 | **0.5075**（p25 0.29 / p75 0.71） |
| ≥0.9 / ≥0.8 | 5.0% / 13.1% |
| `width_bars` 中位 | 12 |
| `yolo_xc` 中位 | 0.48 |

定义：`box_right_frac = (b1+0.5)/WINDOW`，`cut_global = win_start + b1`。  
窗来自历史金标 PNG 的 MAD 对齐，**从未强制 tip 贴右**。中段是裁图习惯，不是意图判决。

## 3. B/C · cut 处行情（机械 tip 探针）

| 量 | 值 |
|----|-----|
| dense_at_cut | **0.0155** |
| dense_in_prior_8 | **0.3227** |
| `spread_chg8>0` | **0.9761** |
| trough_offset 中位（cut − 局部 fast 谷） | **10 bar** |
| bars_to_expand 中位 | **0**（多数 cut 当下已过扩张阈） |
| fast / full 中位 | 0.0106 / 0.0138（远高于 FAST/FULL 门） |

分边：long/short dense_at_cut 皆 ≈1.4–1.7%；中段图像且 dense 仍 ≈1.7%。

## 4. 解读

| 说法 | 对错 |
|------|------|
| 「右缘 50% ⇒ Owner 标的不是 tip」 | **错**（混淆图像坐标与行情 tip） |
| 「Owner 框右缘 cut ≈ 我们的机械 tip」 | **不成立**（1.6% dense、98% 已散开） |
| 「Owner 的 tip 眼 ≈ FAST/FULL 谷底」 | **更接近**：谷底通常早 cut ~10 bar；prior-8 内曾密集 32% |

与 `p_owner_label_feature_verdict`（v11 池 dense≈50%、确认态特征）**同方向**：手感偏启动后选点；  
本审计在 **side-review 池**上更极端（dense 更低），可能因 L/S 任务偏好「方向已可读」的框。

## 5. 对实盘 / 判断层的含义

- 检测金标若继续用 **框右缘当 tip 监督**，分布偏 **早确认 / 扩张初段**，会复制
  「事后训练 → tip 反预测」（holdout#6 教训）。  
- 若坚持 Owner「框=tip」产品语义，候选 remap：**信号 bar = 框邻域内 fast 谷 / 最后密集 bar**
  （见 IT-15；**注意选择偏差**）。  
- **不**因此自动改 live 几何门 / ACTIVE / 阈值——属 Owner 产品决策。

## 6. 风险与诚实声明

- FAST/FULL 是项目旧 emergence 阈值，不是 Owner 眼的金标准。  
- sheet 内 `fast_spread`/`spread_chg8` 列为空，本审计现场重算。  
- 未读 holdout、未改管道默认。

## 7. 下一步（需 Owner 醒来）

1. 是否批准：**金标/入场 cut 默认改「邻域谷底 / 最后密集」**（单变量，先 train 因果，不烧 #8）？  
2. 或：保持右缘 cut，但产品语义改称「启动确认」而非 tip（与纪律 12 张力大）？  
3. tip 映射结论 **不**复活像素双检测器（IT-14 红灯仍在）。

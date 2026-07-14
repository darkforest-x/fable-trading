# YOLO 上关键路径：并行 A/B 设计（owner 决策 2026-07-15）

## 决策
owner 要求 v6 训练完后让 YOLO 检测进入关键路径。方式（owner 选定）：
**并行 PK，赢了才替换**——不动现有验证，让 YOLO 凭数据赢得资格。

## 不可逆风险（为什么不能直接替换）
现有全部验证（深历史 PF 1.74、前向时钟、holdout）建立在"规则扫描出候选"上。
直接换 YOLO 当候选源 → 候选集变 → 所有验证作废、前向时钟清零。
且 YOLO 对 owner 口味 F1 仅 0.66，规则按定义 100% 精确，替换大概率降级。

## 机制
- `scripts/yolo_candidate_source.py`：滑窗渲染每个 SWAP 序列 → owner_best(v6) 检测
  → 框右缘像素反解为信号 bar（ChartTransform.x_at 的逆）→ 去重(≥18根) →
  喂入**完全相同的** labeling/features/train，产出与规则路径同 schema 的数据集。
- `scripts/ab_yolo_vs_rules.sh`：两条候选源各跑同一 train.py（同切分纪律），同表对比。

## 判定（发现级，val only，不碰 holdout）
- **YOLO 赢** = top-decile 净@成本 ≥ 规则路径 且 p<0.01
  → 冻结 YOLO 候选配置，为它启动独立前向时钟确认，通过才切主线；
- **YOLO 输** = 确认规则扫描更强，YOLO 回侦察岗。路径A全程未动，零损失。

## 执行时机
v6 训练+晋升完成后自动触发（queue17）。GPU 活（渲染+检测），排在 v6 之后不抢占。

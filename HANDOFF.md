# HANDOFF — 给下一个会话/模型的执行路线图

**写于 2026-07-08。** 读完本文件 + `CLAUDE.md` + `analysis/p2b_v2_report.md`，即可无损接手本项目。

## 当前状态一句话

**07-09 追加：合约复制性检验通过**（TP5/SL2 纯 SWAP 池 AUC 0.560/p=0.001，top-decile 净@maker +0.225%/笔，判定标准全过）——**主线宇宙即日起为 SWAP**；H1 分批止盈为 v3 出场头号挑战者（AUC 0.608/胜率 65%）。前向验证与后续实验一律用合约配置。

**2b 验收通过（holdout 已消耗）→ 阶段 3 第一轮未通过（PF 1.01@0.3%）→
owner 已委托"按推荐直接执行" → 出场结构扫描完成：TP5/SL2 为 v3 候选标签**
（val 净@0.3% +0.077%/笔 vs 基线 +0.001%，p=0.001，见 `analysis/p2b_v3_barrier_sweep.md`）。
进行中：① purge 参数化解锁 horizon 扫描；② maker 成本模型；③ 看板已部署本地+VPS。
纪律红线：holdout 与验收窗口均已消耗，v3 的确认性验证只能用前向新数据；
val 已被多次选型使用，其数字只用于排序不用于宣称绩效。

## 排序后的下一步（期望价值从高到低）

### ~~1. purged CV / embargo 泄漏修正~~（作废，2026-07-08 核实已实现）

原以为 train/val 边界存在标签窗口泄漏——**读代码核实后确认 purge 已在
`src/judgment/train.py` 实现**（`PURGE_WINDOW = 18.25h` = 73 根 outcome 窗口，
dev/holdout 与 train/val 两个边界均清除；与 `labeling.py` 的 entry=i+1、
HORIZON_BARS=72 精确对应）。v2 报告中的全部指标本来就是泄漏修正后的数字。
教训见 `docs/learnings/grep-before-planning-fixes.md`。

### ~~2. holdout 一次性评估~~（已完成，2026-07-08，owner 批准）

结果：AUC 0.602 / p=0.001 / top-decile 净 +0.083% —— **2b 验收通过**，明细在
`analysis/p2b_v2_report.md` 6.5 节。expanded × v2 的 holdout 已消耗，任何后续
迭代不得再评估 holdout（除非 owner 批准并注明"第 N 次消耗"）。

### 原第 2 步存档（执行方式备查）

- **为什么**：这是 2b 的正式验收。v1 已消耗过一次 holdout，v2 每个配置只许评一次。
- **怎么做**：
  `python3 -m src.judgment.train --data data/judgment_dataset_v2_expanded.csv --tag p2b_v2_expanded_final --eval-holdout`。
- **完成的样子**：holdout AUC / p / top-decile 净收益写入报告，明确判定
  "2b 验收通过/未通过"。通过 → 阶段 3；未通过 → 回 val 迭代，holdout 不许再碰。

### 3. 阶段 3：简单事件驱动回测（当前工作，2b 已验收）

- 按 `PROJECT_PLAN.md` 阶段 3 规范：自写 ~200 行事件驱动回测，taker 费 + 滑点 +
  资金费近似；检测（规则扫描）→ 判断（LightGBM 分数）→ 持仓 → 平仓全链路；
- 资金费率历史可用 CCXT 拉（唯一批准引入的新依赖，仅数据用途）；
- 验收标准在 PROJECT_PLAN 里，别改。Freqtrade 只作为回测结果的交叉验证，不做主框架。

### 4. 2a 全量训练与正式验收（非关键路径，可与 1-3 并行或推迟）

- 新数据已就位，按 `analysis/p2a_detection_report.md` 末节"全量训练建议"执行；
- 目标：mAP50 ≥ 0.90 + 与规则扫描一致率 ≥ 95%。

## 停止做的三件事（含理由）

1. **停止给 strict 池单独调参**——2 898 个样本不够 LightGBM 学出超过单特征基线的
   结构（v2 实测模型 0.543 vs 基线 0.556）。扩池已验证成立，主线就是 expanded。
2. **停止在旧缓存数据上跑新实验**——新拉取的 400 天数据在时间覆盖上全面优于旧缓存
   （旧缓存仍参与 loader 合并，但不要再针对旧数据的特性做任何决策）。
3. **停止评估新框架**——2026-07-07 已做过完整评估（见会话记录/README）：
   阶段 3 自写回测，CCXT 仅拉数据，其余一概不引入。

## 未决队列（2026-07-08 深夜快照，两个后台任务当时仍在跑）

1. **YOLO 全量训练**（本机进程，产物 `runs/detect/runs/detect/dense_15m_full/`）：
   接手时先看 `results.csv` 是否已停止更新。完成后：
   `.venv/bin/python -m src.detection.eval_visualize --weights runs/detect/runs/detect/dense_15m_full/weights/best.pt`
   出官方指标；mAP50 ≥0.90 → 写一致率脚本做正式验收；<0.90（当时最佳 0.862@41ep）
   → 用 yolo11s 重训一轮（`--model yolo11s.pt`，其余参数同 args.yaml）。
2. **合约数据**（okx_*_USDT_SWAP_15m_*.csv 落在 data/kline_fetched/）：
   拉完后跑冻结流水线复制性检验——expanded 池 + TP5/SL2 标签在 SWAP 序列上
   build+train（val only），合约成本：maker 0.02%/taker 0.05% + 资金费近似 0.01%/8h。
   owner 已确认实盘目标是合约。
3. **均线定义已裁决（2026-07-09）**：P0-3 已在合约数据上正面对比
   SMA/EMA 20/60/120 与现行 EMA 8/13/21/34/55+144/200。20/60/120 的 AUC 更高
   但 top-decile 净收益显著弱于 8-55；owner 已拍板 **主线继续 8-55**。
4. 前向验证窗口从 2026-07-08 起积累（每日 8 点自动更新数据），~3-4 周后用冻结的
   TP5/SL2+maker 配置做最终 PF 裁决。

## 明天开工的第一条消息（可直接粘贴）

> 读 CLAUDE.md、HANDOFF.md、analysis/p2b_v2_report.md。2b 已验收通过，
> 当前工作是 HANDOFF 第 3 步：阶段 3 事件驱动回测框架。按 PROJECT_PLAN 阶段 3
> 规范自写实现（taker 费 + 滑点 + 资金费近似），先给出模块划分和成本模型设计
> 让我确认，再写代码。阶段 3 的冻结 holdout 方案也需要先和我讨论
> （2b 的 holdout 窗口已消耗，回测的样本外窗口如何定义是一个待决策问题）。

## 本仓库的知识地图

| 想知道什么 | 看哪里 |
|---|---|
| 为什么做这个项目、旧项目怎么死的 | `README.md` |
| 三阶段路线图与验收标准 | `PROJECT_PLAN.md` |
| 人工标签有没有 alpha（P0） | `analysis/p0_alpha_report.md` |
| YOLO 检测层怎么训、效果如何 | `analysis/p2a_detection_report.md` |
| 判断层 v1 为什么"有信号没利润" | `analysis/p2b_judgment_report.md` |
| v2 双池实验结果与下一步选项 | `analysis/p2b_v2_report.md` |
| 踩坑记录（原子化笔记） | `docs/learnings/` |
| 工作纪律与质量标准 | `CLAUDE.md` |

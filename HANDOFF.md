# HANDOFF — 给下一个会话/模型的执行路线图

**写于 2026-07-08。** 读完本文件 + `CLAUDE.md` + `analysis/p2b_v2_report.md`，即可无损接手本项目。

## 当前状态一句话

**expanded × v2 配置在验证集上通过了 2b 全部验收标准**（置换 p=0.001，top-decile
扣成本净收益 +0.101%）；holdout 一次未碰；数据已补齐（56 币种 × 400 天 OKX 15m）。
2b-v1"有信号没利润"的问题被 v2 宽障碍修复，方向验证成立。

## 排序后的下一步（期望价值从高到低）

### 1. purged CV / embargo 泄漏修正（单变量改进，先做这个）

- **为什么**：triple-barrier 标签有 72 根 15m 的前向窗口，train/val 时间边界附近的
  训练样本标签窗口伸进了 val 区间，存在轻微泄漏。修正后指标更保守、更可信——
  在把最后一发 holdout 子弹打出去之前，必须确认弹道没有虚高。
- **怎么做**：只改 `src/judgment/train.py` 的切分逻辑——每个序列丢弃标签窗口
  与 val 起点重叠的训练样本（embargo = 标签 horizon = 72 根）。不动 build_dataset、
  不动特征、不动模型参数。对两个池分别重跑，tag 用 `p2b_v2_strict_purged` /
  `p2b_v2_expanded_purged`，不加 `--eval-holdout`。
- **完成的样子**：新旧四组指标同表对照追加进 `analysis/p2b_v2_report.md`。
  若 expanded 仍满足 p<0.01 且 top-decile 净收益>0 → 进入第 2 步；
  若指标塌了 → 如实记录，回到特征迭代，**不要**为了指标好看撤销 embargo。
- **要告诉执行模型的话**：embargo 是按"每序列的 bar 序"算的，不是按全局时间戳直接减
  ——不同币种的 bar 是对齐的 15m 网格，但要防止序列内索引错位；写完先在一个小池上
  冒烟验证样本数变化是否合理（应减少约 val 边界附近的几十个样本，不应减半）。

### 2. holdout 一次性评估（需项目所有者在对话中明确批准后才能执行）

- **为什么**：这是 2b 的正式验收。v1 已消耗过一次 holdout，v2 每个配置只许评一次。
- **怎么做**：仅对通过第 1 步的配置跑
  `python3 -m src.judgment.train --data data/judgment_dataset_v2_expanded.csv --tag p2b_v2_expanded_final --eval-holdout`。
- **完成的样子**：holdout AUC / p / top-decile 净收益写入报告，明确判定
  "2b 验收通过/未通过"。通过 → 阶段 3；未通过 → 回 val 迭代，holdout 不许再碰。

### 3. 阶段 3：简单事件驱动回测（2b 验收通过后）

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

## 明天开工的第一条消息（可直接粘贴）

> 读 CLAUDE.md、HANDOFF.md、analysis/p2b_v2_report.md，然后执行 HANDOFF 第 1 步
> （purged CV/embargo），完成后汇报新旧指标对照，不要碰 holdout。

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

# A2 实施计划：隔离 maker 试错桶（VPS 小仓验证）

**日期**：2026-07-30  
**背景**：选项 A 离线模拟完成（回归 net 选 top + maker 成本），CPCV 中位 +19.4bp（15/15 正），15% 未成交情景仍 +16.5bp。按用户选择，下一动作是 **A2**：在 VPS 上用隔离的 maker 试错桶小仓验证，不改动主 forward_log / executor 主路径。

**铁律红线（本次严格遵守）**
- 仅读主线 YOLO + 判断模型；**不写** 主 `forward_log.csv`。
- 试错桶使用**独立文件** `data/forward_log_maker_trial.csv`（类比 `forward_log_h1_scaled.csv`）。
- **不** promote、不切 ACTIVE、不清 forward_log、不改三门（max_signal_age_min / TG / 看板）。
- 真金操作（下单/改仓/改 API）仅在 owner 逐次明确授权下进行。
- 成本/障碍假设不变；本轮只改「入场方式 + 止盈方式」为 maker + 限价 TP。
- 全部实验在 pre-holdout 逻辑与模型上验证，VPS 验证使用**已验证的模型版本**。

---

## 1. 目标与成功标准

- **目标**：在真实盘口下，确认「回归 top 子集 + maker 入场 + 限价 TP」是否能在 15% 未成交假设附近把净收益做到 +10bp 以上（含滑点、部分成交、资金费）。
- **成功标准**（owner 事前定义，建议）：
  - 连续 ≥50 笔试错样本，或累计 ≥7 天。
  - maker 入场真实成交率 ≥70%（否则边被未成交吃掉）。
  - 15% 未成交情景下的中位净 ≥ +8~10bp（保守）。
  - 无连续 5 笔大亏（executor 已有 max_consecutive_losses 保护）。
- **失败即止**：成交率过低、滑点远超 4bp、或连续回撤超阈值，立即停止该桶。

---

## 2. 隔离架构（最小改动）

```
主路径（不变）
  YOLO detect → judgment frozen → forward_log.csv → executor（market 入场 + OCO -1）

隔离试错桶（新增）
  同一 YOLO + 同一判断回归模型（或其 q90 阈值）
        ↓ 仅对 top 10% 写
  data/forward_log_maker_trial.csv   （独立文件，带 trial 标记）
        ↓ 独立进程/脚本读
  maker_trial_executor（或带开关的 executor）
        ↓ 仅该桶
  限价入场（resting sell）+ 限价 TP；SL 仍 taker（必须）
        ↓ 小仓
  notional_usdt 极低（建议 5~10U/笔，或 equity 的极小比例）
```

**文件新增（不破坏主路径）**
- `src/judgment/forward_types.py`：增加 `FORWARD_LOG_MAKER_TRIAL_PATH` 常量（additive）。**已落地。**
- `src/judgment/forward.py`：`run_forward_tracking_maker_trial`（类比 H1 shadow；拒绝写主 log / H1 log）。**已落地。**
- `scripts/forward_maker_trial.py`：独立脉冲入口；`FABLE_MAKER_TRIAL=1` + kill 文件；写 trial 文件并戳 `trial_bucket=maker_entry`。**已落地（ledger only，不真下单）。**
- 可选：`src/execution/maker_trial_loop.py`（极简），或复用 executor 逻辑但读 trial 文件 + 走 maker 下单路径。**未做；需 owner 逐次授权真金。**

**关键列复用**
- 复用现有 forward 列；新增 `trial_bucket` 列（值为 `"maker_entry"`）便于下游区分。
- `maker_filled` 仍保留（记录是否以 maker 成交）。

---

## 3. 运行命令（VPS 侧）

```bash
# 0) 前置：确认已在 main，确认模型版本（owner 批准的回归模型或当前 frozen 的等价物）
git branch --show-current   # 必须输出 main
git status --porcelain      # 干净或仅新增文件

# 1) 启动隔离的 maker 试错 forward 脉冲（写独立文件）
# 建议用 systemd/launchd 模板，日志落 logs/forward_maker_trial.log
FABLE_MAKER_TRIAL=1 \
FABLE_CANDIDATE_SOURCE=yolo \
python3 scripts/forward_maker_trial.py \
  --model artifacts/.../your_reg_model.txt \
  --threshold-q 0.90 \
  --out data/forward_log_maker_trial.csv

# 2) 启动隔离的 maker 试错执行器（小仓、独立进程）
# 选项 A：最小实现（推荐先手写一个 50 行循环）
python3 -m src.execution.maker_trial_loop \
  --forward-log data/forward_log_maker_trial.csv \
  --notional-usdt 8 \
  --max-concurrent 1 \
  --maker-entry-offset-bp 2 \     # resting sell 偏离量，实测后调
  --limit-tp \                     # 限价止盈
  --kill data/executor_KILL_MAKER_TRIAL

# 选项 B：若复用 executor，新增开关（owner 批准后小补丁）
#   EXECUTOR_READ_LOG=data/forward_log_maker_trial.csv \
#   EXECUTOR_MAKER_ENTRY=1 \
#   EXECUTOR_NOTIONAL_USDT=8 \
#   python3 -m src.execution.executor --trial
```

**安全开关**
- 独立 kill 文件：`data/executor_KILL_MAKER_TRIAL`（存在即停该桶）。
- 独立最大并发与 notional。
- 建议在 TG 增加「MAKER_TRIAL」前缀，便于过滤。

---

## 4. 模型与阈值

- 离线验证用的是「回归 net_barrier_taker → 取 val 内 q90」选 tops。
- 实盘需要一个**已冻结的回归模型**（或当前 frozen 的等价物）。
- 若当前 frozen 是二分类（v11_reg 可能是分类/回归混用），需先把回归模型冻结并记录 SHA256。
- 阈值：建议先用训练窗 90% 分位固定值，或 val 窗固定值；**不**在 forward 期间动态重算。

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| maker 入场成交率过低（<50%） | 实测 offset（0~5bp），未成交按 0 计；超过 3 天仍低则停 |
| 滑点/排队吃掉 4bp 节省 | 记录每笔实际成交价 vs 期望；超阈值停 |
| 试错桶误触主路径 | 严格分离文件 + 独立进程 + 独立 kill |
| 连续亏 | executor 已有 max_consecutive_losses；试错桶可设更严（3~4） |
| 资金费/持仓时间 | 仍用 72bar 超时；可提前 trail 离场 |
| 代码侵入主路径 | 本计划只新增文件与独立脚本；主 executor/forward 不改或仅加极小开关（owner 逐行审批） |

---

## 6. 度量与看板（建议）

- 每脉冲落地 `logs/maker_trial_*.jsonl`（每笔：score、offset、filled、realized_maker、cost_proxy）。
- 每日汇总：成交率、平均 maker 节省 bp、15% 未成交情景下的中位净、胜率、最大回撤。
- 与主 forward_log 隔离统计，避免污染 100 笔 gate。

---

## 7. 回滚与停止条件

- 任意时刻 `touch data/executor_KILL_MAKER_TRIAL` 立即停止试错桶下单。
- 删除/重命名 trial forward 文件不影响主路径。
- 若 owner 决定放弃，删除 trial 相关脚本与常量即可（git revert 1~2 文件）。

---

## 8. 交付物（本次已完成）

- `analysis/p_judgment_maker_cost_on_regtop.md` + HTML（选项 A 结果）。
- `analysis/p_judgment_maker_trial_a2_plan.md` + HTML（本计划）。
- `docs/learnings/regression-on-net-plus-maker-route-concentrates-alpha-better-than-binary-labels.md`（已追加）。

**待 owner 批准后才可上 VPS 的最小补丁**
1. `src/judgment/forward_types.py` 加 `FORWARD_LOG_MAKER_TRIAL_PATH`。
2. `scripts/forward_maker_trial.py`（独立 writer）。
3. 可选的 `src/execution/maker_trial_loop.py` 或对 executor 的极小开关（需逐行 review）。

---

## 9. 下一步（需 owner 明确回复）

- [ ] 批准 A2 隔离方案（是/否 + 备注）。
- [ ] 指定/冻结用于试错的回归模型路径与阈值（或确认用当前 frozen 等价物）。
- [ ] 指定试错 notional（建议 5~10U/笔）与最大并发（1）。
- [ ] 指定 maker 入场 offset 初始值（建议 0~3bp 开始实测）。
- [ ] 确认 VPS 上是否需要同步本计划中的新增文件（通过 `git pull` 或 patch）。
- [ ] 确认是否需要为 trial 桶单独开 TG 频道或 tag 过滤。

**铁律确认**：本次操作不消耗 holdout、不改主 forward、不 promote、不自动切换配置。所有真金操作需 owner 逐次授权。

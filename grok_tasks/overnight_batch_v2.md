# Grok 过夜批次 v2（Claude 额度见底 · 2026-07-20）

**背景**：上一轮 task01–10 已完成（见 `grok_tasks/RESULTS.md`）。  
本批目标：**修前向可观测性 → 只推进最高价值挑战者 → 不做主线手术**。

## 铁律（违反=作废，你无权修改）

- **禁**改 `models/ACTIVE`、禁改任何 `models/frozen_tp5_sl2_swap_20260709.*` 内容  
- **禁**评估 holdout（`train.py` 不加 `--eval-holdout`）  
- **禁**改默认成本假设 0.2% / maker 口径约定；报告里沿用既有 net@maker0.06% 对照  
- **禁**改候选阈值预设（strict/expanded）与障碍主线 TP5/SL2  
- **主裁决文件** `data/forward_log.csv`：  
  - **默认禁止 truncate / 改 schema**  
  - 允许 **幂等 append** 仅当任务规格明确写「允许回填」且输出先写旁路文件时  
- 所有发现级实验：**train/val only**；负结果照样写报告  
- 每任务独立 commit + push `grok/overnight`（不合 main）  
- judgment/因子/回测：系统 `python3`；YOLO：`.venv/bin/python`  
- 时间戳：`pd.Timedelta`，禁 `astype(int64)//1e9`  
- 新脚本 `scripts/`，报告 `analysis/`，命名带假设号  

## 任务序（卡住就跳过，RESULTS 记原因）

| # | 任务 | 期望价值 | 报告 |
|---|---|---|---|
| 11 | 主线前向健康诊断 + **旁路回放**（不覆盖裁决文件） | 最高：恢复可观测 | `analysis/p_fwd_health_20260720.md` |
| 12 | 前向 crypto-only 闸门（stockish / BLOCKED 统一） | 堵住样本污染 | 同上报告 §闸门 + 测试 |
| 13 | H3 MA-exit **shadow** 前向 resolver（第二账本） | 次强出场挑战者 | `analysis/p15_h3_forward_shadow.md` |
| 14 | H16 放量突破入场（发现级 val） | 量价族首做 | `analysis/p15_h16_vol_breakout_entry.md` |
| 15 | H1 shadow **crypto-only 续记**诊断与修复 | 最强出场挑战者可累积 | `analysis/p15_h1_shadow_health.md` |

**明确不做**：H4 重跑、成交量 IC 重筛、改 ma206 主线、YOLO 冲 mAP、holdout、降 q90 加速。

## 交付

- 每任务：代码/脚本 + 报告 + commit  
- 全部结束后更新 `grok_tasks/RESULTS_v2.md`（每任务：做了什么 / 结论 / 异常）  
- 仍 **未改主线** 一句写在 RESULTS 末尾  

单任务细则：`grok_tasks/tasks/task11.md` … `task15.md`。

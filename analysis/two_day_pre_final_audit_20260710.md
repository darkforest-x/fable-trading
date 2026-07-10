# 两日任务预终审（2026-07-10）

## 结论

Todo 1-6 与 Todo 9 已有可复核的实现、测试或实机证据。Todo 7 必须等待当前
E2.1b 自然结束，Todo 8 被 Todo 7 阻塞，最终 Todo 10 与 F3 因此尚不能关闭。
本轮没有读取 holdout、修改策略参数、触碰训练进程或开启交易执行器。

## 任务对账

| 任务 | 状态 | 主要证据 |
|---|---|---|
| 1 HSV 全零合规 | 完成 | `src/detection/train.py`、回归测试、运行参数证据 |
| 2 当前系统健康基线 | 完成 | `.omo/evidence/task-2-system-health.txt` |
| 3 pre-holdout 稳定性 | 完成 | `analysis/strategy_stability_preholdout.md` |
| 4 champion/challenger 前向 | 完成 | `.omo/evidence/task-4-shadow-forward.txt` |
| 4A Label Studio 公网审查 | 完成 | `693dc5f`、`.omo/evidence/task-4a-phase-c-browser-qa.md` |
| 4B 开源架构基准与试点 | 完成 | `a7912e1`、`analysis/oss_architecture_benchmark.md` |
| 5 P2.5 本地验收 | 完成 | `7c0839d`、`analysis/p25_local_acceptance_20260710.md` |
| 6 VPS 只读流水线 | 完成 | `3033c99`、`analysis/p25_vps_acceptance_20260710.md` |
| 7 E2.1b 正式评估 | 阻塞 | PID 37441 仍在运行；29 个 epoch，无异常终止标记 |
| 8 固定 SAHI 基准 | 阻塞 | 按预注册顺序必须等待 Todo 7 |
| 9 每日安全链 | 完成 | `3c51c1c`、`analysis/p25_daily_workflow_acceptance_20260710.md` |
| 10 最终报告 | 待办 | 等 Todo 7/8 与 F3 实机终验 |

Label Studio 的 80 图人工审查入口已经可用；把人工修改写回训练集仍需 owner 实际完成
标注后才能执行，这不是部署验收失败。

## F1 计划与实验纪律预审

- 当前训练进程只读观察，未启动、停止、复制或 finalize；E2.1b 暂存最佳为 epoch 25、
  mAP50 `0.80954`、mAP50-95 `0.59198`，仅为训练中快照。
- judgment holdout 本轮未读取。迁移 QA 曾由旧 dashboard 意外读取一次的事件已在状态文件
  如实记录并隔离；不能把该结果用于选择模型。
- 未从短前向样本调阈值或宣传未来收益。当前主线仅 1 笔已裁决，不具统计意义。
- SAHI 参数仍固定为 `640x371`、重叠 `0.2`，未提前试跑或调参。

## F2 代码与安全预审

| 检查 | 结果 |
|---|---|
| 分支 | `codex/grok-2day`，本轮提交后相对共同基线 37 个提交、150 个文件 |
| 全仓测试 | `173 passed` |
| 依赖清单漂移 | 无 requirements / pyproject / lock 文件变更 |
| 已跟踪文件密钥扫描 | 未发现 Telegram token、私钥或字面量 ops token |
| 公开 API | HTTP 200；无 `/Users/` 路径；敏感字段统一为 `[redacted]` |
| VPS 服务 | dashboard、Label Studio、nginx 均 active |
| VPS executor | `ENABLE_JOB_EXECUTOR=0` |
| diff 空白检查 | 通过（本轮清理历史 evidence 行尾空白后） |

公开 API 中的 `token_configured` 与 `OPS_API_TOKEN_configured` 字段会因键名命中脱敏规则
显示为 `[redacted]`。实机比对确认它们与 root 环境中的真实 64 位 token 不相等；这是
保守脱敏，不是凭据泄露。

## F4 范围预审

- owner 在 2026-07-10 明确要求全链迁移为 SMA/EMA 20/60/120，因此 `60f405b` 对 ACTIVE
  的 MA206 迁移是原计划“不得改 ACTIVE”的明确后续授权例外；当前指针 SHA-256 为
  `42df83c98247188873613eec3af04ffd258520a98e8b4b089c5f322b9db8b9c7`。
- 迁移后没有再次变更 ACTIVE，也没有修改成本、TP/SL、阈值或 blacklist。
- 未创建重复训练，未 push/merge `main`，未启用实盘或 VPS job executor。

## 尚未通过

- F3 真实终验必须等待 E2.1b 自然结束，并完成正式 val/一致性/难例与固定 SAHI 基准。
- Todo 10 只能在上述证据齐全后更新最终 HANDOFF/PROJECT_STATUS/NEXT_STEPS；当前系统仍是
  研究与只读前向阶段，不能声称已证明未来正收益。

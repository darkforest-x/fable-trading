# MA206 每日安全链验收（2026-07-10）

## 结论

`update_okx → champion/H1 forward → digest dry-run → pipeline → VPS` 已用当前 MA206 数据完整跑通，并修复三处会破坏无人值守可信度的问题。Codex 每日自动化已更新为这条安全链；旧 Claude Telegram 任务已明确停用。

## 真实运行结果

| 阶段 | 结果 |
|---|---|
| OKX 15m 增量 | 456 个文件，新增 39,141 根，0 API error / traceback |
| 主线首轮 | 358 SWAP，21,302 candidates，2 signals，1 closed + 1 open |
| 主线复跑 | `new_signals=0`、`closed_updates=0`、total=2 |
| H1 首轮 | 同入场新增 2，1 closed + 1 open，不 promote ACTIVE |
| champion/H1 第二轮 | 两本账均 `new_signals=0`、`closed_updates=0` |
| digest | `telegram_send: SKIPPED`；仅 `forward_low_sample` 信息性告警 |
| 本地 pipeline ×2 | 去除 `generated_at` 后 payload 完全相同，SHA `d3571390...` |
| VPS pipeline | total=2、closed=1、decision=1/100、executor off |
| VPS 数据镜像 | 456 物理文件 / 456 series / 0 duplicate versions |

账本第二轮稳定 SHA：

```text
ACTIVE  42df83c98247188873613eec3af04ffd258520a98e8b4b089c5f322b9db8b9c7
main    c903d37798d374bef59404adcc18c92e3024ac77ab348b1435bb760e19198527
H1      02ecccec22dceca0dd324460e6a9baa6e73997aabd22783784502a870a87af36
```

当前只有 1 笔已裁决：主线账面约 `+0.89%`，H1 约 `+0.13%`。这只是工程验收样本，不能推导未来收益或策略优劣。

## 修复的问题

1. `multi_day_pulse.sh` 与 `daily_digest.py` 仍读取旧 E2.1 路径，导致当前 E2.1b epoch/最佳值不可见。现在支持显式结果路径或 sibling-run 自动发现 `dense_15m_full_s_e21b_hsv0`，正式报告目标也已统一；真实 dry-run 已显示 E2.1b 29 epochs、最佳 mAP50 0.810、训练中。
2. pandas 默认 CSV 浮点解析让新记录首次读回后少一位精度，出现“0 新信号但文件 SHA 改变”。`read_csv(float_precision="round_trip")` 后读写字节稳定，并有回归测试。
3. `deploy_vps.sh` 未删除按行数重命名后的远端旧 CSV，VPS 曾累积 1,133 文件 / 456 series。改为两个数据目录分别 `rsync --delete` 后恢复为一序列一文件。

## 自动化状态

- Codex automation `fable`：active，每日 08:10 执行安全全链。
- 自动链使用 `forward_track_shadows.py` 一次完成 champion 与 H1，避免重复主线扫描。
- digest 固定 `--dry-run`，不读取或发送 Telegram 凭据。
- VPS 部署脚本每次重申 `ENABLE_JOB_EXECUTOR=0`。
- 旧 `~/.claude/scheduled-tasks/daily-okx-data-update` 已标记停用，避免多执行者重复写账或发送 Telegram。

## 验证命令

```bash
python3 -m src.data.update_okx
PYTHONPATH=. python3 scripts/forward_track_shadows.py
PYTHONPATH=. python3 scripts/daily_digest.py --dry-run
bash scripts/multi_day_pulse.sh
bash scripts/deploy_vps.sh
```

专项测试覆盖 pulse 路径、forward 字节幂等、shadow 安全边界和 VPS 镜像契约。
全仓门禁结果：`173 passed`。

## 风险与诚实声明

- forward 仅 1/100 已裁决，远未达到 100 笔终审线。
- H8/H10 仍因没有正确冻结模型而明确 unsupported，没有用主线模型近似。
- 自动化会写 `data/` 并部署只读看板，但不会改代码、ACTIVE、阈值或下单。
- 本轮未读取 holdout，未改成本、TP/SL、阈值、ACTIVE，未发送 Telegram，未启动实盘。

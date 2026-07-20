# 前向事后检出日结 — 2026-07-19

## 现象

v11 主线前向日志 10 行全部 `closed`，多数 `tp` 且 net 为正，但：

- TG 无通知  
- executor `opened: 0` 整天  
- 裁决样本 **0/100**（`fresh_detect_min=55`）

## 铁证（VPS `data/forward_log.csv`）

| symbol | signal BJ | detected BJ | lag | outcome |
|-------|-----------|-------------|-----|---------|
| OL | 00:45 | 02:02 | 78m | timeout |
| GPS | 04:15 | 17:02 | 768m | timeout |
| YB | 05:00 | 13:02 | 482m | tp |
| KAITO ×4 | 11:00–12:00 | 13–15 | 92–258m | all tp |
| OPG | 12:00 | 16:47 | 287m | tp |
| EDEN ×2 | 12:30/12:45 | 21:32/22:17 | 527–587m | all tp |

- **fresh lag≤55m: 0**  
- **hindsight >55m: 10**  
- KAITO 同币间隔 1–2 根 bar（策略要求 ~18 根）

## 机制

1. Live YOLO 只扫 tip 附近 ≤6 个窗（`mode=live`）。  
2. 信号 bar 刚形成时，启动后文尚未印在图上 → 往往不检出。  
3. 数小时后 tip 前移，同一 `signal_i` 出现在窗中部且启动已可见 → 检出。  
4. `detected_at=now`，`signal_time`=当时 bar → **巨大 lag**。  
5. 障碍回放仍按 `signal_time+1` 入场 → 日志显示「止盈」= **事后视角**，非盘口可交易。

执行器 / TG 按 `max_signal_age_min=55` 拒绝：**正确**。

## EDEN 专查结论（管道 vs 模型）— 已实测

脉冲在 04:30–05:30 UTC（信号附近）**正常踩点**（`fable-forward.timer` 每 15m）。  
`candidates_seen` 全市场在变，不是整轮空扫。

EDEN 行 `detected_at` 约 13:32 / 14:17 UTC（信号后 9h 量级）。

VPS 复现（`scripts/diag_forward_detect_lag.py`，`owner_best` conf=0.30，max_lag_bars=40）：

| signal_time UTC | log_lag_min | first_live_hit lag_bars | tip_fire |
|-----------------|-------------|-------------------------|----------|
| 04:30 | 587.5 | **None within 40 bars** | **False** |
| 04:45 | 527.5 | **39 bars (~585 min)** | **False** |

→ **不是「管道丢了一条当时就够线的信号」**：在 live 窗逻辑下 tip 附近**根本扫不出**该 `signal_i`；要等 tip 前移约 40 根（~10h）才第一次命中，与日志延迟同量级。

第二层嫌疑（相位 / k 线未齐 / predict 吞异常）**优先级下降**。主因是 **检测器 tip 不可检**。

```bash
PYTHONPATH=. .venv/bin/python scripts/diag_forward_detect_lag.py \
  --symbol EDEN_USDT_SWAP --from-log --max-lag-bars 80
```

产物：`analysis/output/diag_detect_lag_eden.json`。

## 已上线闸门保护

- `forward_payloads.FRESH_DETECT_MIN = 55`：裁决只计新鲜检出  
- `forward.py` 同币 `MIN_GAP_BARS`：跨 pulse 去重  
- 看板：延迟列 + 事后剔除计数 + 图轴北京时间  

## 下一步

见 `analysis/h_tip_plan.md` 与 `scripts/tip_detectability.py`。

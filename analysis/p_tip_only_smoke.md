# tip-only 扫描冒烟诊断 — 2026-07-21

> 只读诊断 + 可回滚开关。**未**改 VPS 脉冲默认、**未**清 forward_log、**未** promote、
> **未**动 holdout / 判断层池。新鲜度三门仍 30min。

## 结论先行

**不要永久改主线为 tip-only。** tip 调度本身几乎不抬 tip_fire；根因仍是模型在
「无后文 tip 窗」上贴边框出生率≈0（与 `p_box_to_bar_lag.md` / A′ 贴边门一致）。

| 证据 | live@0.30 | tip@0.30 + TIP_CONF=0.22 |
|------|-----------|-------------------------|
| 强制当前 tip 扫描（账本 27 币） | **fired 0/27** | **fired 0/27** |
| 账本 32 行 lag-walk（max 8 bar） tip_fire | **1/32** | **1/32** |
| 同上 lag≤30min | **1/32** | **1/32** |
| 账本墙钟 log_lag≤30 | **0/32**（min 32.4，med ≈512） | 同左 |

唯一 tip_fire：`KGEN_USDT_SWAP` 信号 `2026-07-21 05:30`（tip 模式 lag_min=0；
live lag_min=15；账本 log_lag=32.4 — 仍过不了 30 门，属脉冲余量）。

**建议**：

1. **永久 tip-only**：否。省窗有 CPU 价值，但不解决出生率。
2. **仅右缘偏置（RIGHT_BIAS）**：可作可选开关保留，不指望抬 tip_fresh。
3. **主路径**：按 `analysis/p_v13_real_tip_collect_plan.md` 收 **真实 tip 成败图**
   （小样带框预览 → Owner 目视 → 再谈 v13 数据/训练）。自标 1000 张不能替代。

## A. 代码开关（默认 live，可回滚）

| 环境变量 | 作用 | 默认 |
|----------|------|------|
| `FABLE_YOLO_MODE=tip\|live\|full` | 主线扫描模式 | **live**（未设=live） |
| `TIP_CONF=0.22` | 仅 tip 窗 conf 下限；其它 live 窗仍 `0.30` | 关 |
| `FABLE_YOLO_RIGHT_BIAS=1` | min_gap 内保留最右信号 | 关 |

实现要点：

- `src/judgment/yolo_candidates.py`：`resolve_yolo_mode` / `resolve_tip_conf` /
  `resolve_right_bias`；tip 窗可低于其它窗的 conf（batch predict 取 min 后按窗过滤）；
  **tip 模式与 live 一样允许 tip bar 待入场**（对齐实时路径）。
- `TIP_EDGE_BARS=2` **保留**，live/tip 同用。
- `scripts/forward_pulse.sh` 打印开关状态；**不**写死 tip。
- 回滚：unset 上述三变量（或 `FABLE_YOLO_MODE=live`）。

VPS 已 rsync 代码；systemd Environment **无** `FABLE_YOLO_MODE` → 主线仍 live。
影子仍 `yolo_mode=tip`（v12 shadow，旁路）。

## B. 诊断命令与产物

```bash
# VPS（K 线覆盖账本信号时刻；本机 kline 停在 07-16 不可用）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --tip-smoke --tip-conf 0.22 \
  --out analysis/output/diag_tip_smoke.json

OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --compare --tip-conf 0.22 \
  --max-lag-bars 8 --out analysis/output/diag_detect_lag_compare.json

OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --mode tip --tip-conf 0.22 \
  --max-lag-bars 40 --out analysis/output/diag_detect_lag_tip40.json
```

| 产物 | 路径 |
|------|------|
| tip-smoke | `analysis/output/diag_tip_smoke.json` |
| live vs tip lag | `analysis/output/diag_detect_lag_compare.json` |
| tip@40bar | `analysis/output/diag_detect_lag_tip40.json` |
| 账本快照 | `analysis/output/forward_log_vps_20260721.csv` |

权重：VPS `models/owner_best.pt`（主线 v12）。贴边门全程开启。

## C. 数字解读

1. **当前 tip 强制扫描 0/27**：不是「忘了扫 tip 窗」——tip 与 live 都零开火；
   模型在这些币的盘口 tip 上贴边框为空（raw 命中也经 tip_edge 后为空）。
2. **账本 32 行 tip_fire 仅 1**：多数行是事后补认（log_lag 中位 ~8.5h）；
   tip-only 不会把它们变成新鲜。
3. **TIP_CONF=0.22 无增益**：同一 0/27 与 1/32，说明瓶颈不是 0.30 阈值，而是几何/
   形态「要后文才画框」。
4. **与 accept PF / tip_hit 离线指标脱钩**：再次确认离线 tip_hit≠实盘 tip 出生率。

## D. 风险与诚实声明

- lag-walk `max_lag_bars=8` 只覆盖 tip_fire / lag≤30 口径；更长滞后命中未全扫
  （tip40 仍 1/32 tip_fire，与 compare 一致）。
- tip-smoke 只覆盖账本里出现过的 27 币，不是全宇宙 344；但与「盘口几乎无 tip」一致。
- 本报告不声称 tip-only 有害——只声称 **不足以** 作新鲜度解药；作 CPU 削减可另议。
- 未消耗 holdout；未改阈值 / TP·SL / 三门。

## E. 下一步（分叉已落地）

| 选项 | 决策 | 动作 |
|------|------|------|
| 永久 tip-only | **否** | 开关保留，默认 live |
| RIGHT_BIAS | 可选 | 不默认开 |
| v13 真实 tip 成败图 | **是** | `scripts/collect_v13_tip_previews.py` 小样带框预览 |
| 放宽新鲜度 45–60 | **否** | 纪律禁止长期放宽 |
| 重建判断池 / promote | **否** | — |

Owner 决策点（需点头才做）：是否把某次脉冲临时 `FABLE_YOLO_MODE=tip` 做墙钟对照；
v13 小样目视通过后的扩采张数 / 是否开训。

## F. v13 小样采集（诊断分叉，已跑）

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/collect_v13_tip_previews.py --log data/forward_log.csv \
  --limit 12 --conf 0.20 --out analysis/output/v13_real_tip_preview
```

最近 12 个唯一信号 tip，每个 tip/+1/+2 共 36 张带框预览（绿=贴边 KEEP，橙=DROP）：

| tag（信号 tip+0） | n | 含义 |
|------------------|---|------|
| tip_hit | **1** | 贴边框保留（KGEN） |
| tip_miss_any_box | **4** | 有框但全被 tip_edge 丢掉 |
| tip_empty | **7** | conf≥0.20 仍零框 |
| error | 0 | — |

产物：`analysis/output/v13_real_tip_preview/` + `manifest.json`。
与 lag 诊断一致：多数「账本有行」的信号 tip 当下并非贴边开火。

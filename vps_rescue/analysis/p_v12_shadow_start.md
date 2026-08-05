# v12 影子启动记录 — 2026-07-20

**纪律**：未 promote；未改 ACTIVE；未清主线 forward_log；未耗 holdout。

## 已部署（VPS `103.214.174.58`）

| 项 | 路径/值 |
|---|---|
| 权重 | `/opt/fable-trading/models/owner_v12_htip.pt` (~19MB) |
| 脚本 | `scripts/forward_track_v12_shadow.py` + 更新后 `forward_pulse.sh` |
| 环境 | `data/v12_shadow.env` + systemd drop-in `fable-forward.service.d/v12-shadow.conf` |
| 开关 | `FABLE_V12_SHADOW=1` |
| 日志 | `data/forward_log_v12_shadow.csv`（仅影子） |
| 手工跑 | `logs/v12_shadow_manual.log`（首次 tip-only 全宇宙扫描中） |

## 扫描参数

- `yolo_mode=tip`（每币 1 窗）
- `source=yolo` · series≈344 · workers=3
- 判断层：主线 v11 freeze（分数/阈值/TP5-SL2 不变）

## 48h 后要交的

`analysis/p_v12_shadow_48h.md`：影子 tip 数、与主线重合、分数分布、新鲜 lag、风险声明。  
**通过后再向 owner 申请 D4 切流（holdout 第 6 次）。**

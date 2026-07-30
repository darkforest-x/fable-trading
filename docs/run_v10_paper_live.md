# v10 纸面模拟实盘（出信号推 TG + Bark + 前端展示）

**铁律提醒**：这是 **paper / sim only**。
- 不写 `forward_log.csv`
- 不改 `models/ACTIVE`
- 不真下单
- 检测只认 tip / tip-1 / tip-2（discipline 12）
- 新鲜度三门同值 = **30min**（脚本 / TG / 看板 / 执行器一致）

**状态（2026-07-30 本地闭环）**
| 项 | 状态 |
|---|---|
| 权重 `models/owner_short_star_v10.pt` | 本机已有（flat 副本） |
| `USE_STOP` | **True**（TP5/SL2；v10 池上「只止盈无止损」为负） |
| 本地 dry-run（5 币） | 曾 fire=1、**n_fresh=0**（age≈3222min，属陈旧缓存 K 线，预期行为） |
| `data/tg_config.json` | 本机有（gitignored） |
| `data/bark_config.json` | **缺** — Bark 推送会 no-op 并打印提示；补 key 或设 `BARK_KEY` |
| VPS（206.237.14.112） | **未部署** fable 树 / 无 `live_signal` timer — 纸面定时推送尚未上机 |
| 前端 `live_paper` API | 代码已接 `last_scan.json`；有扫描产物即可展示 |

## 1. 准备通知配置（gitignored）

```json
// data/bark_config.json
{"key": "你的Bark设备Key"}
```

TG 仍用 `data/tg_config.json`（已存在则复用）。

或用环境变量：
- `BARK_KEY`
- `TG_BOT_TOKEN` + `TG_CHAT_ID`

## 2. 运行纸面扫描 + 推送

```bash
# 仅渲染不推送（推荐先看）
PYTHONPATH=. python3 scripts/live_signal_tg.py --tip-only --dry-run

# 本地小样本
PYTHONPATH=. python3 scripts/live_signal_tg.py --tip-only --dry-run --n-symbols 5

# 出信号即推 TG + Bark（推荐 VPS 定时；需新鲜 K 线 + bark/tg 配置）
PYTHONPATH=. python3 scripts/live_signal_tg.py --tip-only --send --max-send 8
```

参数：
- `--tip-only`：只认信号 bar（age~0），否则允许 tip-1/tip-2（共 3 窗）。
- `--conf 0.30`：默认生产阈值。
- `--n-symbols 30`：本地调试只扫前 N 个币；生产不加此参扫全池。

建议定时：每 15 分钟一次（与脉冲节拍一致）。**VPS 上机前**先确认：
1. 仓库已 rsync/deploy（当前 206 无 `/opt/fable-trading`）
2. K 线由 VPS 唯一写者维护（discipline 9）
3. 三门仍为 30min，且未往脉冲里塞实验扫描（discipline 7–8）

## 3. 产物

```
analysis/output/live_signals_v10/
  last_scan.json          # 前端 /api/live-paper 读取
  *.png                   # 推送的图，也可通过 /debug-artifacts/... 直达
  paper_signals.csv
```

## 4. 前端

- 总览页顶部状态条出现 `v10纸面 X新/Y总`
- 总览页有小面板展示最近命中（带图链接）
- 刷新按钮可手动拉取

API：
- `GET /api/live-paper`
- `GET /api/status-strip`（含 `paper_live`）

## 5. 纪律与注意

- 仅用 v10 权重（`models/owner_short_star_v10.pt` 优先）。
- **USE_STOP=True**（TP5/SL2）。v10 池上「只止盈无止损」实测为负（HANDOFF 必回滚项，已回）。
- 任何改新鲜度门必须三处同改并附延迟预算。
- 想 promote / 接真执行 → 另批 owner 授权。
- Bark 无 key 时**不崩溃**，只跳过推送（与 TG 缺配置同语义）。

## 6. 一键检查

```bash
python3 -c "
from src.webapp.live_paper import live_paper_payload
p = live_paper_payload()
print(p.get('available'), p.get('n_fresh'), p.get('n_fired'))
"

# 确认出场策略
python3 -c "import importlib.util; s=importlib.util.spec_from_file_location('m','scripts/live_signal_tg.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('USE_STOP', m.USE_STOP, 'FRESH', m.FRESH_GATE_MIN)"
```

## 7. 已知诚实结论（不要当成「坏了」）

- **n_fresh=0 + n_fired>0**：信号 bar 太旧（本地缓存 K 线未刷新 / 未在 bar 关闭后立刻扫），新鲜度门正确丢弃。
- **VPS 未部署**：本地代码闭环 ≠ 实盘纸面在跑。上机是另一步（deploy + timer + K 线写者）。
- **Bark 未配置**：TG 仍可单独工作；两边都缺则只写 `last_scan.json` / PNG / CSV。

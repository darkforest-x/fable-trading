# MA206 运行路径清单

## 唯一生效配置

| 项目 | 当前值 |
|---|---|
| 均线 | SMA20/60/120 + EMA20/60/120 |
| 宇宙 | OKX USDT SWAP 15m |
| 数据集 | `data/ma206/swap_tp5_sl2_ma206.csv` |
| 冻结模型 | `models/frozen_tp5_sl2_swap_ma206_20260710.txt` |
| ACTIVE | 指向上述冻结模型 |
| 阈值 | val q90 = `0.3409333202` |
| 主线前向日志 | `data/forward_log_ma206.csv` |
| H1 影子日志 | `data/forward_log_h1_scaled_ma206.csv` |
| 正式起点 | `2026-07-10 10:30 UTC` |
| H9 | 1h EMA60 斜率 + close above EMA120 |
| MA 结构出场 | EMA20 |

## 已关闭的旧入口

- 旧 8-55 模型、数据集、报告只保留历史审计，不得被 `models/ACTIVE`、看板、runner、部署或定时任务加载。
- 看板只开放 SWAP；旧现货数据集入口关闭。
- VPS 部署只同步 `data/ma206`、MA206 前向日志和带证据范围的安全分数缓存。
- 本机 Claude 每日链和 Codex `fable` 自动任务均显式进入 `/Users/zhangzc/fable-trading-grok-2day`。

## 自动防回归

`tests/test_ma206_runtime_paths.py` 扫描 `src/`、`scripts/`、`.github/`，发现旧 EMA 列、旧日志、旧数据集或旧冻结模型引用即失败。历史报告不在扫描范围内，避免篡改实验记录。

## 证据边界

MA206 holdout 曾被旧看板意外评分一次，结果已隔离作废。当前评分缓存必须包含
`score_scope=pre_holdout_only`，最终裁决只认独立前向账本。

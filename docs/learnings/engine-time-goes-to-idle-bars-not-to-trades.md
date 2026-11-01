# Backtest engine time goes to idle bars, not to trades

- **问题**：周一 1.618 收盘进场的每轮 NautilusTrader 回测都要 1–2 小时（随机对照 263 个币两条路径，约 22,500 CPU 秒、3,058 笔单），Owner 问是不是框架不行、有没有更好的框架。
- **死胡同**：直觉是换一个更快的框架（VectorBT、backtesting.py 等），或者加更多进程。但计时拆开后：每个币读数据约 1–9 秒、`prepare` 不到 1 秒，引擎本身 20–22 秒。每个币要把整段 5 分钟历史（约 20 万根，BTC/ETH 更多）逐根推过引擎，而真正有持仓的只有一两笔、每笔几小时。换框架也要处理同样多的 K 线，还得重新对齐回收、保本、两种 K 线内顺序等成交语义。
- **有效路径**：适配器里已有 `run_native(..., batch_bars=288, skip_idle_bars=True)`：没有持仓和挂单时直接跳到下一笔提交的那天。1000CAT、DEGO 两个币逐笔结果完全一致，引擎时间从约 20 秒降到 0.1–0.2 秒。止损和确认时间两组实验 4 轮共 263 币，合计 622 秒跑完；原规则与已存成交逐笔对账，差 0bp。
- **通用规则**：回测慢先拆计时，看时间花在读数据、准备、还是引擎推空 K 线上。事件驱动、单笔持仓很短的研究，先打开空闲跳过（或只喂交易窗口），并用已存成交做逐笔对账，不要先换框架。
- **牵连**：`yoyo/layers/l3_backtest/nautilus_independent.py`（`skip_idle_bars`）、`yoyo/evaluation/monday_limit_close_stop_grid.py`（8a77b8c68a 起启用）；更早的 `monday_limit_close_reclaim.py`、`monday_limit_close_slice_control.py` 等仍按全量推送，结果不受影响，只是慢。

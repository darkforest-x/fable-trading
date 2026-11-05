# 停用后台 worker 前要先列出它产出数据的所有读者

- **问题**：2026-11-06 性能优化时，V12.8 扫描（约 0.85 核、峰值 1.9 GB）、趋势线联合扫描（约 0.6 核）和模拟开单全市场行情（每 1.5–3 秒拉一次）都像是“很久没用”：两路 Telegram 最后一次发送分别是 10-18 和 09-25。Owner 起初的判断是“都很久没用，删掉吧”。
- **死胡同**：只看 worker 自己的通知和页面会漏掉副作用。V12.8 扫描写进 `monitor.sqlite3` 的 `candle_checkpoints` 是多个模块的数据源：强势标的发现（`altcoin_leader_service.py`）100% 依赖其中的 1H K 线；OKX 自动执行器（`okx_execution/source.py`）靠它管理已开仓位的退出；形态巡检（`yolo_workbench/market.py`）拿它当便宜数据源，但有自己的缓存兜底；策略前向账本（`research_workspace/paper_source.py`）也读它。模拟开单如果只停 `MarketRefresh`，`LimitOrderWorker` 每 5 秒仍会按需拉全市场 tickers，轮询并不会消失；1.618 页的快速模拟下单和模拟账本也走同一套报价。
- **有效路径**：先用 `grep` 查数据库文件名和表名（`candle_checkpoints`、`monitor.sqlite3`），找出所有读者；再逐个确认依赖强度（必需、可兜底、已停用），连同最近一次使用时间和未平仓位一起交给 Owner。本次核实到执行器 10-28 起已停、账本仍有 16 笔未平；模拟开单 10-30 还开过仓，现有 2 笔持仓。Owner 了解这些影响后仍决定三项全部停用。实现上只是不再启动这些 worker：代码、数据库、checkpoint 和发件箱全部保留；状态接口显示 `retired`，不再把上一轮的 `idle` 回执当成运行中；页面报价改为按需读取。
- **通用规则**：要停 worker，先列出它“写什么”和“谁在读”，而不只是“它发什么通知”。每个读者都标明是必需、可兜底还是已停，有真金或持仓的要单独列出来。同一份报价如果有两个轮询者（专门的刷新线程和撮合 worker），要两个都停。停用不等于删除数据。
- **牵连**：`yoyo/monitor/service.py`（`RETIRED_WORKERS`）、`yoyo/monitor/server.py`（不再启动 paper desk 后台，`/api/lines/status` 显示已停用）、`yoyo/monitor/static/app.js`。证据在 `output/perf-20261106/`。相关：[退役模拟功能还要停掉已保存的活动](retiring-paper-features-must-disable-persisted-campaigns.md)。

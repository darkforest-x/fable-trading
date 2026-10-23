# 多币报警要同时控制源状态、启动基线和平台频率

- **问题**：Essential 的独立技术报警额度不足以逐币覆盖 30 个合约；原指标的多周期请求也占用 Pine 的 40 个唯一请求额度。
- **死胡同**：把单图 alertcondition 当全市场监控不会扫描其他币；给每次远端触价直接调用 alert 又可能在三分钟超过 15 次后被平台停止。只有周线价格而没有完整周一与历史首次触价状态，还会补报旧事件。
- **有效路径**：在 V2.39 中将固定 30 个合约分成两个 15 币快照，每组一个 alert() 报警。远端 15m 引擎独立累积纽约周一 96 根 K 线、上下首次触价时间；调用端用 varip 保存启动基线与去重。待发消息合并，至少间隔 15 秒，在宿主更新时发送，避免丢弃同一根 K 线后续币种事件。
- **通用规则**：先核实账户报警额度、脚本请求额度与触发频率，再选分组大小。原生编译与实际活动报警是两项验收；源码支持并不代表服务器已经启用。固定名单更改需要重建报警快照。
- **牵连**：`yoyo/evaluation/pine/monday_range_v2_39.pine`；`tests/evaluation/monday_range_v2_39_native_checks.pine`。源数据过期或缺失时暂停该币，恢复时重新建立基线，不补报缺失期间事件。宿主无行情更新时不能承诺精确秒级延迟。依据：[TradingView alerts FAQ](https://www.tradingview.com/pine-script-docs/faq/alerts/)、[Pine limitations](https://www.tradingview.com/pine-script-docs/writing/limitations/)。

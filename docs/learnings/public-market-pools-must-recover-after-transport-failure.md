# 行情网络恢复后，失效连接池也必须能恢复

- **问题**：模拟交易的 ETH、BONK 持仓报价过期，权益和可用资金变为未知。日志先出现 TLS EOF，随后持续 PoolTimeout；新建连接同源读取两标的行情正常。独立模拟报价不依赖信号扫描名单，不能将时间相邻的币种排除当成原因。
- **死胡同**：只重试原连接池会反复等待无法释放的连接槽；增加池上限只能推迟故障，延长报价有效期则会把旧价用于开仓和权益核算。直接关闭共享池又会中断其他正在读取的请求。
- **有效路径**：用真实 httpcore CONNECT/TLS 路径和内存网络夹具重现故障：握手失败占满池后，后续请求转为 PoolTimeout。公开 GET 遇传输异常后淘汰对应连接池，最多使用新池重试一次；引用计数让旧请求结束后再关闭旧池，旧池的迟到异常不能淘汰新池。报价继续按原始时间戳和15秒门校验，未恢复时明确列出缺失币种。
- **通用规则**：网络连通与长驻客户端可用是两个状态。出现持续池等待时，同时比较新连接和旧客户端；验证重建、并发排空、迟到失败、持续离线与旧行情不得续命。
- **牵连**：`yoyo/research_workspace/paper_desk_market.py`、`paper_desk.py`、`yoyo/monitor/static/paper-desk.js`、`tests/research_workspace/test_paper_http_recovery.py`。仅模拟公开行情读取，不重试交易写请求，不改账户账本或报价新鲜度。参考 [HTTPX PoolTimeout 定义](https://www.python-httpx.org/exceptions/) 与 [httpcore 1.0.9 代理隧道实现](https://github.com/encode/httpcore/blob/1.0.9/httpcore/_sync/http_proxy.py)。

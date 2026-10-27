# Coin artwork needs verified local assets and a browser-tested retry

- **问题**：首页按币名拼接外部 CDN 地址，LAYER 已有本地图标却仍受外网影响；DGAI、SHEIN 的地址没有图片。错误处理只隐藏图片，使成功恢复的条件不清楚。
- **死胡同**：将同一个 src 赋回去并不保证重试。WebKit 的真实 503 注入只产生一次网络请求，图片持续隐藏；纯事件桩测试没有暴露这一点。
- **有效路径**：复用已验证的 433 个 OKX 本地资源，以同源、内容版本 URL 提供；没有资源就显示等尺寸双字母标识。错误时只增加一次独立重试参数，load 时恢复显示，轮询保留图标节点。Chrome 和 WebKit 公网注入都验证了两次请求后恢复。
- **通用规则**：先区分资源不存在和传输失败，再检查实际浏览器是否重新请求。不要猜 logo URL，也不要把 onerror 隐藏当作完整恢复机制。
- **牵连**：`yoyo/monitor/static/coin-icons.js`、`reclaim-watchlist.js`、`asset_delivery.py`；复用 `yoyo/discipline_app/mobile/src/assets/coins/`。原始验证在 `output/playwright/logo-performance-20261027/`。

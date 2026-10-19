# Fast display quotes must remain independent of execution authority

- **问题**：服务端两秒 REST 轮询叠加手机两秒账户刷新，价格显示明显滞后。
- **死胡同**：缩短整个账户轮询会重复读取资金与合约目录；把手机推送行情用作下单依据会绕过服务端新鲜度与风险计算。
- **有效路径**：前台当前币种和持仓订阅 OKX 公开 tickers，100ms 合并渲染；断线清理并退避重连、后台释放连接。账户与执行仍读取服务端账本，推送只替换显示价格。固定节流而非不断重置 debounce，避免密集行情一直不渲染。
- **通用规则**：显示新鲜度和执行授权分层；更快的读通道不应成为新的写依据。
- **牵连**：`mobile/src/useLiveQuotes.ts`、`App.tsx`、`mobile/index.html` CSP；本机公开 WS 实测五次更新，手机网络仍需单独验收。

# 公网隧道进程在线不能证明供应商额度仍可用

- **问题**：2026-10-18 Owner 的固定 ngrok 行情入口不可访问。网页 PID55539、Caddy PID35892、ngrok PID75107 均仍运行，但公网实际返回 HTTP403、`ERR_NGROK_725`，正文明确为当月网络流量额度耗尽。
- **死胡同**：只查进程或本地首页会把站点误判为正常；重启网页无法解除供应商额度。直接请求 loopback 网关而不带已准入的 Host 和 HTTPS 转发头会得到预期的421，不能把它当作网关坏了。现有 `public_tunnel start/status` 优先读取固定域名，也不会自动给出临时回退入口。本轮未盲目重启服务或反复注册 ngrok。
- **有效路径**：分层读取本地首页、带正确请求头的网关状态、公网响应正文，区分上游服务、网关准入与供应商限制。复用已准入 `*.trycloudflare.com` 的原网关，恢复已有 `com.spike.public-tunnel` LaunchAgent；保留 ngrok 进程及 `fixed-endpoint.json`。临时公网首页、`/api/status`、`/static/app.js` 均200，真实浏览器显示行情、六均线与494币列表后才交付。原固定域名仍受限，不能宣称已修好原地址。
- **通用规则**：公网故障先检查端到端响应及供应商错误码，再决定是否重启。回退入口必须实测、明确临时性并记录当前地址；原域名恢复要由额度重置或账号套餐解决，不能用本机存活代替。Quick Tunnel重建会换域名，不能承诺收藏永不变。
- **牵连**：`yoyo/monitor/public_tunnel.py`；用户 Library 下 `Fable/PublicTunnel/` 的 Caddyfile、固定入口、临时入口与access说明；原始证据 `output/public-recovery-20261018/`。未修改应用代码、交易参数、执行进程或巡检计划。收尾临时IAB标签关闭；手动执行的ARX旧单对账阻塞及自动执行既有stopped状态单独保留，不把网页恢复当作交易已就绪。参考 [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。

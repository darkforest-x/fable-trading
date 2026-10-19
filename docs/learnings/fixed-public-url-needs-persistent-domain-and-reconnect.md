# 固定公网入口需要同时固定域名归属与本机重连配置

- **问题**：Quick Tunnel 恢复后可以在手机访问，但重建会更换地址，收藏夹不能长期使用。
- **死胡同**：仅把上次随机域名记在文件里，或只给隧道进程加自动重启，都不能使服务商继续分配同一个域名。进程存在也不能证明公网能到达工作台。
- **有效路径**：先确认本机已有 ngrok 账号的凭据与可用 Dev Domain；显式指定该域名并用独立 LaunchAgent 启动，仍经过原 Caddy 同源网关。新入口实际返回预期工作台健康响应后才退役临时隧道；再重启 ngrok，核对域名和公网响应都不变。凭据留在原私有配置，不复制进仓库或进程参数。
- **通用规则**：交付可收藏链接时，分别验证域名归属、持久启动参数、真实公网可达性与重连后的地址。固定域名不能替代在线机器，也不代表无限额度；浏览器提示页、月度请求和流量限制要如实说明。
- **牵连**：`yoyo/monitor/public_tunnel.py`、`tests/monitor/test_public_tunnel.py`；本机 `PublicTunnel/fixed-endpoint.json`、`access.txt`；[ngrok Dev Domain](https://ngrok.com/docs/gateway/domains#dev-domains)、[免费版额度](https://ngrok.com/docs/pricing-limits/free-plan-limits)。本轮 39 项检查通过，原始公网与重启证据在 `output/playwright/task54/fixed-public-verification.json`。

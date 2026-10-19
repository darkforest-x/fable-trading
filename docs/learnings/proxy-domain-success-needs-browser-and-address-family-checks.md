# 公网 IP 可通不能代替代理域名与浏览器验收

- **问题**：`spikeok.com` 在手机蜂窝可用，Mac 的 Shadowrocket 按域名访问却 TLS 断连；保留原 SNI 和证书校验、指定公网 IPv4 后可返回 200。
- **死胡同**：仅添加该域名的 DNS 服务器仍失败；改为域名直连后偶有 200，但其中一个 Cloudflare IPv4 直连超时，浏览器数据请求不完整。不能把一次 200 或 fake-IP 地址本身当成根因证据。Owner 清理 Chrome DNS 缓存后仍失败，排除了“只需清浏览器缓存”的解释。
- **有效路径**：对比相同主机名的普通解析、代理按域名、代理按真实 IPv4、IPv4/IPv6 请求。两个公网 IPv4 经代理均成功，IPv6 路径失败。Shadowrocket 保留原节点与代理传输，为该域名设置 `Host: server:1.1.1.1`、`always-real-ip`、系统 HTTP 代理例外及精确 `PROXY` 规则，并关闭当前配置的 IPv6；最终普通域名健康检查及 Chrome 全市场、K 线和六均线加载通过。没有写死 Cloudflare 网站 IP、关闭证书验证或启用 HTTPS 解密。未取得代理服务器内部 DNS 日志，不将缓存或远端 DNS 的具体错误冒充已证实根因。
- **通用规则**：分开验证解析、HTTP CONNECT、TUN 转发、地址族与真实浏览器。配置应用会让既有隧道连接重连，应等连接重新注册后再验收；切换瞬间的 502 与持续 TLS 失败不是同一问题。Python 默认 UA 的 Cloudflare 1010 与 DNS 故障也应分开，用真实健康客户端 UA 验证，不关闭防护。
- **牵连**：Shadowrocket `default.conf` 的 IPv6 开关影响整个当前配置，其余新增项仅限 `spikeok.com`。配置备份在仓外 `~/Library/Application Support/Fable/PublicTunnel/shadowrocket-before-spike-direct.conf`（已含本轮 DNS 项，回滚全轮时还需删除该 Host 项）。验收原始结果在 `output/spike-domain-20261018/`。正式入口仍依赖 Mac、网络和 Cloudflare 隧道在线。

# 固定公网地址需要持久隧道身份，域名不能代替服务在线

- **问题**：2026-10-18 下午，ngrok 固定入口仍受月流量限额阻断，凌晨恢复的 Quick Tunnel 又反复报 `Unauthorized: Tunnel not found`。Owner 要求先恢复，再建立地址不变的长期方案。
- **死胡同**：固定 ngrok 域名没有解除供应商额度；临时隧道的 launchd 自动重连没有恢复已丢失的供应商注册。把自有域名直接别名到临时主机也不能获得持久隧道身份。
- **有效路径**：只重启已有 Quick Tunnel，端到端检查首页、状态接口和脚本均 200，再用真实浏览器核验行情和 494 个交易对。更新私有入口记录，保留原固定配置。长期选择自有域名加账户内正式 Cloudflare Tunnel；待域名购买和授权后，映射到现有 Caddy 网关，验收后再退役临时入口。本轮尚未完成这一步。
- **通用规则**：分别验收地址持久性、供应商通道可用性和源站在线。正式隧道可让重连保留主机名，但 Mac 断网或关机仍会中断服务；需要脱离 Mac 的可用性时另行规划常驻服务器与数据依赖。不能把短时连通称为长期稳定。
- **牵连**：`yoyo/monitor/public_tunnel.py`；本机 `Fable/PublicTunnel/access.txt` 与 `fallback-endpoint.json`；原始检查 `output/public-recovery-20261018-afternoon/`。参考 [Quick Tunnel 限制](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) 和 [正式隧道配置](https://developers.cloudflare.com/tunnel/get-started/)。未修改应用、界面或交易执行配置。

# 每个 cloudflared 连接器都要有自己的 1033 看门狗；走本机代理的隧道会静默断线

- **问题**：2026-11-08 owner 反馈纪律 App 一天里多次报错，App 上显示的是 Cloudflare 的英文提示 “The host is configured as a Cloudflare Tunnel, but Cloudflare is currently unable to reach it”，也就是 1033。出问题时本机 `127.0.0.1:8897/health` 一直正常，cloudflared 进程也一直在（从 11-06 起没退出过），但公网返回 530 / 1033。
- **原因**：
  - `connector.log` 里 edge 地址是 198.18.0.14 / .15。这是本机代理 fake-IP 模式的地址段，说明隧道连 Cloudflare 走的是代理。代理一切换节点或掉线，隧道连接就断：当天 12:17、12:55、13:04 都有 “Lost connection with the edge”。
  - fake-IP 只解析出 2 个地址，所以只能建 2 条连接（日志里有 “no free edge addresses left”），而正常是 4 条。
  - 21:48 之后两条连接都静默挂住，没有任何断线日志，cloudflared 不会自己重连；launchd 看到进程还在，也不会重启。
- **死胡同**：
  - launchd 的 KeepAlive 只管进程退出，管不了“进程活着但不通”。
  - 仓库里已经有 `yoyo/monitor/tunnel_watchdog.py`（10-26 两次 1033 后写的），但它只盯监控工作台那条隧道（com.spike.public-cloudflare）。当天那条隧道自己恢复了，纪律服务这条（com.spike.discipline.cloudflared）一直断着。
  - 想直接 `launchctl kickstart` 恢复，被自动权限拦下：重启常驻服务需要 owner 当次点头。
- **有效路径**：
  - 服务端：在同一个 60 秒检查任务里加 `check_discipline`，规则和原来一样：本地 /health 正常、公网连续 3 次确认是 1033 才重启，两次重启至少隔 10 分钟，每小时最多 3 次，只重启连接器、不碰交易服务。它默认不生效，要有 `SpikeDiscipline/public/tunnel-health.enabled` 文件才启用（`python -m yoyo.monitor.tunnel_watchdog enable-discipline`），这一步需要 owner 批准。另外，识别 1033 时把 Cloudflare 的 JSON 错误格式（`"error_code":1033`）也算进去。
  - App：服务本身从不返回 502 / 504 / 52x，所以这几个状态码一律显示中文“服务连接中断，正在自动重连”，不再把 Cloudflare 返回的 `detail` 当成服务自己的报错。
- **通用规则**：
  - 新增一条隧道或一个连接器时，同时把它登记进健康检查；不能默认已有的看门狗也会管它。
  - 看到 edge 地址落在 198.18.0.0/15，就说明隧道在走代理，掉线频率取决于代理是否稳定。要从根上解决，得让 Cloudflare 的隧道域名（`*.argotunnel.com`、`*.cftunnel.com`）走直连还是走固定节点，这是 owner 的网络配置，需要 owner 来改。
- **牵连**：`yoyo/monitor/tunnel_watchdog.py`、`tests/monitor/test_tunnel_watchdog.py`、`native/SpikeDiscipline/Core/APIClient.swift`（`edgeFailure`）、`~/Library/Application Support/SpikeDiscipline/public/connector.log`。参见 [原生 K 线缓存](native-chart-disk-cache-must-drop-on-gap-and-key-by-host.md)，那一条也是“断了要能看出来”。

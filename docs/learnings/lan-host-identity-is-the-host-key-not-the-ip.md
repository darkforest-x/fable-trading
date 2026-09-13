# 局域网机器的身份是主机密钥指纹，不是写死在 plist 里的 IP

- **问题**：2026-09-13 Owner 报 3060 打不开 `http://127.0.0.1:8766/#ashare`，页面给出
  「请检查 SSH 隧道」。这条文案由 Windows 网关在连不上本机 8767 时给出，容易被读成
  「隧道进程挂了」或「Mac 服务挂了」。
- **死胡同**：按字面去查两端服务，全是好的——Mac `8766` 在 LISTEN 且 `/api/health` 200；
  Windows `SpikeDesktopClient` 是 `Running`、loopback `8766` 有进程在听；
  `launchctl list` 里 `com.spike.3060-tunnel` 也有活着的 PID（只有上一次退出码 255 是线索）。
  逐个重启这些服务不会修好任何东西，只会白重启 Mac 扫描。
- **有效路径**：看隧道自己的 stderr 日志（`~/Library/Logs/Spike/WindowsClient/tunnel-error.log`）
  才看见真因：`connect to host 192.168.1.2 port 22: Connection refused / Operation timed out`
  刷了几十万字节。DHCP 把 Windows 从 `.2` 漂回了 `.3`（`.2` 现在是别的设备占着租约，
  所以是 refused 而不是纯超时——refused 比 timeout 更容易骗人，它看着像「机器在、服务没起」）。
  确认「`.3` 就是那台 Windows」靠的不是 ping 通，而是主机密钥指纹：
  `ssh-keyscan -t ed25519 192.168.1.3` 实测 `SHA256:fQTPIk3nUVNYmPuRzt7DhZfZaRjKukRP9fHJn19karY`，
  与 known_hosts 里 `.2`/`.5` 记录的同一把 key、与 `docs/ops/RECONNECT_3060.md` 当年在
  ToDesk 画面上人工核对过的指纹一致。身份确认后只改 plist 最后一个 SSH 参数并
  `launchctl bootout` + `bootstrap`，30 秒恢复；没有关 `StrictHostKeyChecking`，
  没有重启 Mac 扫描（`started_at_ms` 不变）。
- **通用规则**：本机 loopback 页面打不开而两端服务都健康时，**先读转发进程自己的 stderr 日志，
  别先重启服务**——转发失败和服务故障的症状在浏览器里完全一样。换地址前，机器身份一律用
  主机密钥指纹认（`ssh-keyscan | ssh-keygen -lf -` 对已核对过的指纹），
  不用 IP、不用 ping 通、不用 `StrictHostKeyChecking=no` 绕过。
  `Connection refused` 不等于「机器在」——DHCP 环境下它常常是别的设备接了这个租约。
- **牵连**：`~/Library/LaunchAgents/com.spike.3060-tunnel.plist`（最后一个 SSH 参数是唯一需要改的地方）、
  `yoyo/monitor/desktop_client.py` 的 `UPSTREAM_ERROR` 文案、`docs/ops/SPIKE_WINDOWS_CLIENT.md`
  的「已核实 Windows」行。相关：[hung-ssh-channels-look-like-an-unreachable-training-box.md](hung-ssh-channels-look-like-an-unreachable-training-box.md)、
  [local-web-delivery-needs-a-service-owner-beyond-the-agent-shell.md](local-web-delivery-needs-a-service-owner-beyond-the-agent-shell.md)。
  未做（留给 owner 决定）：把 plist 改成 mDNS 名 `win-zzc.local`（实测解析到 `.3`）可免于再漂，
  但需要为该名字新增 known_hosts 条目，且 IPv6 优先解析会走另一条防火墙路径，本轮没动。

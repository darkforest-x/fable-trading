# VPS 可观测装机 — 待 Owner 批准清单

**状态**：规格级，**2026-07-22 夜间未装、未 SSH 改机**。  
对应 wuzao C 档 / `H-TOOL-1` / `H-TOOL-3`。

| # | 组件 | 用途 | 风险 / 成本 | Owner 批后动作 |
|---|------|------|-------------|----------------|
| 1 | [uptime-kuma](https://github.com/louislam/uptime-kuma) | 探活 dashboard / forward 脉冲 / executor HTTP | 探针 ≠ 新鲜度门；占一点内存 | 旁路容器 + 3 个 HTTP 检查 |
| 2 | Grafana **或** [netdata](https://github.com/netdata/netdata)（二选一） | 主机 CPU/内存 + 可选脉冲耗时面板 | 小机资源；优先榨 journal | 先确认 journal 是否够「>600s 查因」 |
| 3 | Prometheus `node_exporter` | 主机指标导出 | 全家桶过重则跳过 | 仅当选 Grafana 时 |
| 4 | [trivy](https://github.com/aquasecurity/trivy) | 若有容器旁路则扫一次 | 误报；不改交易 | 可选卫生 |

**当前建议**：tip≈0 时运维装机 ROI 低；继续用 `journalctl` + 本机 `fable-gpu` alias。  
批准前不要在 VPS 上 `docker compose up` 上述栈。

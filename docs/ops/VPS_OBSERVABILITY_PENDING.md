# VPS 可观测装机 — 待 Owner 批准清单

**状态**：规格级，**2026-07-22 夜间未装、未 SSH 改机**。  
对应 wuzao C 档 / `H-TOOL-1` / `H-TOOL-3`。

| # | 组件 | 用途 | 风险 / 成本 | Owner 批后动作 |
|---|------|------|-------------|----------------|
| 1 | [uptime-kuma](https://github.com/louislam/uptime-kuma) | 探活 dashboard / forward 脉冲 / executor HTTP | 探针 ≠ 新鲜度门；占一点内存 | 旁路容器 + 3 个 HTTP 检查 |
| 2 | Grafana **或** [netdata](https://github.com/netdata/netdata) **或** [OpenObserve](https://github.com/openobserve/openobserve)（**三选一**） | 主机指标 / 轻量可观测；OpenObserve=单二进制日志+指标 | 小机勿叠装；优先榨 journal | 先确认 journal 是否够「>600s 查因」 |
| 3 | Prometheus `node_exporter` | 主机指标导出 | 全家桶过重则跳过 | 仅当选 Grafana 时 |
| 4 | [Grafana Loki](https://github.com/grafana/loki) + [Fluent Bit](https://github.com/fluent/fluent-bit)（可选） | 聚 `discover_wall` / phase2 日志查脉冲超时 | 比完整 Prom 更贴查因；仍占盘 | tip 通且 journal 不够用时再议 |
| 5 | [Caddy](https://github.com/caddyserver/caddy) 或 [acme.sh](https://github.com/acmesh-official/acme.sh) | 看板若公网暴露：自动 HTTPS 反代 | 与交易无关；暴露面卫生 | 仅确认公网暴露后 |
| 6 | [trivy](https://github.com/aquasecurity/trivy) | 若有容器旁路则扫一次 | 误报；不改交易 | 可选卫生 |

来源增量：`analysis/p_wuzao_more_useful.md`（2026-07-22）。

**当前建议**：tip≈0 时运维装机 ROI 低；继续用 `journalctl` + 本机 `fable-gpu` alias。  
批准前不要在 VPS 上 `docker compose up` 上述栈。

# Label Studio 的 host 不是监听地址

- **问题**：systemd 单元写了 `--host 127.0.0.1`，但 Label Studio 1.15 仍监听 `0.0.0.0:8082`，公网可以绕过 nginx 直接访问后端。
- **死胡同**：把常见 Web CLI 的 `--host` 语义套到 Label Studio，并让部署后的 `ss` 检查以 `|| true` 结束；文档看似安全，运行时却没有强制约束。
- **有效路径**：读取目标版本 `label-studio start --help`，确认 `--host` 只用于生成外部 URL，真正的监听参数是 `--internal-host`；单元改用 `--internal-host 127.0.0.1`，部署验收对 `ss` 结果 fail closed。
- **通用规则**：第三方 CLI 的 host、public URL、bind address 必须按 `--help` 分开核验，并用真实端口监听和公网探测双重验收。
- **牵连**：`scripts/label_studio_vps.service`、`scripts/deploy_label_studio_vps.sh`、VPS 8081/8082 暴露边界。

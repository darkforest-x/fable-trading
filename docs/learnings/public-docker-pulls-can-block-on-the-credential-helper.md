# 公开 Docker 镜像拉取卡住也可能是凭据助手在等待

- **问题**：2026-09-07 安装 Hummingbot，Docker daemon 正常、GitHub 下载正常，但官方公开镜像的 manifest 与 pull 长时间没有输出。
- **死胡同**：仅按网络故障解释，会反复等待 registry。实际卡点是 `docker-credential-desktop` 等待钥匙串，尚未完成公开镜像请求。
- **有效路径**：先分离 daemon 可用性和凭据助手行为。对明确公开的官方镜像，在本轮安装目录建立独立空 `DOCKER_CONFIG`，显式连接已核实的 daemon socket，匿名拉取；Hummingbot 与 Gateway 随后完成下载并通过真实模块导入。
- **通用规则**：公开 registry 操作无输出时，先确认卡在凭据助手还是传输层。只有公开资源才适用匿名配置；私有镜像仍需正常认证，不能把两者混同。
- **牵连**：`~/.local/share/crypto-toolkit/hummingbot/` 的安装配置；`install-evidence/hummingbot-pull.log`、`hummingbot-validation.log`、`hummingbot-gateway-validation.log`。原 Docker 登录配置和钥匙串未修改。

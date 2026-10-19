# Docker 清理卡住时，先恢复引擎，再验证镜像删除与原服务恢复

- **问题**：2026-09-11 清理未使用的 Hummingbot / Gateway 镜像时，Docker socket 与镜像查询无响应；owner 明确批准重启 Docker，接受 Label Studio 暂时中断。
- **死胡同**：继续重试镜像删除不能修复无响应的引擎。`docker desktop restart --timeout 45` 也因旧后台进程未退出而失败。命令超时不能当作操作成功，更不能据此直接删除 `Docker.raw`。
- **有效路径**：通过当前 CLI 帮助确认支持的操作，执行 `docker desktop stop --force --timeout 20`，再执行 `docker desktop start --timeout 45`。引擎 `_ping` 恢复 `OK` 后，重新枚举全部容器，逐一核对其镜像 ID，确认两个目标镜像均无容器引用，才执行不带 force 的 `docker image rm hummingbot/hummingbot:latest hummingbot/gateway:latest`。删除命令成功，随后镜像清单只剩 Freqtrade 和 Label Studio。原 `fable_label_studio` 容器按 `unless-stopped` 策略自动启动，`http://localhost:8081/` 跟随重定向后返回 HTTP 200。
- **通用规则**：引擎恢复、删除完成、原服务恢复是三个独立验收项；先获得必要的服务中断授权，再按实际镜像 ID 检查依赖。用 `Docker.raw` 已分配块数和整盘可用空间测量回收量，不能把镜像列表显示大小直接当作实际释放空间。
- **牵连**：本轮没有确定 Docker 最初无响应的根因；强制停止是已获授权的恢复步骤，不是根因修复。空间测量规则见 `docs/learnings/apfs-directory-size-is-not-reclaimable-space.md`。操作记录保存在当轮临时文件 `/tmp/codex-docker-restart-remove-20260911.json`。

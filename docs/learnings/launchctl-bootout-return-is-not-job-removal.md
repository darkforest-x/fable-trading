# launchctl 停止命令返回，不等于任务已经从 launchd 消失

- **问题**：独立纪律隧道重连验收中，`stop` 后立即 `start` 曾显示已加载，但随后任务消失，公网返回 Cloudflare 1033。
- **死胡同**：只看 `bootout` 返回码，并在紧随其后的 `launchctl print` 成功时判定服务已运行，会把仍在退出的任务当成可用任务。
- **有效路径**：`stop` 在 `bootout` 后有界等待该精确 label 从 launchd 消失；超时显式失败，不声称停止完成。再次验证 stop/start 后任务运行，连接器完成注册，原域名恢复健康响应 200。连接器注册前短暂出现的 530 仍作为未就绪记录。
- **通用规则**：进程停止、任务卸载、进程启动、远端可用是不同状态。管理命令应等待其承诺的状态，网络服务启动后还须验证真实入口，不能用 PID 替代。
- **牵连**：`yoyo/discipline_app/scripts/public_service.py`；只操作 `com.spike.discipline.cloudflared`，不重启既有网站或模拟引擎。

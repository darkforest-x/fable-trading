# Cursor agent shell 会杀后台 uvicorn

- **问题**：本机看板 `uvicorn …:8642` 在 Cursor agent 终端里启动后，过几分钟～十几分钟就 `ERR_CONNECTION_REFUSED`。
- **死胡同**：`nohup … &` 挂在同一 agent shell 会话里仍会被收割；终端日志常见优雅 `Shutting down` + `exit_code: unknown`（非 crash）。
- **有效路径**：用 **user launchd**（`~/Library/LaunchAgents/com.fable.local-webapp.plist`，同 v13 训练的 `com.fable.owner-v13-pad200-train`）托管进程；`KeepAlive=true`，日志写 `logs/local_webapp*.log`。
- **通用规则**：凡需要跨 agent 回合存活的本机长驻服务，**不要**靠 Cursor 后台 shell；优先 launchd，其次独立 Terminal.app / `launchctl`。
- **牵连**：`scripts/webapp_start.sh` / `scripts/webapp_status.sh` / `scripts/com.fable.local-webapp.plist`；说明见 `docs/LOCAL_DEBUG_TOOLS.md`。

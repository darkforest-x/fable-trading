# 服务是否在跑看工作进程本身，不看它的面板

- **问题**：Discord 实盘跟单从 2026-09-14 起停摆了三周，没人发现。它的 venv 中 `python3` 软链到 `/Applications/Xcode.app/.../python3`；Xcode 删掉后，launchd 一直重启一个不存在的解释器（退出码 127，日志 4700 多行 `no such file or directory`）。同一项目的管理面板（vite）和 cloudflared 隧道是另外两个 LaunchAgent，照常运行，所以面板能打开，看起来一切正常。
- **死胡同**：看 `launchctl list` 有没有这个 label：label 一直都在，只是 PID 列为 `-`。看面板能不能打开：面板和工作进程是两个进程。看 README 里写的状态：README 描述的是设计意图，不是运行现状。
- **有效路径**：先读 `launchctl list` 的退出码列（127 = 命令找不到），再读 plist 里的 `StandardErrorPath`，最后用 `ls -la .venv/bin/python3` 顺着软链找到不存在的目标。迁移后 `yoyo.copier.manage install` 会拒绝一个无法 `resolve()` 的解释器；`status` 报告 launchd 的 `state` 和 `last exit code`，并列出残留的旧 label。
- **通用规则**：判断一个服务是否存活，看的是真正干活的进程（pid、退出码、它自己的心跳或日志时间），不是它的 UI、隧道或"页面能打开"。venv 建在系统会被替换的解释器上（Xcode、Homebrew 升级）时，安装脚本要校验软链终点；做系统级清理（删 Xcode 等）后要逐个核对依赖它的 venv。本仓 `.venv` 在 09-15 被人重指到 CommandLineTools，copier 的 venv 却没人管，因为它在另一个仓里。
- **牵连**：`yoyo/copier/manage.py`（`install`、`status`）、`~/Library/LaunchAgents/codex.discord-okx-*.plist.retired-20261007`。相关：[依赖可用性属于解释器](dependency-availability-belongs-to-an-interpreter-not-a-machine.md)。

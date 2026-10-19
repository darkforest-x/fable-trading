# 后台外盘权限取决于实际启动入口，不能凭应用开关验收

- **问题**：2026-10-16，Owner 要求把行情和训练样本迁到移动盘并保留网页读取。命令行可读，LaunchAgent 使用同一 venv 却收到 `Operation not permitted`。
- **死胡同**：给 Python.app 开启完全磁盘访问仍失败。TCC 日志显示执行映像是 Python.app 内的 Python，但 responsible path 是同一框架下的 `bin/python3.9`；两种身份不能互相替代。当前系统添加窗口把该命令行文件置灰。
- **有效路径**：Owner 明确批准后台 Python 3.9 的完全磁盘访问并完成系统验证后，将实际 `bin/python3.9` 从访达拖入权限列表。直接 Python 入口的 LaunchAgent 探针读取成功。随后使用看板原有 caffeinate 包装入口复测仍失败，系统日志确认 responsible path 又变成 `/usr/bin/caffeinate`。改用 Python 作为看板入口，防休眠进程改为 Python 的子进程并用 `-w PID` 绑定生命周期，保留原功能且不扩大权限。无效的 Python.app 授权随后关闭，只保留实际命令行入口。
- **通用规则**：存储迁移先用真实启动上下文验证目标盘权限；失败时按系统归属日志定位入口，不根据程序名或同一用户猜测。授权与数据校验分开，后台访问通过、消费者兼容及约定的复制验收完成后才能释放源副本。
- **牵连**：`data/_storage_migrations/20261016-task52-bulk/launchagent-probe-before.json` 与 `launchagent-probe.json`；`/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`；既有经验 `storage-links-must-preserve-catalog-trust-and-identity.md`。

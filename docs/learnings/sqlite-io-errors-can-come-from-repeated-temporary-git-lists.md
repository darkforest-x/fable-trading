# SQLite 的 I/O 错误先核对磁盘与临时产物

- **问题**：界面验收时监控快照与通知读取反复报 `sqlite3.OperationalError: disk I/O error`，状态页看似渠道未配置。磁盘仅剩约 147 MiB。
- **死胡同**：重新启动服务没有恢复快照，继续重启无法解决空间不足；缺失快照也不能直接推断通知凭据被删除。
- **有效路径**：临时目录发现 266 份约 100 MB 的重复 NUL 分隔仓库路径清单，累计约 25 GiB。核对内容格式、时间和无进程占用后，仅清理 20 份一天前的可再生清单，释放 2,026,954,512 字节；状态快照随即恢复，`stale=false`、`refresh_error=null`。
- **通用规则**：遇到 SQLite 磁盘 I/O 错误先查可用空间，再排查文件和权限。清理只针对可证明可再生且无占用的临时文件，不碰数据库、行情、模型或历史证据。持续生成清单的来源仍须另查，少量清理不是容量管理方案。
- **牵连**：macOS 临时目录、`yoyo/monitor/service.py` 状态缓存；本次清理清单与哈希在 `output/playwright/workbench-cleanup/temp-list-cleanup.json`。

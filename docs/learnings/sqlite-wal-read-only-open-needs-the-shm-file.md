# 以 mode=ro 打开 WAL 库，缺 -shm 文件就会失败

- **问题**：拿真实报警库和触价簿的 `.backup` 副本验证补全逻辑，第一次运行时每条报警都返回“不完整、只有触发集合”。之后再跑一次，结果又全对了。
- **死胡同**：一开始以为是新加列或时间窗写错了。直接调用 `_members` 是对的。真正的差别在于：两次运行之间我用 sqlite3 命令行以读写方式碰过一次副本，它顺手生成了 `-shm`/`-wal` 文件。
- **有效路径**：触价簿是 WAL 模式。`file:...?mode=ro` 打开时，如果 `-shm` 不存在又不能创建，SQLite 会报错。代码捕获 `sqlite3.Error` 后退回触发集合，并且不冻结（`final=False`），所以只是暂时少算，不会把错误结果写死。生产环境里写入方是同一个监控进程，`-shm` 一直存在，所以不受影响。
- **通用规则**：只读读取 WAL 库时，读失败必须降级，而且不能把降级结果持久化。拿离线副本验证前，先确认 `-shm` 存在，或者先以读写方式打开一次（只对副本这样做）。“第一次跑结果不对、第二次对了”时，先查文件状态，不要先改逻辑。
- **牵连**：`yoyo/monitor/spike_alert.py` 的 `_members` 和 `_live` 都用 `mode=ro` 读 `monday-fib.sqlite3`。见 [threshold-alerts-must-not-freeze-the-episode-at-the-crossing.md](threshold-alerts-must-not-freeze-the-episode-at-the-crossing.md)。

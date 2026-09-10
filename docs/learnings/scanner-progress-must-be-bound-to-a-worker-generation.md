# 扫描进度必须绑定当前 worker generation

- **问题**：服务 reload 后，SQLite 还保留前一 scanner 的 `scan` 完成计数；新 child 在同步和拉取 universe 前不写 meta，状态页会把旧 `936/1434` 误显示为新实例进度。
- **死胡同**：只用 `scan.started_at_ms` 判断新旧轮次会受持久旧值影响；进程已替换也不能说明数据库计数属于它。
- **有效路径**：父进程在 spawn 前写新的 generation 的 `starting` 元数据，child 在同步前立即用同一 generation、实际 pid 与启动时间覆盖；状态/health 只接受当前 generation，失配时 fail closed 为 `starting/stale_previous_run`。
- **通用规则**：跨进程的持久进度需要 generation 身份和 producer 证据；没有二者，completed 数只能当历史快照。
- **牵连**：`yoyo/monitor/service.py`、`v1_worker.py` 与 focused health/cache tests；不改变 V1 recurrence、信号、cutover、候选或通知。

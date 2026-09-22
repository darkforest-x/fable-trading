# Remote JSON encoding must be explicit

- **问题**：1m import queue 的 batch_27 在 Windows 远端审计阶段报 `AssertionError: remote symbol drift`，尽管远端审计文件中的五个 UTF-8 symbol 与冻结批次逐字相同。
- **死胡同**：把断言当作远端数据或 symbol 发生变化，会导向重拉或删除已完成 archive；UTF-8 读取核对后，compressed audit、source audit SHA 和 run binding 都自洽。
- **有效路径**：审计脚本运行在 CP936 默认编码的 Windows Python 上。显式以 UTF-8 读取 JSON，保留原有 identity、binding 与 source-audit hash 断言，才不会把中文字段错解为不同字符串或因字节组合直接解码失败。
- **通用规则**：跨主机读取 UTF-8 JSON 时，每个语义读取点都指定 `encoding="utf-8"`；不能依赖 Windows 的 locale 默认值。
- **牵连**：`yoyo/data/ma_profit_import_queue.py` 的远端 `run_binding.json` 和 `compressed_audits/*.json` 读取；失败证据在 `experiments/active/exp-ma-profit3r-20260922-v1/import_queue_run_after10_retry2.log`。

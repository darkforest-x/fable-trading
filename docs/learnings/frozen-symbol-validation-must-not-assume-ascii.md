# 冻结市场符号校验不能假设 ASCII

- **问题**：回放图和账本联结只接受 `[A-Z0-9_-]` 合约名，导致已有同源 CSV 与 receipt 的中文热门山寨被误标为冻结 OHLC 缺失。
- **死胡同**：把所有 `ReplayChartUnavailable` 压成 `frozen_ohlc_missing` 会掩盖 provenance/语法边界，随后容易错误地用其他行情替代。
- **有效路径**：允许 Unicode 文件名，但明确拒绝 NUL、`/` 与 `\\`；继续以 resolved parent 等于预期 venue 目录的检查封闭路径。账本 receipt 保留原始不可用错误码，只有实际文件不存在才标 `ohlc_missing`。
- **通用规则**：标识符允许集应来自真实数据域；路径安全由分隔符拒绝和 resolved-parent 封闭共同保证，不能用 ASCII 正则替代。
- **牵连**：`yoyo/monitor/replay_chart.py`、`replay_ledger.py`、三周期冻结 normalized OHLC；仍不允许借其他 venue 或实时行情补图。

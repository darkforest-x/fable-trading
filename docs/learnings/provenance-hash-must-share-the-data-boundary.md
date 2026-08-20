# 数据哈希必须和分析读取共享同一边界

- **问题**：市场 loader 已在 `safe_end` 停止解析，但随后对同一个原始 CSV 做 EOF SHA-256；收益计算没有消费 holdout 行，进程却仍读取了 holdout 字节，破坏“未读取 holdout”的声明。
- **死胡同**：只检查 `holdout_rows_read == 0` 会遗漏旁路读取。文件哈希、行数统计、缓存探测和图表预览都可能绕过主 loader，完整文件哈希不是天然安全的 provenance。
- **有效路径**：先用唯一的 bounded loader 取得前缀，再对该内存前缀做 canonical serialization/hash；报告分别记录“0 行进入评价”和“历史上发生过未授权字节访问”，修复不能擦除事故。
- **通用规则**：任何会打开数据文件的辅助函数都属于数据边界审计范围。声明 zero-holdout 前，搜索并检查 hash、tail、count、preview、cache 与 plotting 路径，而不只看模型/回测 loader。
- **牵连**：`scripts/research_pine_eth_15m.py`、`summary.json`、验证器、artifact manifest、holdout 记账与所有复现命令。

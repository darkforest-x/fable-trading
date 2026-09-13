# 变体拼接账本必须按块检查时间边界

- **问题**：V1/V7 流账本按 variant 依次拼接，每个块内时间升序，但跨块会回到更早时间。把整个文件当成全局时间序列会在第一个块边界报错，或在首个 cutoff 后错误停止并漏掉后续变体的开发期记录。
- **死胡同**：全表 `read_csv` 再排序会先物化受保护结果；全局前缀读取则把文件布局误当成时间布局。缩小窗口也不能修复右端落点和物化边界。
- **有效路径**：只保留 variant、块身份和边界时间标量，验证块不重复、块内时间单调，并扫描至 EOF。交易结果再用第二遍仅物化已通过 entry/exit 双边界的行；跨边界 outcome 即使是无效 UTF-8 也不会送进 CSV parser。逐字节状态机外包一层 `BufferedReader` 只减少 Python 调用次数；逻辑层不解码边界后字段，manifest 也不声称 gzip 没有内部预读。
- **通用规则**：读取历史账本前先审计生成器的排序键和拼接层级。只有生成器明确全局排序时才能在首个 cutoff 停止；分块输出必须用块内前缀合同。
- **牵连**：`yoyo/evaluation/spike_v7_v1_compare.py`、`yoyo/evaluation/spike_market_breadth_study.py`、`tests/evaluation/test_spike_market_breadth_study.py`；开发右开边界为 `2025-09-10T00:00:00Z`。

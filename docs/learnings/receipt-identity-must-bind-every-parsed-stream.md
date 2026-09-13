# 冻结 manifest 不能替代逐份输入收据身份

- **问题**：跨交易所事件研究此前只锁定 replay 的汇总 manifest；单个 signals、trades 或 controls 压缩收据被替换时，即使 stream 数和 V7 总数不变，分析仍会继续。
- **死胡同**：只比对上游 manifest、stream 数和 V7 admission 总数。这些都是汇总不变量，无法表达某一已解析收据的字节身份。
- **有效路径**：为所有实际解析的 streams 收据按 source-relative 路径排序，冻结每份的字节数与 SHA-256，并对整个有序清单再取聚合哈希；运行时先逐项验证清单，再把同一份已校验字节交给 CSV 解析。
- **通用规则**：当实验读取一组冻结分片时，输入契约必须覆盖每一个实际解析的文件及其稳定路径；计数、行数和汇总 manifest 只可作附加完整性检查。
- **牵连**：`yoyo/evaluation/spike_cross_venue_event_study.py`、`experiments/active/exp-spike-cross-venue-events-20260913-v1/input_receipts.json`、正式 `config.json` 与 `results/run_manifest.json`。

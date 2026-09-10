# 覆盖收据必须把损坏源归入终态而不是无限重试

- **问题**：冻结 V1 覆盖评估把一个标为 `complete` 但没有 OHLCV 列的 CSV 传给聚合器，导致 `KeyError` 中断整轮；fetch 循环又只跳过 `complete`，会反复请求已知 gapped/error 的终态源。
- **死胡同**：把空时间轴当作异常会掩盖合法的新上市短历史；只在聚合器里吞掉异常则会丢失是哪份 receipt 与路径造成覆盖缺口。
- **有效路径**：聚合前要求明确的 OHLCV schema；`evaluate-covered` 对读取、schema、聚合和字节 hash 错误 fail-closed 标为 `source_error`，在 coverage detail 保留 receipt 与 source path。`complete/partial/gapped/error` receipt 作为相应 fetch 范围的终态，避免对相同固定窗口反复拉取。
- **通用规则**：覆盖运行器的每一份输入必须归入 evaluated、warmup_insufficient 或可追溯 source failure；错误不能让整个增量重建失败，也不能被无限重试伪装成尚未完成的数据源。
- **牵连**：`yoyo/evaluation/spike_v1_twoyear_allmarkets.py`、`tests/test_spike_v1_twoyear_allmarkets.py`。不改变 V1 条件、入场、止损、成本或任何市场数据。

# Runtime contract tests must migrate with the signal protocol

- **问题**：SPIKE V1 的 YOLO gate 已限制为 live/raw/long/closed 事件，但旧 model-gate fixture 缺 source、confirmation、risk 等字段，并仍参数化空头和 IMACD `md` 失效。
- **死胡同**：为了让旧 fixture 通过而放宽 `is_tv_start` 会让生产入口重新接受当前协议明确排除的事件。
- **有效路径**：只迁移与运行 gate 有关的测试输入到 V1 raw 合同，删去没有 V1 运行语义的 `md` 断言，并以 source、confirmation、direction、side、risk 的 fail-closed 注册测试替代；保留候选到期、端点因果、重启、延迟确认与去重覆盖。
- **通用规则**：协议收缩时，测试应迁移为新入口的正反例；不得把已移除策略条件伪装成新策略的回归要求，也不得通过放宽生产校验留住旧样例。
- **牵连**：`tests/monitor/test_model_gate.py`、`yoyo/monitor/policy.py`、`yoyo/monitor/model_gate.py`；旧 display-only 或日报测试不在这次 focused 迁移范围。

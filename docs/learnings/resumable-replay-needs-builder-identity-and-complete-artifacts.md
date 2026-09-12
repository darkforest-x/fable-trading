# 可续跑回放必须绑定构建身份和完整产物

- **问题**：逐流回放可以在中断后继续，但仅凭 `progress.jsonl` 会把不同配置或不同 builder 的旧结果当作当前结果，也可能把未完全落盘的流跳过。
- **死胡同**：把 progress 追加得更早或更晚都不能证明同目录仍使用相同代码；只检查 completion 文件也不能证明它的 signals、trades 和控制缓存完整。
- **有效路径**：在首次写入前冻结 config SHA、Pine SHA 和五个 builder SHA 为 run identity；每个 completion 记录 identity digest，并同时检查全部逐流必需文件。resume 只信任二者，progress 只保留审计价值。
- **通用规则**：任何允许同一输出目录续跑的研究 builder，先比较不可变运行身份，再把“完成”定义为 receipt 与所有依赖产物共同存在。
- **牵连**：`yoyo/evaluation/spike_v7_v1_compare.py`、`tests/evaluation/test_spike_v7_v1_compare.py`、`run_identity.json`、`completion.json`、`progress.jsonl`。

# Forward provenance 必须从入口注入，不能在扫描中再次发现

- **问题**：forward 入口已经校验了 bundle，但扫描函数又从全局路径重新加载一次；同时 `detected_at` 取批次起始时间，344 个币的候选被写成同一完成时刻。两次发现可能得到不同身份，批次时间也不是候选事实。
- **死胡同**：在每个模块里各自“读取当前配置”。这看似解耦，实际上让一次 pulse 内出现多份可变真相；用一个 scan timestamp 填满所有行则把调度时间冒充了观测时间。
- **有效路径**：生产入口只解析一次 exact bundle，并把不可变 protocol 对象随 `ForwardScanInput` 注入；side、threshold operator、feature semantics、model/detector/dataset hashes 都从该对象传播。候选完成后记录 `detected_at`，L2 gate 完成后另记 `decision_at`。
- **通用规则**：一次业务事件的 provenance 要在边界处冻结并沿调用链传递；下游只能验证，不能重新发现。时间字段必须在对应动作完成处采集。
- **牵连**：`src/judgment/forward.py`、`src/judgment/forward_scan.py`、`src/judgment/forward_types.py`、`src/judgment/forward_records.py`、executor provenance filter；P0 A-01、B-02/B-05、F-05、H-01～H-05。

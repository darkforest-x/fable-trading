# 新增 holdout 消费者时必须同步所有精确集合契约

- **问题**：新实验按 Owner 授权登记了 `holdout_consumed: true`，局部 builder 与边界测试都通过，完整 `pytest tests` 却在两条 registry 守门测试失败；其中一条断言还早已漏掉另一个既存消费者，说明多个精确集合已经漂移。
- **死胡同**：只验证新实验记录能被 schema 读取，或只给其中一条“允许消费者集合”加新 ID，都不够。前者遗漏跨文件的不变量，后者会让另一个守门继续陈述过时事实；把新实验改成 `holdout_consumed: false` 更糟，因为实际已经读取了冻结区，只是在伪造记账。
- **有效路径**：先以 `experiments/registry.yaml` 的真实授权记录为准，搜索全部对 `holdout_consumed` 消费者做精确集合断言的位置，再把每条独立守门同时更新为同一个完整集合，并运行 registry、known-conclusions 与新实验定向测试。这样既保留“新增任何消费者必须显式改测试”的防线，也消除既有集合漂移。
- **通用规则**：任何实验第一次把 `holdout_consumed` 设为 true 时，第一步先运行 `rg -n "holdout_consumed|consumers ==" tests experiments/registry.yaml`；提交前至少跑 `tests/contracts/test_registries.py`、`tests/contracts/test_known_conclusions.py` 和该实验测试，最后再跑 `pytest tests`。
- **牵连**：`experiments/registry.yaml`、`tests/contracts/test_registries.py`、`tests/contracts/test_known_conclusions.py`；外部约束是 Owner 逐配置授权、消费次数永久记账，以及“实际读取不得靠 false 隐藏”的 holdout 铁律。

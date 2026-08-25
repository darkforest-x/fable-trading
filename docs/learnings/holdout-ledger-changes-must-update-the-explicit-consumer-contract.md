# Holdout 账本新增消费者时，显式消费集合也必须同轮更新

- **问题**：一次已获 Owner 授权但 fail-closed 的 4h Top-20 扫描正确登记了
  `holdout_consumed: true`，当前 15m 实验的定向测试也全绿；最后的全仓测试却发现
  `test_every_authorized_holdout_consumer_remains_explicit` 仍只允许旧的三个消费者。

- **死胡同**：把“本轮没读 holdout”理解成“本轮无需检查 holdout 契约”，只运行与新 collector
  直接相关的测试。显式消费者集合是一条跨实验不变量，当前代码是否读 holdout 与它是否会失败无关。

- **有效路径**：先核对新增 registry 行确实有 Owner 授权、消费编号、真实读取量与
  `reuse_allowed: false`，再把该 experiment ID 加进精确集合；不放宽成子集判断或动态读取 registry，
  因为这条测试的价值正是要求每个消费者都由代码审查显式承认。

- **通用规则**：任何提交新增或翻转 `holdout_consumed: true` 时，先搜索
  `test_every_authorized_holdout_consumer_remains_explicit`，同轮更新白名单并跑全仓测试。
  fail-closed 仍然消耗已经读取的 holdout，不能因为没有候选产物就从集合删除。

- **牵连**：`experiments/registry.yaml`、`tests/contracts/test_known_conclusions.py`、
  `exp-btc-4h-ma-launch-similarity-top20-v2`（Owner 授权的配置消费 #2）。

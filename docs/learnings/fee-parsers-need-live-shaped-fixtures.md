# 真实费率必须匹配合约费率组，不能只验证理想化 mock

- **问题**：自由手动预览的离线测试通过，但真实只读预览遇到 OKX 顶层 maker/taker 为空。
- **死胡同**：测试夹具只提供两项数值，掩盖了字段的结算币种语义及废弃状态。直接换一个非空字段，也可能取错合约费率。
- **有效路径**：核对官方 V5 文档和实际只读响应，以 instrument.groupId 匹配 trade-fee.feeGroup，保留返佣符号，开仓预留取非负的最高费类。用顶层空值、组不匹配、返佣的真实形状补回归，再跑 writes=False 的真实预览。
- **通用规则**：涉及真金的外部接口，成功 mock 只能证明内部流程。交付前还需只读核验当前响应结构；字段缺失拒绝执行，不猜费率。
- **牵连**：`yoyo/okx_execution/free_manual.py`、`tests/okx_execution/test_free_manual.py`；[OKX Get fee rates](https://www.okx.com/docs-v5/#trading-account-rest-api-get-fee-rates)。没有发送测试订单。

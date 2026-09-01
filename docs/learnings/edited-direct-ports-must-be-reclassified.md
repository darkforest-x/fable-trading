# 修改过的 DIRECT_PORT 必须重新分类并重录哈希

- **问题**：两个最初逐字节迁入的文件后来在主仓中被有意修改，但迁移账本仍声称它们是 `DIRECT_PORT`，旧 destination SHA 因而同时造成哈希漂移和虚假的“与来源字节相同”声明。
- **死胡同**：只把账本里的 destination SHA 改成新值。这样虽然单文件哈希检查会通过，`DIRECT_PORT` 的 source/destination 相等不变量仍会失败，而且 provenance 语义仍是假的。
- **有效路径**：确认修改来自已提交、可解释且有测试的本仓变更后，把记录改为 `ADAPT_AND_PORT`，同时更新 destination SHA、字节数、记录时间、修改理由和覆盖测试；source repo / commit / path / SHA 继续保留最初来源身份。
- **通用规则**：任何修改迁入资产的提交都要同轮搜索 migration ledger；若字节不再等于来源，必须从 `DIRECT_PORT` 重分类，而不是只刷新哈希。
- **牵连**：`reports/consolidation/migration_ledger.jsonl`、`docs/consolidation/source_asset_registry.json`、`yoyo/contracts/outcomes.py`、`yoyo/layers/l2_judgment/train.py`、`tests/parity/test_migration_ledger_parity.py`。

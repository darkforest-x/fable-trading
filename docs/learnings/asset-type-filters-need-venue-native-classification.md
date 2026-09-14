# 标的类型过滤应按交易所原始分类，而不是跨所套同一 ticker 名单

- **问题**：把股票合约过滤迁移到 V8 时，旧 STOCKISH_BASES 只针对 OKX。若直接跨所复用，OPEN 等加密合约会被当成同名股票，PAXG 等黄金还可能混入股票损失。
- **死胡同**：按常见美股代码或亏损排行榜生成黑名单。它既引入事后选择，也混淆合约标的与代码字符串；同名不是相同资产。
- **有效路径**：先固定交易所 catalog 的内容哈希，以 venue + symbol 联结原始 underlyingType、instCategory、contract_type；股票、指数、黄金、稳定币各自独立。无法确定的分类保留 unknown，不能自动称作加密币或已验证排除。
- **通用规则**：迁移标的过滤时先验身份，不先验收益。当前快照只能支持稳定标的身份假设，不等于历史每个时点都有该分类，也不能据此补齐退市覆盖。
- **牵连**：yoyo/data/universe.py 的 OKX 限定；experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/catalog.json；exp-spike-v8-six-filters-20260914-v1。不改变旧名单或生产规则。

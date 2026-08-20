# 刷新过的 OHLC 文件必须按时间重锚，不能复用旧行号

- **问题**：Gold 事件保留了旧 `source_path` 后缀和 `decision_bar`，但 `data/kline_fetched` 已刷新；相同事件在新文件中的行号可能变化，直接按旧行号重绘会把图画到错误时刻。
- **死胡同**：先拿旧 `decision_bar` 去索引当前 CSV；215 个币种中 213 个出现时间不匹配，本批 2,649 条里有 1,125 条行号偏移非零，最大偏移 24,250。
- **有效路径**：把 `decision_time` 作为跨快照稳定键，每个币种只顺序读取到本批最晚 decision，要求每个目标时间恰好命中，再用当前索引构造因果窗口；目标时间之后和 holdout 一律不读取。
- **通用规则**：只要时间序列文件可能补历史、去重或刷新，第一步先验证“旧索引对应的时间是否仍相同”；不相同就按唯一时间键重锚，旧行号只留作审计字段。
- **牵连**：`yoyo/datasets/fixed_w10_canonical_review.py`、`decision_time`、`decision_bar`、`data/kline_fetched/okx_*_15m_*.csv`、holdout `2026-05-04T00:00:00Z`。

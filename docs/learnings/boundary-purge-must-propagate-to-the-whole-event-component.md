# 边界 purge 必须传播到完整事件连接分量

- **问题**：时间切分先删除了 label interval 穿越边界的行，但同一 `event_group_id` 中落在下一段的邻居仍可能保留，造成表面上时间不重叠、实际事件依赖跨段。
- **死胡同**：只比较已经分配到 train / val / calibration 的 group 集合。穿越边界的桥接行尚未分配，因而不会出现在集合交集中；检查会错误地报告 0 个跨段组。
- **有效路径**：先找出所有未分配的边界穿越行，把它们的 `event_group_id` 标成 tainted，再从所有段删除该组的完整连接分量；嵌套 walk-forward 还要把 inner split 的 tainted group 继续传播到 outer test，最后重新计算 train / early-stop / calibration / test 的两两 group 交集并要求为空。
- **通用规则**：做 interval / event purge 时，第一步不是比较幸存分区，而是把“触边行”向其整个依赖组件、再向所有嵌套分区传播；测试 fixture 必须包含“桥接行被删、同组邻居原本会在下一段或 outer test 幸存”的情形。
- **牵连**：`src/judgment/p2_protocol.py`、`src/judgment/p2_l2.py`、`tests/test_p2_protocol.py`、`tests/test_p2_l2.py`；适用于任何按时间切分的重叠标签、持仓区间、同事件多候选和 connected-component 分组。

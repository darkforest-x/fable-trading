# 旁路模型层必须证明输入血缘与输出等价

- **问题**：一个中间过滤层被判定无效后，“把开关设为关闭”仍不能证明下游没有隐式读取它的分数、阈值或加工 ledger。
- **死胡同**：直接把 factorial 实验里的 `L2-only` 臂改名为新管道；它虽然没有按 L1.5 筛选，但输入文件仍携带 L1.5 字段，血缘上无法排除未来代码误用。
- **有效路径**：从原始 L1 episode ledger 建独立入口，用明确 `usecols` 只读 L1 身份、依赖代表、收益标签和 L2 特征；禁止导入 L1.5 模块，再重训并核对模型哈希、逐事件分数、阈值和入选 ID 全部一致。
- **通用规则**：移除任何模型层时，第一步是建立不经过该层产物的独立数据路径；“零字段读取 + 零模块依赖 + 确定性输出 parity”同时通过，才能称为真正旁路。
- **牵连**：`scripts/research_15m_ma_launch_l1_l2_bypass_l15.py`、`docs/decisions/0003-bypass-l15-until-shape-supervision-is-valid.md`、L1 episode ledger、LONG/SHORT L2 模型；旁路证明不等于经济门或生产门通过。

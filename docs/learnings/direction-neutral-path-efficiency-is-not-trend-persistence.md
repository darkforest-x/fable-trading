# 方向中性的交叉前路径效率不等于交叉后的趋势持续性

- **问题**：V9/V12F 假启动很多，直觉上“交叉前价格越单向、越少折返”似乎越可能成为 runner；固定 32 个变化、右端 `t-1` 的路径效率因此被预注册为高值更好的单一连续特征。
- **死胡同**：把过去路径走得直等同于未来还会延续。在已经经过均线交叉、EMA 方向、斜率和振荡器条件化的池里，同一个高效率值会混合早期延续、成熟衰竭以及与交易 side 相反的单向路径。166 笔账本上 Spearman -0.0047、AUC 0.4991，高十分位反而 -80.05bp/笔（置换 p=0.9671）。
- **有效路径**：先冻结窗口、右端、方向和置换零假设，再只做不改变状态机的单特征排序审计；基础单调性失败后直接淘汰，不运行硬门、不把方向翻转、不扫描其他窗口。完整 335-row surface 只保留为因果接口证据，不把 166 条 executed rows 静态过滤成伪回测。
- **通用规则**：趋势特征进入 gate 或模型前，第一步先问它是在测“过去走得直”还是“未来仍能延续”，并在冻结方向下检查跨时段单调性；若排序接近随机，就停止特征挖掘，不能用事后反向或窗口网格把失败改写成机会。
- **牵连**：`yoyo/layers/l2_judgment/pine_path_efficiency.py`、`scripts/analyze_pine_eth_15m_path_efficiency.py`、`experiments/active/exp-pine-eth-15m-path-efficiency-v1/`；15m、32-change lookback、335 raw candidates、166 V9 on-policy trades、20bp 成本与 P0/P1 training gate。

# 更早的机械入场线只能用于校准，不能直接当金标

- **问题**：形态正例的原检测框普遍开火太晚，需要自动提出更早入场点，又不能让未来数据混入模型输入或把一条机械规则冒充 owner 真值。
- **死胡同**：直接把“框内第一次跌到六条均线下方”写成最终 timing 标签，会继承 v10 已确认下破子群的选择偏差；随机挑 30 张还会被同一行情重复窗和单一迟到程度支配。
- **有效路径**：把机械线明确降级为 calibration proposal；先按 >60 分钟事件去重，再在“剩余不少于已走 / 已走多于剩余”两个未来感知诊断层各取 15 个时序分散事件。人工图可看线后 3 小时，模型图物理截止在线上；只有 owner 的二元复核才能把 proposal 升成 timing 标签。
- **通用规则**：自动锚点涉及未来筛选或上游 detector 框时，先问它是“候选生成器”还是“部署规则”；若是前者，必须保留来源偏差、事件去重、难度分层和独立人工 Gate，绝不能直接扩权训练。
- **牵连**：`scripts/build_eth3m_entry_timing_calibration30.py`、`datasets/eth_3m_entry_timing_calibration30/`、Label Studio 项目 53、固定未来 3h、2026-05-04 holdout 边界；关联 [形态正确的框不能直接充当入场时机标签](shape-valid-boxes-need-separate-entry-timing-supervision.md)。

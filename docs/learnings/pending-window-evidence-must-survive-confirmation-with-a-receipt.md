# 等待窗证据在确认消费后仍须可审计

- **问题**：V6 把方向实体质量作为来源到结构确认之间的可锁定证据；初版在发出确认后立即清空 latch，Data Window 因而把实际确认根显示成“无证据”。
- **死胡同**：只检查信号布尔值和最终清空状态，会把“状态已正确消费”误当成“历史诊断仍正确”。它无法回答确认到底消费了哪根证据，也掩盖了确认与证据是否真的同时满足。
- **有效路径**：将运行中 latch 与每根输出的消费快照分开。确认前先复制 evidence 与 evidenceBar，随后再清运行状态；Data Window 和 Python oracle 在确认根读取消费快照，在等待根读取活跃 latch。
- **通用规则**：任何一次性消费的来源、证据或资格状态，都要同时测试运行清除和消费根审计保留；不可仅凭确认箭头推断其来源。
- **牵连**：`yoyo/evaluation/pine/spike_burst_v6.pine`、`yoyo/evaluation/spike_burst_v6_structure.py`、`tests/test_spike_burst_v6_structure.py`；V6 不改变 V5 风险、显示或交易规则。

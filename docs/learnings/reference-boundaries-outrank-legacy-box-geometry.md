# Reference boundaries outrank legacy box geometry

- **问题**：历史数据保存了Owner画过的5/7根框，于是后续审计把“历史Owner框”误当成当前准确金标；但Owner在ETH参考图上用两条竖线明确指出核心区域后，现有示意框明显向右包入了启动后的快速下跌。
- **死胡同**：只恢复旧框宽、再用窗口位置分布证明数据可用。框是谁画的只能说明来源，不能证明它仍符合最新语义；把结果行情包进红框会让YOLO学习启动后的波动，而不是平台核心。
- **有效路径**：先以Owner最新边界标记冻结核心起止，再把旧框全部降级为geometry proposal。审查按钮同时区分“形态和框都准”“形态像但框要改”“不是目标”，在语义和几何都确认前保持`training_eligible=false`。
- **通用规则**：视觉检测金标至少有两个独立维度：类别语义与边界几何。来源可信不能替代边界复核；最新、明确的Owner边界标记优先于历史标签继承。
- **牵连**：`scripts/build_owner_eth_target_review.py`；`analysis/output/owner_eth_target_review_v2_shortdelay/`；ETH合同图；旧`dense_owner_w20_midbox`框；后续动态短窗重标与YOLO精调。

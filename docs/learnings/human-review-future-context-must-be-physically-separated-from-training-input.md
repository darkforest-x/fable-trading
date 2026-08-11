# 人工审核未来对照必须与训练输入物理隔离

- **问题**：Owner在人工裁决形态时需要看到后续走势，才能判断平台是否真的启动；但模型训练不能把这些未来K线当输入，否则产生前视和视觉泄漏。
- **死胡同**：在同一张训练图上临时追加未来K线，或只在文档里声明“训练时会裁掉”。一旦审核图与训练图共用目录、文件名或manifest，后续构建器很容易误收未来区域，且无法用哈希证明训练输入未变化。
- **有效路径**：冻结训练短窗及其SHA不动，另建`review_future_only/`目录和独立manifest；未来图从同一训练起点延伸，在训练截止位置画明确分界，并写入`future_data_in_training_image=false`与`future_data_in_training_label=false`。生成前后逐张复核训练图SHA，未来目录禁止出现`labels/`。
- **通用规则**：人工可见信息多于模型可见信息时，第一步是拆成两个不可混淆的产物域；审核面可以富信息，训练面必须最小且不可变，二者用路径、manifest、SHA和自动测试共同隔离。
- **牵连**：`scripts/build_owner_eth_shortdelay_review61_gate.py`、`analysis/output/owner_eth_shortdelay_review200_rebox_v1/review_future_only/`、`analysis/html/p1_owner_eth_shortdelay_review61_owner_gate_20260811.html`；受无前视、holdout禁读、逐样本Owner确认与训练资格门约束。

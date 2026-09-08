# Label Studio 连审预框要检查实际下一题路径

- **问题**：同项目保留旧历史预框并追加最新训练框，两个来源的`model_version`不同。
- **死胡同**：看到普通TaskSerializer在空版本时返回所有预测，就认为清空项目版本能支持混合预框。已安装LS1.13.1的NextTaskSerializer改走`get_predictions_for_prelabeling`；空串和None都不给连审返回所需预框。
- **有效路径**：保留旧预测原样，当前队列明确选最新HL2版本并按其protocol过滤。普通详情和实际AsDisplayed连审都需检查，不能只用任务列表接口证明预框能自动带入。恢复旧候选审核时配套切回旧版本和队列。
- **通用规则**：依次验证库存接口、详情接口、下一题接口和真实前端；同一字段在不同路径上的处理不能想当然。过滤队列应使用AsDisplayed，Label All会清掉过滤。
- **牵连**：`yoyo/datasets/label_studio_dataset_union.py`；安装版`tasks/models.py::get_predictions_for_prelabeling`、`tasks/serializers.py::NextTaskSerializer`。此次不改服务端vendor源码或原预测，限制需在交付报告中明确。

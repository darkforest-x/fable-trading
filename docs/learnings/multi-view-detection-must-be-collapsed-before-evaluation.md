# 同一事件的多视图必须在评估前折叠

- **问题**：一个形态渲染 8 个水平位置后，图片数从事件数膨胀近 8 倍；按图片统计 fire rate、bootstrap 或挑最高置信度，会把相关视图当独立样本，甚至让模型用更晚视图重新选择入场。
- **死胡同**：只保证同一事件不跨 train/val，不能解决 split 内事件权重膨胀；只删像素完全相同的图，也不能消除内容高度相关但像素不同的重复视图。
- **有效路径**：训练审计默认一事件一图；若研究必须保留多视图，预测后先按 `event_id` 取最早可见决策，随后才计算命中率、matched control 和 bootstrap，禁止按最高分选择更晚 entry。
- **通用规则**：先确定统计独立单位，再生成增强图；所有评估表都同时报告图片数与唯一事件数，两者不相等时 bootstrap unit 必须是事件。
- **牵连**：dataset manifest、split isolation、proposal collapse、matched controls、confidence selection、置信区间。

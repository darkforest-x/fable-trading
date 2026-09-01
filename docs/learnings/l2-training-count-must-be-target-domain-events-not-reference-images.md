# L2 训练数量必须数目标域事件，不能数参考图片

- **问题**：仓库看起来有上万张形态正负图，L2 却只有 417 个独立训练代表。把 20,000 张参考图联结回 K 线、重新计算 TP5/SL2/72 收益并做完整暴露去重后，虽然得到 13,867 个独立训练块，固定真实 L1 final 上的 28 特征模型反而从 top-decile 净均值 +0.939% 降到 -0.165%，AUC 只有 0.486。
- **死胡同**：把“图片是好/坏形态”理解成“它可直接扩充收益判断层”。形态标签回答 YOLO 应不应该框，收益标签回答真实 L1 提案后是否先 TP；本轮 LONG 正/负形态的 TP 率仅差 +2.42pp，SHORT 甚至差 -1.47pp。参考窗口又占扩充训练代表的 97.18%，因此数量优势放大了来源分布，而不是目标域信息。
- **有效路径**：先把图片、原始窗口、独立事件和真实 L1 提案分开计数；形态字段只保留为审计元数据，逐事件从决策后下一根开盘重算经济标签；最终只在不变的真实 L1 tune/final 上裁决。由此能诚实区分“样本真的不够”和“数据很多但不属于 L2 的目标域”。
- **通用规则**：扩充 L2 前先问“这些行是否由同一个冻结 L1 在相同因果窗口合同下实际提议”。若不是，它们只能作为辅助来源并必须单独做域偏移实验，不能按图片数宣称 L2 样本增长。优先用同一冻结 L1 扩大历史/币种扫描，再按完整输入＋标签暴露合并依赖块，LONG/SHORT 分训，并冻结真实候选 tune/final。
- **牵连**：`scripts/research_15m_ma_launch_l2_reference_augmentation.py`、`scripts/render_15m_ma_launch_l2_reference_augmentation_diagnostics.py`、`experiments/active/exp-15m-ma-launch-l2-reference-augmentation-v1/`、`yoyo/layers/l2_judgment/features.py`、`yoyo/layers/l2_judgment/labeling.py`；固定 TP=5ATR、SL=2ATR、72 根、0.2% 往返成本，禁止读取 holdout 或把形态正负字段作为收益特征。

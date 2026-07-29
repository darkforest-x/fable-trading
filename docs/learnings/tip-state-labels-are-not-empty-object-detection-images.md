# 当前 tip 的二元状态不应被编码成整图空检测标签

- **问题**：ETH 3m v1 把 owner 的“当前 tip 不是”写成 YOLO 整图空标签；但 107 张负图中有 69 张的历史区域包含已知正形态。标签回答的是当前盘口状态，不是整张 200-bar 图中从未出现过目标。
- **死胡同**：继续使用固定右缘正框与整图空负标签。这样一方面把历史真形态监督成背景，另一方面让模型只需学习“最右侧有没有框”，静态 mAP 可以很高，连续盘口却会接近恒开火。
- **有效路径**：把监督目标改成图像级 causal-tip 二分类；训练集只使用 owner 对所展示当前 tip 的明确“是/不是”证据。相邻的 T-1/T+1/T+2/T+3 即使图像很像，也必须留在待复核清单，不能从检测扫描门自动推导标签。历史形态仍可出现在图中，因为分类问题只问当前 tip。
- **通用规则**：先写清标签的空间量词。若真值是“当前时点是否成立”，就用时点分类或显式 ROI 监督；只有当真值是“整张图内是否存在对象”时，空检测标签才表示全图背景。
- **牵连**：`scripts/build_eth3m_short_pilot_dataset_v2.py`、`datasets/eth_3m_short_pilot_v2/`、人工逐 tip 标签、连续盘口 raw-fire 验收，以及 [`static-map-can-hide-sequential-always-fire-collapse.md`](static-map-can-hide-sequential-always-fire-collapse.md)。

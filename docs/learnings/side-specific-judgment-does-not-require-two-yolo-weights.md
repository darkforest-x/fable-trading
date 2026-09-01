# 分方向判断不等于必须拆成两个 YOLO 权重

- **问题**：L2 的多头、空头收益关系明显不同，因此判断层需要分方向训练；容易顺势推断 L1 YOLO 也必须拆成多头、空头两个独立模型。
- **死胡同**：只凭“多空语义不同”直接拆权重，会把每一侧的定位样本量削薄、把一次扫描变成两次推理，并引入两个模型各自的阈值与版本漂移；它没有先回答现有两类模型是否已经学会方向。
- **有效路径**：先核对类别契约和方向零假设。当前单个 YOLO 已用 `dense_long` / `dense_short` 两类联合训练；冻结验证中 LONG/SHORT 图像召回分别为 87.01% / 85.65%，错误方向重叠均为 0，方向翻转后 1,200 张正例命中从 1,035 降为 0。证据支持保留共享定位骨干，同时让 L1.5/L2 按方向采用独立判断函数。
- **通用规则**：先分清“检测类别是否区分方向”和“权重文件是否分开”。只有在冻结、同数据、同阈值口径的单变量对照中，双权重方案对两侧 precision/recall、方向混淆和负样本误报取得稳定增益，才值得承担数据减半、推理翻倍与运维分叉。
- **牵连**：`yoyo/datasets/ma_launch_owner_grade_a8000.py`、`experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1/results/frozen_val_evaluation.json`、`scripts/research_15m_ma_launch_l2_side_split.py`；不涉及 holdout、阈值调整、promote 或部署。

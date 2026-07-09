# YOLO E2.1 训练中期 mAP 振荡

- **问题**：E2.1 收核标签上 yolo11s 训练时，results.csv 显示 ep5 mAP50≈0.66，ep7 崩到 ~0.005 后又部分回升。
- **死胡同**：把中期低点当成最终失败立刻停训或改 conf。
- **有效路径**：依赖 patience + best.pt 保存峰值；最终以 train 结束时 best 与官方 val 为准；新旧标签 mAP 不可直接横比。
- **通用规则**：改 GT 几何后，早期 epoch 波动更大；报告必须区分 interim curve 与 final best。
- **牵连**：`dense_15m_full_s_e21`、`analysis/p2a_e21_train_interim.md`。

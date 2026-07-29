# checkpoint 选优必须对齐真实验收门

- **问题**：ETH 3m 二分类预注册要求 val `TP≥6/8、FP≤2/34`，但 Ultralytics 仍按 top1/fitness 保存 `best.pt` 和早停；在 8 正、34 负的 val 上，全判负类正好得到 80.95%，并被选成 epoch 1 的最佳模型。
- **死胡同**：训练结束后再用混淆矩阵做 fail-fast，只能诚实发现失败，却无法阻止训练器在过程中把“不开火”的 checkpoint 当最优；增加 epoch 或只看 AUC 也不会修正选优目标。
- **有效路径**：先把固定阈值下的 TP/FP 约束或与之等价的 class-balanced metric 接进 checkpoint selection；小样本诊断至少保存逐 epoch 概率/混淆矩阵，且绝不能用二分类恒为 1 的 top5 参与 fitness。
- **通用规则**：任何验收门只要不是普通 accuracy，就必须在训练前追问“best checkpoint 到底按什么保存”；训练选优、早停和最终验收应衡量同一个失败模式。
- **牵连**：`scripts/train_eth3m_short_pilot_v2_cls.py`、Ultralytics classification fitness、val 8 正/34 负、固定 `p=0.50`、预注册 TP/FP gate；关联 [分类准确率必须先和多数类常数预测比较](classification-accuracy-must-be-audited-against-the-majority-class.md)。

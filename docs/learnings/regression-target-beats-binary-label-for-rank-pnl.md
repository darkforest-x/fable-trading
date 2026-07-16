# 回归 realized_ret 比二分类 label 更贴 top-decile 净收益

- **问题**：判断层默认 LightGBM 二分类（TP/非 TP），经济成功标准却是 top-decile 扣费净收益；超参拧不动时还要不要换模型族？
- **死胡同**：加深树 / 放慢 lr / 类别权重 / 近因权重 / 多种子 ensemble / 裁特征——AUC 偶有抖动，跨池 top 净几乎不稳升；YOLO 池 AUC 已 >0.8 仍可能因目标错位浪费排序能力。
- **有效路径**：同一特征、同一切分，只把目标改成回归 `realized_ret`，按预测收益排序；三池（YOLO / 规则 expanded / 纯 SWAP）同一变体夺冠，SWAP 甚至把 top 净从负翻正。
- **通用规则**：先对齐「损失语义 ↔ 交易成功标准」，再堆模型复杂度；本项目优先检验 regression / ranking，而不是换 XGB/ViT。
- **牵连**：`scripts/ml_layer_opt_sweep.py`；报告 `analysis/p2b_ml_layer_opt_summary.md`；升级需影子冻结，分数不再是概率（q90 语义变为 top 预测收益）。

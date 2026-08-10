# Hard-negative 验收集不能参与选模

- **问题**：模型误报挖掘同时覆盖 train/val 时间块时，如果把 val-block hard negative 放进训练器的 `val`，它虽然不参与梯度，却会影响 early stopping 与 best epoch，因而不能再称为独立的误报验收集。
- **死胡同**：把 train/val 两块挖出的所有误报都追加到 YOLO 数据集，看似同时扩大了训练负例和验证负例；但 Ultralytics 会用 `val` 指标决定何时停止及保留哪个 checkpoint，最终模型已针对这把“验收尺”做过选择。
- **有效路径**：只把 train-block hard negative 暴露给 `data.yaml`；把 val-block 误报复制到单独的 evaluation-only bank，既不参与梯度，也不参与 early stopping/best-epoch selection。普通 val 保持和基线完全相同，hard-negative bank 另报误报率。
- **通用规则**：凡声称 held-out 的样本，先画清它是否影响参数、早停、checkpoint 选择或阈值选择；只要影响其中任一项，就不是独立验收集。
- **牵连**：`scripts/build_local_signal_v2_p2_hardneg.py`、`analysis/output/p2_local_signal_v2_hardneg_r1_prereg_20260811.json`、YOLO `data.yaml`、固定阈值 `conf=0.35`、holdout 禁读纪律。

# 错框几何不能当作坏形态标签

- **问题**：同一段行情可能是合格形态，但旧框偏左或偏右；把这种样本加入 bad-reference pool，会让所有候选远离这段行情本身，而不是只远离错误边界。
- **死胡同**：把 semantic reject 与 boundary-wrong 合并成一个负参考池，虽然能扩大“坏样本”数量，却混淆了类别错误和定位错误，并污染距离尺度及对比分数。
- **有效路径**：只有 Owner 明确否定形态语义的样本进入坏形态池；旧框与重框后的同一事件仅做成对边界对照，不参与类别距离、distance scale 或训练标签。
- **通用规则**：先问“错的是对象类别，还是对象边界”；边界控制只能检验定位敏感度，不能自动升级为类别负例。
- **牵连**：`yoyo/datasets/ma_launch_owner_perfect_filter.py`、`owner_reference_geometry.boundary_wrong_reboxed`、contrastive similarity、Gold/negative 资格。

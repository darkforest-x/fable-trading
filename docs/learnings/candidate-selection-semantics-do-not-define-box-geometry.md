# 候选由均线筛出，不代表检测框就在均线上

- **问题**：一批“均线密集启动”候选被正确筛出后，YOLO 框却全部围在 K 线上。候选类别名和筛选特征让人误以为框自然继承了均线语义，但实际监督目标是另一套几何。
- **死胡同**：只看预览图容易归因到画框显示、图像缩放、letterbox 或训练增强；只验证候选筛选使用了 MA 特征，也证明不了 `.txt` 的坐标来自 MA。重新渲染、换 `imgsz` 或同步变换图片与标签都不会修复目标语义。
- **有效路径**：沿 `candidate manifest → geometry assignment → yolo_box_from_core → label txt` 追踪坐标来源，再对全量标签同时重建“核心 K 线 high-low 框”和“六 MA 包络框”。存量标签 9,938/9,938 与前者 IoU≥0.9999，而与后者纵向 IoU 中位数仅 0.152，直接区分了显示问题和标签定义问题。
- **通用规则**：任何检测数据集在训练前都要分别审计三件事：谁决定样本是正类、谁决定框的横向边界、谁决定框的纵向边界。类别筛选、时间锚点和像素几何必须各自有可执行定义，并用反事实框叠加图验证；名字相同不算契约。
- **牵连**：`yoyo/datasets/ma_launch_t3_training.py` 的 `yolo_box_from_core`、`experiments/active/exp-15m-ma-launch-t3-yolo10000-v1/preregistration.json` 的 `positive_geometry`、`yoyo/layers/l2_judgment/pine_dense_start.py` 的 12-bar 密集筛选；另见 [动态重裁剪不能修复标签语义](dynamic-recrop-does-not-repair-label-semantics.md)。

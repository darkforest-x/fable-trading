# YOLO 训练加速：动 I/O 与 early-stop，不动 imgsz/增强

- **问题**：owner 检测器训得慢；想加速又不能掉 F1/mAP 或破坏 K 线语义。
- **死胡同**：降 imgsz、开 mosaic/fliplr、盲目 patience=25 跑满 100 epoch（chain 第 7 轮已 best 仍空转）。
- **有效路径**：`workers↑` + `cache=disk` + `plots=False`；续训 `patience≈10/epochs≈40`，冷启 `patience≈20`；训练与 A/B 扫图不要抢 MPS。
- **通用规则**：加速先动数据管道与停止条件；优化路径（imgsz/aug/模型大小）要单独做精度对照。
- **牵连**：`src/detection/train.py`；queue10/12/14/15；`ab_yolo_vs_rules.sh`

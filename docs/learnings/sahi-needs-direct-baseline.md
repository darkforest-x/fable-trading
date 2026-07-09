# SAHI Needs A Direct Baseline

- **问题**：SAHI 看起来适合小目标检测，但只跑 SAHI 样本会让人误以为它天然提升 YOLO 质量。
- **死胡同**：第一版离线评估只输出 SAHI 的 80 张样本结果；没有同一批图片的 direct YOLO 对照，无法判断新增切片推理到底是增益还是放大误报。
- **有效路径**：在同一脚本里固定验证图抽样 seed，同时跑 direct YOLO 与 SAHI，使用相同权重、相同 conf、相同 IoU50 一对一匹配规则。结果显示 direct YOLO 匹配 77/97、预测 106 框；SAHI 匹配 75/97、预测 178 框。
- **通用规则**：任何推理策略实验必须自带同批 direct baseline；没有 baseline 的单边指标不能进入路线图决策。
- **牵连**：涉及 `output/offline_tasks/run_yolo_tooling_eval.py` 与 `output/offline_tasks/yolo_sahi_direct_comparison_20260710.md`；不涉及 holdout、训练、阈值预设或标注规则修改。

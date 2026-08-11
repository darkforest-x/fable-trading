# 审核标记也必须与原始训练图物理隔离

- **问题**：人工审核既要看到模型预测的橙框，又要保留模型决策当刻的原始因果输入；若两者共用同一图片，审核渲染会永久改变训练像素，未来误收时形成明显的标签提示泄漏。
- **死胡同**：只保存一张带橙框的“因果图”，然后约定训练时忽略框线。这无法靠manifest证明原始像素仍在，训练构建器也可能直接把带框图当负例或正例输入。
- **有效路径**：每个候选同时落三份物理文件：`causal_input/`保存无标记原图，`causal_review/`只保存橙框审核副本，`future_review_only/`保存决策后对照；审核manifest把三条路径和`training_eligible=false`显式写出，目录树禁止生成`labels/`。只有Owner导出的独立裁决才能启动后续训练集构建。
- **通用规则**：凡是给人看的框线、文字、热力图或未来区域，都不应覆盖模型输入。先保存不可变原始像素，再从副本生成审核视图，并用路径、资格字段和目录级检查阻止审核产物被训练脚本误收。
- **牵连**：`scripts/build_owner_short_hardneg_canary_review.py`、`analysis/output/owner_short_gold_center_hardneg_canary_review331_v1/`、`analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_20260811.html`；延伸自[人工审核未来对照必须与训练输入物理隔离](human-review-future-context-must-be-physically-separated-from-training-input.md)。

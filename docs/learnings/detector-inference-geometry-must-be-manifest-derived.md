# 推理几何必须从该权重的训练 manifest 反推

- **问题**：替换 YOLO 权重后，扫描器仍需要窗口长度、核心根数和确认根数才能生成模型输入与把框映射回 K 线；这些参数不是通用的“项目配置”，而是每个权重自己的训练合同。
- **死胡同**：沿用上一版探针的窗口/核心/确认参数，或只读候选源上的 `post_core_context_bars`。代码可以正常推理并出图，但动态重裁剪数据集的实际输入可能另有 `pre_bars/post_bars`；把候选源上下文当成最终图片几何，会把新权重放进训练时未见过的窗口并错误解释框。运行成功不能证明输入语义正确。
- **有效路径**：在读取 holdout 前逐行扫描该权重训练 manifest 的正例，优先从最终模型输入的 `window_bars`、`core_bars`、`post_bars` 反推支持集；旧 manifest 只有在 schema 证明没有动态重裁剪时才回退到 `window_start_i/window_end_i` 与 `post_core_context_bars`。同时锁定 manifest SHA、正例数和各档计数，让预注册、扫描器和 verifier 只接受同一支持集。旧模型是 W18–25/core4–5/post4–6；Grade-A 动态重裁剪模型则是 W18/W19/core4–5/post2–9。
- **通用规则**：更换检测权重时，第一步不是复制旧推理参数，而是用该权重对应的不可变训练 manifest 生成并验证推理几何合同；没有 manifest 或身份哈希对不上，就不要运行正式探针。
- **牵连**：`datasets/*/manifest.jsonl`、`window_bars/pre_bars/core_bars/post_bars`、推理预注册、窗口生成、框坐标回映、事件去重、结果 verifier；也关系到 holdout 单次消费纪律，因为用错几何后再重跑会额外消耗数据。

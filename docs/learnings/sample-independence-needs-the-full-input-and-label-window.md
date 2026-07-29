# 样本独立性必须按完整输入窗加标签窗计算

- **问题**：ETH 3m v2 报告有 137 张、71 个 global events、29 个正事件，但模型每张图看
  `[T-199,T]`，人工标签还看 `[T+1,T+60]`。按完整 `[T-199,T+60]` 区间合并后，全部只剩
  32 个时间依赖块，30 张正图只剩 23 块，val 8 张正图只剩 5 块。
- **死胡同**：只按框区间或未来 label horizon 合并事件，再把事件数称为独立样本。这个口径可以
  防一部分重复，却会漏掉共享大量模型输入的样本，并让多图行情块在训练中获得更高权重。
- **有效路径**：把每行表示成完整暴露区间 `[input_start_time,label_end_time]`，对重叠区间求连通
  分量；切分、样本量报告、val 配额和 sampler 权重全部使用同一个 `dependency_block_id`。原有
  378-bar split embargo 仍证明无跨 split 泄漏，但不能把块内多图当统计独立。
- **通用规则**：任何滑动窗口监督学习先定义“模型看到的最早时间”到“标签用到的最晚时间”；
  区间重叠即同一依赖块。报告必须同时列图片数和依赖块数，每块训练总权重相同，验收集按正/负
  依赖块设最低数量。
- **牵连**：`WINDOW=200`、`FUTURE_BARS=60`、260-bar embargo、
  `src/detection/eth3m_v2_quality_audit.py`、event-balanced sampler、walk-forward 时间切分；关联
  [锚点时间分开不代表滑动窗口输入已经分开](anchor-time-splits-need-full-window-embargo.md)。

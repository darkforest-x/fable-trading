# L2 时间隔离要覆盖完整输入窗和标签窗

- **问题**：L2 使用 168 根历史特征并用未来 72 根 K 打标签；只留 72 根（18 小时）purge 虽能阻止标签跨界，却仍让相邻 split 的 168 根输入大量重叠，把共享行情误当成独立时间外证据。
- **死胡同**：把 purge 等同于 label horizon，并仅按 L1 局部框或 episode 去重。它没有覆盖 L2 真正看到的 42 小时历史，也没有处理多个事件的完整暴露区间经重叠形成的传递依赖。
- **有效路径**：在产生任何 L2 outcome/score 前，把每个事件表示成 `[available_at-42h, available_at+18h)`；split 间 embargo 固定为 60 小时，同币重叠区间求连通分量，训练、早停、阈值选择、最终指标和 matched control 只使用每块最早的因果事件。后续重叠事件仍可评分和渲染，但不得重复计作独立证据。
- **通用规则**：滑动窗口模型的 purge 第一项先算“最早输入到最晚标签”的完整跨度；事件去重必须对这个完整半开区间求传递闭包，而不是只看锚点间距或未来 horizon。
- **牵连**：`exp-15m-ma-launch-l2-global-context-v1` 的 60h split、`dependency_block_id`、LightGBM fit/tune/final 行、matched controls、permutation 样本量与报告中的事件数/独立块数。

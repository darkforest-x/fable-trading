# 时序任务包的有限值校验必须落在实际引用切片上

- **问题**：跨机器推理任务包保存了带因果 SMA/EMA 的整段上下文；最长 SMA 的开头 warmup 行按定义是 NaN，但所有真正送入模型的 W18 已经过 140 根连续历史门且完全有限。把两者混为一谈会让合法任务包在推理前失败。
- **死胡同**：对整个 symbol frame 直接执行 `isfinite().all()`。这看似更严格，实际把“未被任何任务引用的合法 warmup 缺值”误判成“模型输入缺值”，无法区分存储上下文与消费切片的契约。
- **有效路径**：frame 层只禁止无论何处都不应出现的 `inf`；task 层再逐个检查实际 `[window_start_i, window_end_i]` 恰为 18 行且全部有限。远端渲染后仍由 Mac 对每个有框任务重画，并要求 BGR 像素 SHA 与 `ChartTransform` 完全一致。
- **通用规则**：共享时序上下文时，完整性断言必须贴着真正的消费切片；先允许契约内 warmup，再对每个引用窗口 fail-closed，不能用整表有限值检查代替输入验证。
- **牵连**：`scripts/mine_15m_ma_launch_grade_a_daily_movers_5000.py`、`scripts/remote_infer_15m_ma_launch_grade_a_taskpack.py`、SMA120 warmup、W18/140-bar 历史门、Mac/3060 像素 parity；不改变标签、模型阈值、holdout 或训练资格。

# 锚点时间分开不代表滑动窗口输入已经分开

- **问题**：ETH 3m pilot v1 按事件保证了 train 最大锚点早于 val 最小锚点，相关测试也通过；
  但两者只相隔 178 根 3m bar，而每张图片使用 200 根，导致边界处一对 train/val 图片共享 22 根原始 K 线。
- **死胡同**：只比较 `train.anchor_time.max() < val.anchor_time.min()`，并检查同一 `event_id`
  不跨 split。这个判据只能证明标签锚点有序，不能证明模型实际看到的输入像素没有跨切点。
- **有效路径**：把每个样本表示为完整输入区间 `[anchor-window+1, anchor]`，直接检查任意跨 split
  区间是否相交；若图像来自同一 OHLC 序列，还应报告重叠 pair 数、受影响样本数和最大共享 bars。
  本次独立重算得到 1 个跨 split pair、各影响 1 张图片、最大共享 22/200 bars。
- **通用规则**：时序图像切分必须在锚点之间加入至少一个完整输入窗口的 embargo：
  `first_val_anchor - last_train_anchor >= window_bars`。如果标签还使用未来 `horizon_bars`，
  embargo 还必须覆盖标签 horizon；测试要检查完整输入区间，而不只是锚点时间和事件 ID。
- **牵连**：`scripts/build_eth3m_short_pilot_dataset.py::assign_event_split`、
  `tests/test_build_eth3m_short_pilot_dataset.py::test_event_split_is_chronological_and_never_crosses_event`、
  `WINDOW=200`、判断层未来 3h 标签窗口、所有滑动窗口图像数据集。

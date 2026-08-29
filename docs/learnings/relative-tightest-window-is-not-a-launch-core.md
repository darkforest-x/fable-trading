# 相对最窄窗口不是均线密集启动核心

- **问题**：在每个弱候选的 `t-12..t-1` 中强制挑选六均线价格包络最小的 5 根，会把长期平行、平坦或已经离启动很远的片段也画成正例；框的几何测试可以全部通过，但 Owner 看到的形态与时效仍然错误。
- **死胡同**：把“每张图内部总有一个最小值”当成“每张图都有密集核心”，再靠固定根数、全影线包围和事后左右平移修框。相对 argmin 没有绝对拒绝门，也不检查均线收敛/交织、启动边界或到输入右端的延迟，所以只会稳定地产生错误框。
- **有效路径**：先定位逐样本启动首根，把核心右端锚在启动前一根；只在紧邻该边界的 4--7 根中检查绝对紧密度、均线收敛或次序交叉、价格压缩与局部唯一性。任一硬门不通过就标 `REJECT/IGNORE`，不能强迫产生框。研究确认 K 与模型输入物理分离；实盘候选还必须满足核心右端在 tip/tip-1/tip-2。
- **通用规则**：任何“每个候选必选一个最优窗口”的标注器，第一项 QA 都应是空结果能力和时延分布；相对排名只能排序已通过语义硬门的窗口，不能代替正类资格。
- **牵连**：`yoyo/datasets/ma_launch_ma_box_review.py::select_tightest_span`、`yoyo/datasets/ma_launch_density_core_box_review.py`、`experiments/active/exp-15m-ma-launch-density-core-box-review50-v3/`、`docs/protocol/local_signal_v2.md`、[逐图启动边界规则](per-image-reboxing-needs-indexed-boundaries-not-global-offsets.md)。

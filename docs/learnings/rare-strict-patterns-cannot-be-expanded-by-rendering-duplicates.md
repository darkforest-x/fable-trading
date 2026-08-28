# 稀有严格形态不能靠重复渲染扩成大数据集

- **问题**：10,000 个 weak-positive 经冻结完美形态门只剩 260 个独立事件，但训练直觉希望得到 2,000–3,000 张。
- **死胡同**：放宽门、同一事件换上下文重复渲染、缩短去重间隔或做方向镜像都能增加“图片数”，却没有增加独立时序证据，还会把位置和重复事件捷径教给模型。
- **有效路径**：先用已登记的 scan receipt 计算独立事件母池上限，再决定是否值得对新增 pre-holdout 数据做正式预注册扩容；本轮原母池 1 小时 NMS 后仅 11,381 个且已有 10,000 个，因此即使余下 1,381 个全部通过完美门也达不到 2,000 个，目标数量不能在标准不变时硬凑。
- **通用规则**：承诺样本数量前，先报告“独立事件母池上限 × 冻结门通过率”；渲染张数、滑窗数和增强副本永远不能替代独立事件数。
- **牵连**：`experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/results/scan_receipt.json`、`experiments/active/exp-15m-ma-launch-owner-perfect-filter10000-v1/results/summary.json`、4 小时事件 NMS、pre-holdout 数据边界。

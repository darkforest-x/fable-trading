# 算力回退必须同步降低证据等级

- **问题**：语义发现原计划从全新 pre-holdout 时间块重扫候选，但局域网 3060 不可达，Mac MPS 对完整 W12–19 暴露窗的推理速度无法在合理时间内完成。
- **死胡同**：先生成八块因果/未来快照，再尝试 MPS batch 32；进度慢到不足以支撑当轮交付。把 batch 提到 128 不但没有解决吞吐，还触发 Ultralytics NMS time-limit warning。快照存在不等于已有可审事件，不能拿未完成扫描伪装成新时间块审核包。
- **有效路径**：先盘点两个冻结候选池，按 event_id 精确减去四轮 700 个已审核事件，确认仍有 784 个从未审核、从未进入训练的候选；再从中构建 300 张主动语义发现集。同时把证据声明从“新时间块独立验证”明确降为“旧 train-time 块未审余量的主动发现”，保留 0 holdout、0未来检索和盲审合同。
- **通用规则**：外部算力失败时，先区分“样本是否新”“时间块是否独立”“是否能估计总体 precision”三个不同轴；可以换可复现的数据来源，但必须同步降低结论等级，并在 manifest、报告与停止条件中写明，不能只改实现不改证据措辞。
- **牵连**：`scripts/build_local_signal_v2_early_frontier_review.py`、`analysis/output/local_signal_v2_early_frontier_review300_v1/`、W12–19、conf 0.25、NMS 0.70、holdout 起点 2026-05-04。

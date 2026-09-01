# 完成态 YOLO 要用训练语义的因果前缀复核

- **问题**：YOLO 能在自家验证集获得较高命中率，却会把均线稀疏、K 线远离均线的框当成“密集启动”；只按框横坐标映射 core4/5 + post2–9，等于允许模型用像素捷径代替正例生成语义。
- **死胡同**：一是继续相信置信度或 mAP 会自动约束六均线密集度；它们只衡量模型相对标签的检测表现。二是把完整 post1/post2/post3/post5 门一次性套到所有窗口；post2 输入若读取 post3/post5 就重新引入前视。三是看完 4h 结果再发明框高或均线阈值；那是在 holdout 上调参。
- **有效路径**：保留原权重作 proposal 层，在相同原始框后增加数值语义门；阈值逐项复用训练正例生成合同。core 密集、K/MA 距离、core 方向、post1/post2 始终检查，post3/post5 只有在窗口端已经可见时才检查。先在同一批 pre-holdout 图片和同一原始预测上做严格配对 A/B。该做法把空标签结构框从 35 降到 10（-71.4%），正例事件命中从 142 降到 141（保留 99.3%）。
- **通用规则**：检测器负责“在哪里可能有”，业务语义负责“它是否仍属于训练定义”。后处理必须复用**检测时已经可见**的训练谓词；未来才可见的谓词应条件缺席，而不是从完整历史文件偷偷补齐。任何新阈值先在 pre-holdout 预注册，不能用目标周期 holdout 反调。
- **牵连**：`yoyo/layers/l1_detection/semantic_gate.py`、`scripts/evaluate_15m_ma_launch_owner_yolo_semantic_gate.py`、`exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1`；冻结阈值来自 `exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json`。框纵向覆盖率在 v1 仅记录、不放行。参见 [几何后处理不能保留训练语义](geometry-only-postfilter-does-not-preserve-training-semantics.md) 与 [跨周期提案要重验语义](cross-timeframe-yolo-proposals-need-semantic-revalidation.md)。

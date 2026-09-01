# 检测输入完全同源，不代表它能预测未来收益

- **问题**：L2 改为逐像素复用 L1 的 18/19 根 1280×742 原图和原框后，抽样形态看起来合理，但收益回归在最终 pre-holdout 验证上仍失败；容易误以为“图没问题”就应该能筛出更赚钱的信号。
- **死胡同**：继续加训练轮数、因为 SHORT 子组暂时为正就事后删掉 LONG，或把局部形态好看当作经济标签正确。这些做法要么无法给弱信号创造信息，要么重复使用已经看过的 final，且混淆了 L1 的形态真值与 L2 的未来收益真值。
- **有效路径**：先证明输入契约真的相同（全部候选像素校验、同一原框、零未来 K），再用独立时间段、净收益、置换检验和匹配随机对照裁决经济目标。结果可同时成立：L1 正确找到了目标局部形态，而同一小窗对未来 TP/SL 路径没有稳定排序信息。
- **通用规则**：上游 detector 与下游经济判断层即使共享完全相同的输入，也必须分别定义真值和验收门；“输入 parity 通过”只排除数据错位，不能代替下游目标验证。若 final 失败，新的表征或目标必须另立预注册并使用新的未见时间段。
- **牵连**：`yoyo/layers/l2_judgment/short_window_features.py`、`scripts/research_15m_ma_launch_l2_short_window_side_split.py`、`analysis/p3_15m_ma_launch_l2_short_window_side_split_20260901.md`；与[形态正确的框不能直接充当入场时机标签](shape-valid-boxes-need-separate-entry-timing-supervision.md)共同约束 L1、L1.5 与 L2 的职责边界。

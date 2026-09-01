# L2 排序器必须证明相对 L1 的增量经济价值

- **问题**：冻结 L1 候选池在独立时间块上扣成本后仍有 `+7.35 bp`，L2 却同时出现看似有信息的 `AUC=0.5646`、`Spearman=0.1652` 和肉眼可分的 KEEP/REJECT 图。若只看这些诊断，很容易误判为“可以开启 L2”。
- **死胡同**：把 AUC、相关性或图像分组观感当成启用依据，或者在 final validation 上继续移动分数阈值。它们都没有回答 L2 是否比不筛选的 L1 更赚钱；事后调阈值还会把最终验证集变成调参集。
- **有效路径**：先在 tune 段冻结 q90 门，再只在完整暴露依赖块的 final validation 首个事件上比较同一批 L1、L2 top-decile、冻结门和同币同时间同波动匹配对照。结果显示 top-decile 净收益 `-15.0 bp`、冻结门 `+3.4 bp`，均没有改善 L1 的 `+7.35 bp`，置换检验 `p=0.656434`，因此保留 L1、不启用本 L2。
- **通用规则**：判断层的成功标准是“在相同信号时钟、成本、暴露隔离和候选池上稳定增加 L1 的净价值”，不是单独的 AUC、相关性或视觉可解释性；冻结门未增益就记录负结果，禁止 threshold shopping。
- **牵连**：`scripts/research_15m_ma_launch_l2_global_context.py`、`scripts/build_15m_ma_launch_l2_global_context_report.py`、`experiments/active/exp-15m-ma-launch-l2-global-context-v1/`、`analysis/p3_15m_ma_launch_l2_global_context_20260901.md`；约束包括 60 小时完整暴露 purge、dependency-block 首事件、0.2% 往返成本、8 组匹配随机对照和未授权 holdout 禁读。

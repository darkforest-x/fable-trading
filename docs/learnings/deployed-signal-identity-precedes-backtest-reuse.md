# 复用回测前，先沿运行入口确认信号与退出的真实身份

- **问题**：Owner 要给“突破＋SPIKE”加 BTC 全局均线门。仓内同时存在普通 V9、V10.4 六根窗口联合、V11 框内联合、V11.2 支撑门研究以及已加入 RSI7 的页面，版本标题不能唯一确定基线。
- **死胡同**：看到近期 SPIKE 报告就复用其汇总收益，会把普通 SPIKE、上级方向门或支撑门的效果混进 BTC 门；只从旧成交表筛选又会丢掉原先被占仓挡住的候选。若仅新门使用当前 RSI 退出，改善还会混入第二个变量。本轮在回放前排除了这些路径，未把它们当作实验结果。
- **有效路径**：从 `spike_lines_worker` 到 `spike_lines.analyze` 再到 `positions` 跟踪实际调用，确认当前入场是 `box_any`，没有研究版 `box_support`，退出已采用每笔入场起计的 RSI7。用已保存的完整 9301 个 box_any 候选，分别在原退出与 RSI7 退出两组内比较全局门，每组独立重放串行占用；旧 price/none 先逐笔对齐原账本。
- **通用规则**：策略名字不是执行契约。开始新单变量研究，先冻结“候选生成、决策时间、占仓、成交、退出、成本、数据源”七项身份；有历史与当前两个退出版本时分层比较，不能把变化合并归因。
- **牵连**：`yoyo/monitor/spike_lines.py`；`yoyo/evaluation/spike_joint_rsi_exit.py`；`yoyo/evaluation/spike_v112_support_study.py`；`experiments/active/exp-spike-joint-btc-gate-20260922-v1/PROJECT_PLAN.md`。本笔记只记录基线身份核查，不代表 BTC 过滤改善收益。

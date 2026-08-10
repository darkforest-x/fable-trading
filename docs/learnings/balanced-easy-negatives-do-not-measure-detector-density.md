# 平衡 easy-negative 验证尺不能代表检测器自然触发密度

- **问题**：B2 在 358 正例 + 357 easy negatives 的平衡事件尺上通过 Precision/Recall/FP 门，随后却在已经预筛的 v10 proposal ledger 中命中 3,880/7,795 行。报告一度把这些 L1 fire rows 误写成交易/订单，并据此跳向判断层。
- **死胡同**：把平衡抽样尺的 FP/1000 当作线上密度，把 proposal row 当作自然市场窗口，再把 detector fire 当作可执行订单。三次粒度跃迁都缺少分母；继续抬 conf 虽能压数量，却会把验证召回从 73.46% 压到 6.98%，只是把问题藏起来。
- **有效路径**：先逐层对齐 grain：P1 endpoint、预筛 proposal row、L1 fire row、event group、P3 order；再分别核算 easy-negative 命中率与 proposal-pool 命中率，并用唯一 ID、最小间隔、edge2/edge3 一致性、数组/PNG 推理一致性排除计数和传输 bug。最后用阈值梯度同时观察密度与召回，确认根因是负例分布不足而不是阈值。
- **通用规则**：检测器进入下一层前，必须在与真实入口同基率的连续因果 endpoint 流上冻结并报告 fires/time、event precision、去重后事件数；平衡验证集只回答区分能力，不能回答线上密度。任何数量先写清 grain，未经过判断与执行的 fire 永远不叫订单。
- **牵连**：`scripts/audit_local_signal_v2_b2_density.py`、`scripts/build_p1_b2_short_l2_backtest_report.py`、`analysis/output/p1_b2_density_diagnostic_20260811.json`、P2 hard-negative mining、连续 causal-tip 密度回放、P3 阻断；holdout 仍不得读取，conf 不得事后修改。

# 指标简称相同不能证明备份源码属于同一版本

- **问题**：TradingView 原始码入口受方案限制后，在 Notion 找到同日编辑、同简称 Stoch 的代码，需要判断能否用于“翻身V1”的退出回测。
- **死胡同**：按显示名和编辑日期直接套用旧 Stoch，或只把参数14／1／3改成5／3／3。显示简称不是唯一身份，日期也不证明代码相同。
- **有效路径**：核对 TV 原生设置的输入字段与样式输出。目标包含做多、做空和Williams Vix Fix，候选代码只有超卖金叉买入；结构性差异足以拒绝等价。保留原文及来源，不补造缺失逻辑。
- **通用规则**：私有指标备份先核对组件、输入、输出和信号时间，再核对参数值。完整源码未取得时，相关旧版只能标为候选，检索无结果也不能宣称绝对不存在。
- **牵连**：`analysis/p1_spike_fanshen_source_audit_20260914.md`；`experiments/active/exp-spike-fanshen-source-audit-20260914-v1/source/stoch_with_signal_alert_notion_candidate.pine`；Notion bonk 1h 页面；后续六周期退出实验。


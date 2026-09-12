# 只筛开发段汇总，不等于隔离了验证结果

- **问题**：V8 的候选表只按 development 行汇总，但上游发现文件先合并了完整交易账本，因此 validation 行仍携带 `net_return`、`net_r`、`mfe_r` 和退出原因。报告据此误写成“冻结前未读取验证结果”。
- **死胡同**：在同一个完整 DataFrame 里先读入所有时期，再靠 `period == development` 过滤统计。这样能让最终表格看起来只含开发段，却不能阻止研究程序、缓存或操作者访问验证结果；事后把 validation 列清空也无法恢复盲性。
- **有效路径**：把物理输入边界作为门禁。发现程序只接受独立的 `baseline_trades_development` 文件，并在任何特征收集前拒绝缺失该输入或含非 development 行的账本；输出 validation 特征行时所有 outcome 列保持空值，manifest 同时声明 `validation_outcomes_present=false`。已污染的历史产物保持不可变，以哈希绑定的 erratum 降级为非盲描述性证据。
- **通用规则**：验证隔离要检查“进程能读到什么文件、输出文件保存了什么列”，不能只检查最后的 groupby 或报告筛选条件。发现产物一旦物理携带验证 outcome，就算没有公开汇总，也按验证暴露处理。
- **牵连**：`yoyo/evaluation/spike_v8_noise_study.py`、`yoyo/evaluation/spike_v8_report.py`、`experiments/active/exp-spike-v8-noise-filter-20260913-v1/discovery_isolation_erratum.json`；当前 V8 需要新的前向影子样本，不能用本轮验证结果晋级生产。

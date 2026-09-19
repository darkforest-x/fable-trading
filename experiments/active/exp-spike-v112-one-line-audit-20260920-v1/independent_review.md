# Luna Max 有界独立复核

2026-09-20，`/root/v112_1h_evidence`，GPT-5.6 Luna / max。只读规则审查及冻结输出核对，无市场引擎重跑、无策略编辑。

源码确认：程序要求三点确认，每点left12/right8；相邻min_gap24、跨度72；双轨独立找点。V11.2默认多头框内规则不再要求旧6根配对，但必须trendSide==1。Pine的当前/最近面板不能代替历史逐根轨迹。

实际证据独立核对：

- C9/16 19:30北京index1189，high0.0006486；B17:45 index1182，high0.0006518，差7<24。
- C之前12根index1177–1188包含B，raw最高为0.0006518；soft=max(open,close)加0.6ATR后与high取小，左窗最高仍是B的0.0006518。因此两轨C都不是pivot。
- `15m_pivots.csv`中B有raw/soft两条记录，20:00收盘才确认；C没有记录。
- 9/17 08:00/09:00北京两行`long_open=False`、`box_entry_i=-1`、`v9=False/v9_long=False`。15m的break_event=False仅指本周期，不否定1h两次突破。
- summary确认截图游标四价匹配；该三日诊断窗无15m V9/突破，无gap/零量；1h08:00/09:00突破。

审查过程保留：第一次提取错误地按不存在的symbol字段/字符串筛选，未能验证C和多头框，彼时明确不作独立核对声明；第二次按bar_bj精确定位后得到上述数值。没有将失败提取冒充证据。

边界：没有读取TV实际参数或导出全历史，不保证不同数据源/设置全面parity。高点是严格更高，C失败不依赖pivot tie规则。

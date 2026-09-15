# 复刻图表策略时，K线染色与副图信号必须分别追到源码

- **问题**：Owner以15m MA Shift的K线颜色限制5m Stoch箭头方向；指标同时存在均线、四态振荡柱、箭头和报警，名称无法说明实际交易条件。
- **死胡同**：仅凭MA Shift振荡器颜色说明或常见K/D交叉语义推断策略，会把15/0.5振荡器条件加到K线颜色，或把普通交叉、WVF过滤报警与超买超卖箭头混在一起。它们实际是不同的布尔流。
- **有效路径**：从当前TradingView输入设置核实SMA40/hl2与K5/3/3，再在Pine编辑器分别追踪barcolor、plotcandle、plotshape和alertcondition。MA颜色由hl2相对SMA决定；Stoch箭头要求交叉当根K/D同时严格低于20或高于80，多头报警另加WVF。聚合15m的结果必须以收盘时钟映射到5m。
- **通用规则**：用户指向哪种视觉对象，就追踪生成该对象的变量；参数数量、图例文字和同名指标不能替代源码身份。多周期信号先证明可见时钟，再看收益。
- **牵连**：`experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/PROJECT_PLAN.md`；`yoyo/evaluation/spike_fanshen_exit.py`；当前TV公开ChartPrime源码与私有翻身V1保存版。仅确认公式及设置，不宣称全部历史TV/Python逐bar数值已认证。

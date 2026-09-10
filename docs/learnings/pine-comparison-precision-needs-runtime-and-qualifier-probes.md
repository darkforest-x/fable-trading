# Pine 比较精度必须按运行时和 qualifier 实测

- **问题**：将 Pine 移植到 Python 后，需要确认微价币的近零、均线交叉及风险门是否保持相同语义。官方类型文档描述 float 比较按9位小数处理。
- **死胡同**：审查据此把所有比较统一 round9。实际原生测试反而推翻该方案：动态 series 的约0.4899e-9仍大于0，而 input.float(1e-10) 与 simple syminfo.mintick/100 在比较时等于0。
- **有效路径**：先用17项常量/series探针发现矛盾，再用真实close参与的非恒定series排除常量折叠，最后用 input、simple、series 三类值进入同一个函数验证。保留源码、截图与限定语义，当前重放保留行情series原比较，只对已证实的simple步长门作对应处理。
- **通用规则**：官方文档是起点；当可重复原生证据矛盾时，不强迫实际运行符合概括说明。用相同qualifier、相同函数边界构造最小探针，再决定移植行为；不要把价格缩放不变性当作语言规范。
- **牵连**：`experiments/active/exp-spike-burst-validation-20260910-v1/qa/`、`spike_burst_replay.py`。本次检查在任何收益评分前完成，不修改冻结 Pine。官方参考：https://www.tradingview.com/pine-script-docs/language/type-system/ 。该发现不宣称所有Pine表达式均已覆盖。

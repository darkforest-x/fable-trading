# Entry filters cannot suppress a shared reversal exit

- **问题**：SPIKE V6 的旧 ETH Freqtrade bridge 只执行预生成止损，关闭 exit signal 且没有 exit trend；若直接复用它，WVF 被拒的反向信号会悄悄不再退出既有仓位。
- **死胡同**：把“WVF 过滤信号”简化成先删掉信号表，再回放交易。这样新入场和旧仓退出共用同一被删事件，比较的是不同出场规则而不是单变量入场过滤。
- **有效路径**：保留完整原始 V6 信号账本；WVF 只决定新开仓。无论筛选状态，反向 V6 确认都在下一可交易 open 平旧仓。开盘已越过旧保护价时先按 gap stop 结算，同根同向信号不重入。
- **通用规则**：给已有策略增加入场过滤前，先列出每个原事件的入场、出场和状态机职责；任何同时承担退出职责的事件必须从过滤输入中独立保留。
- **牵连**：`yoyo/evaluation/spike_v6_wvf_study.py`、`tests/test_spike_v6_wvf_study.py`、`yoyo/evaluation/pine/spike_burst_v6.pine`、旧 `FrozenV6Bridge.py`；固定执行口径为 next-open、0.2% round trip、五根初损与 4 ATR 跟踪。

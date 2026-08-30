# 收盘入场的 barrier 标签必须从下一根 bar 开始

- **问题**：图像在某根 K 线收完后决策，并以该根 close 入场，却把同一根已经发生过的 high/low 纳入 TP/SL 判定；这会让标签拥有入场前的盘中信息。
- **死胡同**：只把 `entry_price` 改成 decision close，或只缩短渲染窗口，都不能修复 outcome slice 仍从 decision bar 开始的问题；价格写对了不等于时间顺序正确。
- **有效路径**：把“decision close 入场、`decision_i + 1` 开始判 barrier”封装成一个共享 resolver，让标签生成和经济评估只能调用同一入口，并用 decision bar 极值很大但下一根不触发的反例测试钉死边界。
- **通用规则**：任何 close-entry 策略先画事件时间线；如果入场发生在 bar close，该 bar 的 high/low 一律是历史，首次可成交 outcome bar 是下一根。
- **牵连**：`yoyo/contracts/outcomes.py`、`yoyo/datasets/ma_launch_5m_causal.py`、标签 horizon、exit timestamp、matched control。

# 原版 Pine 复现必须保留订单创建时钟

- **问题**：Owner 要复测 ALLIN V7 的 ETH 4H 表现，仓库已有同名回测器，但它的文档明确采用修正版执行口径。
- **死胡同**：直接将旧 15m 回测器的输入重采样为 4H，会让原本到入场 bar 收盘才创建的止损提前生效，还会统一原码混用的时区；即使指标数值正确，也回答了另一套策略。
- **有效路径**：分离指标数学与状态机，只复用已核实的因果指标。新回放明确记录信号、下单、成交、止损提交的不同时间，保留原码先计算冷却布尔值再更新计数、同向信号重置止损等顺序。用合成路径验证入场首根跌破止损：原版继续持仓，单变量保护版本当根退出。
- **通用规则**：复现前先对齐订单生命周期，尤其要检查 strategy.position_size 判断是在成交前还是成交后执行。策略名、参数值和收益公式一致不足以证明成交语义一致；未和原生交易清单逐笔比对的 Python 回放必须明确限定结论。
- **牵连**：`yoyo/layers/l3_backtest/pine_allin_v7.py`（只读复用指标）、`yoyo/layers/l3_backtest/pine_allin_eth4h.py`、`tests/test_pine_allin_eth4h.py`、`experiments/active/exp-pine-allin-eth4h-20260907-v1/PROJECT_PLAN.md`。无生产、阈值或 holdout 变更。

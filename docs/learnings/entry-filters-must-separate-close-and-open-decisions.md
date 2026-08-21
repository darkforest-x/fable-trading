# 入场过滤器不能靠 AND 信号实现：反向平仓与新开仓必须拆成两个决定

- **问题**：把 W8 直接写成 `rawSignal and gatePass` 会同时删除“关闭反向持仓”和“新方向开仓”，
  还会改变 cooldown 消耗。这样测到的收益不是纯入场过滤效果，而是过滤、持仓延长和状态机重写的
  混合结果。
- **死胡同**：对现成 trade CSV 做静态删行，无法重建被拒绝信号之后的持仓、反转、权益和 skip
  状态；把 gated columns 传给原 replay 也只代表 full-state gate，不代表 entry-only。
- **有效路径**：状态机先读取 raw opposite signal 并决定下一开盘是否平旧仓，再独立携带
  `pending_entry_allowed` 决定是否开新仓。Pine 同样分支：gate 通过且数量有效时用
  `strategy.entry` 反手；否则若有反向仓位，只调用 `strategy.close`。raw signal 仍按 V9 消耗
  cooldown。若 gate 通过但数量为 0，也必须走 close-only，不能把旧仓意外留下。
- **验证**：同一 W8 因子在 2025-01～2026-02 的 full-state 版为 +31.41%、97 笔，entry-only
  版为 +26.75%、102 笔；差异证明“拒绝过早反转”是收益来源之一，而不只是删掉坏入场。
  单元测试覆盖“被拒绝的反向信号会平仓但不重开”和 Pine 的 quantity=0 分支。
- **通用规则**：任何判断层/过滤层接入有持仓状态的趋势策略，都必须明确回答三个问题：这个事件
  是否消耗 cooldown、是否关闭反向仓、是否允许新开仓。三者不可被一个布尔 AND 隐式捆绑。
- **牵连**：`yoyo/layers/l3_backtest/pine_allin_v7.py`、V12F/V12E、未来 LR/LightGBM 动态 gate、
  逐笔 TradingView parity。

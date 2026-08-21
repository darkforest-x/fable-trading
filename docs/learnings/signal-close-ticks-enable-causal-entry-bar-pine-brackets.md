# Pine 入场当根保护：未知成交价时先冻结信号收盘距离 tick

- **问题**：策略在确认 bar `t` 收盘提交、在 `t+1` 开盘成交。若 TP 定义为“成交价的
  30%”，`t` 时还不知道下一开盘成交价；等检测到 `strategy.position_avg_price` 后再下
  `limit`，历史回放里入场当根可能没有 TP 保护，与 Python 从入场 bar 起检查障碍的语义不一致。
- **死胡同**：打开 `calc_on_order_fills` 取得成交价会增加同 bar 重算并改变整套状态机；只在
  `newLongPosition/newShortPosition` 后创建 TP 又无法证明入场 bar 已受保护。两者都不是原 V9
  的单变量迁移。
- **有效路径**：在确认信号 bar 用 `close[t] * TP%` 计算距离，按 `syminfo.mintick` 冻结成
  ticks，并与 `strategy.entry` 同时提交 `strategy.exit(..., profit=ticks, loss=ticks)`。成交后再用
  `strategy.position_avg_price ± frozen_ticks * mintick` 维护绝对 stop/limit。Python replay 和匹配
  对照必须显式使用同一 `take_profit_distance_basis="signal_close"`，不能继续按 entry 百分比算。
- **验证**：V12T 生成器静态检查初始 long/short 两条 bracket 均带 `profit`；Python 单测验证
  信号收盘距离与 entry 百分比在 gap 开盘时会得到不同目标。安全前缀内 TBSL 共 0 个 stop/TP
  同 bar 冲突，因此本轮没有触发 Python 的 stop-first 与 TradingView broker emulator 路径差异。
- **通用规则**：任何“订单成交前必须存在、但参数依赖未知成交价”的保护单，都要先选择并记录
  因果距离基准；Pine、本地 replay、随机对照和逐笔 parity 四处必须同值。编译通过不等于成交路径
  parity，出现同 bar 双触时仍需更低周期或官方成交导出裁决。
- **牵连**：`scripts/generate_pine_eth_15m_optimized_variants.py`、
  `yoyo/layers/l3_backtest/pine_allin_v7.py`、`scripts/research_pine_eth_15m.py`、
  V12T trade-export parity。

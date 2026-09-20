# 趋势跟踪的长期证据不能直接迁移到短周期和单一资产群

- **问题**：系统收益不稳定时，如何借鉴经典趋势策略，同时避免把外部名气当成本仓有效性证据？本轮为2026-09-20文献核查，没有新增收益实验或策略改动。
- **死胡同**：仅复制海龟的20/55数字，或看到机构用均线就认为短周期加密均线系统已有支持。20日变为20根15分钟K线，时间尺度已经改变；跨资产组合的收益也不能直接归因于一个入场指标。该迁移路线本轮未实测，不能宣称其失败或成功。
- **有效路径**：先区分公开信号、交易尺度、退出、仓位和组合范围，再核查证据类型。[海龟原始规则](https://www.tradingblox.com/originalturtles/originalturtlerules.htm)是完整交易规则；[AQR研究](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing)是跨资产历史模拟；[Man AHL研究](https://www.man.com/insights/need-for-speed-trend-following)说明速度与费用存在权衡。它们提供可检验的基线，不证明Spike的具体参数盈利。
- **通用规则**：引入外部方法前先写清原市场、时间单位、信号可用时刻、完整退出与风险口径。以冻结基线、时间分段、既定成本和匹配随机对照验证迁移；没有反事实实验，不把“复杂入场”“止盈压尾”“币种相关性”写成已确认根因。
- **牵连**：现有 `analysis/p1_btc_rsi1h_sixma5m_20260920.md` 证明的是特定成本假设下的毛净差，`analysis/p1_spike_v112_trade_review_20260920.md` 明确区分机制线索与因果结论；不改变成本、止损、阈值、生产准入或历史报告。六份外部资料已保存至Spike Notion交易资料库，适用性均为待验证。

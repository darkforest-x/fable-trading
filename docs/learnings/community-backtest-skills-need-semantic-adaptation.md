# Community backtest skills need semantic adaptation before reuse

- **问题**：2026-10-23 接入 marketcalls/vectorbt-backtesting-skills 时，上游工作流默认印度股票费率、整股数量、NIFTY 基准、OpenAlgo 拉数与自动报告；与本仓 OKX 永续研究不一致。其止损示例把 `sl_trail` 写成比例，双向信号被描述为同时多空持仓。
- **死胡同**：原样安装、只把币名换成 BTC 不能建立语义兼容。依赖可导入并不证明费用、信号时点或持仓语义正确；`sl_trail=0.03` 也不等价于 3% 跟踪止损。本次在执行上游示例前发现并核对了这些问题，未把模板结果作为交易证据。
- **有效路径**：固定上游 commit 并保留原文，重写本机入口使其复用已有数据与隔离环境；以官方 Portfolio API 校正 `sl_stop=0.03, sl_trail=True` 和单列净持仓边界。用合成数据检查下一根开盘成交、逐笔 maker/taker 扣费、批量与标量一致性、跟踪止损和多空反转。复杂独立持仓研究继续由已有 Nautilus 入口核验。
- **通用规则**：接入回测 skill 时先比较时间、数量、持仓、费用和输出约定，再执行最小可手算案例。原始示例保留为参考；官方 API、项目协议和用户授权决定实际行为。不要把安装烟测标记为策略验证。
- **牵连**：上游 `marketcalls/vectorbt-backtesting-skills` commit `05d9e8b12fa408f1373c6b79620f8a5473fb874c`；`/Users/zhangzc/.codex/skills/vectorbt-expert/references/local-workflow.md`；`/Users/zhangzc/.codex/skills/vectorbt-expert/scripts/smoke_check.py`；`/Users/zhangzc/.local/share/crypto-toolkit/install-evidence/vectorbt-20261023/`。官方依据：[Portfolio API](https://vectorbt.dev/api/portfolio/base/)。本笔记不变更项目交易参数或授权门槛。

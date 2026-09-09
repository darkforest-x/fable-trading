# 开源策略的收益宣传必须追到记账路径

- **问题**：2026-09-10 为 owner 筛选 GitHub 趋势跟踪策略时，`EstebanSP23/crypto_systematic_research` 宣称 Quattro BTC 4H 回测年化 83%、最大回撤 37.5%，根 README 还列了单边 0.10% 费用假设。需要判断这些材料是否足以支持推荐。
- **死胡同**：仅按 README 的“walk-forward validated”、费用段和权益图片判断可信度，会把方法声明当成已执行的计算。初筛已经看到作者承认滑点为零、资金费率未建模，但这两项披露不能证明其他账目正确。
- **有效路径**：固定提交 `d722857a70331f9605481ef8f32df85b990d351d`，逐项追踪信号、成交、费用、权益和指标。公开 `backtest.py` 的 `total_pnl_at()` 只算价差乘数量，`close_all_units()` 直接把该值加入余额，开仓及加仓也未扣手续费；回撤输入只有每笔平仓余额，缺少持仓期间浮盈亏。由此只能将其列为规则思路参考，不能将宣传值作为已核实净收益或全过程权益回撤。代码是否对应作者绘图所用的另一个版本仍未知，本轮没有重跑回测。
- **通用规则**：外部策略先固定源码版本，再沿“信号→成交→逐项扣费→逐时点权益→绩效指标”检查同一条计算链。费用参数必须进入账户更新；最大回撤应基于按研究粒度盯市的权益；报告、参数、样本截止日和逐笔记录必须可联结。材料缺失时降低证据等级，不能推断作者从未做过其他验证。
- **牵连**：本地策略筛选方法；不涉及任何模型、阈值、订单或 holdout 变更。本次只读公开源码，没有执行第三方程序或读取本地行情。

固定来源：

- [根 README 费用假设](https://github.com/EstebanSP23/crypto_systematic_research/blob/d722857a70331f9605481ef8f32df85b990d351d/README.md)
- [损益与余额更新，L111–120](https://github.com/EstebanSP23/crypto_systematic_research/blob/d722857a70331f9605481ef8f32df85b990d351d/2_strategies/01_quattro_donchian/backtest.py#L111-L120)
- [仅平仓余额的回撤计算，L248–254](https://github.com/EstebanSP23/crypto_systematic_research/blob/d722857a70331f9605481ef8f32df85b990d351d/2_strategies/01_quattro_donchian/backtest.py#L248-L254)

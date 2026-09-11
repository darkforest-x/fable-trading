# Vix Fix relief describes a prior downside excursion, not every trend launch

- **问题**：Owner 要分析 SPIKE V6 与 CM_Williams_Vix_Fix 的配合，同时保留直接横盘爆发的大趋势。需要判断 WVF 补充的信息，而不是把两个信号机械求交集。
- **死胡同**：尚未进行组合实验；以下是公式审计排除的设计误读。原始 WVF 为 `(highest(close, 22) - low) / highest(close, 22) * 100`。低值既可能出现在横盘，也可能出现在连续上涨，不能等同于盘整或见顶。高值是相对近期最高收盘价的下行偏离，不证明资金流、真实期权隐含波动率或底部成立。强制每个多头启动都先有高 WVF 会排除没有明显下行冲击的直接突破；把高 WVF 普遍用于禁止做空，则可能过滤下跌趋势本身。
- **有效路径**：仅提出待验证的组合设计，未修改或部署指标。V6 保留结构与量价触发；对发生过下行冲击的那类盘整，单独记录 WVF 异常发生、回落和价格重新恢复的时间，检验「冲击退去后 V6 启动」是否优于完整 V6 基线。冲击证据须关联同一段价格结构并有有效期限，不能无限保留。直接横盘突破保留独立对照，不因缺少恐慌峰值直接判为无效。做空的 WVF 语义与做多不同，顶部镜像属于另一个待验证特征。
- **通用规则**：检查指标分子、分母及滚动窗口的机械影响，再解释经济含义。近期最高收盘价滚出窗口可令 WVF 下降，即使低点没有回升；阈值上移也可能解除高亮。因此不能只用「绿变灰」宣称风险退去，需要独立的价格修复证据。新增过滤是否有用要比较保留和剔除信号、启动延迟及实际净收益，不能只报信号减少量或最大浮盈。
- **牵连**：`yoyo/evaluation/pine/spike_burst_v6.pine`；`analysis/p0_imacd_okx_multitimeframe_20260907.md` §8；[Larry Williams 原文（2007，PDF 第 6 页）](https://c.mql5.com/forextsd/forum/77/fix_the_vix.pdf)；[ChrisMoody 原版](https://www.tradingview.com/script/og7JPrRA-CM-Williams-Vix-Fix-Finds-Market-Bottoms/)。原版、V3 过滤版和 Top/Bottom 改版不视为相同信号合同。本文没有回测、参数优化或收益结论。

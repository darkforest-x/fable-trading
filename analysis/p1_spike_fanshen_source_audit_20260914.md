# 翻身 V1 源码核验：找到旧 Stoch，尚未找到目标完整版本

日期：2026-09-14。唯一键：exp-spike-fanshen-source-audit-20260914-v1。

**后续更新**：Owner在本报告形成后于当前对话提供了完整目标代码，源码缺口已补齐；下文保留此前Notion检索的当时结论。新的目标代码及退出回测计划在 `experiments/active/exp-spike-fanshen-exit-multitf-20260914-v1/`。

## 结论

Notion 中找到一份完整的 Stochastic with Signal & Alert Pine v6 源码，保存在「bonk 1h」，页面最后编辑时间为 2025-07-06 11:27:32 UTC。它的简称是 Stoch，但缺少当前 TradingView「翻身版本V1-2025-07-06」的做空信号和 Williams Vix Fix 模块，不能作为目标版本的等价实现。因此尚未运行 V8 配合该指标退出的六周期回测，没有新收益数字。

## 来源与实际差异

- 候选源码：[bonk 1h](https://app.notion.com/p/2278856479af809fbeeeebe17910a3e6)。
- 上级目录：[Stochastic + Vix + Impulse MACD策略回测](https://app.notion.com/p/2258856479af80d5bbf3dd45c59278d2)。
- 规则线索：[CM_Williams_Vix_Fix交易系统](https://app.notion.com/p/2248856479af80d8b0fdcb7360750e01)。
- 「三层共振指标」页面目前为空：[页面](https://app.notion.com/p/2258856479af8024b1b0f1661ccf4f66)。
- 当前 TV 身份来自收藏精确名称的选中记录；设置来自原生 UI，OKX ETHUSDT.P，3m，普通 K 线。没有修改参数或创建订单。

| 核验项 | Notion 候选源码 | 当前 TV 目标设置 |
|---|---|---|
| 简称 | Stoch | Stoch |
| K长度／K平滑／D平滑 | 默认 14／1／3 | 当前 5／3／3 |
| 买入标记 | K 上穿 D 且当前 K、D 都低于20 | 样式列表有「做多」 |
| 卖出标记 | 没有 | 样式列表有「做空」 |
| Williams Vix Fix | 没有 | 样式列表存在该输出 |
| Vix 输入 | 没有 | 22／20／2／50／0.85／1.01 |
| 收盘确认 | 候选 signal 条件没有 barstate.isconfirmed | 计算周期为图表，「等待时间周期结束」勾选；不能替代源码核验 |

默认参数不同本身不足以排除版本相同，因为用户可以改参数；缺少做空与 Vix 计算则是结构性差异。不能只改 14／1／3 为 5／3／3 就称为翻身 V1。

## 检索范围与证据限制

先读取 Notion self：普通 search 可用，AI search 为 plan_required，因此使用普通检索，没有尝试绕过计划限制。搜索翻身、完整版本名、日期、Stoch、Stochastic with、Vix、VixTop、WVF、smoothK，以及 TV 的 Vix 输入名称。读取 CM 系统及其相关子页、指标 todo、new-指标、信号、二合一指标、指标更新-20251203等候选页。

找到了上面的旧代码；未检索到精确目标版本的完整备份。不把检索无结果解释成工作区绝对不存在：图片内容、未授权页面、附件和检索索引可能遗漏；指标更新-20251203还报告 truncated=true 与一个未知 codepen 块，该页可见代码是均线／支撑阻力系统，不是已确认的目标源码。

TradingView「原始码」入口弹出“当前方案只允许1个已保存脚本”的升级提示。关闭提示并改查 Notion，没有升级账户、读取隐藏应用缓存或绕过限制。

## 多周期准备情况

已完成只读数据入口映射，未运行新的行情回放：

- 3m 已有 OKX pre-only 上下文，历史可追溯至2023年。
- 15m 的既有前缀报告记录数据起点2022-01-03，截止2026-05-01，前缀缺口／重复为0。
- 5m 已有 OKX 源文件；元数据支持从2026-01-01开始的窗口。
- 30m／1h／4h 可以从完整15m或5m constituent 聚合，必须校验桶内完整性及UTC边界；不能从3m拼5m。
- 六周期共同候选窗口为2026-01-01至2026-05-01，不代表本轮已重新验证全部前缀。更早共同覆盖仍待数据核验。
- 通用重放可读取 frame.attrs["minutes"]；旧3m study 的确认时间加3分钟等硬编码不能直接套到其他周期。

参考：`analysis/p1_release_eth_multitf_20260914.md` 数据前缀表；`exp-imacd-okx-descriptive-20260907-v1/summary.json` 来源元数据；`yoyo/data/release_eth_prefix.py` 安全前缀读取；`yoyo/evaluation/spike_recovery_exit.py` 通用重放接口。

## 数据统计与对照

这是一项源码身份审计。完整保存可检查的候选源码1份；市场交易样本0笔，val样本0笔；正类率、val AUC、置换检验p、top-decile毛／净收益、胜率、单特征入场基线及匹配随机入场对照不适用，因为没有执行方向性实验。

对应的可证伪零假设是“同简称、同编辑日期足以认定为同一指标”。逐字段对照发现候选缺少做空和Vix模块，否定该身份判定。没有根据外观反推或补造这些公式。

## 复现入口

重新用 Notion fetch 读取上述页面，提取 bonk 1h 的 javascript fenced code block，与保存的 `source/stoch_with_signal_alert_notion_candidate.pine` 按字节比较；Notion后续改动须另存版本。原生 TV 重看收藏精确版本的输入及样式页，核对上表。当前备份的哈希见 `source_receipt.json`。

报告重建：
```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python scripts/md_to_html.py analysis/p1_spike_fanshen_source_audit_20260914.md --out-dir analysis/html
```

## 风险与诚实声明

本轮没有完成目标指标的源码获取，也没有做六周期回测；没有新配置消耗holdout（0次）。TV截图／设置仅用于身份识别，未评分图上行情。候选源码保持原文，未编译验证或宣称可直接替代目标指标。参数、箭头时点、是否盘中变动、是否存在高周期调用，均需完整目标源码才能确认。

## 下一步

缺少的输入是目标指标完整 Pine 源码，或可读的另一份备份地址。取得后先核对多空信号和收盘时点，再冻结退出规则，比较3m／5m／15m／30m／1h／4h的原V8退出与新退出。当前证据不支持用旧Stoch代替后直接报“翻身V1”的收益。


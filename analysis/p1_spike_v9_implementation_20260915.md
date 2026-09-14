# SPIKE V9：标的、量比与 UTC 周日过滤已实现

日期：2026-09-15。版本：`spike-v9-entry-bundle-20260915-v1`。源码提交：`ef4de009d068c30bba33166b52423e53d26d1781`。

V9 在 V8 基础上同时排除底层 USDC、确认量比大于 50、计划开仓时刻落在 UTC 周日的新入场，并显示资产类型、确认价初始风险和往返 0.2% 的参考成本R。134 项合成功能及项目边界检查通过。Pine 脚本已交付源文件，尚未在 TradingView 编译或加载。

Owner 对三条候选明确要求“不需要对照啊 直接做”，随后要求“升级为v9”。本轮执行组合实现与功能验收，没有运行新的收益对照、历史行情回放或参数搜索。授权已记录到根 PROJECT_PLAN 与实验 PROJECT_PLAN。

## 交付入口

- [V9 Pine 源码](../../yoyo/evaluation/pine/spike_burst_v9.pine)
- [V9 Python 准入与回放适配器](../../yoyo/evaluation/spike_v9.py)
- [版本合同](../../experiments/active/exp-spike-v9-entry-bundle-20260915-v1/PROJECT_PLAN.md)
- [固定配置](../../experiments/active/exp-spike-v9-entry-bundle-20260915-v1/config.json)
- [交付清单与哈希](../../experiments/active/exp-spike-v9-entry-bundle-20260915-v1/delivery_manifest.json)
- [Notion V9 版本记录](https://app.notion.com/p/3db8856479af81e1b257dbd9ae09aca3)

以上链接相对交付 HTML 所在目录。Pine Editor 中的新脚本名为“SPIKE V9 · 标的量能时段过滤”；本轮交付不等于已替换图表中的 V8 或已切换监控。

## 最终规则

| 项目 | V8 | V9 |
| --- | --- | --- |
| 原有结构、BB 压缩、离六线 3ATR 门 | 原规则 | 默认继承 |
| 底层资产 | 无本条排除 | 精确底层 USDC 不准入；ETH/USDC 底层为 ETH，不因此排除 |
| 确认量比 | 无 50 上限 | RV ≤ 50 保留，RV > 50 排除 |
| 入场时段 | 无本条排除 | 计划开仓时刻的 UTC 周日排除 |
| 初始止损、2R 启用 4ATR 跟踪 | 原规则 | 原规则 |
| 原始反向 V6 确认结束已有跟踪 | 原规则 | 仍接收被新入场门过滤的反向确认 |
| 信号解释 | 原有信息 | 增加资产类型/底层、过滤原因、量比、参考风险%、参考成本R |

RV 沿用当前确认 K 线成交量除以当前连续片段内此前 20 根有效成交量的中位数，不使用三根量比，也不含未来量。三条新门固定同时生效；底层、量比或时钟未知时显示原因并不准入。

日历门使用信号收盘时刻，也就是连续时间 K 线的计划下一根开盘时刻。UTC 周日对应北京时间周日 08:00（含）至周一 08:00（不含）。周六 23:00 的 1H 信号 K 线在周日 00:00 收盘，属于排除范围。它不使用图表显示时区，也不把信号 K 线的开盘日当作成交日。

Python 在实际收到下一根时检查时间缺口；若计划下一根缺失，沿用原缺口处理取消待开仓，不把周六准入延迟到周日。该处理不使用未来缺口回写过去准入。Pine 的时钟限定于连续时间 K 线，不宣称适配交易时段休市缺口、非时间图或延迟实盘成交。

## 风险与成本说明

参考初始风险距离来自确认收盘价、当前 ATR、含确认 K 线的最近 5 根高低点，以及原有 tick 向外取整、2ATR 下限和 0.2ATR 缓冲。参考风险比例为初始价格风险距离除以确认价；参考成本R为 `0.002 / 参考风险比例`。

例如确认价 100、初始止损 98，参考风险为 2%，往返成本为 0.1R。该数值只解释成本负担，没有增加成本阈值或改变止损。资料不足时显示未知，不补成零成本。

Pine 保留 V8 的确认收盘视觉参考；Python 保留原 next-open 串行成交。这两套时序并未在本轮统一，参考风险不是实际成交风险，也不是账户风险百分比。输出表新增 `strategy_version` 与 `arm=v9`，保留旧 engine cohort/policy 作为来源。

## 验证与数据统计

| 检查 | 证据与结果 |
| --- | --- |
| 功能与项目边界 | 134 passed，57.38 秒 |
| USDC 精确识别 | 大小写、空值、ETH 计价 USDC、USDCX 等合成边界通过 |
| 量比阈值 | 49.99、50、50.001、NaN、无穷、负值与空值覆盖 |
| 日历边界 | UTC 与北京时间周日开始/结束、周六收盘跨周日覆盖 |
| 因果性 | 改写未来 OHLC/RV 不改变过去准入和参考成本；未来缺口不回写旧信号 |
| 串行退出 | 被 RV 门过滤的原始反向确认仍以原 next-open 规则结束旧交易 |
| 冻结父版本 | Pine 原结构引擎、风险 helper、参考状态块逐字节相同，V8 文件未改 |
| 版本身份 | 空表及非空交易/成交/事件表均保留 V9 字段 |
| 项目守门 | 层间 import、包路径、实验隔离、单一定义 holdout 边界通过 |
| TradingView 编译与图表 | 未执行，不能用 Python 检查代替 |

所有行情输入均为测试代码现场生成的 2025 年 40 根 1H 合成序列及边界变体，未读取 data/、真实候选或历史交易账本。真实候选数、正类率、val 样本数：不适用，本轮未抽取市场样本；本配置 holdout 消耗 **0 次**。

val AUC、置换检验 p、top-decile 毛/净收益、胜率、PF、最大回撤、10R 保留率、单特征收益基线和匹配随机入场对照均不适用：本轮是按 Owner 选定规则实现版本，未开展方向性收益评估。对应的代码零假设检查是：仅改变明确边界输入，只有预期门应改变；未来数据变化不能改变过去决定；新门不得改变原始反向退出与冻结引擎块。不得从这些检查推导盈利改善。

新增缺口测试首轮出现 1 个测试断言失败：未来缺口使 pandas 全索引的 freq 元数据由 Hour 变为 None。对过去逐行时间戳与业务字段的检查继续保留，只对该变形测试关闭全局 freq 元数据比较。最终 134 项全部通过，没有忽略价格或信号差异。

## 复现命令

源码先提交后生成此报告；使用仓库现有 Python 环境，没有安装或修改依赖。已有环境须可导入 requirements/constraints 所约定的依赖。

```bash
cd /Users/zhangzc/fable-trading
python3 -m pytest -q tests/evaluation/test_spike_v9.py tests/evaluation/test_spike_v8_replay.py tests/boundaries/test_layer_imports.py tests/boundaries/test_yoyo_package_is_local.py tests/boundaries/test_experiment_isolation.py tests/causality/test_holdout_boundary_is_single_valued.py
python3 scripts/md_to_html.py analysis/p1_spike_v9_implementation_20260915.md --out-dir analysis/html
```

以上从合成输入重现功能验收，不启动行情下载或收益回测。版本源文件及对应哈希见交付清单。

## 风险与诚实声明

V9 是 Owner 选定的实现版本，本组合的盈利能力没有在本轮评估，不能把三条旧候选的改善数字相加，也不能由版本号推导已通过实盘验证。已有失败的 MFE/BE/分批退出实验没有纳入本版。

底层元数据缺失会阻止新入场；Pine 的 syminfo.basecurrency 在不支持的品种可能为空，因此不能把本版默认为全资产通用。三条新门作用于新信号；确认冷却与原始反向事件仍沿用父版本，过滤不会把上游事件从历史中删除。

旧 V7/V8 shadow 有独立冻结源码与协议，不能把既有账本改名为 V9。该交付尚未切换线上监控、建立 TradingView 警报、promote 模型或进行账户操作；`training_eligible=false`，`production_eligible=false`。HTML 仅做结构和链接检查，不声称图表视觉或 Pine 成交 parity。

## 后续使用

当前可交付 V9 源码与固定规则合同。TradingView 编译/加载属于尚未完成的平台验证；如果要将其接入现有监控，应保留独立 V9 血统并明确切换目标。新的真实历史评分或实盘配置切换仍按原有项目范围单独记录；本轮不自动追加收益对照。

## 来源

- [TradingView 官方：图表与 syminfo 元数据](https://www.tradingview.com/pine-script-docs/concepts/chart-information/)
- [TradingView 官方：时间与显式时区](https://www.tradingview.com/pine-script-docs/concepts/time/)
- [TradingView 官方：策略成交时序](https://www.tradingview.com/pine-script-docs/concepts/strategies/)
- 既有候选证据：[SPIKE V8 六类过滤研究](p1_spike_v8_six_filters_20260914.html)。本轮仅继承候选定义，不新增其收益数字。

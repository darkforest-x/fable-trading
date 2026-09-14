# SPIKE · 均线下压预警 V1

已实现独立 Pine v6 空头形态指标，TradingView 原生编译无可见错误，私有保存第 1 版。当前是可使用的形态标记工具，真实样例命中、误报与盈利效果尚未验证。

## 使用

TradingView → 指标 → 我的脚本 → **SPIKE · 均线下压预警 V1**。用户可自行添加到标准 K 线图；本轮只编译保存，没有把新指标挂到历史行情图上评分。

- 黄色「预警」：此前均线密集，短线下弯，价格在线下缓慢走弱。
- 红色向下三角「确认」：先有预警，之后某根收盘严格跌破预警时冻结的整理低点。
- 红色水平线：等待破位期间的冻结低点，不随新低向下追移。确认当根保留显示。
- 淡黄色背景：等待破位。收盘收回六线带则取消；最多等待 8 根，第 9 根起超时。
- 面板：显示上一个已收盘状态、形态条件及冻结低点。尚未收盘的条件不会替换它。
- 警报条件有「SPIKE 下压预警」「SPIKE 下压确认」「SPIKE 下压事件」；需要用户在 TradingView 单独创建警报，建议选择每根 K 线收盘。修改脚本参数后须重新创建原有警报，服务器保存的是创建时快照。[TradingView 官方警报说明](https://www.tradingview.com/pine-script-docs/concepts/alerts/)。本轮未创建运行中的警报。

源码：[spike_ma_drift_short_v1.pine](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/spike_ma_drift_short_v1.pine)。适合先在用户关注的 1m / 3m 标准加密 K 线图检查语义；同样根数在不同周期覆盖的分钟数不同。

## 可核对的规则

| 阶段 | V1 条件 |
|---|---|
| 均线 | close 的 SMA20、EMA20、SMA60、EMA60、SMA120、EMA120 |
| 先前密集 | 预警前已结束的 12 根中，有连续 3 根六线跨度 / 各自前一根 ATR ≤ 3 |
| 短线下弯 | SMA20 与 EMA20 均低于各自 3 根前的值；没有要求六线一起向下 |
| 高低点下移 | 最近 3 根 high 均值、low 均值，分别低于此前 3 根 |
| 线下缓跌 | 最近 6 根末收盘低于首收盘，且至少 4 根收盘在各自六线下沿以下 |
| 小步约束 | 最近 6 根 TR / 各自前一根 ATR 的最大值 ≤ 1.8 |
| 位置约束 | 当前收盘在六线下方，距离六线下沿 ≤ 1.5 倍前 ATR |
| 预警 | 上述条件满足、没有等待中形态且冷却结束；冻结最近 6 根 low 最小值，包含预警根影线 |
| 确认 | 预警之后第 1–8 根，收盘严格低于冻结低点且仍低于当前六线下沿；预警当根不能确认 |
| 取消与超时 | 收盘重新进入均线带即取消；第 9 根起超时优先。终态当根不重新挂起 |
| 冷却 | 终态之后完整等待 12 根，第 13 根可重新预警 |
| 断档/预热 | 无效 OHLC 或不连续时间清空等待；至少 max(120+ATR长度, 360) 根连续已结束K线后才允许新预警 |

密集是「此前足够窄」的代理，当前没有额外要求宽度持续单调下降。均线沿用标准全历史 SMA/EMA；断档后重置的是事件状态与有效窗口计数，不另创均线播种算法。

输入可调：ATR 长度 14、密集宽度 3、最大 TR 比 1.8、最大离线距离 1.5、等待 8、冷却 12；显示选项可隐藏六线、面板或显示取消/超时标记。均线期数、12/3 密集窗口与 6 根缓跌几何固定。所有默认值是未经行情优化的设计起点。

## 版本对照与工程结果

| 项目 | 原有 SPIKE V8 | 本次独立下压 V1 |
|---|---|---|
| 六线 | SMA/EMA 各 20、60、120 | 同一绘线定义 |
| 信号定位 | 原有结构/量价等多项确认 | 线下缓跌预警，之后冻结低点破位 |
| MA 斜率 | 非六线逐条斜率模型 | 明确要求短线两条向下 |
| 预警和确认 | 沿用原 V8 配置 | 两个独立收盘事件，不回填 |
| 本轮改动 | 0 | 新增独立文件；现有配置不变 |

| 工程验证 | 结果 |
|---|---|
| TradingView 原生编译/私有保存 | 无可见编译错误，2026-09-14 13:17 UTC+8 保存第 1 版；未挂到行情图运行 |
| 静态因果/状态契约 | 5 项通过 |
| PineTS 0.9.33，完整源码 + 合成 OHLC | 8 组检查通过，其中 1 组包含 9 个原样纯状态函数断言 |
| 缓跌正向工程夹具 | 413 根合成K线，0起算第403根预警，第409根确认；最后第412根是另设的大幅下跌 |
| 横盘零假设对照 | 440 根恒定收盘、固定影线合成K线，0预警/0确认 |
| 改写未来与截断重跑 | 已收盘前缀标记一致 |
| 时间缺口 | 重置预热，本夹具内0预警/0确认 |
| 1m/3m合成时间尺度 | 相同每根OHLC几何得到相同事件根号；不是跨周期收益验证 |
| 状态分支 | 同根禁止确认、冻结低点不追移、等待最后一根、超时优先、收回取消、无效输入、冷却边界均通过 |

工程夹具只有手工生成的数字与 2025-01-01 起的人工时间戳，无真实行情输入；不能把其第403/409根当成图中对应时间。没有 val 样本或正类率：本次未建立带标签的数据集。

## 复现命令

实现与验证器已先提交 `4b7fa9f050`，再执行合成测试。

```bash
cd /Users/zhangzc/fable-trading
mkdir -p /tmp/spike-ma-drift-runtime-20260914
npm install --prefix /tmp/spike-ma-drift-runtime-20260914 --ignore-scripts --no-audit --no-fund --dry-run pinets@0.9.33
npm install --prefix /tmp/spike-ma-drift-runtime-20260914 --ignore-scripts --no-audit --no-fund --save-exact pinets@0.9.33
node experiments/active/exp-spike-ma-drift-short-20260914-v1/verify_synthetic.mjs
python3 -m pytest -q tests/test_spike_ma_drift_pine.py
python3 scripts/md_to_html.py analysis/p0_spike_ma_drift_short_indicator_20260914.md --out-dir analysis/html
```

原生编译复现：在 TradingView Pine 编辑器新建指标 → 全文粘贴上述 `.pine` → 保存为新脚本 → 检查编译日志和保存状态。只保存编译不等于图表执行；本轮没有点击「添加到图表」。PineTS 原样执行的是独立辅助运行器，不能冒称 TradingView 原生运行。[辅助运行器官方 API](https://docs.luxalgo.com/developers/pinets/initialization-and-usage)。

验证收据：[合成检查](/Users/zhangzc/fable-trading/experiments/active/exp-spike-ma-drift-short-20260914-v1/results/verification.json)、[原生编译](/Users/zhangzc/fable-trading/experiments/active/exp-spike-ma-drift-short-20260914-v1/results/tradingview_compile.json)。原生回执绑定实际粘贴文本 SHA；辅助功能截断全文选择，因此没有声称从云端逐字节回读整个源码。

## 风险与诚实声明

- 这是工程实现，不是方向性收益实验。val AUC、置换 p、top-decile 毛/净收益、胜率、单特征盈利基线、匹配随机入场收益均不适用：没有交易账本或收益标签。相应零假设对照是无形态横盘夹具，另外用未来篡改和边界断言检验时间因果性，不能拿它替代未来策略验收。
- Owner 图中已可见后续大跌，属于事后语义样例；本轮没有逐根核对该图，也没有读取 2026-09-02 16:33 / 3m 对应 OHLC。不能宣布两例都已命中或已经提前预测。
- 新配置 holdout 行情评分 0 次。本轮未向新指标提供历史行情；打开 TradingView 编辑器时既有图表与既有策略面板可见，其数字未作为本指标的证据或调参输入。
- 预警可以失败，确认也可能反弹；未研究成本、交易滑点、流动性、入场与持仓退出。无仓位或订单功能，无通知发送。
- 当前周期用 `barstate.isconfirmed` 更新事件，窗口只向后；这限制盘中闪烁和回填，不保证行情提供方修订数据、改变历史加载起点或用户改参数后结果仍相同。[TradingView 收盘状态说明](https://www.tradingview.com/pine-script-docs/concepts/bar-states/#barstateisconfirmed)。
- 本轮没有改变旧 V8、监控/通知、生产阈值、ACTIVE、执行器或模型。

## 下一步

Owner 可以先核对标记是否符合心目中的形态；如果需要以历史样例定参数或统计误报，另行明确样本与 holdout 使用范围，再冻结规则测试。当前没有自动后续扫描。

[Notion 形态记录](https://app.notion.com/p/3db8856479af817583c7e11b3e98abb4)，状态 Inbox/想法；[原始观察](https://app.notion.com/p/3db8856479af813f9e4ff902bc963eeb)保留。

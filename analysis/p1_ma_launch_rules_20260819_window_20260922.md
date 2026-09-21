# 2026-08-19 18:00–22:00 双均线密集启动规则回放

状态：输入准备中，结果尚未生成。此文件将在扫描完成后填写实际结果。

Owner更正将范围固定为北京时间18:00–22:00，所有可用币种，3m/5m/15m/30m/1h/4h。这里的检查时间是原规则完成核心后第5根确认K线的**收盘时间**，不是核心启动时刻。更早历史只作预热/形态上下文，所有价格输入与图片不超过22:00。无训练、生产或收益评估。

## 冻结方法

原Grade-A粗筛、50张认可家族距离、严格形态门、参考对照与质量阈值保持不变。发现规则用CLOSE均线，后来的HL2重渲染不改变本次规则定义。保留左14根连续性约束；删除只供出图的第6根右边距，右端止于原5根确认。按原60分钟固定簇去重，再严格评分，先筛Grade-A，再按同币同周期同方向240分钟质量优先去重。没有8000张数据集的配额约束。

数据池来自既有Binance/OKX USDT永续30分归档；同资产选择可见截止覆盖较新的场所，再优先Binance。完整UTC桶聚合1h/4h；同场所公共接口补3m/5m/15m。目标前1200根预热，逐源SHA冻结。目录不是历史上市币完整普查，可能含幸存者偏差。参考资料： [Binance官方K线接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)、[OKX官方历史K线接口](https://www.okx.com/docs-v5/en/)。

## 复现命令

```bash
.venv/bin/python -m pytest tests/test_ma_snapshot_inputs.py tests/test_ma_launch_snapshot_scan.py -q
.venv/bin/python -u -m yoyo.data.ma_snapshot_inputs --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/plan.json
# 冻结生成的scan_plan.json并在main提交后：
.venv/bin/python -u -m yoyo.datasets.ma_launch_snapshot_scan --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/scan_plan.json
.venv/bin/python -m yoyo.datasets.ma_snapshot_gallery --plan experiments/active/exp-ma-launch-rules-20260819-window-20260922-v1/scan_plan.json
```

首次源码743e91fc4d，修正/图册7bf22434bb。高周期预跑结果results_high已失效保留：epoch-ms读取器将+1ns向下取整，遗漏恰好22:00收盘bar；完整端点计数发现，正式全量改+1ms并新增毒化集成测试。初次输入准备在聚合阶段主动中断，改为批量聚合并保留已生成输入；没有规则扫描结果或调参。此前模型、训练图、50张后续图及生产路径均不改。

## 指标适用性与对照

本轮检查原规则在固定时段触发哪些形态，不读取未来标签，不属于收益或新模型评估。val AUC、val样本、置换p、top-decile毛/净收益、胜率及匹配随机交易对照均不适用；不以命中数量证明有效性。以时间边界毒化、完整高周期桶、原NMS行为、参考校准相等和输入SHA/端点交叉检查作为相应严格对照。

## 风险与诚实声明

原规则5根确认意味着1h要等5小时、4h要等20小时。此回放不能称为在核心启动当下提前预测；规则本身是后来建立的，也不是8月19日真实在线运行记录。其他周期沿用15分参考和阈值，未经跨周期质量验证。命中不是逐样本Owner金标，不自动进入训练。缺失/损坏数据与不足预热必须列出，不能隐去。

## 下一步选项

Owner可逐图确认是否符合目标形态。如果希望识别18:00–22:00刚开始的核心，需要另定义无需未来5根的实时任务；本轮不为找回已知截图调低阈值。

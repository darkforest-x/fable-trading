# Fable 4h MA Launch Causal V1

这是 `exp-btc-4h-ma-launch-similarity-v1` 的因果前缀版 Pine 指标。

它沿用 Owner 参考形态的 SMA/EMA 20、60、120、30 根启动前窗口和冻结结构门槛，
但不读取启动后的未来 K 线。每次判断只允许候选启动点位于当前 bar、前 1 bar 或前 2 bar，
信号只画在当前已收盘 bar，不回填到过去。

## 怎么试

1. 打开 TradingView Pine Editor。
2. 粘贴 `pine/fable_4h_ma_launch_causal_v1.pine` 的全部内容。
3. 使用普通蜡烛图并切到 **4 小时**；其他周期 HUD 会显示 `SWITCH TO 4H`，且不出信号。
4. 添加到图表。绿色 `L` 是多头候选，红色 `S` 是空头候选。
5. `anchor -0/-1/-2 bar` 表示程序认为形态从当前、前一根或前两根开始；箭头始终留在实际确认时刻。
6. 如需提示，在 TradingView 中手动创建本指标的 LONG 或 SHORT alert condition，并选择每根 K 线收盘一次。

## 它没有做什么

- 不是 `strategy()`，没有订单、仓位、止盈止损或收益回测。
- 没有 `request.security()`，一次只判断当前图表币种。
- 没有用完成形态版的未来 12 根、RMSE、DTW 或 p 值冒充实时证据。
- 诊断分数只用于解释同一时刻的合格候选，不是概率，也不参与准入。
- 本轮只允许静态契约与官方编译验证；没有历史/holdout 评分、参数选择、forward 或部署。

冻结规格见 `preregistration.json`。任何门槛、窗口、lag、分数权重或去重修改都会产生新版本。

## 当前验收状态

- TradingView 官方 Pine v6 编译：通过，0 error。
- 编辑器源码与仓库文件 SHA-256：完全一致，`55512e102b148550d052fab431c0bdc191f99967d9f43805790a79c35932c8be`。
- 私有脚本：已保存并添加到一个 4h Candles 图表。
- alert / webhook / order：均未创建或发送。
- 历史、holdout、收益或 forward 评价：均未运行。

编译证据见 `results/tradingview_compile_receipt.json`，完整边界与复现命令见
`analysis/p0_btc_4h_causal_pine_v1_20260825.md`。

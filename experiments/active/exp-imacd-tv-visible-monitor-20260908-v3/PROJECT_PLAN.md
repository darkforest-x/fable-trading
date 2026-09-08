# 以 TradingView 实际可见主图标记为监控契约

Owner再次纠正并明确：“TradingView 主图真正出现启动箭头，才发 TG”。本轮先核对真实界面，不从“零轴”一词另造信号定义。

## 真实观察

2026-09-08 07:51北京时间，通过TradingView原生UI读取当前OKX ETHUSDT.P 4H的IMACD：模式密集启动、34/9、minZero1；showFocus=true、focusMinBars12、focusAtrBand0.1、showMarks=false、showPriceText=true。普通系统启动/退出隐藏。主图实际标签为“蓄势释放↑ ·44根，收盘1922.23”。未修改或保存图表参数。

Pine V2.2的可见priceTag条件为showFocus && focusRelease，主线越出已合格近零段的冻结阈值。复核公开OKX RAM数据，该标签开盘2026-08-19 08:00UTC、收盘12:00UTC；单独zero_breakout在00:00UTC开盘那根提前8小时出现，不是目标标记。

## 固定范围

- canonical kind tv_start，source_kind release，tv_marker focus_release，profile imacd-v2.2-focus12-band0.10-marks-off。
- 只有当前profile的可见focus释放进入主列表、主箭头、24h计数和TG；隐藏entry/exit、单独zero_breakout、sb单独越界、未满足蓄势资格、后续光晕均不通知。
- 12根资格与0.1ATR冻结带复用原Pine当前参数，不是新优化。previous_md可以非零，zero_bars可以为0；上一根双线在冻结带内，当前主线严格出带。
- 生效点继续使用校准OKX时钟，历史不补发，旧回执保留。维护期间停止本服务，验证后恢复；不下单，不碰其他服务、凭据、VPS缓存、模型。
- 当前TradingView参数是人工读取快照，不宣称远端参数变化会自动同步。本轮不改TradingView或Pine。
- 既有全日期授权覆盖本固定配置第1次live-observation holdout日期使用，无收益评价、调参或训练。

## 交付门

源码先提交再跑正式参考重建。真实ETH标记的方向、44根、1922.23、开/收盘时间必须同表一致；提前8小时的zero事件必须不通知。补方向对称、未合格区、sb越界、光晕续段、API/发送端隔离、切换与重启测试。全473合约1H/4H真实扫描、页面匹配和HTML报告。收益AUC/p/胜率/随机入场不适用，以视觉锚点对照、因果反事实、队列故障注入验证。

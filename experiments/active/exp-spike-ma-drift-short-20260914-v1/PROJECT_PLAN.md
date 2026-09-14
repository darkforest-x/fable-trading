# 均线下压预警 V1：实现边界

- Owner 请求：2026-09-14「你写个这个指标吧」。独立 Pine v6 空头指标，延续本对话截图的结构定义。
- 来源：Owner 的 OKX ETHUSDT.P 1m 截图，以及提及的同品种 UTC+8 2026-09-02 16:33 / 3m 样例。后者尚未读取行情验证。
- 六线：close 的 SMA/EMA，各 20、60、120。现有 SPIKE V8 的绘线契约保持。
- 输入：只使用当前及此前的 OHLC 和时间；不依赖成交量、跨周期请求、需要未来确认的 pivot 或回填箭头。
- 输出：形态预警、后续破位确认、等待取消/超时；全部按本周期收盘确认。预警不是开仓指令。
- 形态：此前连续收敛、短均线斜率转负、线下缓慢下压；慢均线可以走平。
- 预警根冻结整理低点；同根不得确认。后续收盘严格跌破冻结低点且仍在六线下方才确认。
- 重新进入均线带取消；超过等待期取消；缺失/不合法K线重置状态并重新预热。
- 默认值是设计起点，未经历史优化；1m 与 3m 独立计算，同样根数不代表同样分钟跨度。
- 工程检验：原生编译；独立 PineTS 运行合成 OHLC；纯状态函数分支、前缀不变性、未收盘门、无形态负对照。
- 数据纪律：本次不采集/读取/评分其他历史 OHLC，不做 holdout 评估、不回测收益。截图只作为 Owner 提供的语义参考。
- 不改变现有 V8、实时监控、阈值预设、通知、风控或订单。
- 交付：Pine 文件、合成工程测试收据、MD/HTML 说明、learning、实验/产物登记。
- training_eligible: false；production_eligible: false。

## 官方实现依据

- https://www.tradingview.com/pine-script-docs/concepts/bar-states/#barstateisconfirmed
- https://www.tradingview.com/pine-script-docs/language/execution-model/
- https://www.tradingview.com/pine-script-docs/concepts/alerts/
- https://docs.luxalgo.com/developers/pinets/initialization-and-usage （辅助运行器，不冒称 TradingView 原生运行）

# 手机 App 分享出的图表链接可能只是图片快照

- **问题**：为修复 Bark 到 iPhone TradingView 的币种和周期定位，Owner 按请求给出两条手机原生分享链接；它们能展示正确图表，但是否能导航实时图表仍须核实。
- **死胡同**：`/x/` 后恰好也是8字符，不能因此把它当成 AASA 的 `/chart/????????/` 布局ID；图片画着“4小时”也不代表分享地址携带周期参数。
- **有效路径**：2026-09-09 实读 [ETH 分享页](https://cn.tradingview.com/x/GpAAywbJ) 和 [LIT 分享页](https://cn.tradingview.com/x/wXnIZOgm)，检查 HTML 与实际 PNG：均为4H图片快照。页面的 `og:image` 和主 `<img>` 指向 snapshots PNG。官方 `snapshot.7a867555d4260d1b3434.js` 的打开图表按钮构造网页 `chart?symbol=...`，没有 interval；cn/www AASA相同且没有 `/x/*`。这两条不能提供可靠的手机双参数入口，继续保留现有网页备用，不伪造App深链。
- **通用规则**：对分享链接分别确认资源类型、标识符语义、点击处理器和参数来源。来自 App 的链接也不一定是返回 App 某页的入口；图片中的字段、网页按钮中的字段和原生路由支持要分开举证。
- **牵连**：`docs/ops/SPIKE_MODEL_CONFIRMATION.md`；原始网页、PNG和官方JS仅存 `output/qa/spike_bark_ios_20260909/user_share_links/`。源码和线上配置本轮未改，未发送通知。此结论不等于审计了iOS App的所有私有入口。

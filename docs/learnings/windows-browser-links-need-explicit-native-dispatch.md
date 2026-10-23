# 相同 HTTPS 地址的打开位置由发起程序决定

- **问题**：Windows 的 Telegram 图表按钮能打开 TradingView 客户端，监控页面上相同地址却打开网页；用户明确要求监控列表也使用本机客户端。
- **死胡同**：仅复制 TG 的 HTTPS URL、增加新标签或依赖 Windows 的网站应用关联，不能使浏览器必然把导航交给系统。进程启动成功也不能证明客户端已切换币种。
- **有效路径**：浏览器点击明确的本机协议，由当前用户安装的小入口严格解析币种和周期，再复用已验证的 Windows TradingView 程序参数桥接。Windows 没有网页回退；派发回执始终保留 `verified:false`，主图验收单独进行。
- **通用规则**：先区分链接内容、发起程序、系统关联和目标应用四个环节。需要可靠本机行为时，用已验证的原生入口，并明确每台电脑需要的安装条件；不要把系统应用关联当成浏览器导航契约。
- **牵连**：`yoyo/monitor/static/monday-fib.js`、`yoyo/monitor/windows_chart_link.cs`、`yoyo/monitor/windows_tradingview.ps1`、`scripts/windows/install_spike_chart_link.ps1`；官方依据见 `docs/ops/SPIKE_WINDOWS_CLIENT.md`。2026-10-23 的 Windows 编译与参数测试已通过，远程自动输入未传入 Windows，浏览器到主图的本轮实机验收仍需另行确认。

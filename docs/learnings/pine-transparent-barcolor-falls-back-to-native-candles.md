# Pine 全透明 barcolor 不会隐藏K线，会退回原生红绿

- **问题**：Monday Range v1 想把周六、周日的K线“隐藏”，对这些 bar 用了 `barcolor(color.new(color.white, 100))`，同时让前景 `plotcandle` 也用全透明色。Owner 在 OKX BTCUSDT.P 1H 上截图（2026-10-13）看到，周末K线显示的是原生红绿色，没有消失。
- **死胡同**：把 100% 透明当作“隐藏”。TradingView 对全透明的 barcolor 不当成透明颜色画，而是当作“不着色”，于是用回图表原生配色；前景 plotcandle 全透明也什么都遮不住。内置浏览器里又连不上 TradingView 行情 websocket，所以交付前没能实机看到这个现象，只靠静态检查就以为没问题。
- **有效路径**：想让某根K线“看不见”，就用不透明的 `chart.bg_color` 去画它：`barcolor` 和 `behind_chart=false` 的同色 `plotcandle` 都传 `chart.bg_color`，用背景色盖住原生K线（v1.1）。这个修法还没有 owner 截图确认；渐变背景时 `chart.bg_color` 只取中间色，可能留下痕迹。后来 owner 澄清，“只显示周一到周五”的意思其实是周末K线和其他K线同为灰色，不需要隐藏，所以 v1.2 把隐藏功能整个删了。到了 2026-10-14，owner 又决定改回用 v1 的K线表现（v1.3 在 v1 基础上加了周K高低点）。显示需求反复改过几次，所以每个版本都保留独立文件，不覆盖旧版。
- **通用规则**：Pine 只能改K线颜色，不能删 bar。需要隐藏K线时，用不透明的背景色遮罩，或者让用户在图表设置里关掉原生K线，再全部改由 plotcandle 画；不要指望 alpha=100 起作用。显示类改动没有实机截图之前，不要说成已验证。
- **牵连**：`yoyo/evaluation/pine/monday_range_v1.pine`（有此问题）、`yoyo/evaluation/pine/monday_range_v1_1.pine`（遮罩修法）；z-order 前提见 [pine-candle-style-parity-requires-foreground-z-order.md](pine-candle-style-parity-requires-foreground-z-order.md)。

# TradingView 标签标题不能证明主图已经切换

- **问题**：本机桥接返回成功、TradingView 标签标题也变成目标合约，但主图仍可能短暂保留旧币种或旧周期；前端因此会把尚未完成的跳转显示成成功。
- **死胡同**：用窗口标题、标签标题或已点击“从剪贴板打开链接”作为完成条件。这些状态先于图表画布更新，尤其在已有多个桌面标签和复杂指标布局时会产生假阳性；只等待固定的两三秒也会把正常的慢加载误报成失败。
- **有效路径**：把已校验的合约和周期绑定到 owner 保存的具体布局 URL，通过 Desktop 的“从剪贴板打开链接”入口打开；随后轮询主图画布的可访问性描述，只有同一元素同时包含目标 `OKX:<SYMBOL>.P` 和目标周期才返回成功。Desktop 打开链接时会重建 Electron 标签内容，派发前保存的 AXWindow 可能随即失效，因此每轮只重新取得当前前台窗口，绝不能扫描其他后台窗口。轮询覆盖 Desktop 的实际加载延迟，并由依次更宽的 AppleScript、Python 和前端截止时间兜底。
- **通用规则**：自动化外部图表应用时，第一步验证最终内容载体的身份；导航回执、窗口标题和标签标题都只能说明请求已开始。成功回执必须晚于内容级验证。
- **牵连**：`yoyo/monitor/tradingview.py`、`yoyo/monitor/tradingview.applescript`、前端整卡点击状态；依赖 macOS Automation/Accessibility 权限和 `~/Library/Application Support/Fable/ImpulseMonitor/tradingview-layout.txt` 中的已保存布局，不涉及信号规则、通知或交易执行。

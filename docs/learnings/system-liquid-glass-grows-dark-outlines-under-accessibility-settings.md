# 系统液态玻璃在辅助功能设置下会长出深色描边：要看真机，必要时自绘

- **问题**：原生纪律 App 2.2.0 在模拟器里玻璃按钮干净透亮，owner 真机截图里周期按钮外圈一层灰色光晕，比例按钮、关闭键、杠杆键、卖出键全有黑色描边，整体发灰，“色调不对”。
- **死胡同**：以为是配色或白色填充不够，先调 `FrostedCard` 白度和背景——卡片确实变亮，但按钮描边纹丝不动，因为描边来自系统 `.buttonStyle(.glass/.glassProminent)`、`glassEffect`、`Menu` 和工具栏自动玻璃，不归我们画。
- **有效路径**：在模拟器执行 `xcrun simctl ui <id> increase_contrast enabled` 后复现了同样的深色描边（与“增强对比度/降低透明度”一致）。改为 App 自绘：材质 + 白色薄纱 + 柔和阴影的 `frosted()`、四种 `PillButtonStyle`、自绘分段与下拉、`RoundIcon`；工具栏项加 `.sharedBackgroundVisibility(.hidden)`；系统 `Menu` 换成自绘下拉。开着增强对比度复核，所有控件无灰边。
- **通用规则**：给 owner 看的界面不能只在默认设置的模拟器上验收；先问或推断真机的显示/辅助功能设置，并在模拟器里用 `simctl ui … increase_contrast` / 深色模式复核。需要稳定外观的控件不要依赖系统自动材质。
- **牵连**：`yoyo/discipline_app/native/SpikeDiscipline/Shared/DesignSystem.swift`、各页面按钮与工具栏；见 [ui-rework-not-css-patches 记忆] 关于必须拿真机证据。

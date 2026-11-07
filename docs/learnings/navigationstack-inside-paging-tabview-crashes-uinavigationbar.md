# 分页 TabView 里每页一个 NavigationStack，会让 UINavigationBar 布局断言崩溃

- **问题**：原生纪律 App owner 反馈“经常闪退”。真机拉回 6 份 .ips，版本 2.0.3–2.2.0 全部同一栈：`NSInternalInconsistencyException`，`-[NSAssertionHandler …]` ← `-[UINavigationBar layoutSubviews]`，主线程 CATransaction 提交时触发，没有我们代码的帧。
- **死胡同**：先按“列表重复 id、强制解包、数组越界”做了一轮代码审计并修掉（重复 id 去重、`uniqueKeysWithValues` 换成可合并版本）——这些是真隐患，但崩溃栈里根本没有业务帧，说明不是它们。模拟器里也一直复现不了，只能靠真机日志。
- **有效路径**：用 `xcrun devicectl device copy from --domain-type systemCrashLogs` 拉日志，按 `lastExceptionBacktrace` 看出是导航栏布局断言；对照时间线，2.0.3 起把五个页面（各带 `NavigationStack`）放进 `TabView(.page)` 实现左右滑。滑动时 UIKit 在分页容器里移动/布局多个导航控制器，触发断言。修法：页面不用系统导航栏（自绘标题 `PageHeader`，二级页改弹层），翻页用纯 SwiftUI `ScrollView(.horizontal)` + `.scrollTargetBehavior(.paging)`。
- **通用规则**：闪退先拿真机崩溃日志（devicectl 或 Xcode Organizer），看 `lastExceptionBacktrace` 再动手；栈里没有业务帧时优先怀疑容器组合（导航栈嵌分页/嵌滚动）。SwiftUI 里不要把多个 `NavigationStack` 放进可滑动的分页容器。
- **牵连**：`yoyo/discipline_app/native/SpikeDiscipline/App/SpikeDisciplineApp.swift`（RootView 分页）、`Shared/DesignSystem.swift`（PageHeader）、各页面文件；另见 `fullscreen-chart-inside-paging-tabview-needs-paging-off.md`。

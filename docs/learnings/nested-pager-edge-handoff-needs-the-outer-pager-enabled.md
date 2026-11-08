# 嵌套横向分页：要把边缘拖动交给外层，外层分页必须保持可用

- **问题**：原生 App 2.7.0 监控页把顶部 4 个菜单做成横向分页 ScrollView，嵌在 RootView 的主 tab 横向分页里。要求左右滑动只切 4 个菜单，但滑过第一个/最后一个菜单时仍能去相邻主 tab。
- **死胡同**：照 1.618 页的做法在 `tab == .monitor` 时 `.scrollDisabled` 外层分页，再用 `onScrollGeometryChange` 量内层的越界回弹（>70pt 就切主 tab），加 `.scrollBounceBehavior(.always)`。模拟器里切菜单正常，但边缘拖动什么都不发生。打日志看 `contentOffset.x` 一直停在 0 / 1206，没有任何越界——内层在边缘根本不回弹：嵌套同向 UIScrollView 到边缘时会让自己的 pan 失败，把手势让给外层；外层被禁用，这次拖动就丢了。所以“量回弹再手动切 tab”这条路在嵌套场景里不成立。
- **有效路径**：撤掉外层的禁用和自制越界检测，保持外层分页可用。UIKit 原生规则就是想要的行为：内层能滚的方向优先给内层（菜单之间切换），到边缘的拖动交给外层（跟手滑到 合约 / 1.618）。模拟器里两种拖动都验证过。
- **通用规则**：嵌套同向滚动/分页时先别锁外层，先在模拟器里打 `onScrollGeometryChange` 的 `contentOffset/contentSize` 看手势到底落在哪一层；只有外层的手势会和内层的“非滚动”手势（如 K 线平移）冲突时才锁外层（1.618 页就是这种情况）。
- **牵连**：`yoyo/discipline_app/native/SpikeDiscipline/Monitor/MonitorView.swift`（内层分页）、`App/SpikeDisciplineApp.swift`（外层 `.scrollDisabled(tab == .fib && target == nil)`，监控页不在条件里）。模拟器注入的第一下点击偶尔被吞，验收时点按后要截图确认。

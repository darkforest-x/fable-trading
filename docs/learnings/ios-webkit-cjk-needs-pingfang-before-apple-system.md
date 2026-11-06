# iOS 26.3 WebKit：-apple-system 打头的字体栈会把中文渲染成缺字方框

- **问题**：纪律 App 1.0.0 装进 iPhone 17 Pro 模拟器（iOS 26.3）后，WebView 里所有中文都是 "?" 方框；同一屏状态栏的原生 UIKit 中文（"Spike 纪律交易"）正常。旧版字体栈同样是 `-apple-system, BlinkMacSystemFont, …, "PingFang SC"`，所以这不是本次样式改动引入的。
- **死胡同**：第一反应是改动的 CSS 写坏了 `font-family`，或者是模拟器缺中文字体。前者说不通，因为旧栈也是 `-apple-system` 打头；后者被原生中文正常显示否定了。日志里的 `FontParser could not open filePath …simruntime` 看着像字体缺失，其实是误导。把 `"PingFang SC"` 留在栈尾也没用：WebKit 把中文字形交给了 `-apple-system`，不会再往后回退。
- **有效路径**：在模拟器 Safari 打开一页探针，每行用一个不同的 font-family 写同一句中文，一张截图就能定位。实测：默认字体、`"PingFang SC"`、`"Hiragino Sans GB"` 正常；`-apple-system` 和 `system-ui` 出方框。修法是把具名的 PingFang SC 放在 `-apple-system` 前面（`"SF Pro Text","SF Pro Display","PingFang SC",…,-apple-system`），并让 button/input 显式继承同一个栈。canvas 的 `ctx.font` 不走 CSS 继承，要单独改（`FibRangePrimitive.ts`）。
- **通用规则**：WebView 出现文字类渲染异常时，先用一页逐项对照的探针页在目标运行时截图，再动 CSS。中文界面的字体栈里，具名 CJK 字体要排在 `-apple-system`/`system-ui` 前面；canvas 文字单独检查。
- **牵连**：`yoyo/discipline_app/mobile/src/liquidGlass.css`（`--font-ui`）、`FibRangePrimitive.ts`。真机上是否也出现未验证（iPhone 不可连接）；旧版在真机上未见报告，但修法对真机无害。相关：[模拟器 Keychain 需签名](unsigned-simulator-builds-cannot-write-keychain.md)。

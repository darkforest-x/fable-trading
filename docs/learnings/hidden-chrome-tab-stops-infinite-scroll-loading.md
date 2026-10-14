# 被遮挡的 Chrome 标签页不会加载无限滚动——JS 滚动没用，要靠截图逼它出帧

- **问题**：2026-10-14 用 Claude in Chrome 抓 X 搜索结果（`from:RektProof`）。页面里跑的后台 JS 循环一直滚动，几分钟只拿到 15 条帖子，`document.visibilityState` 显示为 `hidden`：owner 的 Chrome 窗口被其他窗口挡住了。
- **死胡同**：
  1. 用 `window.scrollTo` 跳到页底：X 是虚拟列表，中间的帖子根本不会渲染，所以抓不到。
  2. 改成小步 `scrollBy` 加 `setTimeout` 的后台循环：隐藏页的定时器被节流到大约每分钟一次，而且 IntersectionObserver 不会触发，滚到底也不会加载下一页。
  3. 改用扩展发送 Page_Down 键：滚动位置变了，可还是没有新帧，加载器照样不触发。
- **有效路径**：扩展的 `scroll` 动作和 `screenshot` 动作都会强制页面出一帧，加载器随之触发。最省的做法是每翻一次页，就截一张 `scale 0.1` 的小图（只有几十个 token），然后再用 JS 抓取 DOM。即便这样，在隐藏标签页里每步都要来回调用一次工具，又慢又容易出错，滚动位置还会被重置。最可靠的办法是请 owner 把窗口露出来。
- **通用规则**：做浏览器抓取前，先查 `document.visibilityState`。如果是 `hidden`，先请用户让窗口可见，不要先写后台循环。依赖 rAF、IntersectionObserver 或 ResizeObserver 的页面，在隐藏状态下都不会推进。
- **牵连**：Claude in Chrome 的 `javascript_tool`、`computer.scroll` 和 `computer.screenshot`；X 搜索页；同类问题见 [background-tab-rendering-suspension.md](background-tab-rendering-suspension.md)。

# 隐藏图表浮层必须同时释放原生弹窗的模态阻挡

- **问题**：双击／双点需要隐藏所有图表浮层，再次操作恢复原状态；原生 `showModal()` 弹窗也属于浮层。
- **死胡同**：代码检查发现，只给弹窗设 `visibility:hidden` 或 `pointer-events:none` 会留下浏览器的模态阻挡，背景图表仍不可交互，用户无法双点恢复。直接清空或重建面板则会丢失填写值、展开状态与滚动位置。
- **有效路径**：用一个独立的可逆显示状态隐藏普通浮层，保留其 DOM 和布局；对已打开的原生弹窗记录 modal/nonmodal 模式，暂时 `close()` 释放背景，恢复时按原模式打开。切换工作区时丢弃暂存弹窗，避免离开后又自动打开。浏览器验证弹窗输入 37 在恢复后不变，期间没有提交请求。
- **通用规则**：先区分视觉隐藏、焦点可达性和浏览器 top layer 的模态语义。双点只接受两次短、近、单指触点；拖动、多指、取消与长按使候选失效。阻止触摸合成点击误中恢复后的控件，但保留 pointerup 传播，保证图表与拖动控制器结束手势。
- **牵连**：`yoyo/monitor/static/chart-focus.js`、`monday-reclaim-paper.js`、`monday-reclaim-paper.css`；`tests/monitor/frontend_chart_focus.test.cjs`；原始浏览器验收在 `output/playwright/chart-focus-20261027/`。图表实例和时间范围不因显示切换重建；全局实际报警独立于日常图表工具。

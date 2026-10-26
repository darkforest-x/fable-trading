# 触屏焦点回退不能提前关闭待点击的导航链接

- **问题**：iPhone/WebKit 上点击 SPIKE 展开菜单的「信号记录」或「形态巡检」，菜单收起而 URL 不变。真实事件顺序为链接 `pointerdown → pointerup → mousedown → main.focusin`，全局焦点离开处理立即关闭 details，随后 `mouseup` 和 `click` 改落到背景。
- **死胡同**：Chromium 的 390px 页面、鼠标点击和触屏模拟都能跳转；已有菜单测试也全部通过。只缩小视口不能证明 Safari 的触屏兼容性，旧测试没有覆盖实际生产控制器中的跨事件时序。
- **有效路径**：在 WebKit 的 iPhone 15 触屏配置中复现并记录事件链；记录 pointerdown 所属菜单，让该手势的焦点回退保留目标。链接 click 后下一任务再关闭菜单，让原生链接先完成默认激活；取消手势、键盘操作和路由切换清除手势标记。移除周一页重复的同步关闭监听。修复前回归测试失败，修复后真实 WebKit 点击已跳转。
- **通用规则**：导航收起须服从链接激活时序；手机故障至少用目标浏览器引擎和真实触屏事件验证。键盘焦点离开、外部点击与触屏合成焦点应分别检查，不靠统一 focusin 立即收起来处理。
- **牵连**：`yoyo/monitor/static/stitch-workspace.js`、`yoyo/monitor/static/monday-fib.js`、`tests/monitor/frontend_stitch_menu.test.cjs`。设计参考先由官方 Stitch `GEMINI_3_8_FLASH` 生成，项目 `8978791379095391159`、画板 `a79f437582af48efb7c69b890f97443b`；保留现有外观与原生链接，仅修事件时序。

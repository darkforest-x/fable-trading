# 导航高亮要跟随路由提交，而不只监听 hashchange

- **问题**：把顶部导航改为 SPIKE 菜单后验收发现，从“更多”进入研究页，模拟页仍显示为当前页。
- **死胡同**：只监听 `hashchange` 能覆盖原生锚点点击，却漏掉已有路由器通过 `history.pushState` 执行的页面切换。页面内容已更新，不代表导航状态已更新。
- **有效路径**：观察路由器已提交的 `data-workspace` / `data-account-route`，只同步导航展示，不再次调用路由生命周期；保留原生链接的路径。用“模拟→更多→研究→信号”验证高亮清除与设置，并验证没有二次 navigate。
- **通用规则**：导航改版先找出所有合法的路由入口和提交信号；URL 事件只是其中一条路径。弹层关闭后的焦点也要回到仍可见的入口，而不是已折叠菜单中的按钮。
- **牵连**：`yoyo/monitor/static/stitch-workspace.js`、`workspace-command.js`；`tests/monitor/frontend_stitch_menu.test.cjs`、`frontend_glass_navigation.test.cjs`。不触及页面数据、交易或后台调度。

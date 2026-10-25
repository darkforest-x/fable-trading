# Theme switching must preserve chart ownership and explicit route state

- **问题**：Apple Glass 的固定浅色分支覆盖了主题选择；壁纸图、图表预览、独立页面和 App 也各自持有颜色与画布。把默认首页改成模拟盘后，懒加载期间还会短暂显示旧信号内容。
- **死胡同**：只换根背景不能重绘 canvas；重建图表则会丢缩放、订阅和已选择交易。只改最终模块入口无法避免前置 HTML 在下载期间闪出不相关内容。
- **有效路径**：单一同步前置控制器仅存 light/dark，使用语义色值和路由局部适配；现有 canvas 用 `applyOptions` 更新，不替换 series、数据或范围，统计曲线更换配色而保留成交时间和值。模拟盘首页在模块下载前就确定 pane，空根地址不恢复旧币种或旧交易。
- **通用规则**：外观是呈现状态，不是业务生命周期。检查首帧、已打开弹层、刷新持久性、窄屏与跨标签同步；默认入口应验证无 hash 的真实地址，而非只验证深链接。
- **牵连**：`theme.js`、`design-tokens.css`、`night-mode.css`、App `chartAppearance.ts`、`app.js`；106 项前端回归、App TypeScript/Vite 构建与线上浏览器验收。旧 system 值回退浅色，Owner 仅要求两个模式。

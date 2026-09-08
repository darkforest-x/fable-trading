# 标注鼠标快捷操作应调用原提交按钮，并在模拟界面验证

- **问题**：Owner 希望项目 77 审核时右键直接提交，同时保留原有预框检查、更新和保存语义。
- **死胡同**：全局鼠标映射会影响别的网站；伪造 Ctrl+Enter 受键盘布局和焦点作用域影响；直接写 annotations API 会绕过编辑器验证。用真实样本测试则会把测试点击冒充人工确认。
- **有效路径**：核对已装前端与实际 DOM，限定项目/审核路由/任务身份/编辑器，点击唯一可用的原 `button[aria-label="submit"]`。输入控件上的右键和 Shift+右键保留菜单，900ms 防连击跨题继续生效。浏览器模拟页使用相同选择器但只递增本地计数，真实样本只验脚本已加载，不提交。
- **通用规则**：便利入口只改变触发方式，不另建保存路径。第三方静态包的本地补丁要保留原始字节，先持久化恢复凭据再改包；回滚不能依赖尚存的新版源码，未知版本或外部改动不得覆盖。
- **牵连**：`yoyo/review/label_studio_right_click.js`、`yoyo/review/install_label_studio_shortcut.py`、`tests/fixtures/label_studio_right_click.html`、[3060 审核说明](../ops/YOLO_REVIEW_FROM_3060.md)。

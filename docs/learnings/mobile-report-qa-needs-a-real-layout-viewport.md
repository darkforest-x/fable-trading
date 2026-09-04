# 手机报告验收必须验证真实布局视口

- **问题**：一个 390px 宽的 Chrome 命令行截图仍把报告右侧裁掉，看起来像 HTML 在手机上横向溢出。
- **死胡同**：继续缩短标题只能掩盖首屏症状；macOS headless Chrome 即使输出 390px 图片，也可能保留更宽的最小布局视口，再把画面裁成 390px，因此图片尺寸不是 viewport 证据。
- **有效路径**：先让模板全局采用 `border-box`，使正文的 `width: 100%` 包含左右 padding；再用 Playwright 显式设置 390×844 viewport，同时检查 `innerWidth == clientWidth == scrollWidth == 390`，最后才做视觉复核。
- **通用规则**：移动端报告验收先读取浏览器的布局尺寸与 `scrollWidth`，再看截图；若二者不等，先区分页面溢出和截图工具裁切。
- **牵连**：`scripts/md_to_html.py`、`tests/test_md_to_html.py`、所有由该脚本生成的 `analysis/html/*.html`；历史 HTML 不会自动重生成。

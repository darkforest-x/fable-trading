# 涨跌与盈亏颜色应共享交易语义变量

- **问题**：同一首页的跌幅、账户亏损、行情列表分别呈现三种红色；正收益还可能继承导航的品牌绿色。
- **死胡同**：只统一主按钮和页签颜色不能修复数据色，因为 r-negative、is-negative、down 分别依赖 red、negative-strong、trade-short。
- **有效路径**：从浏览器计算样式定位三条来源，再将旧别名统一映射到交易语义变量；r-positive/r-negative 显式使用 positive-text/negative-text。保留品牌 mint 和图表多线颜色各自的职责。
- **通用规则**：验收颜色时同时比较同一语义在不同组件上的计算值，不能只看设计 token 的定义。缺失或过期的方向读数必须移除多空色，而不是把缺失当零。
- **牵连**：design-tokens.css、compact-workspaces.css、market-sync.js；新鲜度逻辑与真实分数计算保持原契约。Stitch 页面 66017cd619d74ab5bda6597d23f51d2b 提供视觉稿和代码。

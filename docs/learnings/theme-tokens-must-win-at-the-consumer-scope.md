# 主题令牌必须在消费组件的同级作用域胜出

- **问题**：新视觉层在 `:root` 定义了绿色深色主题，但切换后主按钮仍变成旧系统的蓝色，页面边缘回弹区域也保留了浅色背景。
- **死胡同**：只修改 `:root` 或给单个按钮补颜色，看起来能修一处，却赢不了旧样式中 `body.theme-dark` 的更高优先级；继续逐组件覆盖会让主题语义再次分叉。
- **有效路径**：在 `body.hb-shell.theme-dark` 作用域完整重声明设计令牌，让所有消费 `var(...)` 的组件同时继承正确语义；主题切换时也同步给 `html` 增删主题 class，使根背景与 `color-scheme` 一致。最后用浏览器读取 computed style，而不是只看源码判断是否生效。
- **通用规则**：多层样式表共存时，先检查令牌实际由哪个选择器提供；新主题必须在相同或更高的级联作用域一次性覆盖完整 token 集，并验证 `html`、`body` 和关键组件的 computed style。
- **牵连**：`src/webapp/static/app.js`、`src/webapp/static/clauseos.css`、旧 `style.css` 的 `body.theme-dark` 规则、图表重载与页面回弹背景。

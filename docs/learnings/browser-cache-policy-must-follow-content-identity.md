# 浏览器长期缓存必须绑定内容身份

- **问题**：工作台 HTML、实时 API 和静态脚本共用 no-store，重复访问每次下载全部代码。
- **死胡同**：只去掉 no-store 或给固定文件名加一年缓存，会让新代码仍被旧浏览器副本遮住；只改上游也会被网关覆盖。
- **有效路径**：新鲜 HTML 提供内容哈希清单，入口与懒加载资源使用同一版本。仅匹配当前字节的 JS/CSS 允许 private immutable；未版本化资料协商缓存，API 仍 no-store。压缩与缓存分开验证，并核对网关保留上游策略。
- **通用规则**：先区分实时数据、可变资料和按内容寻址资源，再逐跳核查响应头与版本失效。
- **牵连**：yoyo/monitor/asset_delivery.py、server.py、public_tunnel.py、static/module-loader.js；缓存不改变信号新鲜度门或交易参数。

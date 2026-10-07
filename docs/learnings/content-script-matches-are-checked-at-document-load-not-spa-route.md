# 扩展内容脚本只在页面加载时匹配 URL，单页应用里点进去的频道不会注入

- **问题**：跟单扩展的 `content_scripts.matches` 是 `https://discord.com/channels/*`，owner 的感受是"之前只监控了几个频道"。Discord 网页版是单页应用：从 `discord.com/app` 或私信页打开、再点进服务器频道，地址栏会变成 `/channels/...`，但这只是 pushState，不是一次新的页面加载。
- **死胡同**：怀疑是 Discord 改版导致选择器失效。`[id^="chat-messages-"]` 这些选择器仍然有效，问题不在这里。也怀疑过后端在丢消息：后端对未登记的频道确实会静默丢弃，但这次后端日志里连一个 ingest 请求都没有。
- **有效路径**：Chrome 只在页面加载那一刻用 URL 匹配内容脚本，之后单页路由跳转不会再次注入，所以只有直接用频道链接打开的标签页才会被监控。改成匹配整个 `https://discord.com/*`，在脚本里按 `location.pathname` 判断：`/channels/<guild>/<channel>` 才读；`@me` 私信一律不读，也不发送。上报 `guild_id`，后端据此把首次出现的服务器频道自动登记为"仅监控、不下单"；没有交易路由的频道原本会回落到默认交易所（OKX）下单。
- **通用规则**：给单页应用写扩展，`matches` 按站点写，具体页面在脚本里按当前路由判断，并且每次处理时重新读取路由（`location`），不要在初始化时缓存。扩大匹配范围之后，要在代码里主动排除隐私页面（私信），不能依赖 URL 匹配挡住它们。
- **牵连**：`tools/discord_copier_extension/`（manifest 0.2.0、`content.js` 中的 `getGuildId`）、`yoyo/copier/discord_channels.py`。Chrome 里需要重新加载扩展才会生效。

# Stitch 生成超时后取回画板：get_screen 要传资源名 name

- **问题**：2.7.0 同时发了 5 个 `generate_screen_from_text`，3 个约 4 分钟返回，另外 2 个客户端 25 分钟读超时。
- **死胡同**：`get_screen` 传 `projectId` + `screenId` 返回 “Request contains an invalid argument”，容易误判为画板没生成、进而重发生成（违反 CLAUDE.md 的 Stitch 恢复规则，也浪费额度）。
- **有效路径**：先 `list_screens`（返回顺序不是按时间，按标题关键词筛），找到两个已完成的画板 ID；再用 `get_screen` 只传 `name="projects/<project>/screens/<id>"` 读到 screenshot / htmlCode 下载地址，下载后解 `dataString`。
- **通用规则**：超时 ≠ 失败。超时后先按标题在 `list_screens` 里找，再用资源名 `name` 调 `get_screen`；两步都确认没有，才考虑重发。
- **牵连**：`yoyo/discipline_app/design/native-2.7.0/*-screens.json` 记录了 `recovered` 来源；与 CLAUDE.md「Stitch 列表同步故障恢复」一条配合使用。

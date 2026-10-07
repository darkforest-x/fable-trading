# Chrome 本地扩展监听 Discord 网页

这个方案不使用 Discord Bot，也不使用用户 Token。它在你本机 Chrome 里读取当前 Discord 网页上**新增出现的交易信号**，然后提交到本项目已有接口：

```text
Discord 网页新信号 -> Chrome 扩展 -> /api/messages/ingest -> DeepSeek -> 风控 -> OKX/dry_run
```

## 使用步骤

1. 启动后端：

```bash
cd /Users/zhangzc/discord-okx-copier
PYTHONPATH=. .venv/bin/python -m src.main api-only
```

2. 打开 Chrome 扩展管理页：

```text
chrome://extensions
```

3. 打开右上角「开发者模式」。
4. 点「加载已解压的扩展程序」。
5. 选择目录：

```text
/Users/zhangzc/discord-okx-copier/browser-extension
```

6. 打开 Discord 网页版并进入要监听的频道：

```text
https://discord.com/channels/...
```

7. 点浏览器右上角扩展图标，确认「监听当前 Discord 网页」已开启。
8. 弹窗会显示当前频道和「最新可见信号」。如果你已经打开频道，可以点「提交当前可见最新信号」手动测试。
9. 让目标频道出现一条**新交易信号**，再打开管理台信号台查看：

```text
http://127.0.0.1:5173/messages
```

## 行为说明

- 扩展默认跳过页面加载前的历史消息，避免一打开频道就批量提交旧消息。
- 自动监听只提交看起来像交易信号的消息，例如包含「飞扬合约策略 / 进场点位 / 止损点位 / 止盈点位」等关键词。
- 弹窗手动按钮会在当前可见区域内向上寻找最新交易信号；如果最新消息只是提醒/闲聊，不会误提交。
- 扩展只处理网页 DOM 中能读到的文本；图片喊单目前只会提交简单的图片提示，不做 OCR。
- 每条 Discord 消息会用原始消息 ID 作为 `external_id`，重复点击不会重复入库。
- 如果后端没启动，扩展弹窗会显示提交错误。
- 本方案不触碰 Discord API 和用户 Token，但仍依赖 Discord 网页结构。Discord 改版后，选择器可能需要调整。

## 风险和建议

- 这不是 Discord 官方集成，不能承诺零风险。
- 风险低于用户 Token 轮询，因为它不伪装 API 客户端，也不高频请求 Discord。
- 建议先保持 `dry_run=true`，确认消息能正确进入管理台并被 AI 解析后，再考虑交易执行。

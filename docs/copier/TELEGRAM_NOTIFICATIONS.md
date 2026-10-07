# Telegram 群通知

系统可以把关键事件推送到你的 Telegram 群：

- 新开单信号刚被 AI 识别
- 实盘下单已提交
- 模拟下单已记录
- Discord 里的 TP / SL / 平仓结果更新
- 交易所持仓新增、实际仓位变化、消失（可能 TP / SL / 手动平仓）
- OKX 下单失败
- 风控拒绝开单
- 可行动信号因置信度不足或执行开关关闭而跳过

## 配置

1. 在 Telegram 找 `@BotFather` 创建或复用一个 Bot。
2. 把 Bot 加入你的目标群，并确保它可以发消息。
3. 获取群 `chat_id`。群 ID 通常类似 `-1001234567890`。
4. 在 `.env` 填入：

```bash
TELEGRAM_BOT_TOKEN=你的BotToken
TELEGRAM_NOTIFY_CHAT_ID=-100xxxxxxxxxx
TELEGRAM_NOTIFY_ENABLED=true
```

## 重启与测试

重启 API：

```bash
launchctl kickstart -k gui/$(id -u)/codex.discord-okx-api
```

测试通知：

```bash
curl -sS -X POST http://127.0.0.1:8080/api/notifications/telegram/test | python3 -m json.tool
```

如果返回 `{"ok": true}`，群里应该能看到测试消息。

## 控制按钮

发送按钮面板：

```bash
curl -sS -X POST http://127.0.0.1:8080/api/notifications/telegram/panel | python3 -m json.tool
```

也可以直接在配置好的通知群/频道里发：

```text
/panel
```

面板按钮：

- `Ping`：确认 Bot 在线
- `状态`：查看实盘/模拟、dry_run、kill switch、保证金比例、杠杆等
- `开单历史`：ChartPrime 博主历史开单列表，每页 12 笔，支持 `上一页` / `下一页`
- `频道分析`：ChartPrime `988830102957736027` 频道历史开单、胜率、理论收益摘要
- `亏损样本`：文档中保存的明确 SL 亏损样本
- `我的订单`：本系统实际提交到 OKX 的订单列表
- `最近信号`：最近开仓信号
- `刷新`：回到主面板

按钮面板会尽量保持核心按钮位置固定；进入 `开单历史` 时，只在固定按钮下方额外显示 `上一页` / `下一页` 翻页行。

当前实盘口径已经收敛为只做 Discord 频道 `988830102957736027`，仓位为账户全仓保证金、5x 杠杆。例如账户 500 USDT 时，保证金 500 USDT，名义仓位约 2,500 USDT，并限制最多 1 个持仓。

## 自动推送规则

- `新开单信号已识别`：Discord 新消息被解析为 open 时立即推送，随后仍会继续推送下单成功、风控拒绝或执行失败结果。
- `交易结果更新`：Discord 新消息包含 `TARGET HIT`、`TP`、`SL hit`、`trade closed`、止盈/止损命中等语义时推送，不会重复创建订单。
- `交易所持仓已出现/变化/已消失`：后台每 60 秒只读同步一次持仓，使用 USDT 实际仓位展示。首次启动只记录快照，不推送；之后发现变化才推送。持仓消失会提示可能是 TP / SL / 手动平仓，最终成交仍以交易所为准。

## 临时静音

管理台「控制中心」里关闭 `Telegram 通知`，或把 `.env` 的 `TELEGRAM_NOTIFY_ENABLED=false` 后重启。

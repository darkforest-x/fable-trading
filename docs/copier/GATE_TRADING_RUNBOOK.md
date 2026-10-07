# Gate 实盘接入步骤

## 1. 创建 Gate API Key

在 Gate 后台创建 APIv4 Key，只授予合约交易需要的权限，不授予提现权限。

## 2. 配置 `.env`

```env
EXCHANGE=gate
GATE_API_KEY=你的 Gate API key
GATE_SECRET_KEY=你的 Gate secret
GATE_SETTLE=usdt
GATE_TESTNET=false
```

如果先用测试网，把 `GATE_TESTNET=true`。测试网和实盘 key 不能混用。

## 3. 频道路由

当前系统支持按 Discord 频道选择交易所：

```json
{
  "988830102957736027": "okx",
  "1226095564073205780": "gate"
}
```

也就是说，旧频道继续走 OKX；新加的频道走 Gate。没有写入映射的频道会走默认交易所，也就是 `EXCHANGE` / `config.yaml` 里的 `okx.exchange`。

## 4. 保持风控设置

Woods 频道当前按“全仓保证金 5x 名义仓位”计算；ChartPrime 仍走 OKX 5x：

```json
{
  "channel_leverage_map": {
    "988830102957736027": 5,
    "1226095564073205780": 5
  }
}
```

Gate 的持仓接口里 `leverage=0` 表示全仓/跨保证金；系统按 `可用余额 × 5` 计算 Woods 订单名义仓位。

## 5. 先跑只读预检

```bash
cd /Users/zhangzc/discord-okx-copier
.venv/bin/python scripts/gate_live_preflight.py
```

预检只读取余额、持仓、合约和行情，不会下单。

## 6. 重启服务

```bash
launchctl kickstart -k gui/$(id -u)/codex.discord-okx-api
```

## 7. 小额验证顺序

当前已经进入实盘。重启后先查看 Telegram 的“跟单系统已启动”状态卡，确认 Gate 为正常状态；首单完成后在 Gate 后台核对主订单、TP 触发单、SL 触发单。

# OKX 实盘接入步骤

目标：先完成实盘只读连通性验证，再切换真实下单。不要跳过预检。

## 当前状态

- `.env` 已配置 OKX key/secret/passphrase。
- 当前 `OKX_DEMO=true`，连接的是 OKX 模拟盘。
- 当前 `dry_run=true`，系统不会真实下单。
- 当前 `okx_execute=true`，一旦 `dry_run=false` 且 `OKX_DEMO=false`，符合风控的信号会进入真实下单流程。

OKX 官方 Python SDK 参数：`flag=0` 是生产实盘，`flag=1` 是模拟盘。

## 1. 准备实盘 API key

在 OKX 创建生产环境 API key：

- 开启交易权限。
- 不要开启提现权限。
- passphrase 与 `.env` 中保持一致。
- 如果 OKX 账户开启了 IP 白名单，需要确认这台机器的出口 IP 已加入白名单。

不要把 key 粘贴到聊天里；只写进本机 `.env`。

## 2. 先保持 dry run，切到实盘只读环境

编辑 `.env`：

```dotenv
OKX_API_KEY=你的实盘 API key
OKX_SECRET_KEY=你的实盘 secret
OKX_PASSPHRASE=你的实盘 passphrase
OKX_DEMO=false
```

保持数据库/前端设置：

```text
dry_run=true
kill_switch=false
require_stop_loss=true
```

重启 API：

```bash
launchctl kickstart -k gui/$(id -u)/codex.discord-okx-api
```

## 3. 运行只读预检

```bash
.venv/bin/python scripts/okx_live_preflight.py
```

预检通过的关键条件：

- `mode` 是 `live`
- OKX 三个 key 都是 `true`
- `balance.available_usdt` 或 `balance.total_usdt` 大于 0
- `dry_run` 仍然是 `true`
- `kill_switch` 是 `false`
- `require_stop_loss` 是 `true`
- `instrument_check` 中白名单合约可读

## 4. 建议第一阶段风控

实盘首轮建议保守：

```text
position_pct=0.001
max_leverage=3
max_open_positions=1
max_position_per_symbol=1
symbols_whitelist=[BTC, ETH]
require_stop_loss=true
dry_run=true
```

`position_pct=0.001` 表示每单用可用 USDT 的 0.1% 作为保证金。确认下单、撤单、止损止盈链路稳定后，再逐步提高。

## 5. 最终切实盘

只有在预检通过后，再把 dry run 关闭：

```bash
curl -sS -X POST http://127.0.0.1:8080/api/control/dry-run \
  -H 'Content-Type: application/json' \
  -d '{"enabled":false}'
```

切回模拟/只读保护：

```bash
curl -sS -X POST http://127.0.0.1:8080/api/control/dry-run \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true}'
```

紧急停止：

```bash
curl -sS -X POST http://127.0.0.1:8080/api/control/kill-switch \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true}'
```

## 6. 实盘后检查

查看持仓和余额：

```bash
curl -sS -X POST http://127.0.0.1:8080/api/orders/sync | python3 -m json.tool
```

查看订单流水：

```bash
curl -sS http://127.0.0.1:8080/api/orders | python3 -m json.tool
```

## 注意

实盘下单会产生真实资金风险。信号回测收益不等于实盘收益，实盘会受滑点、手续费、资金费率、未成交、网络延迟、交易所规则、API 权限和风控配置影响。

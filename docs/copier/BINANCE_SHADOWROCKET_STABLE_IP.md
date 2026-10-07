# Binance API Stable IP With Shadowrocket

目标：让 Binance API 请求始终走同一个出口 IP，避免 API 白名单反复失效。

当前问题：

- 机器开了 Shadowrocket/TUN 后，Binance API 请求可能走代理出口。
- 出口节点变化时，Binance 会看到不同 IP。
- Binance API 白名单只允许固定 IP，所以会报：
  `Invalid API-key, IP, or permissions for action, request ip: xxx.xxx.xxx.xxx`

当前最近一次检测到的出口 IP：

```text
185.212.57.77
```

## 推荐方案：固定一个代理节点

这是最稳的方式。把所有 Binance API 域名固定走同一个节点，然后把这个节点的出口 IP 加到 Binance API 白名单。

在 Shadowrocket 当前配置的规则区，把下面规则放到 `FINAL` 前面。

把 `你的固定节点名` 替换成 Shadowrocket 里真实的节点名称，例如 `US-VPS-1`。

```ini
DOMAIN,fapi.binance.com,你的固定节点名
DOMAIN,api.binance.com,你的固定节点名
DOMAIN,sapi.binance.com,你的固定节点名
DOMAIN,papi.binance.com,你的固定节点名
DOMAIN,dapi.binance.com,你的固定节点名
DOMAIN,fstream.binance.com,你的固定节点名
DOMAIN-SUFFIX,binance.com,你的固定节点名
```

示例：

```ini
DOMAIN,fapi.binance.com,US-VPS-1
DOMAIN,api.binance.com,US-VPS-1
DOMAIN,sapi.binance.com,US-VPS-1
DOMAIN,papi.binance.com,US-VPS-1
DOMAIN,dapi.binance.com,US-VPS-1
DOMAIN,fstream.binance.com,US-VPS-1
DOMAIN-SUFFIX,binance.com,US-VPS-1
```

然后打开 Binance API 管理页面，把该固定节点的出口 IP 加入白名单。

## 备选方案：Binance API 直连

如果你不想让 Binance 走代理，可以把规则写成 `DIRECT`。

```ini
DOMAIN,fapi.binance.com,DIRECT
DOMAIN,api.binance.com,DIRECT
DOMAIN,sapi.binance.com,DIRECT
DOMAIN,papi.binance.com,DIRECT
DOMAIN,dapi.binance.com,DIRECT
DOMAIN,fstream.binance.com,DIRECT
DOMAIN-SUFFIX,binance.com,DIRECT
```

注意：直连 IP 取决于当前网络。重启路由器、切换 Wi-Fi、换热点，都可能让 IP 变化，所以不如固定代理节点稳定。

## TUN / Fake-IP 额外配置

如果 Shadowrocket 里开启了 TUN 或 Fake-IP，需要把 Binance 域名加入 Fake-IP 排除 / DNS 排除。

可加入：

```ini
*.binance.com
fapi.binance.com
api.binance.com
sapi.binance.com
papi.binance.com
dapi.binance.com
fstream.binance.com
```

否则系统可能把 Binance 解析成 `198.18.x.x` 这种 TUN 假 IP，导致请求仍然走错出口。

## 配完后的验证

配置完成后，在项目目录执行：

```bash
cd /Users/zhangzc/discord-okx-copier
curl -sS https://api.ipify.org && echo
curl -sS https://fapi.binance.com/fapi/v1/time && echo
.venv/bin/python - <<'PY'
from src.binance.client import BinanceClient
c = BinanceClient(timeout=20)
print(c.health_check())
PY
```

预期结果：

- `curl https://api.ipify.org` 显示固定出口 IP。
- `curl https://fapi.binance.com/fapi/v1/time` 能返回 Binance 时间。
- `BinanceClient().health_check()` 返回 `{'ok': True, ...}`。

如果仍然返回 `request ip: xxx.xxx.xxx.xxx`，把那个 IP 加进 Binance API 白名单，或者检查 Shadowrocket 是否切到了别的节点。

## 配好后要做的事

告诉 Codex：“Binance 白名单配好了”，然后让 Codex 执行：

1. 重新检查 Binance 连接。
2. 同步飞扬已有 ETH/BTC 挂单到交易所最高杠杆。
3. 确认后续新单会按：
   - 名义仓位：账户总权益 × 12
   - 交易所杠杆：该合约允许的最高倍


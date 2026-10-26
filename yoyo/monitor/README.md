# Fable · Impulse Monitor

## 信号中心分周期、多空排除名单（2026-10-17）

Owner明确要求将V13.1全历史累计净R倒数20币用于信号中心。规则版本
`signal-universe-20261017-bottom20-v1`：15m多/空、1H多/空分别使用各自20币名单，
不是把80个币种/方向条目合并为全周期禁用。此前全周期10币排除及USDT线性永续要求继续有效。
完整源名单、笔数和净R在
`experiments/active/exp-spike-v131-symbol-summary-20261017-v1/results_v1/bottom20_20261017_symbols.csv`
的`full_history_retrospective`、`side != 0`行。此为Owner选择的范围限制，不是新增盈利验证。

`contracts/signal_universe.py`是单一判定来源。扫描观察入口拒绝新记录；Telegram/Bark在发送前再次
检查已排队事件；自动执行在读取信号、准备、下单前和等待重试时分别检查。现有参考记录与自有仓位
继续更新退出，不删除账本、不重置切点。旧记录仍可查询，但命中名单不显示为可用新鲜信号。
只按合约调用的市场发现与独立均线手动账户保持原范围；自动执行器的共享父类入口为手动事件保留
原判定，不能因同一引擎继承关系误套SPIKE分组名单。

研究名称只做明确映射：LUNA2→LUNA、1000XEC→XEC、1000RATS→RATS；不自动剥除任意数字前缀，
不将LUNA映射到LUNC。部署时公开OKX在交易永续快照中，四组分别有10/9/13/8个对应合约；
未上市或已下架名称仍留在规则中，不重新抓取历史。原始部署核对与测试在`output/signal-bottom20-20261017/`。

来源：[Binance LUNA2标的说明](https://www.binance.com/en/support/announcement/detail/3b7184c80d544586993045a0e6e36e57)、
[OKX LUNA永续](https://www.okx.com/en-us/help/okx-to-list-usdt-margined-perpetual-for-luna)、
[Binance 1000XEC合约](https://www.binance.com/ar-BH/support/announcement/detail/974acd319e2343a094a9485d99fd5049)、
[Binance 1000RATS官方公告](https://www.binance.com/en/square/post/1407623093433)。

## VIDYA 趋势线回踩模拟盘（2026-10-12）

Owner 要求把 VIDYA 趋势线回踩做成独立菜单和模块，对所有币种跑实时模拟实盘。入口为侧栏
“验证与运行 → VIDYA 回踩”（`/#vidya`）。只做模拟：不下单、不推送、不持有密钥；不改动现有
信号、通知、账本和新鲜度门。

- **规则**（`vidya_retest.py`，回测与实时共用同一份代码）：BigBeluga Volumatic VIDYA 逐行复现，
  Length 10 / Momentum 20 / 通道 2 ATR(200)。多头时 K 线影线碰到趋势线且收盘守住、上一根未碰线；
  碰线 K 线同向直接确认，反向等下一根同向收盘守住；确认时 Delta ≥ +50%（空头 ≤ −50%）；
  止损在碰线/确认 K 线极值外 1 倍 ATR(14)。`tl-2r` 止盈 2R；`tl-trend` 趋势云变色收盘离场，碰线容差 0.2 ATR。
- **成交与成本**：确认收盘后下一根开盘成交，20bp 往返（与其他模拟账本相同），按每笔 1% 风险复利统计。
- **数据**：每小时刷新 OKX `live` 状态的加密币 `-USDT-SWAP`（`instCategory=1`、非盘前；代币化股票和大宗商品不在范围内，已混入的预热记录会被清除）；每根 15m 收盘后约 20 秒拉取已确认 K 线，
  独立进程限速 4 次/秒（扫描器保持自己的 8 次/秒）。只取最近 1440 根；在 ETH/BTC/SOL 共 444 个截断点上，
  最新一根的趋势、趋势线和 Delta 与全历史计算 100% 一致。
- **来源**：首次见到某币时按同一规则回放约 10 天作为“预热回算”，只供对照，不计入实时统计；
  实时从启用后第一根新 K 线开始。停机后补算缺失 K 线以继续管理持仓，但收盘超过 30 分钟的确认
  不开新仓（记为“信号已过期”）；停机超过可取历史则从最新一根重新开始并记录缺口。
- **文件**：`vidya_retest.py`（指标、规则、回测）、`vidya_retest_worker.py`（独立进程，唯一写者）、
  `vidya_retest_api.py`（只读 API 与进程守护）、`static/vidya-retest.{js,css}`；运行库为运行目录下的
  `vidya_retest.sqlite3`。API：`GET /api/vidya-retest/overview|trades|market|backtest`。
- **历史回测页**（`static/vidya-history.js`）：页面顶部切到“历史回测”。九个方案 A–I 与 2026-10-11 回测报告同口径、同数字（`experiments/active/exp-vidya-report-nine-configs-20261011-v1/`，`build.py` 逐个核对报告的笔数与每笔平均 R，不一致就中止）：核心数字、K 线进出场图（趋势线、入场/出场、止损/止盈；1 天至 1 年，长范围在服务端合并 K 线；点交易跳转）、累计 R、分年与分段、多空/离场/Delta 分组、九方案对比、4009 笔交易明细。页面下方另列趋势线回踩三个币对照与 Length 敏感性（标准口径：次开成交、20bp，来自 `exp-vidya-trendline-retest-20261012-v1`）。K 线缓存在 `data/vidya_retest/candles_ETH_{15m,1h}.npz`（不入 git，由 build.py 生成）。接口：`GET /api/vidya-retest/history` 与 `/api/vidya-retest/history/candles`。
- **证据**：`experiments/active/exp-vidya-trendline-retest-20261012-v1/`（`build.py`、`results.json`、
  `pine/`）。ETH 2022-01～2026-10 次开成交 20bp：`tl-2r` 53 笔 +0.296R（匹配随机 −0.107R，p 0.0105），
  `tl-trend` 66 笔 +0.559R（p 0.05）；BTC 两版均为负，SOL 不一致。参数在 ETH 上挑选，状态 inconclusive，
  生产与训练资格均为 false；全币种表现以本模块前向记录为准。
- **测试**：`tests/monitor/test_vidya_retest.py`（规则语义、实时与离线逐笔一致、API 合同）。

## 当前运行：SPIKE V12.8（2026-09-24，服务 1.19.0）

Owner 要求信号中心使用最新 Pine 逻辑。普通信号生产者为 `v128_signals.py`：
15m 追加 V12.8 默认的 H1 SMA60 方向门，取本图 K 线开盘时已经完成的连续 60 根 H1；
多头严格高于、空头严格低于，缺小时或预热不足不出信号。其它监控周期保持原准入。
独立突破/联合生产者为 `v128_lines.py`，使用 V12.6 有序配对引擎（V12.8 的父版）和
V12.8 原价格退出；RSI 提前退出不属于该 Pine 版本。Python 趋势线 pivot 同值边界尚未
完成 TradingView 原生逐事件对账，不宣称所有市场/设置完全一致。

普通与联合账本均使用新协议和持久通知切点。旧 V9 普通信号、V11.2 联合记录及发送回执
保留在旧版视图；新版预热重算不补发通知。重启不会重置切点。普通启动和 YOLO 补充确认
均支持 Bark + Telegram，多空独立；联合信号继续支持双渠道，其原策略仅产生多头。
30 分钟新鲜度与固定成本未调整。此次没有更改 Pine 源文件。

## 自有域名与正式 Cloudflare 隧道（2026-10-18，已接通）

固定入口为 `https://spikeok.com/#market?tab=overview`。域名审核、Cloudflare DNS 激活和
通用 TLS 证书均已完成；两个公网节点的首页、健康接口及 app.js 返回 200，Owner 已确认手机
蜂窝网络可打开。正式隧道重启后同一地址恢复健康。当前状态以 `HANDOFF.md` 顶部为准。

Mac 的 Shadowrocket 访问已恢复：该域名使用本地动态 DNS、真实 IP 应答、系统 HTTP 代理
例外和精确 PROXY 规则，由 TUN 继续代理实际 IPv4；当前配置的 IPv6 支持关闭。
正常域名的首页、脚本、健康和市场接口均200，Chrome已加载完整行情与图表；旧临时入口
与ngrok连接器已退役。`status.public_ready` 是这台 Mac 网络路径的检查结果，不能单独
代表其他网络是否可达。无需将Cloudflare网站IP写入hosts；完整排障边界见
`docs/learnings/proxy-domain-success-needs-browser-and-address-family-checks.md`。
域名留在阿里云续费，DNS 托管到 Cloudflare；使用账户内的正式命名隧道，路由到
原 Caddy `127.0.0.1:8780`，由 Caddy 保留现有工作台访问规则。

完成 Cloudflare 添加域名、DNS 委派和本机 `cloudflared tunnel login` 后，创建或复用
本账户的命名隧道，将根域名 DNS 路由到其 UUID。凭据只保留在仓外私有目录。
本机安装命令如下（UUID 和源凭据路径必须取自真实创建结果）：

```bash
.venv/bin/python -m yoyo.monitor.public_tunnel start-cloudflare \
  --url https://spikeok.com --tunnel-id <TUNNEL_UUID> \
  --credentials-file "$HOME/.cloudflared/<TUNNEL_UUID>.json"
.venv/bin/python -m yoyo.monitor.public_tunnel status
```

`com.spike.public-cloudflare` 由 launchd 独立托管，Mac 登录后启动，使用持久 UUID
与精确主机名 ingress；未匹配的域名返回 404。凭据复制到 `PublicTunnel/` 的 0600
文件，服务参数不包含凭据内容。`named-endpoint.json` 保存当前配置，后续 `start`
继续该提供商，`stop` 同时停止公网连接器与网关，不操作交易执行器。

DNS/实名审核尚未就绪时返回 `pending_activation=true`，保留旧入口及其说明。
域名能实际返回工作台 `/api/status` 后，再运行 `start` 完成激活，更新 `access.txt`
并退役旧 Quick Tunnel 和 ngrok。固定名称不能保证源站永远在线；Mac 与网络仍需可用。
来源：[Cloudflare 本机管理隧道](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/)。

### 持续 1033 的有限自动恢复（2026-10-26）

`com.spike.public-tunnel-health` 独立于行情/巡检进程，每 60 秒检查本机源站、带正确 Host
的网关和公网 `/api/status`。只有三次连续 Cloudflare 1033、且前两层健康，才重启已经加载的
`com.spike.public-cloudflare`。不以 PID 或连接器 `/ready` 作为公网可达证明。
DNS/超时/403/502、源站异常及手动停止的连接器均不触发重启。重启尝试先落盘，冷却 10 分钟，
每小时最多 3 次；长期离线或检查间隔超过 90 秒后重新累计。没有修改代理、协议、凭据或交易服务。

```bash
.venv/bin/python -m yoyo.monitor.tunnel_watchdog install
.venv/bin/python -m yoyo.monitor.tunnel_watchdog status
.venv/bin/python -m yoyo.monitor.tunnel_watchdog stop
```

最后一条只停止自动恢复检查。原公网连接器停止后，检查会显示 `connector_disabled`，不会重新启动它。
状态与有限日志保存在原私有 `PublicTunnel/` 目录。自动恢复是应对失联的保护，不代表已证明底层网络
故障原因或永久消除断线。连续三次检查通常约 2–3 分钟，另加连接器启动与 Cloudflare 重连时间。

## 原 ngrok 固定公网访问（2026-10-15）

当前使用现有 ngrok 账号的固定 Dev Domain → Caddy `127.0.0.1:8780` → 工作台 `127.0.0.1:8766`。
入口保存在本机 `~/Library/Application Support/Fable/PublicTunnel/access.txt`，手机收藏其中的 `/#paper-desk` 地址即可。
已验证隧道重启后域名不变；LaunchAgent 在 Mac 登录后启动并负责异常重连，Mac 与工作台仍需在线。
首次设置用 `start-fixed --url https://<account-dev-domain>`；之后 `start/status/stop` 自动沿用保存的固定域名。
ngrok 使用已有的本机私有配置，令牌不复制到仓库或启动参数；HTTP 检查器关闭。
只有新入口实际返回工作台健康响应后，才移除旧 Quick Tunnel 服务。网关原有同源检查与桌面接口限制保留。

```bash
.venv/bin/python -m yoyo.monitor.public_tunnel status
.venv/bin/python -m yoyo.monitor.public_tunnel start
.venv/bin/python -m yoyo.monitor.public_tunnel stop
```

免费账号的固定域名不能自选名称；浏览器首次进入需点 Visit Site，此提示按官方说明由 Cookie 隐藏 7 天。
免费额度为每月 20,000 HTTP 请求 / 1 GB 下行；工作台会轮询，勿将固定地址理解为无限使用或持续可用保证。
来源：[固定域名](https://ngrok.com/docs/gateway/domains#dev-domains)、[免费版限制](https://ngrok.com/docs/pricing-limits/free-plan-limits)。

### 原 Quick Tunnel 试用配置（2026-09-24）

使用 Cloudflare Quick Tunnel → Caddy 免登录网关 `127.0.0.1:8780` → 原工作台 `127.0.0.1:8766`。
8771 继续通过统一工作台内部代理访问，不单独公开；监控与视觉服务无需改监听地址。
Owner 于 2026-09-24 明确要求去掉登录；页面、API、图片和下载均无需账号密码。
持有链接即可访问完整工作台。公网写请求仍先核对外部 HTTPS Origin，再转换为原有本机同源请求；
浏览器遗留的 Authorization、Cookie 等凭据不传给后端。
公网看图打开当前设备的 TradingView 网页，本机访问保留原桌面调用；网关拦截远程桌面打开接口。

```bash
brew install cloudflared caddy
.venv/bin/python -m yoyo.monitor.public_tunnel start
.venv/bin/python -m yoyo.monitor.public_tunnel status
.venv/bin/python -m yoyo.monitor.public_tunnel stop
```

独立 LaunchAgent 负责重启，`stop` 仅移除这两项公网服务，原监控继续运行。配置和日志在
`~/Library/Application Support/Fable/PublicTunnel/`；目录 0700，配置与入口说明 0600。
`access.txt` 只记录地址与免登录说明，不入 Git；旧凭据文件不再读取或使用。
`start` 先验证配置，有变化时仅重启 Caddy，保留正在运行的隧道和地址；重复执行不恢复登录。
`status` 显示最近取得的 URL、`access_mode=no_login` 与匿名可用性 `gateway_ready`。
隧道重建会产生新地址，Mac 需保持在线；Quick Tunnel 无 SLA、不支持浏览器 SSE，仅适合试用。
当前 VLM 服务端聚合模型 SSE 后返回 JSON，因此不依赖浏览器 SSE 通道；真实付费调用未在本次公网测试中验证。

## 统一通知中心（2026-09-24）

入口为侧栏“运维 → 通知中心”（`/#notifications`）。三类来源为 SPIKE V12.8 启动、
YOLO 补充确认、突破＋spike 联合信号，各自独立选择 Telegram / Bark。页面汇总渠道状态、
订阅、累计服务接受回执及失败/未知状态；记录支持渠道、来源、发送状态、方向、周期、
合约筛选和服务端分页。旧版回执单独标识，不冒充 V12.8 新通知。

`notification_center.py` 管理运行目录内的 `notifications.sqlite3` 订阅表和修改审计，
只读汇总原有普通/联合账本的四个 outbox。生产者与发送前检查使用同一订阅规则，
每个渠道只有一个发送循环，轮流处理普通与联合队列。一个来源未就绪不会阻塞另一个。
两渠道独立认领、持久化回执；同一事件不会因另一渠道成功而消失。

本次首次启用普通 Telegram 时另存经过交易所校时的启用切点，保留 Bark 原切点。
重启不重置；订阅重新开启以开启时点为新边界，不补发历史，YOLO 也校验原始启动时间。
关闭不会撤回已开始的 HTTP 请求；429 仍限次重试，未知结果不自动重发。
服务接受只说明 Telegram/Bark 接口接受请求，不代表设备收到或已读。

API 为 `GET /api/notifications`、`GET /api/notifications/events` 和
`PUT /api/notifications/routes/{topic}/{channel}`。写入只允许同源页面明确修改布尔开关，
不开放密钥修改、历史重推或测试推送。已有私有凭据继续由原渠道适配器加载。

以下章节记录先前版本的交付与操作背景；当前身份以本节、API 协议及事件源哈希为准。

## 联合信号通知（2026-09-22，监控服务 1.16.0）

Owner 授权“突破+spike”同时推送 Bark 和既有 TG 入口“Yolo均线密集交易系统”。
联合信号在独立 lines 账本中保存双通道队列、启用切点和回执；只发启用后首次发现、
收盘后30分钟内的 15m/30m/1H/4H 联合信号。历史、重复和普通趋势线突破不入队。
两通道独立发送；429 限次重试，发送结果未知不自动重发。页面显示各自回执。
V9 原始启动及 YOLO 追加仍保持原 Bark 范围，其 TG 继续关闭。通知不执行交易。
本次双通道测试均获服务端接受；测试消息不计为行情信号。
详见 `analysis/p1_spike_joint_notifications_20260922.md`。

## 历史迁移：SPIKE V9（2026-09-15）

Owner 已授权前端与 Bark 改为 V9，并删除此前信号。本次使用 V9 原始多空准入，
保留 15m/30m/1H/4H 与两阶段 Bark，Telegram 关闭。算法依次使用 V6 结构、
V7 布林压缩、V8 距六均线方向边界不超过 3ATR，再排除 USDC 底层资产、RV>50
和 UTC 周日计划开仓。参数来自已冻结 V9，未调参。

前端仅显示本次切点后的 V9 原始事件及其 YOLO 追加确认。价格、止损为确认收盘参考，
暂不计算持仓路径 R。独立 V7/V8 影子账本保留原版本，不是当前通知来源。

首次迁移流程（重跑 reset 幂等，不再删除新信号）：

```bash
.venv/bin/python -m yoyo.monitor.manage stop
.venv/bin/python -m yoyo.monitor.v9_migration --apply
.venv/bin/python -m yoyo.monitor.manage install
```

迁移取得服务独占锁，备份 `monitor.sqlite3` 到运行目录的私有 `backups/`，
再在一个事务中清空事件、候选、通知队列、相关回执及展示缓存，保存新启用时间。
纯行情预热缓存保留。数据库插入端拒绝旧协议、回放与原始收盘时间不晚于切点的事件，
所以冷启动不能恢复已清除的信号。运行回执在 `v9-reset-receipt.json`。
禁止在服务运行时手工删表；需要再次清空时应另建明确的 reset 操作，不改首次切点。

以下旧版本细节仅供追溯，现行信号/通知身份以本节和 `__init__.py` 为准。


Local, notification-only OKX all-live-perpetual monitoring, authorized by the
owner on 2026-09-08. Active signal periods are **15m / 30m / 1H / 4H / daily**. This service
has no exchange credentials or order endpoints.

## Open and operate

Open **http://127.0.0.1:8766** on this Mac. The backend serves the frontend and
read-only API from the same loopback origin. There is no separate frontend build.

The startup page presents confirmed signals as selectable cards: contract and
period, direction, original close, preparation bars, and Beijing confirmation
time. Fresh cards precede older records. Freshness requires the API flag and the
runtime's existing time limit, with elapsed time measured from the calibrated
status clock. Failed synchronization leaves cached records visible but removes
fresh emphasis. Notification receipts remain independent for TG and Bark.

Search, period and direction filters apply to the latest 2,000 loaded records;
cards load in batches of 24. The observation page has its own paginated cards
and is explicitly separate from confirmed startups. Only the selected contract
loads a structure chart. On narrow screens, selecting a card jumps to that
chart; the return button restores focus to the originating card. Static edits
take effect on a browser page reload without restarting the monitor.

Client behavior checks: `node --test tests/monitor/frontend_cards.test.cjs`.
These DOM-stub tests do not replace real browser layout and keyboard checks.

From `/Users/zhangzc/fable-trading`:

```bash
.venv/bin/python -m yoyo.monitor.manage install
.venv/bin/python -m yoyo.monitor.manage status
.venv/bin/python -m yoyo.monitor.manage restart
.venv/bin/python -m yoyo.monitor.manage stop
```

`install` is idempotent for an identical configuration; it does not replace an
unexpected existing LaunchAgent. `stop` unloads only this service; `install`
loads it again. The LaunchAgent starts on login and restarts a crashed process.
`restart` reloads source, preserving the SQLite journal and deduplication keys.
Do not run multiple server instances against the same runtime directory.

For foreground development, stop the managed instance first:

```bash
.venv/bin/python -m yoyo.monitor.server --port 8766
```

The existing repository `.venv` supplies Python 3.9, FastAPI, Uvicorn, requests,
pandas and NumPy. Pillow renders notification snapshots. This delivery did not
install or change dependencies.

## What the signals mean

The owner explicitly requires a **visible startup marker on the actual
TradingView price chart** before Telegram. The observed chart on 2026-09-08
uses IMACD V2.2 with `showFocus=true`, `focusMinBars=12`, `focusAtrBand=.10`,
`showMarks=false`. Its visible marker is **focus release**, not raw zero
axis departure or a hidden original-system entry.

- **Visible chart start (`tv_start`)** clones the confirmed `focusRelease`
  price label. At least 12 near-zero bars qualified the zone, its ATR band is
  frozen, both previous IMACD lines were still inside that band, and current
  md strictly leaves it in the marked direction.
- Previous md can already be nonzero. A raw `zero_breakout` inside the band
  does not show this marker and never sends Telegram. A signal-line-only exit,
  an unqualified zone, and subsequent glow bars do not trigger the marker.
- Density and HTF remain background because the observed visible-marker
  branch is independent of the hidden ordinary system entries and exits.
- The signal API, main arrows, counts and notifications all use `tv_start`
  under `imacd-tv-visible-start-monitor-v3`. Original observations and old
  receipts remain historical and are never relabelled as current signals.

The profile ID is `imacd-v2.2-focus12-band0.10-marks-off`. This is an observed
settings snapshot, **not automatic synchronization of future TradingView
setting edits**. Match any future settings change explicitly before relying
on correspondence. The current settings were read and left unchanged.

A concrete chart comparison is ETH 4H on 2026-08-19: the visible label is
44 near-zero bars / close 1922.23, on the 08:00 UTC opening candle (12:00 UTC
close). Raw zero departure at price 1911.20 occurs two 4H candles earlier
and is correctly excluded. The notification caption shows confirmation time;
the image's time axis labels candle opens.

The first calibrated exchange-time activation is persisted. Reconstructed
bars at or before that cutover are historical and cannot be sent after an
upgrade; restarts preserve the same cutover. Old pending notifications are
marked skipped. The delivery worker independently checks the marker contract.

All displayed signal prices are confirmed candle **closing prices**, not fills.
15m receives 1H context; 30m receives 2H context; 1H receives 4H context; 4H receives UTC daily context; daily receives Monday-open UTC weekly context. Higher-timeframe values
must already have been available at the local candle's **open**. Missing higher
history is unknown. Daily boundaries can differ from a manually selected Pine
chart/session; finite startup history can also cause marginal state differences.
The monitor does not claim profitable or TradingView tick-for-tick equivalent
signals. It does not modify the saved Pine indicator.

## Data and timing

The universe refreshes hourly from public `SWAP` instruments in `live` state,
including USDT and coin-margined contracts. Symbols are not ranked or excluded
by recent returns or volume. An initial 720 closed bars per 15m/30m/1H/2H/4H/1Dutc/1Wutc stream
are fetched into RAM. The engine needs bar index 340 before readiness; very new
contracts remain explicitly warming up. A gap resets warmup instead of creating
a fake candle; conflicting confirmed quotes fail closed. The same 12-bar focus
rule applies on every period: on 15m that is three hours, on 30m six hours, and on daily twelve days. Daily needs at least 341 closed daily bars; weekly background needs 341 weeks. Missing or insufficient weekly history stays unknown and never blocks a visible daily start. This does not change
the indicator thresholds. Seven days is the journal retention window, not a
guarantee of seven days of initialized signals: 720 bars minus 340 warmup bars
initially covers about four days of eligible 15m observations.

Each newly added signal period has its own persisted exchange-clock activation.
Both queue insertion and TG/Bark delivery reject new-period closes at or before that
activation, including the most recent historical bar. Restart preserves this
cutover. Existing 1H/4H channel activations and receipts are unchanged.

Each completed scan is followed by a 120-second wait. At most eight public GET
requests per second are made across eight workers. Fully unchanged candles use
the in-memory result. During one process lifetime the original recurrence seed
is retained. On restart the finite history is fetched again; the UI explicitly
shows initialization and preserves past signals.

With N live contracts, a fully cold initialization needs up to about 21N candle
requests when all seven streams have three pages (short weekly histories need fewer). At the unchanged 8 requests/second,
475 contracts imply roughly 1,247 seconds for that full-page request budget. Parsing,
calculation and network overhead add to this. An ordinary 15m boundary needs
about N requests (59 seconds at 475 contracts), and fully aligned boundaries
can need 7N (416 seconds, only when the weekly close aligns too). Add the unchanged 120-second post-scan wait and
notification queue delay. Delivery may therefore lag beyond one shortest-period candle.
The scanner, sender and frontend still use the same 30-minute freshness gate.
Cold warming may exceed 15 minutes; inspect progress before restarting.
This Mac notification service is separate from the VPS forward pulse.
In-memory history retains its original seed, so RAM and recalculation cost
grow over time and are visible through scan duration.

30m has new persistent cutovers for both direct starts and model confirmation;
old periods and channel activations remain unchanged. No earlier 30m arrow is
replayed. The frozen model is reused on 30m without a profitability claim.
The owner disabled 5m: pending deliveries are skipped and unfinished model
candidates are marked disabled. Existing receipts and events are preserved;
the sender rechecks the active period before any delivery.

Only signals no more than **30 minutes** past close can enter the TG queue;
delivery rechecks the same limit. The frontend uses that same freshness limit.
Seven days of historical events are initially shown without notifying old
signals. The page polls every 15 seconds. Scanner health and stale chart state
are exposed independently from HTTP service availability.

Public contract: [OKX market data](https://www.okx.com/docs-v5/en/#order-book-trading-market-data).

## Telegram, Bark and privacy

The existing `yoyo.notify._load()` reads the owner's gitignored config or
`TG_BOT_TOKEN` / `TG_CHAT_ID` environment. No credentials enter frontend JSON,
source, logs, SQLite or acceptance artifacts. Exchange API keys are unnecessary.

Telegram uses a compact three-line caption: symbol/period/direction,
confirmed close/preparation bars, and Beijing confirmation time. A single
TradingView button replaces the raw URL. Each new eligible event includes a
1080-square PNG with up to 120 actual candles, six thin moving averages,
the startup marker and price, and IMACD blue/orange lines with a visible zero
axis. The chart ends at the signal candle; no future candles are included.

The renderer validates the target timestamp, close, and indicator values before
delivery. Rendering failure falls back to the compact text before HTTP. There
is never a second text send after an uncertain photo upload. New PNG bytes and
their SHA-256 are persisted with the event/outbox transaction so a 429 retry
uses the same picture. Existing historical/sent events are not backfilled or
replayed; an old pending event without media can use text. Snapshot counters
measure generated media, not photo-delivery receipts. Derived PNG storage grows
with notified events; raw candle arrays remain in RAM.

SQLite atomically inserts an immutable event, PNG and its outbox row. Repeated scans,
concurrent jobs and restarts do not requeue the same identity. Telegram 429s
honor `retry_after`; definite rejection is failed. Timeout, malformed response,
5xx or interrupted sending is **unknown**, not automatically resent. This
avoids blind duplicates but can lose a notification when delivery cannot be
confirmed; the signal and uncertain status remain visible. A successful delivery
requires an actual Telegram `message_id` receipt, plus photo metadata for images.

Public contract: [Telegram Bot API](https://core.telegram.org/bots/api#sendphoto).

Bark is an additional independent channel. Its owner-provided device key lives
only in `~/Library/Application Support/Fable/ImpulseMonitor/bark.json` with
mode 0600, separately from other project notification configurations. Restart
this service to load a changed configuration. The frontend exposes configured
state and counts, never the key or the private endpoint.

Only a new eligible `tv_start` after Bark's first calibrated-clock activation
can enter `bark_outbox`. Existing observations and Telegram receipts are not
replayed. Both channel rows are created atomically with a new event, then
handled by independent workers. Telegram remains enabled. No startup/test
notifications are generated. Failure or uncertainty in one channel cannot
consume the other's queue or success receipt.

Bark uses JSON POST to the official `/push` endpoint, with redirects disabled.
Its HTTP 200 / code 200 / server timestamp confirms server acceptance; it is
not a device display or read receipt. Definite client rejection is failed,
429 retries respect a bounded Retry-After, and uncertain delivery is not
automatically resent. Notifications contain the symbol, timeframe, direction,
close price, preparation count, candle times and the TradingView link.
Public contract: [Bark API V2](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md).

## Runtime locations

- SQLite: `~/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3`
- Singleton lock: same directory, `service.lock`
- Logs: `~/Library/Logs/Fable/ImpulseMonitor/`
- LaunchAgent: `~/Library/LaunchAgents/com.fable.impulse-monitor.plist`
- Code: `yoyo/monitor/`; tests: `tests/monitor/`

The VPS-owned OHLCV cache, forward log and production executor are not written.
No raw K-line files are persisted. Event rows contain derived state and the
signal price. The extra Codex heartbeat `imacd-mac` is paused following the
owner's question about unnecessary recurring AI checks. Market scanning,
Telegram and Bark run in the local service without an active Codex conversation.

`caffeinate -is` prevents idle sleep (system-sleep assertion while on AC power).
Leave this Mac plugged in and connected. Closing its lid, shutting it down,
logging out or losing network can interrupt monitoring. A phone's localhost is
not this Mac; the provided page is intentionally local to this computer.

## Verification

```bash
.venv/bin/python -m pytest -q tests/monitor
curl -fsS http://127.0.0.1:8766/api/health
curl -fsS http://127.0.0.1:8766/api/status
```

Tests include future perturbation, HTF availability, frozen focus bands, wick
semantics, failure/recovery at unchanged timestamps, concurrent SQLite insertion,
uncertain delivery, restart recovery, old-message suppression and clock rejection.
No economic performance metric is implied by passing these software tests.

### Open a structure in Mac TradingView

Clicking a signal, candidate or watch card sends that card's exact OKX swap
and `15m` / `30m` / `1H` / `4H` / `1Dutc` interval to `POST /api/tradingview/open`. The whole card
is a keyboard-accessible native button, with a separate **页内预览** button
that only opens spike's chart. The detail pane has a full-text **在 TradingView
打开** button and an explicit web fallback. Rendering, scanner updates and
refresh never activate the desktop app. Pending requests disable desktop-open
actions; preview remains available.

The owner authorized an AppleScript bridge on 2026-09-08. Mac Desktop 3.4.0
supports the [clipboard menu workflow](https://www.tradingview.com/support/solutions/43000673907-how-to-open-a-tradingview-chart-link-in-desktop-app/),
not a chart deep link through its registered login URL scheme. The bridge uses
the desktop window's internal menu: the macOS menu dispatch returned without
navigating in local tests. It reads live accessibility bounds for the title-bar
button; it does not assume absolute screen coordinates or modify a saved layout.
The generic `/chart/` URL is insufficient: it initially showed the requested
symbol in the desktop tab title, then restored a different symbol and interval
from the saved layout. Bind the query to the owner's verified concrete layout
and regional host instead. The local preference is a single HTTPS layout URL
in `~/Library/Application Support/Fable/ImpulseMonitor/tradingview-layout.txt`,
for example `https://cn.tradingview.com/chart/Ab12Cd34/` (example ID only).
Only concrete alphanumeric layout IDs on the `cn`, `www` or bare TradingView
host are accepted. Missing/invalid preferences fail before UI or clipboard
changes. This is an explicit local preference, not automatic synchronization
with future layout changes. The owner's existing `综合过滤` layout was verified
and configured on 2026-09-08; no Pine code or indicator settings were edited.

The service must run in this Mac's unlocked GUI session with TradingView
installed and signed in. Apple Events and Accessibility authorization are
evaluated for the process macOS identifies; shell/Codex authorization does
not prove LaunchAgent authorization. On 2026-09-08 the Automation switch
remained off after authentication, while focused TCC logs later showed the
owner's Accessibility grant for caffeinate. After the layout fix, actual
frontend clicks through the unchanged running LaunchAgent opened the correct
loaded chart. The switch's displayed state alone did not diagnose navigation.
Verify the completed chart's symbol and selected interval, not just the tab
title or a successful `open` command. No privacy database reset, helper app
installation or monitor restart was required for this fix.

Cold-start correction later on 2026-09-08 (installed Desktop 3.4.1, Electron
41.7.1): UI inspection had initialized Electron's accessibility tree before
the earlier acceptance checks. A clean process without pre-inspection could
instead expire in `window_ready`; another cold start returned a stale window
index (-1719). The bridge now sets `AXManualAccessibility` once, then polls
inside its existing deadline. Electron debounces that request, so the setter
must not be repeated inside the polling loop. Stale window references are
retried only during observation, never after dispatch. This does not grant or
reset macOS permissions. See [Electron's contract](https://www.electronjs.org/docs/latest/tutorial/accessibility#macos).

Failures include a bounded stage/code/reason contract. The Python handler logs
only those controlled fields and returns distinct layout, menu, clipboard,
readiness and timeout messages. -1719/-1728 are stale UI references;
-1712 is a timeout, not proof that the user must change Automation permissions.
-1743/-25211 retain permission guidance. No clipboard or raw UI text is logged.

Only same-origin POSTs carrying `X-Spike-Action: open-tradingview` are accepted;
the service remains loopback-only. Inputs are restricted swap/interval values,
and the URL is argv data, never shell or AppleScript source. One action runs at
a time. The script checks a 15-second deadline with 3-second per-event timeouts;
Python has a 25-second outer timeout. The clipboard is restored on ordinary
success/error unless the user copied something else. Hard process/OS failures
cannot guarantee restoration. The response says **requested**, not that market
data loaded. Errors are sanitized and surfaced; no automatic retries occur.

Validation:

Live frontend acceptance on 2026-09-08: whole-card click opened TRUTH-USDT
15m, Enter opened TRUTH-USDT 1H, and Space opened BICO-USDT 4H. Each completed
desktop chart exposed the corresponding symbol and selected interval, with
IMACD present. Selecting POET's **页内预览** updated spike's detail while the
desktop stayed on TRUTH. The enlarged detail button and card actions were
visually checked in the in-app browser; browser error/warning logs were empty.
The focused Python suite passed 31 tests, and card/theme JavaScript suites
passed 42 tests. These checks do not claim automatic desktop-load validation
on every future click or permissions persistence across macOS upgrades.

Cold-start retest: quit TradingView before each request and do not inspect its
UI until the request finishes. The live service opened BICO-USDT 4H and a
whole-card frontend click opened POET-USDT 15m; both completed chart identities
and intervals were verified afterward. The expanded focused Python suite
passed 85 tests. The previous warm-only check was insufficient for cold start.

```bash
.venv/bin/python -m pytest tests/monitor/test_tradingview.py tests/monitor/test_tradingview_layout.py -q
node --test tests/monitor/frontend_cards.test.cjs
osacompile -o /tmp/spike-tradingview-bridge.scpt yoyo/monitor/tradingview.applescript
```

### Appearance

The header's **外观主题** picker supports **深色**, **浅色**, and **跟随系统**
(default). An explicit choice is stored only in this browser's `spike.theme`
localStorage key and synchronized to other spike tabs on the same origin.
Restricted storage falls back to a working, nonpersistent selection. The theme
controller runs before CSS first paint to avoid flashing the wrong appearance.
Cards, status states, chart lines/candles/zero axis and the expanded chart dialog
share semantic CSS color tokens; switching does not reload chart data or reset
filters/selection. The Mac TradingView app's own theme remains independent.

```bash
node --test tests/monitor/frontend_theme.test.cjs tests/monitor/frontend_cards.test.cjs
```

## Daily stream rollout (2026-09-09)

The owner enabled daily starts and additive YOLO confirmations. Native `1Dutc`
closes at 00:00 UTC / 08:00 Beijing; native `1Wutc` starts Monday at that time.
The frontend and Bark label this period 日线; chart links use `interval=1D`.
Only events after the independent daily direct/model cutovers can notify. Existing
period cutovers and terminal receipts remain intact, with Telegram disabled.
Daily reuses the prior 4H background feed; only weekly context adds a data feed.
Signal thresholds, model weights, nine-bar confirmation budget and the 30-minute
freshness gate do not change. Daily model generalization/profitability is not
established by the engineering tests. Official bar contract:
https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks

### 1.14.2 · 多空信号与全量 R 账本（2026-09-13）

- 信号中心新增真实 V1 空头：按照同一 Pine 源的“方向=空头”输入独立回放，原有多头身份与状态保持不变。这是多、空两个独立参考流的并集，不是 Pine“方向=双向”的共享持仓回放。空头以 `spike-burst-v1-short-monitor-v1` 记录，当前只展示，不进入 Bark 或 YOLO。
- 首次同步持久化 `display_policy:spike-v1-short`。切点之前的空头进入“预热历史”；缓存补齐每轮最多 16 个未变化单元，出现新收盘时正常更新。历史回填不冒充新信号、不补通知。
- `GET /api/signal-ledger` 提供全量统计与分页。参数：`source=live|warmup|replay`、`confirmation=raw|yolo`、`period=today|week|all`、`timeframe=15m|30m|1H|4H`、`side=long|short`、`search`、`outcome=all|active|profit|loss|breakeven|unknown`、`sort=newest|oldest|r_desc|r_asc`、`offset`、`limit`（1–2000）。旧 `/api/signals` 保留兼容，raw 返回多、空协议。
- 统计先按原始事件去重，YOLO 关联原始信号的最新参考路径。按原始信号收盘日归属北京时间当天/本周（周一开始）；不是当天平仓现金流。所有筛选先于统计和分页，四周期表遵循相同筛选。
- 已结束合计用 `exit_r`，运行中合计用 `current_r`；缺失及非有限值保持未知，不借用峰值 R。胜率分母为具有有限退出 R 的已结束信号，保本计入分母。这些是未扣成本的信号参考 R，不是实际账户成交或可累乘账户收益。
- 历史回放记录没有可信 `performance` 时显示“等待计算/—”，不会把 `covered_ledger` 中不同执行时钟与成本口径的收益混入信号参考统计。
- 卡片整卡打开本机 TradingView、深浅色与无右侧预览布局保留；新增多空、状态、时间筛选和全量 R 排序。兼容旧 Windows 网关的前端使用 `/api/signals?view=ledger` 只读别名，无需同步升级 Windows 客户端；新 `/api/signal-ledger` 也在更新版网关 allowlist 中。

定向验证：

```bash
.venv/bin/python -m pytest -q tests/monitor/test_signal_analytics.py tests/monitor/test_spike_v1_api_replay.py tests/monitor/test_v1_short_signals.py tests/monitor/test_v1_card_performance.py tests/monitor/test_v1_worker_cache.py tests/monitor/test_v1_status_snapshot.py
node --test tests/monitor/frontend_cards.test.cjs tests/monitor/frontend_theme.test.cjs
```

工程验证使用合成事件：2003 条全量分页、北京时间边界、YOLO 去重/最新 R、多空隔离与历史切点。浏览器实测盈利筛选按退出 R 降序、当天统计、预热空头卡片与两种主题。未做新的策略收益评估，不代表空头表现已验证。

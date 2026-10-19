# Discord 跟单（yoyo/copier）

Discord 频道里的交易信号 → DeepSeek 解析意图 → 风控 → 按频道路由到 OKX / Gate 下单，
Telegram 推送结果。2026-10-07 从 `~/discord-okx-copier` 迁入本仓，操作界面并入统一工作台。

**这是真金系统。** 切到实盘、解除保护开关、修改仓位/杠杆参数、更换 API key，
都只能由 owner 本人操作（CLAUDE.md 实盘纪律 11）。迁移后的默认状态是模拟模式。

## 结构

| 部分 | 位置 | 说明 |
|---|---|---|
| 后端服务 | `yoyo/copier/`，`python -m yoyo.copier.main` | 127.0.0.1:8080，独立进程 |
| 依赖 | `requirements-copier.txt` → `.venv-copier/` | 不进研究 venv，避免动 torch/numpy/pandas 契约 |
| 运行数据 | `~/Library/Application Support/Fable/DiscordCopier/` | `.env`（0600）、`config.yaml`、`config/`、`data/copier.db`、历史抓取 JSON；不入 git |
| 操作界面 | 统一工作台 `http://127.0.0.1:8766/#copier` | 经同源代理 `/api/copier/*`（`yoyo/monitor/copier_proxy.py`） |
| 信号入口 | Chrome 扩展 `tools/discord_copier_extension/` | 直接 POST 到 8080 `/api/messages/ingest`；工作台不代理这个入口 |
| Telegram 截图页 | 8080 `/capture/orders-card` | `admin_snapshot.py` 用无头 Chrome 截 `.capture-orders-panel` |
| 测试 | `tests/copier/` | 只在 `.venv-copier` 里跑；断网、空运行目录、假凭据 |

## 监控哪些频道

扩展只读 Chrome 里正在显示的 Discord 服务器频道，私信一律不读。要监控的频道各保留一个标签页。

2026-10-15 owner 收敛为唯一频道 **Arthur 行情分析**：服务器 `1004707886657699901`，频道 `1131521990814089276`，路由 `paper`。`discord_watchlist.channel_ids` 是严格边界；标题变化和手动打开其他频道不会扩大范围。其他频道退出采集与撮合，历史消息、订单和持仓保留。

Chrome 桥接扩展当前为 `0.4.0`，源目录 `tools/discord_copier_extension/`。更新已安装扩展后，在 `chrome://extensions` 重新加载，再刷新指定频道。扩展每 30 秒发送频道心跳；工作台显示心跳与最后入库时间。心跳只证明页面连接，不能证明历史消息完整。Discord 要求重新登录时采集不能继续。

- 原消息发布时间取 Discord snowflake，入库时间另列。
- 初始加载的历史恢复只解析、不执行；定期可见消息扫描仍经过服务端 10 分钟新鲜度门。
- 修改后的原文另存审计版本、等待核对，不作为第二次开仓执行。
- 上传失败允许重试，成功接收后才去重。
- 博主实际业绩需要完整成交与原始风险证据；页面的模拟胜率和净 R 不能替代它。

## 模拟实盘（paper 路由）

路由到 `paper` 的频道和实盘走同一条链路：解析、风控、入场价、杠杆、仓位算法都相同，
只是订单落到本地账本（`yoyo/copier/paper/`），不碰任何交易所账户。
- 每个频道一个独立账户，起始 1000 USDT。开仓按固定风险（owner 2026-10-07）：每笔 1R = 账户已实现权益的 1%（`paper_risk_pct` 设置），仓位 = 1R ÷ 止损距离，以该频道杠杆允许的最大仓位封顶；没有止损的信号不开仓。实盘仍按保证金比例开仓（可用余额 × 仓位比例 × 杠杆）。
- 撮合用 OKX 已收盘 1 分钟 K 线的高低点，加上每 5 秒一次的最新价，能看到两次轮询之间的插针。
  同一根 K 线同时碰到止损和止盈，按先止损处理；已存在持仓只从开仓后的完整分钟线开始检查；历史分钟线内补成交的限价单会检查该根止损。无法确认高低点先后时，不将可能发生在入场前的止盈计作盈利。
  OKX 没有的合约改用 Gate 的最新价，但 Gate 的 K 线接口需要 key，这部分只按最新价撮合。
  限价单价格被穿越时成交；止损按触发价成交，跳空开盘时按开盘价，最差不超过强平价；止盈按止盈价成交。
  强平按逐仓近似计算。
- 每笔 R 分母累计每次入场的初始止损风险，部分退出不改写分母。旧版多次加仓记录无法可靠还原时保留未知 R；未知 R 不计入 R 指标，但已知净盈亏仍计入净胜率。
- 成本按单边 0.1%（往返 0.2%），不计资金费和盘口冲击。
- 博主后续的平仓、部分平仓、移止损、改止盈、撤单消息，都作用在模拟持仓上。
- 全局 `dry_run` 保持开启：路由到真实交易所的频道仍然不下单。

模拟结果是否跑赢随机入场没有验证，不能拿来作为切实盘的依据（见仓库 CLAUDE.md 验收口径）。

## 通知与 AI 调用（2026-10-07）

- Telegram 通知已全部关闭（功能开关 `telegram_notify=false`），只在工作台查看；
  机器人仍会回复你在控制群里主动发的命令。
- 本地格式规则认不出、又没有币种、数字和交易用语的闲聊，直接判为非信号，不调用 DeepSeek
  （`yoyo/copier/ai/prefilter.py`）。用历史 505 条消息回放过：被挡掉的只有 34 条非信号和 1 条"观察"类消息，
  真实的开仓、平仓、移止损一条都没挡。DeepSeek 调用设 20 秒超时、最多重试 1 次。
- 超过 10 分钟才到达的消息（断网恢复、睡眠唤醒后补出来的）只记录不执行，可用 `max_signal_age_min` 设置调整。

## 常用命令

```bash
.venv/bin/python -m yoyo.copier.manage status     # launchd 真实状态 + 旧服务是否残留
.venv/bin/python -m yoyo.copier.manage restart
.venv/bin/python -m yoyo.copier.manage stop
.venv/bin/python -m yoyo.copier.manage backup     # sqlite 一致性备份到运行目录 backups/
.venv-copier/bin/python -m pytest tests/copier -q
```

从零重建：

```bash
/usr/bin/python3 -m venv .venv-copier
.venv-copier/bin/pip install -r requirements-copier.txt
.venv/bin/python -m yoyo.copier.manage migrate --source ~/discord-okx-copier   # 只在首次迁移时
.venv/bin/python -m yoyo.copier.manage retire-legacy
.venv/bin/python -m yoyo.copier.manage install
.venv/bin/python -m yoyo.monitor.manage restart                                 # 工作台加载代理
```

`migrate` 拒绝覆盖已存在的数据库，并在副本里写入 `dry_run=true` 和一条审计记录。

## 安全边界

- 8080 只接受本机 Host；带浏览器 Origin 的写请求只放行 Chrome 扩展和本服务自身。
  旧服务对所有来源开放 CORS，又由 cloudflared 隧道把 8080 无鉴权地暴露到公网，
  拿到链接的人可以注入信号或切实盘。旧隧道已下线（`retire-legacy`），`install` 在它仍加载时拒绝启动。
- 工作台公网入口（`yoyo.monitor.public_tunnel`）给请求加 `X-Fable-Gateway: public`。
  经公网只能读取，以及做"停止交易"方向的操作（开保护开关、切回模拟）；
  恢复实盘、解除保护、改参数只能在本机。
- 测试进程不加载真实 `.env`：旧项目跑测试时会把实盘 key 读进来，
  部分测试还会经本机代理真实请求交易所账户。

## 迁移时发现的问题（2026-10-07）

- 服务自 2026-09-14 起停摆：旧 venv 的 `python3` 指向 `/Applications/Xcode.app`，Xcode 删除后
  launchd 持续重启一个不存在的解释器（退出码 127），管理面板和隧道却还在跑，看起来像正常。
- 最后一条 Discord 信号是 06-26，说明 Chrome 扩展在服务停摆前就已不再提交信号。
- OKX（ChartPrime 路由）返回 `API key doesn't exist`，需 owner 重新创建 key；Gate 三个账户连接正常。
- 旧 venv 同时装了 `python-okx` 与 `okx-sdk`，两者都写 `okx/` 包目录；新环境只装前者。
- 已弃用的 `user_poll`（discum 自动化用户 token）不再安装依赖。

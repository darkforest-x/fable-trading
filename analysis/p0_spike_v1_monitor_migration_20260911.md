# SPIKE V1 Mac monitor migration：信号浏览、冻结回放图与服务验收记录

## 结论

本轮把本机 `127.0.0.1:8766` 的监控协议收敛到冻结的 **SPIKE V1 长多 30m / 1H / 4H**：收盘后的原始 V1 启动和额外 YOLO 确认是独立事件、独立 Bark 阶段；历史回放只用于浏览且绝不补发 Bark。已导入的 1,019 条 V1 回放信号可按 event id 读取同源冻结 OHLC，并在前端明确标为历史复盘上下文；它们已另行联结到 v2 覆盖账本，但联结不是盈利或策略有效性证明。

本机服务可监听且关键 `/api/status` 超时根因已经通过 trace 确认并修复；当前首轮扫描仍在进行，不能把进程存活或部分完成误称为市场就绪。共享 IAB 已完成一张实时卡的实际预览验收，桌面/窄屏的全部交互仍待补齐。没有任何盈利、模型泛化或 Bark 手机送达声明。

## 协议与数据边界

| 项目 | 当前行为 |
| --- | --- |
| 实时信号 | 原版 SPIKE V1、仅 long、30m / 1H / 4H 的已收盘 bar |
| 通知 | `source=live, confirmation=raw` 与随后 `source=live, confirmation=yolo` 分别去重、分别允许 Bark；Telegram 关闭 |
| 回放 | `source=replay, confirmation=raw`；无候选、无 outbox、无通知 |
| 回放图 | 只由已保存 event id 定位 `venue/symbol/timeframe/bar_open_ms`；按需读取冻结 gzip OHLC；后续 K 线标为历史复盘，未输入扫描器或 YOLO |
| 回测指标 | 回放只显示有四元组与 SHA 证据的 v2 单笔退出/净 R；已实现与 censored 分开，均标为未独立收益审核，不推断 PnL |

回放导入命令已在此配置下执行一次：

```bash
python3 -m yoyo.monitor.replay_import \
  experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz \
  --database "$HOME/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3" \
  --import-id spike-v1-covered-ledger-20260911-unverified-partial
```

结果为 783 条新导入、236 条既有相同记录、11 条不属于 V1 30m/1H/4H 的记录跳过；运行库最终有 1,019 条 `replay/raw`。对这 1,019 条已导入记录按文件存在性检查，冻结 OHLC 覆盖为 1,019/1,019；这里的覆盖只说明可画出对应源 K 线，不说明交易完整性或策略表现。

## 回放账本逐笔联结（`9015364`、`f831db3`）

signal-only importer 保持不复制 outcome；随后独立命令以 `venue/symbol/timeframe_min/signal_bar_open_ms` 精确联结运行库中的 1,019 条 replay/raw 与冻结 `covered-v2-next-open-risk-realized-event-sequence` ledger。实际运行得到 1,001 条 realized、18 条 censored、0 unmatched/source mismatch/OHLC missing；四元组、monitor journal id 和 ledger source-event id 均无重复。每条 payload 同时保存两种 id、Pine/source、ledger、coverage receipt 和冻结 OHLC SHA。

此操作只调用已有事件的 payload update，不经 event insertion、outbox 或 candidate 路径；运行后 Bark outbox 仍为 0。前端为 realized 行显示冻结账本的退出与“覆盖净 R · 非账户”，为 censored 行明确显示“不计胜率、PF 或净收益”，并持续标注“未独立收益审核”。逐笔 receipt、命令和完整边界见 [p1_spike_v1_replay_ledger_link_20260911.md](p1_spike_v1_replay_ledger_link_20260911.md)。

历史遗留处理限定为源为空、协议为旧 IMACD 的 9,160 条错误 journal 行；它们已在有完整数据库备份后移除。迁移期间另发现 5 个可由 canonical V1 raw identity 替换的旧重复 id，已幂等清理；11 个无 canonical 替代的旧 id 未伪造替代记录。没有再次清库，也没有触及本次 live/replay 行。

## 回放图实际接口核验

清理 trace 后，对本机 API 读取一条已导入记录：

| 字段 | 实测 |
| --- | --- |
| event id | `120295e8085b51f7fc21ca36` |
| 来源 | Binance `ARCUSDT`，30m，`bar_open_ms=1788971400000` |
| `/api/replay/chart` | HTTP 200，105 根实际可用上下文 K 线 |
| 定位 | 返回 target K 线时间与该 event 的 `bar_open_ms` 完全相同 |
| target OHLC | 0.07349 / 0.07850 / 0.07297 / 0.07774 |
| 未来上下文 | `historical_review_only_not_model_input` |

该端点拒绝客户端提供 venue、symbol 或时间；只从数据库中的 event 读取。缺冻结文件会显式返回 `frozen_ohlc_missing`，不会改画当前 OKX 行情。

## HTTP 与扫描真实状态

诊断 trace 先排除了 `/api/status` 自身的数据库读取：它的 handler 可在约 16ms 到达。真正阻塞来自 `/api/markets`：旧实现会同步 JSON 解码 214 行完整 market payload，合计约 11.5MB（每行含 chart/events），并在共享解释器路径序列化响应。trace 记录的两次旧请求内耗时为 25.792s 与 19.595s。

提交 `181193f` 后，概览改读排除 `chart/events` 的摘要，选中图才按主键读取完整记录。受当前机器资源竞争影响，摘要仍需 SQLite 解析旧 JSON 输入，trace 内为 5.569s，实际响应约 6.323s，输出仅 85,842 bytes；但紧跟其后的 `/api/status` 成功返回，handler 约 488ms、请求总计 556ms，没有再次零字节超时。若未来必须进一步压低市场概览延迟，正确范围是写入时保存紧凑 summary 列或表，不能把 chart/events 再塞回概览。

最终 cleanup reload 后的单次实际 API 检查：

| 检查 | 结果 |
| --- | --- |
| `/api/health` | HTTP 200，`service_alive=true`，扫描写入当时为 `30 / 1434`、errors `0`；但 `market_ready=false`、`model_ready=false`、`ok=false` |
| `/api/status` | HTTP 200，非 stale 快照；紧邻读取的已缓存扫描快照为 `27 / 1434`、errors `0`，模型仍 `loading`。扫描写入与状态快照不同步，故不把两者混为一个精确完成计数 |
| `/api/markets` | HTTP 200，214 条摘要：30m 72、1H 71、4H 71，三周期这些已记录行均为 ready；没有 `chart/events` 字段 |
| Bark 状态 | `v1_bark_arm` 已设置；当前 V1 bark outbox 为 0，未发送测试消息 |

因此三周期已开始扫描但远未完成，当前自然 live raw 为 22 条、live YOLO 为 0 条。没有把待预热、扫描中或模型加载中表示为可通知状态。

## 发布后运行验收（`cc6bf62`）

`cc6bf62` 保留完整 720 根的冻结特征与 replay，只在 worker 内避免构造随后会丢弃的前 480 条 chart JSON；state、events 与末 240 条 chart 的逐值等价测试通过。它还将无候选时的 YOLO 状态从误导性的 `loading` 改为 `idle`：该状态仍为 `loaded=false`、不等于 ready，也不会改变任何通知判定。

发布后的第一次读取看到 `117 / 1434`，但其 `scan.started_at_ms` 早于新 monitor 的 `started_at_ms` 约 15 分钟，故它是重启前轮次留下的持久 meta，不能作为此次发布的吞吐证据。进程树复核只剩新 parent `72471` 与其两个 multiprocessing child `72653` / `72777`，没有旧孤儿 writer。新 scanner 随后写入新的 `scan.started_at_ms=1789068913215`；低频连续两点从 `3 / 1434` 到 `39 / 1434`、errors `0`，约 33 cells/min。这只是当前资源条件下的一分钟观测，不是 15 分钟 SLA 或全轮预测。

同一最终检查的 `/api/health` 为 HTTP 200 / 696ms，`market_ready=false`、`model_ready=false`、`ok=false`；`/api/status` 为 HTTP 200 / 384ms，scanner 仍为 `scanning`。YOLO 为 `idle`、`loaded=false`、queue `0`、processed endpoints `0`、last error `null`，即没有 raw candidate 时按设计延迟加载，而不是已经推理就绪。首轮全市场尚未完成，不能称 ready。

## Cutover 候选与实际卡片预览

冷首轮发现 IBM `4H` 的 raw 记录 `0b2652408b7f176fa6b5f831`：它在 Bark arm 前约 24.8 小时收盘，但仍落在旧的九根候选窗口内。raw 的 Bark 判定已正确拒绝它，所以 outbox 始终为零；问题是旧代码仍会把它送入 YOLO 候选。`3f98028` 现以同一 raw eligibility 判定注册候选，并在模型 worker 处理持久候选前再次验证原始 bar 的 cutover。这个历史候选在数据库中标为 `disabled/raw_not_eligible:before_bark_activation`，原始事件和审计保留；没有删除 live/replay 记录、没有发送错误通知，也没有为这项小修重启正在缓存首轮的 worker。

另一个真实前端合同错误出现在实时 DATA 30m 卡片。浏览器内显示周期为 `30`，而 `/api/chart` 只接受 `30m`；`85566e2` 在请求边界显式映射 `30/60/240 → 30m/1H/4H`，未知周期 fail closed。已直接读取 `DATA-USDT-SWAP` 的 `/api/chart?timeframe=30m`，返回 240 根 K 线。共享 IAB 刷新后实际点“页内预览”，右侧呈现价格刻度、6 MA、120 根信号附近 K 线、V1 风险线和时间轴；截图为 `analysis/output/spike_v1_ui_20260911/desktop-live-before-theme.png`。图中未出现服务 HTTP 400 或伪造行情。

实机继续覆盖了深/浅主题、放大、滚轮缩放和拖拽：缩放后时间轴从 `9/8 17:00` 延至 `9/10 16:30`，拖拽后为 `9/8 05:00` 至 `9/10 04:30`，风险线和 K 线保持同一视图；收起时 dialog 已关闭。桌面与 390×844 窄屏也成功读取 Binance AIXBT 4H 的同源冻结 OHLC：信号 `2026-09-05 12:00`、收盘 `0.02197`、风险 `0.01915`，图中明确注明信号后的 K 线仅供回看。窄屏 `clientWidth=scrollWidth=390`，详情、放大/收起和返回卡片均可用。相应截图在 `analysis/output/spike_v1_ui_20260911/`。

随后发现 source/周期切换时，已飞行的实时请求可在回放筛选之前落地，短暂重选 IBM live 详情。`ad6a98e` 用 `source + timeframe + revision` 作为查询上下文：切换时先清空旧卡片/详情/图表为加载态，旧回包不再写入；若切换发生在同步期间，则当前上下文自动排队重取。图表回包还必须匹配当前 id、source、confirmation 与收盘时刻。受控 IAB 复验中，DATA live 立即切到 replay 4H 后，页面显示“正在读取历史回放”、卡片为空、详情为空且没有 IBM/DATA 残留；AIXBT 的展示符号已从 `AIXBTUSDT · USDT` 收敛为 `AIXBT · USDT`，预览 aria 标签加入 venue。

`c70f10f` 进一步清除了空详情时遗留的 chart aria，并使加载态 aria 直接描述当前 source、合约和周期。最终受控检查中，replay ARC Binance 1H 实际载入 98 根冻结 K 线（`2026-09-06` 至 `2026-09-10`）、风险 `0.0714`；工具 DOM 已确认真实图表数据，筛选截图为 `desktop-replay-1h-filter.png`。随后从 1H 切到 30m 仍立即回到空列表/空详情加载态，没有旧卡片、旧图或旧 aria。

最后，ARC Binance 30m 也从同一冻结来源真实载入 105 根 K 线、风险 `0.07145`（`desktop-replay-30m-filter.png`）。因此当前桌面和窄屏均已验 live/replay、30m/1H/4H、主题、选择、放大/收起、缩放、拖拽、同源历史图和筛选加载态；没有重复执行这些 UI 场景。

独立复核还验证 `3f98028` 的候选守门按 **raw bar 的原始收盘时刻** 重判，所以不会把合法的 1H/4H 等待窗口误杀。`e5f006b` 对第二阶段明确分时钟：raw 仍必须在当时通过 cutover/新鲜度，YOLO 追加事件则按自己的确认收盘时刻发送 Bark。回归覆盖“raw 已两小时、当前 1H 确认刚收盘 → 入库并追加 Bark”及“确认自身过期 → 仅入库、不补发”。这个 Python 后端提交尚未载入正在完成冷首轮的 worker；静态前端提交无需重启已由 IAB 验收。

在 2026-09-11 的低频只读检查中，当前同一 scan `started_at_ms=1789068913215` 处于 `875 / 1434`（status 快照为 871）、errors 0；`/api/health` 为 636ms，`/api/status` 为 491ms。模型已实际 `ready`、`loaded=true`，已处理 7 个 endpoint，队列 0，唯一候选为上述 disabled 历史项。`market_ready=false` 仍正确，因为首轮没有完成；Bark outbox 的 pending/sent/failed/unknown 均为 0。

## 实现与验证

本轮 monitor 提交链：`bf08b3c`、`8129087`、`603ea45`、`07e2db7`、`468fc5a`、`229b654`、`6b11464`、`a481311`、`1e5a286`、`7ce4971`、`df6c8f6`、`63e0b29`、`f35caf4`、`181193f`、`cc6bf62`、`3f98028`、`85566e2`、`ad6a98e`、`a006636`、`c70f10f`、`e5f006b`。其中 `f35caf4` 使 replay 图依赖在选卡时才导入，避免 pandas/NumPy/PyArrow 在 FastAPI 监听前阻塞；`181193f` 是上述 overview blocker 的最小修复；`cc6bf62` 是保留因果输入的显示物化优化与 V1 gate 测试迁移；后续提交分别保护 cutover 候选、实时图表周期、筛选后详情、跨查询上下文的旧回包、加载态 aria 和延迟确认的通知时钟。

本轮实际执行的定向代码验证：

```bash
python3 -m pytest -q \
  tests/monitor/test_v1_chart_limit.py \
  tests/monitor/test_v1_worker_cache.py \
  tests/monitor/test_model_gate.py \
  tests/monitor/test_spike_v1_yolo_extra.py \
  tests/monitor/test_spike_v1_health.py
python3 -m py_compile yoyo/monitor/signals.py yoyo/monitor/v1_worker.py yoyo/monitor/model_gate.py
node --check yoyo/monitor/static/app.js
node --test tests/monitor/frontend_cards.test.cjs tests/monitor/frontend_theme.test.cjs
```

本次最终 V1 gate/worker focused suite 为 46 passed；此前回放/API 相邻 suite 为 10 passed。它们不是两个独立测试集，不应相加。`test_model_gate.py` 已迁到 current V1 live/raw/long/closed 合同，保留到期、延迟确认、去重、重启和因果端点覆盖；旧 IMACD `md` 失效断言以 V1 source/confirmation/direction/side/risk fail-closed 注册测试替代。随后对 cutover 修复的组合 suite 为 25 passed，前端 Node 合同测试为 10 passed，延迟确认 focused suite 为 11 passed；它们含重叠文件，不能相加。此前独立 CUA surface 的 `getTab('3', {browser:'1'})` 返回 `Browser is not available: 1`，但 root 已用共享 IAB 完成上述真实卡片与窄屏验收。静态/API 合同检查不替代尚未完成的全市场扫描。

## 扫描覆盖显示（`4419b1b`）

首页曾将持久化的 `counts.ready` 显示为“窗口已就绪”，但它可以来自上一轮 feature phase，不能证明新一轮已追平交易所收盘；短历史合约也可能未达到 340 根 warmup。现在概览只显示本轮 `scan.completed / scan.total`：扫描中为“已扫描 · 预热/追平中”，整轮完成后为“已覆盖，按收盘刷新”。它不把 `phase=ready` 或强制 `stale=false` 当作最新 bar 的证据。

## Scanner recurrence checkpoint（`64d4308`，待单次受控 reload）

诊断确认 scanner 的完整 OHLC recurrence 过去只在 worker RAM；`markets.chart` 只有 240 根，不能替代 V1 的 340 根 warmup，也不能安全拼接增量。`64d4308` 新增 monitor 私有 SQLite 的 gzip raw checkpoint：每个成功获取的完整序列在 replay/market write 前保存，重启时只有通过周期对齐、连续性、有限 OHLCV 和价格边界校验的序列才会恢复；异常或 gap payload fail closed，回到 cold fetch。

定向恢复测试以 341 根种子重启并追加一根，断言恢复后的 V1 state 和末 240 chart 与完整序列重放相同；另测 gap checkpoint 不会进入 recurrence seed。它不裁剪输入、不改 Pine、特征、cutover 或通知逻辑。

在第一轮完成后仅执行一次受控 LaunchAgent reload；旧 RAM seed 因旧 worker 尚未具备 checkpoint 能力而不可迁移，这是首次部署的明确边界。新 worker 的 scan `started_at_ms=1789077675850` 从 0 进至 21/1,434、errors 0，并已有 22 个 checkpoint。model ready 后，两个重启前遗留且早于 Bark cutover 的 IBM 4H、STABLE 1H candidates 都变为 `disabled/raw_not_eligible:before_bark_activation`；Bark pending/failed/unknown 保持 0，未发生历史补发。

## 运行环境与未完成项

受控 trace 已从 LaunchAgent 移除并以无 trace 的 cleanup reload 启动。启动仍受本机其它 ccxt-mcp/ChatGPT 作业竞争影响：诊断时系统 load 约 100、CPU idle 0%、swap 约 6.4GB；本任务无权停止其它会话。监听器可在约一分钟后建立，因而不能以冷启动瞬时健康响应设定性能承诺。

此前本任务暂停的 `yoyo.evaluation.spike_v1_twoyear_allmarkets fetch --venue binance`（PID 51814）已核对命令身份并 `SIGCONT` 恢复，进程从 `T+` 变为 `S+`。它是独立两年采集，不是本轮 monitor 结果；恢复不表示其评估账本问题已解决。

后续仍需在资源可用时完成：全 1,434 单元扫描，并在不丢失内存 OHLC cache 的受控 reload 时载入 `e5f006b`。更广泛 V1 或 YOLO 有效性、收益和手机实际送达均不在本次功能验收范围。
